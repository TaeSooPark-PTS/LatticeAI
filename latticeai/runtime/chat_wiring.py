"""Chat and interaction wiring seam for ``latticeai.app_factory``."""

from __future__ import annotations

from typing import Any, Optional

from latticeai.runtime.permission_mode_wiring import (
    bind_dispatch_permission_mode,
    resolve_active_permission_mode,
)
from latticeai.services.router_context import InteractionRouterContext, ToolRouterContext


def build_chat_agent_runtime_from_context(
    *,
    build_agent_runtime: Any,
    model_router: Any,
    execute_tool: Any,
    recent_chat_context: Any,
    clear_history: Any,
    knowledge_save: Any,
    audit: Any,
    hooks: Any,
    brain_memory: Any,
) -> Any:
    # Ensure dispatch + agent share the same autonomy dial before the runtime
    # is constructed (process-wide service; data_dir refined later at router mount).
    bind_dispatch_permission_mode()
    return build_agent_runtime(
        model_router=model_router,
        execute_tool=execute_tool,
        recent_chat_context=recent_chat_context,
        clear_history=clear_history,
        knowledge_save=knowledge_save,
        audit=audit,
        hooks=hooks,
        brain_memory=brain_memory,
        permission_mode=resolve_active_permission_mode,
    )


def build_interaction_contexts(
    *,
    config: Any,
    ingestion_pipeline: Any,
    data_dir: Any,
    static_dir: Any,
    model_router: Any,
    require_user: Any,
    require_admin: Any,
    get_current_user: Any,
    clear_history: Any,
    append_audit_event: Any,
    enforce_rate_limit: Any,
    bytes_match_extension: Any,
    classify_sensitive_message: Any,
    save_to_history: Any,
    enable_graph: bool,
    knowledge_graph: Any,
    require_graph: Any,
    local_kg_watcher: Any,
    load_mcp_installs: Any,
    recommend_mcps: Any,
    install_mcp: Any,
    mcp_public_item: Any,
    hooks: Any,
    workspace_service: Any,
    chat_context: Any,
    search_service: Any,
    allowed_workspaces_for: Any,
    embedding_info: Any,
    agent_registry: Any,
    memory_service: Any,
    platform: Any,
    active_model_getter: Any = None,
    brain_intelligence: Any = None,
) -> tuple[ToolRouterContext, InteractionRouterContext]:
    tool_router_context = ToolRouterContext(
        config=config,
        ingestion_pipeline=ingestion_pipeline,
        data_dir=data_dir,
        static_dir=static_dir,
        model_router=model_router,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
        clear_history=clear_history,
        append_audit_event=append_audit_event,
        enforce_rate_limit=enforce_rate_limit,
        bytes_match_extension=bytes_match_extension,
        classify_sensitive_message=classify_sensitive_message,
        save_to_history=save_to_history,
        enable_graph=enable_graph,
        knowledge_graph=knowledge_graph,
        require_graph=require_graph,
        local_kg_watcher=local_kg_watcher,
        load_mcp_installs=load_mcp_installs,
        recommend_mcps=recommend_mcps,
        install_mcp=install_mcp,
        mcp_public_item=mcp_public_item,
        hooks=hooks,
        workspace_service=workspace_service,
        allowed_workspaces_for=allowed_workspaces_for,
    )
    interaction_router_context = InteractionRouterContext(
        chat_context=chat_context,
        search_service=search_service,
        allowed_workspaces_for=allowed_workspaces_for,
        require_user=require_user,
        embedding_info=embedding_info,
        tool_context=tool_router_context,
        hooks=hooks,
        agent_registry=agent_registry,
        memory_service=memory_service,
        platform=platform,
        active_model_getter=active_model_getter,
        brain_intelligence=brain_intelligence,
    )
    return tool_router_context, interaction_router_context


def maybe_build_telegram_chat_mirror(
    *,
    enable_telegram: bool,
    spawn: Any,
) -> Optional[Any]:
    if not enable_telegram:
        return None

    def telegram_chat_mirror(role: str, text: str, source: Optional[str] = None) -> None:
        from latticeai.integrations.telegram_bot import broadcast_web_chat
        spawn(broadcast_web_chat(role, text), name="telegram_broadcast")

    return telegram_chat_mirror
