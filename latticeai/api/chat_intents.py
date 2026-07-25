"""Fast-path intent and governed file-action handlers for chat."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from latticeai.api.chat_contracts import AgentRequest
from latticeai.api.chat_helpers import (
    file_action_target,
    format_network_status,
    inline_file_action_content,
)
from latticeai.api.chat_stream import (
    agent_live_stream,
    agent_payload_stream,
    single_answer_response,
)
from latticeai.core.agent import AgentState
from latticeai.core.file_generation import (
    PREVIEWABLE_EXTENSIONS,
    generate_file_content,
    infer_file_target,
    infer_project_manifest,
    repair_bundle_references,
    validate_project_bundle,
)


def next_available_path(root: Path, target: str) -> str:
    """Return ``target`` unchanged, or a ``name_2.ext``-style variant when the
    file already exists in the workspace.

    The direct chat write path must never silently overwrite an existing file:
    overwrites belong to the reviewable-proposal flow. Deterministic suffixing
    keeps "make me an html page" repeatable without a 409 dead end.
    """
    candidate = (root / target)
    if not candidate.exists():
        return target
    stem, suffix = candidate.stem, candidate.suffix
    # An earlier auto-suffix run may have produced name_2 already; restart the
    # numbering from the base name so we fill the first free slot.
    base = re.sub(r"_\d+$", "", stem) or stem
    for index in range(2, 100):
        variant = candidate.with_name(f"{base}_{index}{suffix}")
        if not variant.exists():
            return str(Path(target).with_name(variant.name))
    raise HTTPException(
        status_code=409,
        detail=f"'{target}' 이름의 파일이 너무 많습니다. 다른 이름을 지정해 주세요.",
    )


def ingest_generated_enabled() -> bool:
    """Feature toggle for auto-indexing generated files into the Brain."""
    return os.environ.get("LATTICEAI_INGEST_GENERATED", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }


class ChatIntentController:
    def __init__(
        self,
        *,
        model_router: Any,
        config: Any,
        public_model: str,
        chat_service: Any,
        notify: Any,
        clear_history: Any,
        clear_conversation: Any,
        history_scope_for_user: Any,
        append_audit_event: Any,
        enable_graph: bool,
        knowledge_graph: Any,
        enforce_tool_policy: Any,
        network_status: Any,
        tool_error: type[Exception],
        execute_tool: Any,
        agent_controller: Any,
        agent_root: Path,
        ingestion_pipeline: Any = None,
        funnel_metrics: Any = None,
    ) -> None:
        self.router = model_router
        self.config = config
        self.public_model = public_model
        self.chat_service = chat_service
        self.notify = notify
        self.clear_history = clear_history
        self.clear_conversation = clear_conversation
        self.history_scope_for_user = history_scope_for_user
        self.append_audit_event = append_audit_event
        self.enable_graph = enable_graph
        self.knowledge_graph = knowledge_graph
        self.enforce_tool_policy = enforce_tool_policy
        self.network_status = network_status
        self.tool_error = tool_error
        self.execute_tool = execute_tool
        self.agent_controller = agent_controller
        self.agent_root = Path(agent_root)
        self.ingestion_pipeline = ingestion_pipeline
        self.funnel_metrics = funnel_metrics

    def _funnel_increment(self, name: str) -> None:
        """UX funnel counter (backlog #16) — advisory, never raises."""
        if self.funnel_metrics is None:
            return
        try:
            self.funnel_metrics.increment(name)
        except Exception as exc:  # noqa: BLE001 — metrics must never break chat
            logging.warning("funnel metrics increment failed: %s", exc)

    def no_model_response(self) -> JSONResponse:
        detail = "No model loaded. Call /models/load first."
        if self.config.is_public:
            detail = (
                "No public model loaded. Set OPENAI_API_KEY and "
                f"LATTICEAI_PUBLIC_MODEL={self.public_model}, or call /models/load "
                "with an OpenAI-compatible model."
            )
        return JSONResponse(
            status_code=400,
            content={
                "error": "no_model_loaded",
                "detail": detail,
                "message": detail,
                "action": "load_model",
            },
        )

    async def network(
        self,
        req: Any,
        *,
        current_user: str,
        history_meta: Dict[str, Any],
        history_user: Dict[str, Any],
    ):
        history_message = (
            f"{req.message}\n[Image attached]" if req.image_data else req.message
        )
        self.enforce_tool_policy(
            "network_status",
            {},
            current_user=current_user,
            source="chat_intent",
            trusted_admin=not bool(getattr(self.config, "require_auth", False)),
        )
        try:
            answer = format_network_status(self.network_status())
        except self.tool_error as exc:
            answer = f"네트워크 정보를 확인하지 못했습니다: {exc}"
        await self.chat_service.persist_exchange(
            request_message=req.message,
            stored_user_message=history_message,
            answer=answer,
            source=req.source,
            history_meta=history_meta,
            history_user=history_user,
            notify=self.notify,
        )
        return single_answer_response(req, answer, model="network_status")

    async def clear(
        self,
        req: Any,
        *,
        effective_email: Optional[str],
        workspace_id: Optional[str],
    ):
        command = req.message.strip().lower()
        clear_scope = "all" if command == "/clear_all" else "conversation"
        if self.enable_graph and self.knowledge_graph:
            try:
                self.knowledge_graph.ingest_event(
                    "ClearEvent",
                    f"{command} requested",
                    user_email=effective_email,
                    user_nickname=req.user_nickname,
                    source=req.source or "web",
                    conversation_id=req.conversation_id,
                    workspace_id=workspace_id,
                    metadata={"command": command, "scope": clear_scope},
                )
            except Exception as exc:
                # Clear remains available even when optional graph audit ingest
                # is unhealthy; the primary audit event below is authoritative.
                logging.warning("knowledge graph clear event ingest failed: %s", exc)
        scope = self.history_scope_for_user(effective_email)
        if command == "/clear_all":
            result = self.clear_history(0, **scope)
            prefix = "채팅창을 정리했습니다."
        elif req.conversation_id:
            result = self.clear_conversation(req.conversation_id, **scope)
            prefix = "현재 대화방 채팅창을 정리했습니다."
        else:
            result = self.clear_history(0, **scope)
            prefix = "채팅창을 정리했습니다."
        answer = (
            f"{prefix} 화면에서 제거 {result.get('removed', 0)}개. "
            "감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        )
        self.append_audit_event(
            "clear_command",
            user_email=effective_email,
            user_nickname=req.user_nickname,
            source=req.source or "web",
            conversation_id=req.conversation_id,
            command=command,
            scope=clear_scope,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        self.notify("user", req.message, req.source)
        self.notify("assistant", answer, req.source)
        return single_answer_response(req, answer, model="history")

    async def current_url(
        self,
        req: Any,
        *,
        history_meta: Dict[str, Any],
        history_user: Dict[str, Any],
    ):
        answer = f"현재 페이지 URL: {req.client_url}"
        await self.chat_service.persist_exchange(
            request_message=req.message,
            stored_user_message=req.message,
            answer=answer,
            source=req.source,
            history_meta=history_meta,
            history_user=history_user,
            notify=self.notify,
        )
        return single_answer_response(req, answer, model="client_url")

    async def direct_file_action(
        self,
        req: Any,
        *,
        model_id: Optional[str],
        effective_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        # An explicit path ("report.txt") wins; otherwise infer one from an
        # explicit type keyword ("html 파일 만들어줘") so weak models never
        # have to drive the JSON tool loop just to create a file.
        explicit_target = file_action_target(req.message)
        if explicit_target is None:
            # Multi-file intent ("todo 앱 html+css+js") becomes a real linked
            # project bundle instead of one inlined page. Single-type requests
            # return None here and keep the unchanged single-file flow.
            manifest = infer_project_manifest(req.message)
            if manifest is not None:
                return await self.direct_project_action(
                    req,
                    manifest,
                    model_id=model_id,
                    effective_email=effective_email,
                    workspace_id=workspace_id,
                )
        target_path = explicit_target or infer_file_target(req.message)
        if not target_path:
            return None
        # Never silently overwrite: an existing target gets a _2/_3 suffix so
        # regeneration is safe and repeatable without the proposal flow.
        deduped_path = next_available_path(self.agent_root, target_path)
        renamed = deduped_path != target_path
        target_path = deduped_path
        content = inline_file_action_content(req.message)
        generation_meta: Optional[Dict[str, Any]] = None
        if content is None and not model_id:
            return self.no_model_response()
        if content is None and model_id:
            # Model-agnostic pipeline: strict extension-aware prompt →
            # extraction → validation → one corrective retry → deterministic
            # repair. Guarantees a structurally valid file from any LLM.
            async def _generate(context: str) -> str:
                return str(
                    await self.router.generate_as(
                        model_id,
                        message="Return only the requested file content.",
                        context=context,
                        max_tokens=max(int(req.max_tokens or 0), 4096),
                        temperature=min(req.temperature, 0.3),
                    )
                )

            content, generation_meta = await generate_file_content(
                _generate,
                target_path=target_path,
                user_request=req.message,
            )
        if content is None:
            raise HTTPException(
                status_code=400,
                detail="File content could not be generated.",
            )
        try:
            result = self.execute_tool(
                "write_file",
                {"path": target_path, "content": content},
            )
        except self.tool_error as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        final_path = result.get("path") or target_path
        answer = f"{final_path} 파일을 만들었습니다."
        if renamed:
            answer += " (같은 이름의 파일이 있어 새 이름으로 저장했습니다.)"
        repaired = bool(generation_meta and generation_meta.get("repaired"))
        if repaired:
            answer += (
                " (모델 출력이 불완전해 자동 보정을 거쳐 유효한 파일로 저장했습니다.)"
            )
        created_files = [
            {
                "path": final_path,
                "filename": Path(final_path).name,
                "bytes": result.get("bytes", 0),
                "action": "write_file",
            }
        ]
        # Artifact-first contract: every file the request produced, with the
        # honesty flags the UI needs (valid vs repaired scaffold, previewable).
        artifacts: List[Dict[str, Any]] = [
            {
                "kind": "file",
                "path": final_path,
                "filename": Path(final_path).name,
                "bytes": result.get("bytes", 0),
                "previewable": Path(final_path).suffix.lower() in PREVIEWABLE_EXTENSIONS,
                "valid": True,
                "repaired": repaired,
            }
        ]
        brain_ingest = self._ingest_generated_file(
            final_path,
            content,
            effective_email=effective_email,
            workspace_id=workspace_id,
            conversation_id=getattr(req, "conversation_id", None),
        )
        self._funnel_increment("real_file_delivered")
        self.notify("user", req.message, req.source)
        self.notify("assistant", answer, req.source)
        payload = {
            "status": "ok",
            "response": answer,
            "workspace": str(self.agent_root),
            "steps": [
                {
                    "state": AgentState.EXECUTING.value,
                    "action": "write_file",
                    "args": {"path": target_path},
                    "result": result,
                }
            ],
            "state_history": [AgentState.EXECUTING.value, AgentState.DONE.value],
            "final_state": AgentState.DONE.value,
            "created_files": created_files,
            "artifacts": artifacts,
            "routed_to_agent": True,
            "action_route": "direct_write_file",
        }
        if generation_meta is not None:
            payload["generation"] = generation_meta
        if brain_ingest is not None:
            payload["brain_ingest"] = brain_ingest
        if req.stream:
            return StreamingResponse(
                agent_payload_stream(
                    answer,
                    payload,
                    router=self.router,
                    model_id=model_id,
                ),
                media_type="text/event-stream",
                headers={"X-Model": model_id or "tool", "X-Routed-To": "agent"},
            )
        return JSONResponse(content=payload)

    async def direct_project_action(
        self,
        req: Any,
        manifest: Dict[str, Any],
        *,
        model_id: Optional[str],
        effective_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        """Artifact Loop: manifest → per-file generate/validate → bundle write.

        Every file goes through the same model-agnostic pipeline as the
        single-file path; after generation the *bundle* is verified as a whole
        (dangling href/src references are deterministically repaired first),
        then written together under one project directory, exposed through the
        standard ``artifacts[]`` contract, and downloadable as a zip.
        """
        if not model_id:
            return self.no_model_response()
        project_dir = next_available_path(self.agent_root, manifest["name"])
        bundle_files = [str(f["path"]) for f in manifest["files"]]

        async def _generate(context: str) -> str:
            return str(
                await self.router.generate_as(
                    model_id,
                    message="Return only the requested file content.",
                    context=context,
                    max_tokens=max(int(req.max_tokens or 0), 4096),
                    temperature=min(req.temperature, 0.3),
                )
            )

        contents: Dict[str, str] = {}
        file_meta: Dict[str, Dict[str, Any]] = {}
        for spec in manifest["files"]:
            name = str(spec["path"])
            request_text = f"{req.message}\n\nThis file's role: {spec.get('brief', '')}"
            content, meta = await generate_file_content(
                _generate,
                target_path=name,
                user_request=request_text,
                bundle_files=bundle_files,
            )
            contents[name] = content
            file_meta[name] = meta

        contents, reference_fixes = repair_bundle_references(contents)
        bundle_validation = validate_project_bundle(contents)

        steps: List[Dict[str, Any]] = []
        created_files: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []
        brain_ingests: List[Dict[str, Any]] = []
        for name, content in contents.items():
            rel_path = f"{project_dir}/{name}"
            try:
                result = self.execute_tool(
                    "write_file",
                    {"path": rel_path, "content": content},
                )
            except self.tool_error as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            final_path = result.get("path") or rel_path
            steps.append({
                "state": AgentState.EXECUTING.value,
                "action": "write_file",
                "args": {"path": rel_path},
                "result": result,
            })
            created_files.append({
                "path": final_path,
                "filename": Path(final_path).name,
                "bytes": result.get("bytes", 0),
                "action": "write_file",
            })
            verdict = bundle_validation["files"].get(name, {})
            artifacts.append({
                "kind": "file",
                "path": final_path,
                "filename": Path(final_path).name,
                "bytes": result.get("bytes", 0),
                "previewable": Path(final_path).suffix.lower() in PREVIEWABLE_EXTENSIONS,
                "valid": bool(verdict.get("valid", True)),
                "repaired": bool(file_meta.get(name, {}).get("repaired")),
            })
            ingest = self._ingest_generated_file(
                final_path,
                content,
                effective_email=effective_email,
                workspace_id=workspace_id,
                conversation_id=getattr(req, "conversation_id", None),
            )
            if ingest is not None:
                brain_ingests.append({"path": final_path, **ingest})

        file_list = ", ".join(Path(a["path"]).name for a in artifacts)
        answer = (
            f"{project_dir} 프로젝트를 만들었습니다 "
            f"({len(artifacts)}개 파일: {file_list})."
        )
        if not bundle_validation["ok"]:
            answer += " 일부 파일은 검증 경고가 있어 결과를 확인해 주세요."
        repaired_any = any(a["repaired"] for a in artifacts)
        if repaired_any:
            answer += " (모델 출력이 불완전한 파일은 자동 보정을 거쳤습니다.)"

        from urllib.parse import quote

        payload: Dict[str, Any] = {
            "status": "ok",
            "response": answer,
            "workspace": str(self.agent_root),
            "steps": steps,
            "state_history": [AgentState.EXECUTING.value, AgentState.DONE.value],
            "final_state": AgentState.DONE.value,
            "created_files": created_files,
            "artifacts": artifacts,
            "routed_to_agent": True,
            "action_route": "direct_project_bundle",
            "project": {
                "dir": project_dir,
                "files": bundle_files,
                "zip_url": f"/tools/download_zip?path={quote(project_dir)}",
                "bundle_validation": bundle_validation,
                "reference_fixes": reference_fixes,
            },
            "generation": {
                "files": file_meta,
                "repaired": repaired_any,
            },
        }
        if brain_ingests:
            payload["brain_ingest"] = brain_ingests
        self._funnel_increment("real_file_delivered")
        self.notify("user", req.message, req.source)
        self.notify("assistant", answer, req.source)
        if req.stream:
            return StreamingResponse(
                agent_payload_stream(
                    answer,
                    payload,
                    router=self.router,
                    model_id=model_id,
                ),
                media_type="text/event-stream",
                headers={"X-Model": model_id or "tool", "X-Routed-To": "agent"},
            )
        return JSONResponse(content=payload)

    def _ingest_generated_file(
        self,
        rel_path: str,
        content: Optional[str],
        *,
        effective_email: Optional[str],
        workspace_id: Optional[str],
        conversation_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Optionally index a just-generated file into the Brain.

        The knowledge promise is "what Lattice makes, Lattice remembers":
        without this, generated files are invisible to recall until the user
        re-uploads them. Best-effort and additive — ingestion problems never
        fail the file creation, and LATTICEAI_INGEST_GENERATED=0 disables it.
        """
        if self.ingestion_pipeline is None or not ingest_generated_enabled():
            return None
        try:
            from lattice_brain.ingestion import IngestionItem

            item = IngestionItem(
                source_type="file",
                title=Path(rel_path).name,
                text=content,
                path=str(self.agent_root / rel_path),
                source_uri=f"workspace://{rel_path}",
                owner=effective_email,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                metadata={"origin": "generated_file", "route": "direct_write_file"},
            )
            result = self.ingestion_pipeline.ingest(item, user_email=effective_email)
            payload = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
            return {
                "status": payload.get("status"),
                "node_id": payload.get("node_id"),
                "chunk_count": payload.get("chunk_count", 0),
                "duplicate": payload.get("duplicate", False),
            }
        except Exception as exc:  # noqa: BLE001 — indexing must never break creation
            logging.warning("generated-file ingest failed: %s", exc)
            return {"status": "failed", "detail": str(exc)[:200]}

    async def route_file_to_agent(
        self,
        req: Any,
        request: Request,
        *,
        effective_email: Optional[str],
        workspace_id: Optional[str],
        model_id: Optional[str],
    ):
        agent_request = AgentRequest(
            message=req.message,
            conversation_id=req.conversation_id,
            source=req.source or "web",
            max_steps=25,
            temperature=min(req.temperature, 0.2),
            user_email=effective_email,
            user_nickname=req.user_nickname,
            workspace_id=workspace_id,
        )

        def finalize(result: Dict) -> str:
            """History/funnel side effects once the run has a terminal payload."""
            answer = str(result.get("response") or "파일 작업을 처리했습니다.")
            # UX funnel (backlog #16): a file-intent request that the agent loop
            # finished without producing a single artifact is the "code-only"
            # failure mode the >95% real-file goal watches. Paused/failed runs
            # count neither way — they did not answer with code instead of files.
            delivered = bool(result.get("created_files") or result.get("artifacts"))
            if delivered:
                self._funnel_increment("real_file_delivered")
            elif str(result.get("status") or "") == "ok":
                self._funnel_increment("code_only_responses")
            self.notify("user", req.message, req.source)
            self.notify("assistant", answer, req.source)
            result["routed_to_agent"] = True
            return answer

        if req.stream:
            # Live loop visibility (review Wave 1.1): run the agent inside the
            # SSE generator so step events stream while EXECUTING; the final
            # frames keep the exact historical payload shape.
            async def start(observer):
                return await self.agent_controller.agent(
                    agent_request, request, on_step=observer
                )

            return StreamingResponse(
                agent_live_stream(
                    start,
                    router=self.router,
                    model_id=model_id,
                    finalize=finalize,
                ),
                media_type="text/event-stream",
                headers={"X-Model": model_id or "agent", "X-Routed-To": "agent"},
            )

        result = await self.agent_controller.agent(agent_request, request)
        finalize(result)
        return JSONResponse(content=result)


__all__ = ["ChatIntentController"]
