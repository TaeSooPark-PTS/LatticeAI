"""Composition root for chat, history, document, and local-agent routes.

The stable public factory remains ``create_chat_router(AppContext)``.  Feature
implementation is split across focused HTTP modules while this file owns only
dependency assembly and the top-level chat request pipeline.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from latticeai.api.chat_agent_http import AgentHTTPController
from latticeai.api.chat_contracts import (
    AgentEvalRequest,
    AgentRequest,
    AgentResumeRequest,
    ChatRequest,
)
from latticeai.api.chat_documents import (
    DocumentGenerationCoordinator,
    extract_screenshot_context,
)
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
from latticeai.api.chat_history import HistoryRouteDependencies, register_history_routes
from latticeai.api.chat_intents import ChatIntentController
from latticeai.api.chat_stream import stream_chat
from latticeai.services.app_context import AppContext
from latticeai.services.chat_service import ChatService
from latticeai.services.tool_dispatch import build_agent_runtime, enforce_tool_policy
from latticeai.tools import (
    AGENT_ROOT,
    ToolError,
    ensure_agent_root,
    execute_tool,
    knowledge_save,
    network_status,
)


__all__ = [
    "AgentEvalRequest",
    "AgentRequest",
    "AgentResumeRequest",
    "ChatRequest",
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
    """Build the unchanged chat/history/agent route surface."""

    api_router = APIRouter()
    model_router = context.model_router
    config = context.config
    require_user = context.require_user
    workspace_service = context.workspace_service
    allowed_workspaces_for = context.allowed_workspaces_for

    chat_service = ChatService.coerce(
        context.chat_service,
        store=context.workspace_store,
        get_history=context.get_history,
        save_to_history=context.save_to_history,
        get_history_user=context.get_history_user,
    )

    def notify_chat_message(role: str, text: str, source: Optional[str]) -> None:
        """Mirror persisted web exchanges to an injected bridge, never echoing it."""
        if context.on_chat_message is None or source == "telegram":
            return
        try:
            context.on_chat_message(role, text, source)
        except Exception as exc:
            logging.warning("chat message bridge failed: %s", exc)

    def authenticated_identity(
        current_user: str,
        claimed_email: Optional[str],
    ) -> Optional[str]:
        if current_user and claimed_email:
            if current_user.strip().lower() != claimed_email.strip().lower():
                raise HTTPException(
                    status_code=403,
                    detail="user_email must match the authenticated user.",
                )
        return current_user or claimed_email or None

    def write_workspace(
        requested: Optional[str],
        current_user: str,
    ) -> Optional[str]:
        if workspace_service is None:
            return requested
        try:
            return workspace_service.resolve_write_scope(
                requested,
                current_user or None,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def history_scope_for_user(user_email: Optional[str]) -> Dict:
        return chat_service.history_scope(
            user_email,
            require_auth=bool(getattr(config, "require_auth", False)),
            allowed_workspaces_for=allowed_workspaces_for,
        )

    def recent_chat_context(
        limit: int = 10,
        include_image_missing_replies: bool = True,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        return build_recent_chat_context(
            get_history=context.get_history,
            limit=limit,
            include_image_missing_replies=include_image_missing_replies,
            user_email=user_email,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    agent_runtime = context.chat_agent_runtime
    if agent_runtime is None:
        # Keep construction here so the historical monkeypatch/import seam on
        # latticeai.api.chat.build_agent_runtime remains valid.
        agent_runtime = build_agent_runtime(
            model_router=model_router,
            execute_tool=execute_tool,
            recent_chat_context=recent_chat_context,
            clear_history=context.clear_history,
            knowledge_save=knowledge_save,
            audit=context.append_audit_event,
            hooks=context.hooks,
            brain_memory=context.brain_memory,
        )

    agent_controller = AgentHTTPController(
        runtime=agent_runtime,
        model_router=model_router,
        require_user=require_user,
        require_admin=context.require_admin,
        enforce_rate_limit=context.enforce_rate_limit,
        authenticated_identity=authenticated_identity,
        write_workspace=write_workspace,
        save_to_history=context.save_to_history,
        workspace_store=context.workspace_store,
        workspace_graph=context.workspace_graph,
        hooks=context.hooks,
        execute_tool=execute_tool,
        base_dir=context.base_dir or Path.cwd(),
        agent_root=AGENT_ROOT,
        ensure_agent_root=ensure_agent_root,
    )
    intent_controller = ChatIntentController(
        model_router=model_router,
        config=config,
        public_model=context.public_model,
        chat_service=chat_service,
        notify=notify_chat_message,
        clear_history=context.clear_history,
        clear_conversation=context.clear_conversation,
        history_scope_for_user=history_scope_for_user,
        append_audit_event=context.append_audit_event,
        enable_graph=context.enable_graph,
        knowledge_graph=context.knowledge_graph,
        enforce_tool_policy=enforce_tool_policy,
        network_status=network_status,
        tool_error=ToolError,
        execute_tool=execute_tool,
        agent_controller=agent_controller,
        agent_root=AGENT_ROOT,
    )
    document_coordinator = DocumentGenerationCoordinator(
        model_router=model_router,
        knowledge_graph=context.knowledge_graph,
        enable_graph=context.enable_graph,
        chat_service=chat_service,
        notify=notify_chat_message,
    )

    def request_model(model_id: Optional[str]) -> Optional[str]:
        selected = model_id or model_router.current_model_id
        if model_id and model_id not in model_router.loaded_model_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_id}' not loaded.",
            )
        return selected

    @api_router.post("/chat")
    async def chat(req: ChatRequest, request: Request):
        current_user = require_user(request)
        context.enforce_rate_limit(current_user, "chat")
        logging.debug(
            "/chat request: stream=%s image_data_len=%s message_len=%s",
            req.stream,
            len(req.image_data) if req.image_data else 0,
            len(req.message or ""),
        )
        effective_email = authenticated_identity(current_user, req.user_email)
        workspace_id = write_workspace(
            workspace_scope_from_request(request),
            current_user,
        )
        history_user = chat_service.history_user(
            effective_email,
            req.user_nickname,
        )
        history_meta = {
            "source": req.source or "web",
            "conversation_id": req.conversation_id,
            "workspace_id": workspace_id,
        }

        if is_network_status_request(req.message):
            return await intent_controller.network(
                req,
                current_user=current_user,
                history_meta=history_meta,
                history_user=history_user,
            )
        if is_clear_command(req.message):
            return await intent_controller.clear(
                req,
                effective_email=effective_email,
                workspace_id=workspace_id,
            )
        if is_current_url_request(req.message) and req.client_url:
            return await intent_controller.current_url(
                req,
                history_meta=history_meta,
                history_user=history_user,
            )

        selected_model_id = request_model(req.model)
        if is_file_action_request(req.message):
            direct_response = await intent_controller.direct_file_action(
                req,
                model_id=selected_model_id,
            )
            if direct_response is not None:
                return direct_response
        if not selected_model_id:
            return intent_controller.no_model_response()
        if is_file_action_request(req.message):
            return await intent_controller.route_file_to_agent(
                req,
                request,
                effective_email=effective_email,
                workspace_id=workspace_id,
                model_id=selected_model_id,
            )

        language = detect_language(req.message)
        prompt_context = f"[LANGUAGE: {_LANG_HINT[language]}]\n" + (req.context or "")
        context_trace = None
        try:
            if context.context_assembler is not None:
                assembled = context.context_assembler.assemble(
                    req.message,
                    user_email=effective_email,
                    workspace_id=workspace_id,
                    conversation_id=req.conversation_id,
                    budget=2000,
                )
                context_trace = assembled.trace()
                if assembled.text:
                    prompt_context += "\n\n" + assembled.text
        except Exception as exc:
            logging.warning("Context assembly skipped: %s", exc)

        document_preparation = document_coordinator.prepare(
            req,
            prompt_context,
            workspace_id=workspace_id,
        )
        prompt_context = document_preparation.context
        if req.image_data:
            screenshot_context = extract_screenshot_context(req.image_data)
            if screenshot_context:
                prompt_context += f"\n\n{screenshot_context}"

        if getattr(config, "auto_read_chat_paths", False):
            file_path_pattern = re.compile(
                r'(?:^|[\s\'"(])((~|/[\w.])[^\s\'")\]]*)',
                re.MULTILINE,
            )
            requested_paths = [
                match.group(1).strip()
                for match in file_path_pattern.finditer(req.message or "")
            ]
            if requested_paths:
                context.append_audit_event(
                    "auto_file_context_blocked",
                    user_email=effective_email,
                    path_count=len(requested_paths),
                    allow_file_context=req.allow_file_context,
                    reason=(
                        "local file context requires an explicit approved "
                        "file/tool flow"
                    ),
                )
                if req.allow_file_context:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Automatic local file reads are disabled in chat. "
                            "Attach the file, upload it, or use an approved "
                            "local-file tool flow."
                        ),
                    )

        trace_seed = chat_service.build_graph_trace(
            req.message,
            context.knowledge_graph
            if (context.enable_graph and context.knowledge_graph)
            else None,
            prompt_context,
            allowed_workspaces={workspace_id} if workspace_id else None,
        )
        if context_trace is not None and isinstance(trace_seed, dict):
            trace_seed["context_assembly"] = context_trace

        history_message = (
            f"{req.message}\n[Image attached]" if req.image_data else req.message
        )
        await chat_service.persist_entry(
            "user",
            history_message,
            history_meta=history_meta,
            history_user=history_user,
        )
        notify_chat_message("user", req.message, req.source)

        document_response = await document_coordinator.response(
            req,
            document_preparation,
            model_id=selected_model_id,
            effective_email=effective_email,
            workspace_id=workspace_id,
            history_meta=history_meta,
            trace_seed=trace_seed,
        )
        if document_response is not None:
            return document_response

        if req.stream:
            recent_context = recent_chat_context(
                user_email=effective_email,
                conversation_id=req.conversation_id,
                workspace_id=workspace_id,
            )
            stream_context = prompt_context
            if recent_context:
                stream_context = (
                    f"[RECENT CONVERSATION]\n{recent_context}\n\n{prompt_context}"
                ).strip()
            return StreamingResponse(
                stream_chat(
                    req,
                    stream_context,
                    req.image_data,
                    router=model_router,
                    chat_service=chat_service,
                    knowledge_graph=context.knowledge_graph,
                    enable_graph=context.enable_graph,
                    notify=notify_chat_message,
                    trace_seed=trace_seed,
                    effective_email=effective_email,
                    history_meta=history_meta,
                    model_id=selected_model_id,
                    workspace_id=workspace_id,
                ),
                media_type="text/event-stream",
                headers={"X-Model": selected_model_id},
            )

        if req.image_data:
            recent_context = recent_chat_context(
                limit=6,
                include_image_missing_replies=False,
                user_email=effective_email,
                conversation_id=req.conversation_id,
                workspace_id=workspace_id,
            )
            full_context = (
                f"[RECENT CONVERSATION]\n{recent_context}\n\n{prompt_context}".strip()
                if recent_context
                else prompt_context
            )
        else:
            history_context = recent_chat_context(
                user_email=effective_email,
                conversation_id=req.conversation_id,
                workspace_id=workspace_id,
            )
            full_context = (
                f"{history_context}\n{prompt_context}"
                if prompt_context
                else history_context
            )
        result = await model_router.generate_as(
            selected_model_id,
            req.message,
            full_context,
            req.max_tokens,
            req.temperature,
            req.image_data,
        )
        response_text = str(result)
        trace_record = await chat_service.persist_answer(
            question=req.message,
            response=response_text,
            conversation_id=req.conversation_id,
            user_email=effective_email,
            user_nickname=req.user_nickname,
            source=req.source,
            trace=trace_seed,
            workspace_id=workspace_id,
            history_meta=history_meta,
            notify=notify_chat_message,
        )
        return JSONResponse(
            content={
                "response": response_text,
                "trace_id": trace_record["id"],
                "trace": trace_record,
            }
        )

    register_history_routes(
        api_router,
        HistoryRouteDependencies(
            chat_service=chat_service,
            require_user=require_user,
            scope_for_user=history_scope_for_user,
            group_conversations=context.group_history_conversations,
            get_conversation_messages=context.get_conversation_messages,
            conversation_title=context.conversation_title,
            clear_conversation=context.clear_conversation,
            clear_history=context.clear_history,
            append_audit_event=context.append_audit_event,
        ),
    )
    agent_controller.register_routes(api_router)
    return api_router
