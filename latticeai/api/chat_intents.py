"""Fast-path intent and governed file-action handlers for chat."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from latticeai.api.chat_contracts import AgentRequest
from latticeai.api.chat_helpers import (
    file_action_target,
    format_network_status,
    inline_file_action_content,
    strip_generated_file_content,
)
from latticeai.api.chat_stream import agent_payload_stream, single_answer_response
from latticeai.core.agent import AgentState


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

    async def direct_file_action(self, req: Any, *, model_id: Optional[str]):
        target_path = file_action_target(req.message)
        if not target_path:
            return None
        content = inline_file_action_content(req.message)
        if content is None and not model_id:
            return self.no_model_response()
        if content is None and model_id:
            generation_context = (
                "Create the exact content for the requested file. "
                "Return only the file bytes as plain text. "
                "Do not wrap the answer in Markdown fences, commentary, or explanations.\n\n"
                f"Target path: {target_path}\n"
                f"User request: {req.message}"
            )
            raw_content = await self.router.generate_as(
                model_id,
                message="Return only the requested file content.",
                context=generation_context,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            content = strip_generated_file_content(str(raw_content))
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
        answer = f"{result.get('path') or target_path} 파일을 만들었습니다."
        created_files = [
            {
                "path": result.get("path") or target_path,
                "filename": Path(result.get("path") or target_path).name,
                "bytes": result.get("bytes", 0),
                "action": "write_file",
            }
        ]
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
            "routed_to_agent": True,
            "action_route": "direct_write_file",
        }
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
        result = await self.agent_controller.agent(agent_request, request)
        answer = str(result.get("response") or "파일 작업을 처리했습니다.")
        self.notify("user", req.message, req.source)
        self.notify("assistant", answer, req.source)
        result["routed_to_agent"] = True
        if req.stream:
            return StreamingResponse(
                agent_payload_stream(
                    answer,
                    result,
                    router=self.router,
                    model_id=model_id,
                ),
                media_type="text/event-stream",
                headers={"X-Model": model_id or "agent", "X-Routed-To": "agent"},
            )
        return JSONResponse(content=result)


__all__ = ["ChatIntentController"]
