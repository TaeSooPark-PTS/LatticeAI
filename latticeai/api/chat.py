"""Chat, history, and local agent API routes."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from PIL import Image

from latticeai.core.agent import AgentRunContext, AgentState
from latticeai.core.context_builder import format_sources_footnote, retrieve_context_for_generation
from latticeai.core.document_generator import DocumentGenerationSession, detect_document_intent
from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.services.app_context import AppContext
from latticeai.services.tool_dispatch import build_agent_runtime, collect_created_files
from tools import AGENT_ROOT, ToolError, ensure_agent_root, execute_tool, knowledge_save, network_status
# Pure chat helpers (language/intent detection, file-action parsing, recent-context
# assembly) live in chat_helpers; re-imported here so ``from latticeai.api.chat
# import <helper>`` keeps resolving. See __all__ below.
from latticeai.api.chat_helpers import (
    _LANG_HINT,
    build_recent_chat_context,
    detect_language,
    file_action_target,
    format_network_status,
    inline_file_action_content,
    is_clear_command,
    is_current_url_request,
    is_file_action_request,
    is_network_status_request,
    pair_user_history,
    single_text_stream,
    strip_generated_file_content,
    workspace_scope_from_request,
)

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    client_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.2
    stream: bool = True
    context: Optional[str] = None
    source: Optional[str] = None
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    image_data: Optional[str] = None
    allow_file_context: bool = False


class AgentRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    source: Optional[str] = None
    max_steps: int = 25
    temperature: float = 0.1
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    workspace_id: Optional[str] = None
    planning_model: Optional[str] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None
    human_in_loop: bool = False


class AgentResumeRequest(BaseModel):
    context_id: str
    approved: bool = True
    modified_plan: Optional[dict] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None


class AgentEvalRequest(BaseModel):
    skill: str
    case_id: Optional[str] = None


# The chat helpers imported at the top of the module are re-exported so
# ``from latticeai.api.chat import <helper>`` (tests, app_factory) keeps resolving
# after the split; __all__ marks them as intentional re-exports.
__all__ = [
    "create_chat_router",
    "build_recent_chat_context",
    "pair_user_history",
    "detect_language",
    "file_action_target",
    "inline_file_action_content",
    "is_file_action_request",
    "is_network_status_request",
    "is_current_url_request",
    "is_clear_command",
    "format_network_status",
    "strip_generated_file_content",
    "workspace_scope_from_request",
    "single_text_stream",
]


def create_chat_router(context: AppContext) -> APIRouter:
    """Build the chat/history/agent router from the typed application context.

    Replaces the historical ~25-kwarg factory signature: ``context``
    (:class:`latticeai.services.app_context.AppContext`) carries the same
    dependencies as typed fields.
    """
    api_router = APIRouter()
    router = context.model_router
    hooks = context.hooks
    workspace_graph = context.workspace_graph
    context_assembler = context.context_assembler
    require_user = context.require_user
    enforce_rate_limit = context.enforce_rate_limit
    get_history_user = context.get_history_user
    save_to_history = context.save_to_history
    append_audit_event = context.append_audit_event
    clear_history = context.clear_history
    clear_conversation = context.clear_conversation
    get_history = context.get_history
    group_history_conversations = context.group_history_conversations
    get_conversation_messages = context.get_conversation_messages
    conversation_title = context.conversation_title
    allowed_workspaces_for = context.allowed_workspaces_for

    CONFIG = context.config
    CHAT_SERVICE = context.chat_service
    WORKSPACE_OS = context.workspace_store
    ENABLE_GRAPH = context.enable_graph
    KNOWLEDGE_GRAPH = context.knowledge_graph
    PUBLIC_MODEL = context.public_model
    BASE_DIR = context.base_dir
    _doc_gen_sessions: dict = {}
    _pending_agents: dict[str, tuple] = {}
    _pending_agents_lock = threading.Lock()
    _background_tasks: set[asyncio.Task] = set()

    on_chat_message = context.on_chat_message

    def notify_chat_message(role: str, text: str, source: Optional[str]) -> None:
        """Mirror a chat exchange to the injected external bridge, if any.

        ``on_chat_message`` is registered by ``create_app`` only when
        ENABLE_TELEGRAM is truthy; exchanges that originated *from* telegram
        are never echoed back. No bridge registered → no-op.
        """
        if on_chat_message is None or source == "telegram":
            return
        try:
            on_chat_message(role, text, source)
        except Exception as exc:
            logging.warning("chat message bridge failed: %s", exc)

    def schedule_background_task(coro) -> None:
        task = asyncio.create_task(coro)
        _background_tasks.add(task)

        def _finish(done: asyncio.Task) -> None:
            _background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.warning("background chat task failed: %s", exc)

        task.add_done_callback(_finish)

    async def save_history_entry(role: str, content: str, history_meta: Dict, history_user: Dict) -> None:
        await asyncio.to_thread(save_to_history, role, content, **history_meta, **history_user)

    async def persist_chat_exchange(
        req: ChatRequest,
        answer: str,
        *,
        history_meta: Dict,
        history_user: Dict,
        user_message: Optional[str] = None,
    ) -> None:
        message = user_message if user_message is not None else req.message
        await save_history_entry("user", message, history_meta, history_user)
        await save_history_entry("assistant", answer, history_meta, history_user)
        notify_chat_message("user", req.message, req.source)
        notify_chat_message("assistant", answer, req.source)

    def no_model_response() -> JSONResponse:
        detail = "No model loaded. Call /models/load first."
        if CONFIG.is_public:
            detail = f"No public model loaded. Set OPENAI_API_KEY and LATTICEAI_PUBLIC_MODEL={PUBLIC_MODEL}, or call /models/load with an OpenAI-compatible model."
        return JSONResponse(
            status_code=400,
            content={
                "error": "no_model_loaded",
                "detail": detail,
                "message": detail,
                "action": "load_model",
            },
        )

    def single_answer_response(req: ChatRequest, answer: str, *, model: str) -> JSONResponse | StreamingResponse:
        if req.stream:
            return StreamingResponse(
                single_text_stream(answer),
                media_type="text/event-stream",
                headers={"X-Model": model},
            )
        return JSONResponse(content={"response": answer})

    def agent_payload_stream(answer: str, payload: Dict[str, Any]) -> AsyncIterator[str]:
        async def _stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'chunk': answer, 'model': router.current_model_id, 'agent': payload}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'chunk': '', 'model': router.current_model_id, 'agent': payload}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return _stream()

    async def handle_network_status_intent(
        req: ChatRequest,
        *,
        history_meta: Dict,
        history_user: Dict,
    ) -> JSONResponse | StreamingResponse:
        history_message = f"{req.message}\n[Image attached]" if req.image_data else req.message
        try:
            answer = format_network_status(network_status())
        except ToolError as exc:
            answer = f"네트워크 정보를 확인하지 못했습니다: {exc}"
        await persist_chat_exchange(req, answer, history_meta=history_meta, history_user=history_user, user_message=history_message)
        return single_answer_response(req, answer, model="network_status")

    async def handle_clear_intent(
        req: ChatRequest,
        *,
        effective_email: Optional[str],
    ) -> JSONResponse | StreamingResponse:
        command = req.message.strip().lower()
        clear_scope = "all" if command == "/clear_all" else "conversation"
        if ENABLE_GRAPH and KNOWLEDGE_GRAPH:
            try:
                KNOWLEDGE_GRAPH.ingest_event(
                    "ClearEvent",
                    f"{command} requested",
                    user_email=effective_email,
                    user_nickname=req.user_nickname,
                    source=req.source or "web",
                    conversation_id=req.conversation_id,
                    metadata={"command": command, "scope": clear_scope},
                )
            except Exception as e:
                logging.warning("knowledge graph clear event ingest failed: %s", e)
        if command == "/clear_all":
            result = clear_history(0, **history_scope_for_user(effective_email))
            answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        elif req.conversation_id:
            result = clear_conversation(req.conversation_id, **history_scope_for_user(effective_email))
            answer = f"현재 대화방 채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        else:
            result = clear_history(0, **history_scope_for_user(effective_email))
            answer = f"채팅창을 정리했습니다. 화면에서 제거 {result.get('removed', 0)}개. 감사 로그와 지식 그래프/RAG 데이터는 유지됩니다."
        append_audit_event(
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
        notify_chat_message("user", req.message, req.source)
        notify_chat_message("assistant", answer, req.source)
        return single_answer_response(req, answer, model="history")

    async def handle_current_url_intent(
        req: ChatRequest,
        *,
        history_meta: Dict,
        history_user: Dict,
    ) -> JSONResponse | StreamingResponse:
        answer = f"현재 페이지 URL: {req.client_url}"
        await persist_chat_exchange(req, answer, history_meta=history_meta, history_user=history_user)
        return single_answer_response(req, answer, model="client_url")

    async def handle_direct_file_action(req: ChatRequest) -> Optional[JSONResponse | StreamingResponse]:
        target_path = file_action_target(req.message)
        if not target_path:
            return None
        content = inline_file_action_content(req.message)
        if content is None and not router.current_model_id:
            return no_model_response()
        if content is None and router.current_model_id:
            if req.model and req.model != router.current_model_id:
                if req.model not in router.loaded_model_ids:
                    raise HTTPException(status_code=404, detail=f"Model '{req.model}' not loaded.")
                router.switch_model(req.model)
            generation_context = (
                "Create the exact content for the requested file. "
                "Return only the file bytes as plain text. "
                "Do not wrap the answer in Markdown fences, commentary, or explanations.\n\n"
                f"Target path: {target_path}\n"
                f"User request: {req.message}"
            )
            raw_content = await router.generate_as(
                router.current_model_id,
                message="Return only the requested file content.",
                context=generation_context,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            content = strip_generated_file_content(str(raw_content))
        if content is None:
            raise HTTPException(status_code=400, detail="File content could not be generated.")
        try:
            result = execute_tool("write_file", {"path": target_path, "content": content})
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        answer = f"{result.get('path') or target_path} 파일을 만들었습니다."
        created_files = [{
            "path": result.get("path") or target_path,
            "filename": Path(result.get("path") or target_path).name,
            "bytes": result.get("bytes", 0),
            "action": "write_file",
        }]
        notify_chat_message("user", req.message, req.source)
        notify_chat_message("assistant", answer, req.source)
        payload = {
            "status": "ok",
            "response": answer,
            "workspace": str(AGENT_ROOT),
            "steps": [{
                "state": AgentState.EXECUTING.value,
                "action": "write_file",
                "args": {"path": target_path},
                "result": result,
            }],
            "state_history": [AgentState.EXECUTING.value, AgentState.DONE.value],
            "final_state": AgentState.DONE.value,
            "created_files": created_files,
            "routed_to_agent": True,
            "action_route": "direct_write_file",
        }
        if req.stream:
            return StreamingResponse(
                agent_payload_stream(answer, payload),
                media_type="text/event-stream",
                headers={"X-Model": router.current_model_id or "tool", "X-Routed-To": "agent"},
            )
        return JSONResponse(content=payload)

    async def route_file_action_to_agent(
        req: ChatRequest,
        request: Request,
        *,
        effective_email: Optional[str],
    ) -> JSONResponse | StreamingResponse:
        agent_req = AgentRequest(
            message=req.message,
            conversation_id=req.conversation_id,
            source=req.source or "web",
            max_steps=25,
            temperature=min(req.temperature, 0.2),
            user_email=effective_email,
            user_nickname=req.user_nickname,
            workspace_id=workspace_scope_from_request(request),
        )
        result = await agent(agent_req, request)
        answer = str(result.get("response") or "파일 작업을 처리했습니다.")
        notify_chat_message("user", req.message, req.source)
        notify_chat_message("assistant", answer, req.source)
        result["routed_to_agent"] = True
        if req.stream:
            return StreamingResponse(
                agent_payload_stream(answer, result),
                media_type="text/event-stream",
                headers={"X-Model": router.current_model_id, "X-Routed-To": "agent"},
            )
        return JSONResponse(content=result)

    def history_scope_for_user(user_email: Optional[str]) -> Dict:
        require_auth = bool(getattr(CONFIG, "require_auth", False))
        scoped_user = user_email if require_auth else None
        allowed = None
        if require_auth and scoped_user and allowed_workspaces_for is not None:
            allowed = allowed_workspaces_for(scoped_user)
        return {
            "user_email": scoped_user,
            "allowed_workspaces": allowed,
            "include_legacy_global": not require_auth,
        }

    def recent_chat_context(
        limit: int = 10,
        include_image_missing_replies: bool = True,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        return build_recent_chat_context(
            get_history=get_history,
            limit=limit,
            include_image_missing_replies=include_image_missing_replies,
            user_email=user_email,
            conversation_id=conversation_id,
        )

    def extract_screenshot_context(image_data: Optional[str]) -> str:
        if not image_data:
            return ""

        lines = ["[SCREENSHOT INGESTION]"]
        image_bytes = b""
        try:
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            lines.append(f"- image_size: {image.width}x{image.height}")
            lines.append(f"- image_mode: {image.mode}")
        except Exception as e:
            lines.append(f"- image_decode_error: {e}")
            return "\n".join(lines)

        tesseract_path = shutil.which("tesseract")
        if not tesseract_path:
            lines.append("- ocr: unavailable; install `tesseract` to enable OCR text extraction.")
            return "\n".join(lines)

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="ltcai-screenshot-", suffix=".png", delete=False) as temp:
                temp.write(image_bytes)
                temp_path = temp.name

            ocr_text = ""
            for lang in ("kor+eng", "eng"):
                completed = subprocess.run(
                    [tesseract_path, temp_path, "stdout", "-l", lang, "--psm", "6"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    ocr_text = completed.stdout.strip()
                    lines.append(f"- ocr_language: {lang}")
                    break

            if ocr_text:
                lines.append("- ocr_text:")
                lines.append(ocr_text[:4000])
            else:
                lines.append("- ocr: no text extracted.")
        except Exception as e:
            lines.append(f"- ocr_error: {e}")
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass

        return "\n".join(lines)

    _AGENT_RUNTIME = context.chat_agent_runtime
    if _AGENT_RUNTIME is None:
        _AGENT_RUNTIME = build_agent_runtime(
            model_router=router,
            execute_tool=execute_tool,
            recent_chat_context=recent_chat_context,
            clear_history=clear_history,
            knowledge_save=knowledge_save,
            audit=append_audit_event,
            hooks=hooks,
            brain_memory=context.brain_memory,
        )

    @api_router.post("/chat")
    async def chat(req: ChatRequest, request: Request):
        current_user = require_user(request)
        enforce_rate_limit(current_user, "chat")
        img_len = len(req.image_data) if req.image_data else 0
        logging.debug(
            "/chat request: stream=%s image_data_len=%s message_len=%s",
            req.stream,
            img_len,
            len(req.message or ""),
        )
        effective_email = req.user_email or current_user or None
        history_user = get_history_user(effective_email, req.user_nickname)
        history_meta = {
            "source": req.source or "web",
            "conversation_id": req.conversation_id,
            "workspace_id": workspace_scope_from_request(request),
        }

        if is_network_status_request(req.message):
            return await handle_network_status_intent(req, history_meta=history_meta, history_user=history_user)

        if is_clear_command(req.message):
            return await handle_clear_intent(req, effective_email=effective_email)

        if is_current_url_request(req.message) and req.client_url:
            return await handle_current_url_intent(req, history_meta=history_meta, history_user=history_user)

        if is_file_action_request(req.message):
            direct_response = await handle_direct_file_action(req)
            if direct_response is not None:
                return direct_response

        if not router.current_model_id:
            return no_model_response()

        if req.model and req.model != router.current_model_id:
            if req.model not in router.loaded_model_ids:
                raise HTTPException(status_code=404, detail=f"Model '{req.model}' not loaded.")
            router.switch_model(req.model)

        if is_file_action_request(req.message):
            return await route_file_action_to_agent(req, request, effective_email=effective_email)

        lang = detect_language(req.message)
        context = f"[LANGUAGE: {_LANG_HINT[lang]}]\n" + (req.context or "")
        # v4 Context System: one budgeted, provenance-carrying assembly
        # (workspace memories + hybrid search + garden notes) replaces the
        # ad-hoc vault-scan + LIKE-search concatenation. The trace records
        # why each section is in the prompt.
        context_trace = None
        try:
            if context_assembler is not None:
                assembled = context_assembler.assemble(
                    req.message,
                    user_email=effective_email,
                    conversation_id=req.conversation_id,
                    budget=2000,
                )
                context_trace = assembled.trace()
                if assembled.text:
                    context += "\n\n" + assembled.text
        except Exception as e:
            logging.warning("Context assembly skipped: %s", e)

        is_doc_gen = detect_document_intent(req.message)
        doc_gen_context_result = None

        try:
            if ENABLE_GRAPH and KNOWLEDGE_GRAPH and is_doc_gen:
                # Specialized multi-hop retrieval for document generation.
                doc_gen_context_result = retrieve_context_for_generation(
                    KNOWLEDGE_GRAPH, req.message, max_results=10, max_hops=2,
                )
                graph_md = doc_gen_context_result.get("context_markdown", "")
                if graph_md:
                    context += f"\n\n[KNOWLEDGE GRAPH — Document Generation Context]\n{graph_md}"
                    logging.debug("Document generation context retrieved from knowledge graph.")
        except Exception as e:
            logging.warning("Knowledge graph reinforcement skipped: %s", e)

        if req.image_data:
            screenshot_context = extract_screenshot_context(req.image_data)
            if screenshot_context:
                context += f"\n\n{screenshot_context}"

        if CONFIG.auto_read_chat_paths:
            _file_path_re = re.compile(r'(?:^|[\s\'\"(])((~|/[\w.])[^\s\'")\]]*)', re.MULTILINE)
            requested_paths = [_m.group(1).strip() for _m in _file_path_re.finditer(req.message or "")]
            if requested_paths:
                append_audit_event(
                    "auto_file_context_blocked",
                    user_email=effective_email,
                    path_count=len(requested_paths),
                    allow_file_context=req.allow_file_context,
                    reason="local file context requires an explicit approved file/tool flow",
                )
                if req.allow_file_context:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Automatic local file reads are disabled in chat. "
                            "Attach the file, upload it, or use an approved local-file tool flow."
                        ),
                    )

        trace_seed = CHAT_SERVICE.build_graph_trace(
            req.message,
            KNOWLEDGE_GRAPH if (ENABLE_GRAPH and KNOWLEDGE_GRAPH) else None,
            context,
        )
        if context_trace is not None and isinstance(trace_seed, dict):
            # Persisted with the answer trace: 'why is this in my context?'
            # is answerable from the stored record (UI surface lands in T9b).
            trace_seed["context_assembly"] = context_trace

        history_message = f"{req.message}\n[Image attached]" if req.image_data else req.message
        await asyncio.to_thread(save_to_history, "user", history_message, **history_meta, **history_user)
        notify_chat_message("user", req.message, req.source)

        if is_doc_gen and ENABLE_GRAPH and KNOWLEDGE_GRAPH:
            conv_key = req.conversation_id or "default"
            session = _doc_gen_sessions.get(conv_key)
            if session is None:
                session = DocumentGenerationSession()
                _doc_gen_sessions[conv_key] = session
            graph_md = (doc_gen_context_result or {}).get("context_markdown", "")
            system_prompt = session.get_system_prompt(graph_md)
            sources = (doc_gen_context_result or {}).get("sources", [])
            footnote = format_sources_footnote(sources)

            if req.stream:
                async def _stream_doc_gen():
                    collected = []
                    stream_error = None
                    try:
                        async for chunk in router.stream_generate_document(
                            req.message, system_prompt,
                            max_tokens=req.max_tokens or 8192,
                            temperature=req.temperature or 0.3,
                        ):
                            collected.append(chunk)
                            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                    except Exception as exc:
                        stream_error = str(exc)
                        logging.warning("document stream failed: %s", exc)
                        yield f"data: {json.dumps({'error': stream_error}, ensure_ascii=False)}\n\n"
                    full_text = "".join(collected)
                    if footnote:
                        yield f"data: {json.dumps({'text': footnote}, ensure_ascii=False)}\n\n"
                        full_text += footnote
                    if stream_error:
                        full_text = f"{full_text}\n\n[stream_error] {stream_error}" if full_text else f"[stream_error] {stream_error}"
                    session.update(graph_md, full_text, req.conversation_id)
                    await asyncio.to_thread(save_to_history, "assistant", full_text, **history_meta, **history_user)
                    trace_record = CHAT_SERVICE.record_trace(
                        question=req.message,
                        response=full_text,
                        conversation_id=req.conversation_id,
                        user_email=effective_email,
                        trace=trace_seed,
                    )
                    notify_chat_message("assistant", full_text, req.source)
                    yield f"data: {json.dumps({'text': '', 'trace_id': trace_record['id'], 'trace': trace_record}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(
                    _stream_doc_gen(),
                    media_type="text/event-stream",
                    headers={"X-Model": router.current_model_id, "X-Doc-Gen": "true"},
                )
            else:
                result = await router.generate_document(
                    req.message, system_prompt,
                    max_tokens=req.max_tokens or 8192,
                    temperature=req.temperature or 0.3,
                )
                if footnote:
                    result += footnote
                session.update(graph_md, result, req.conversation_id)
                await asyncio.to_thread(save_to_history, "assistant", str(result), **history_meta, **history_user)
                trace_record = CHAT_SERVICE.record_trace(
                    question=req.message,
                    response=str(result),
                    conversation_id=req.conversation_id,
                    user_email=effective_email,
                    trace=trace_seed,
                )
                notify_chat_message("assistant", str(result), req.source)
                return JSONResponse(content={"response": str(result), "trace_id": trace_record["id"], "trace": trace_record})

        if req.stream:
            recent_context = recent_chat_context(user_email=effective_email, conversation_id=req.conversation_id)
            stream_context = context
            if recent_context:
                stream_context = f"[RECENT CONVERSATION]\n{recent_context}\n\n{context}".strip()
            return StreamingResponse(
                _stream_chat(
                    req,
                    stream_context,
                    req.image_data,
                    trace_seed=trace_seed,
                    effective_email=effective_email,
                    history_meta=history_meta,
                ),
                media_type="text/event-stream",
                headers={"X-Model": router.current_model_id},
            )
        else:
            if req.image_data:
                recent_context = recent_chat_context(
                    limit=6,
                    include_image_missing_replies=False,
                    user_email=effective_email,
                    conversation_id=req.conversation_id,
                )
                full_context = f"[RECENT CONVERSATION]\n{recent_context}\n\n{context}".strip() if recent_context else context
            else:
                history_context = recent_chat_context(user_email=effective_email, conversation_id=req.conversation_id)
                full_context = f"{history_context}\n{context}" if context else history_context

            result = await router.generate(req.message, full_context, req.max_tokens, req.temperature, req.image_data)

            await asyncio.to_thread(save_to_history, "assistant", str(result), **history_meta, **history_user)
            trace_record = CHAT_SERVICE.record_trace(
                question=req.message,
                response=str(result),
                conversation_id=req.conversation_id,
                user_email=effective_email,
                trace=trace_seed,
            )
            notify_chat_message("assistant", str(result), req.source)

            return JSONResponse(content={"response": str(result), "trace_id": trace_record["id"], "trace": trace_record})

    
    @api_router.get("/history")
    async def fetch_history(request: Request):
        """웹 화면에서 이전 대화를 불러올 수 있도록 히스토리를 반환합니다."""
        current_user = require_user(request)
        return get_history(**history_scope_for_user(current_user))

    @api_router.get("/history/conversations")
    async def fetch_history_conversations(request: Request):
        """저장된 히스토리를 대화 단위로 묶어 반환합니다."""
        current_user = require_user(request)
        return group_history_conversations(get_history(**history_scope_for_user(current_user)))

    @api_router.get("/history/conversations/{conversation_id:path}")
    async def fetch_history_conversation(conversation_id: str, request: Request):
        """선택한 대화의 메시지를 반환합니다."""
        current_user = require_user(request)
        messages = get_conversation_messages(conversation_id, **history_scope_for_user(current_user))
        if not messages:
            raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
        return {"id": conversation_id, "messages": messages}

    
    @api_router.delete("/history/conversations/{conversation_id:path}")
    async def delete_history_conversation(conversation_id: str, request: Request):
        """선택한 대화방의 메시지만 삭제합니다."""
        email = require_user(request)
        result = clear_conversation(
            conversation_id,
            request.query_params.get("started_at"),
            **history_scope_for_user(email),
        )
        append_audit_event(
            "conversation_delete",
            user_email=email,
            conversation_id=conversation_id,
            started_at=request.query_params.get("started_at"),
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        return result

    
    @api_router.delete("/history")
    async def delete_history(request: Request, keep_last: int = 0):
        email = require_user(request)
        result = clear_history(keep_last, **history_scope_for_user(email))
        append_audit_event(
            "history_delete",
            user_email=email,
            keep_last=keep_last,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        return result

    @api_router.get("/history/search")
    async def search_history(q: str, request: Request):
        """키워드로 채팅 히스토리를 검색합니다."""
        current_user = require_user(request)
        if not q or not q.strip():
            return {"results": [], "query": q}
        q_lower = q.strip().lower()
        history = get_history(**history_scope_for_user(current_user))
        matches = [item for item in history if q_lower in (item.get("content") or "").lower()]
        grouped: Dict[str, Dict] = {}
        for item in matches:
            cid = item.get("conversation_id") or "legacy"
            if cid not in grouped:
                grouped[cid] = {"conversation_id": cid, "title": conversation_title(item), "messages": []}
            grouped[cid]["messages"].append(item)
        return {"results": list(grouped.values())[-30:], "query": q}

    async def _stream_chat(
        req: ChatRequest,
        context: str = "",
        image_data: str = None,
        *,
        trace_seed: Optional[Dict] = None,
        effective_email: Optional[str] = None,
        history_meta: Optional[Dict] = None,
    ) -> AsyncIterator[str]:
        full_response = ""
        stream_error: Optional[str] = None
        try:
            async for chunk in router.stream_generate(req.message, context, req.max_tokens, req.temperature, image_data):
                clean_chunk = chunk.text if hasattr(chunk, "text") else chunk
                full_response += str(clean_chunk)
                yield f"data: {json.dumps({'chunk': clean_chunk, 'model': router.current_model_id}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            stream_error = str(exc)
            logging.warning("chat stream failed: %s", exc)
            yield f"data: {json.dumps({'error': stream_error, 'model': router.current_model_id}, ensure_ascii=False)}\n\n"
        history_user = get_history_user(effective_email or req.user_email, req.user_nickname)
        persisted_response = full_response
        if stream_error and not persisted_response:
            persisted_response = f"[stream_error] {stream_error}"
        elif stream_error:
            persisted_response = f"{persisted_response}\n\n[stream_error] {stream_error}"
        trace_record = None
        try:
            await asyncio.to_thread(save_to_history, "assistant", persisted_response, **(history_meta or {}), **history_user)
            trace_record = CHAT_SERVICE.record_trace(
                question=req.message,
                response=persisted_response,
                conversation_id=req.conversation_id,
                user_email=effective_email or req.user_email,
                trace=trace_seed or CHAT_SERVICE.build_graph_trace(
                    req.message,
                    KNOWLEDGE_GRAPH if (ENABLE_GRAPH and KNOWLEDGE_GRAPH) else None,
                    context,
                ),
            )
            notify_chat_message("assistant", persisted_response, req.source)
        except Exception as exc:
            logging.warning("chat stream persistence failed: %s", exc)
        trailer = {"chunk": "", "model": router.current_model_id}
        if trace_record:
            trailer.update({"trace_id": trace_record["id"], "trace": trace_record})
        if stream_error:
            trailer["error"] = stream_error
        yield f"data: {json.dumps(trailer, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    @api_router.post("/agent/eval")
    async def agent_eval(req: AgentEvalRequest, request: Request):
        """Run a skill's eval cases from schema.json and return pass/fail per case."""
        require_user(request)
        skill_dir = BASE_DIR / "skills" / req.skill
        schema_path = skill_dir / "schema.json"
        if not schema_path.exists():
            raise HTTPException(404, detail=f"Skill '{req.skill}' not found or missing schema.json")

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        eval_cases = schema.get("evals", [])
        if req.case_id:
            eval_cases = [c for c in eval_cases if c.get("id") == req.case_id]
        if not eval_cases:
            return {"skill": req.skill, "total": 0, "passed": 0, "failed": 0, "results": [],
                    "message": "No eval cases defined in schema.json"}

        action_name = schema.get("action", req.skill)
        results = []
        for case in eval_cases:
            case_id = case.get("id", "?")
            try:
                case_input = case.get("input", {})
                result   = dispatch_tool(hooks, action_name, case_input,
                                         lambda: execute_tool(action_name, case_input), source="eval")
                criteria = case.get("pass_criteria", "")
                if "success == true" in criteria:
                    passed = result.get("success") is True
                elif "success == false" in criteria:
                    passed = result.get("success") is False
                else:
                    passed = True  # manual review required
                results.append({"id": case_id, "description": case.get("description", ""),
                                "passed": passed, "result": result, "pass_criteria": criteria})
            except Exception as exc:
                results.append({"id": case_id, "description": case.get("description", ""),
                                "passed": False, "error": str(exc),
                                "pass_criteria": case.get("pass_criteria", "")})

        n_passed = sum(1 for r in results if r.get("passed") is True)
        return {
            "skill": req.skill, "action": action_name,
            "total": len(results), "passed": n_passed, "failed": len(results) - n_passed,
            "results": results,
        }

    
    @api_router.post("/agent")
    async def agent(req: AgentRequest, request: Request):
        """Natural-language local agent.

        State machine:
            IDLE → PLANNING → WAITING_APPROVAL → EXECUTING → VERIFYING
                                           ↓                     ↓
                                         FAILED       DONE | EXECUTING(retry) | ROLLBACK
                                                                                      ↓
                                                                                   FAILED
        """
        current_user = require_user(request)
        enforce_rate_limit(current_user, "agent")
        req.workspace_id = req.workspace_id or workspace_scope_from_request(request)
        if not router.current_model_id:
            raise HTTPException(status_code=400, detail="No model loaded. Call /models/load first.")

        ensure_agent_root()
        lang = detect_language(req.message)
        lang_hint = _LANG_HINT[lang]
        max_steps = max(1, min(req.max_steps, 50))
        max_retry = 3

        ctx = AgentRunContext()
        ctx.executing_model = req.executing_model
        ctx.reviewing_model = req.reviewing_model

        # PLANNING phase
        ctx.state = AgentState.PLANNING
        ctx.state_history.append(ctx.state.value)
        await _AGENT_RUNTIME.plan(ctx, req, lang_hint, current_user, model_id=req.planning_model)

        # Human-in-the-loop: pause after planning, return plan to UI
        if req.human_in_loop:
            context_id = secrets.token_urlsafe(16)
            with _pending_agents_lock:
                _pending_agents[context_id] = (ctx, req, lang_hint, current_user)
            return {
                "status": "waiting_approval",
                "context_id": context_id,
                "plan": ctx.plan,
                "steps": ctx.transcript,
                "state_history": ctx.state_history,
                "planning_model": req.planning_model or router.current_model_id,
                "executing_model": req.executing_model or router.current_model_id,
                "reviewing_model": req.reviewing_model or router.current_model_id,
            }

        # Auto-approve and run to completion (default behaviour)
        _AGENT_RUNTIME.approve(ctx, current_user, approved_by_human=True)
        return await _agent_finish(ctx, req, lang_hint, current_user, max_steps, max_retry)

    
    async def _agent_finish(
        ctx: AgentRunContext, req: AgentRequest, lang_hint: str,
        current_user: str, max_steps: int, max_retry: int,
    ) -> dict:
        """HTTP glue: drive the runtime to a terminal state, persist, shape the response."""
        await _AGENT_RUNTIME.run_to_completion(ctx, req, lang_hint, current_user, max_steps, max_retry)
        schedule_background_task(_AGENT_RUNTIME.memory_update(ctx, req, current_user))

        message = ctx.final_message or "작업을 완료했습니다."
        await asyncio.to_thread(save_to_history, "user", req.message, source=req.source or "web", conversation_id=req.conversation_id, workspace_id=req.workspace_id)
        await asyncio.to_thread(save_to_history, "assistant", message, source=req.source or "web", conversation_id=req.conversation_id, workspace_id=req.workspace_id)
        try:
            WORKSPACE_OS.record_agent_run(
                agent_id="agent:executor",
                status="ok" if ctx.state == AgentState.DONE else "failed",
                input_text=req.message,
                output_text=message,
                user_email=current_user or None,
                timeline=ctx.transcript,
                relationships=["agent:planner", "agent:reviewer"],
                graph=workspace_graph(),
            )
        except Exception as exc:
            logging.warning("workspace agent run record failed: %s", exc)
        created_files = collect_created_files(ctx.transcript)
        return {
            "status": "ok" if ctx.state == AgentState.DONE else "failed",
            "response": message,
            "workspace": str(AGENT_ROOT),
            "steps": ctx.transcript,
            "state_history": ctx.state_history,
            "final_state": ctx.state.value,
            "created_files": created_files,
        }

    
    @api_router.post("/agent/resume")
    async def agent_resume(req: AgentResumeRequest, request: Request):
        """Resume a paused agent after human approval of the plan."""
        current_user = require_user(request)

        with _pending_agents_lock:
            entry = _pending_agents.pop(req.context_id, None)
        if not entry:
            raise HTTPException(status_code=404, detail="Agent context not found or expired. Start a new request.")

        ctx, orig_req, lang_hint, _orig_user = entry

        if not req.approved:
            return {"status": "cancelled", "response": "사용자가 계획을 취소했습니다."}

        if req.modified_plan:
            ctx.plan = req.modified_plan
            ctx.transcript[-1].update(ctx.plan)  # keep transcript in sync

        # Apply model overrides from resume request (takes priority over original request)
        ctx.executing_model = req.executing_model or ctx.executing_model
        ctx.reviewing_model = req.reviewing_model or ctx.reviewing_model

        _AGENT_RUNTIME.approve(ctx, current_user)

        max_steps = max(1, min(orig_req.max_steps, 50))
        max_retry = 3
        return await _agent_finish(ctx, orig_req, lang_hint, current_user, max_steps, max_retry)

    return api_router
