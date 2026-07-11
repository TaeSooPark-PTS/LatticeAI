"""Platform and automation wiring for the application factory.

This module owns the cross-subsystem runtime assembly that used to live inline
inside ``app_factory._build``.  Keeping it here reduces the app factory's global
surface without changing router order or the legacy exported runtime names.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_platform_automation_runtime(
    *,
    model_router: Any,
    workspace_store: Any,
    workspace_service: Any,
    plugin_registry: Any,
    get_current_user: Callable[..., Any],
    workspace_graph: Callable[[], Any],
    workspace_scope_from_request: Callable[..., Any],
    get_tool_permission: Callable[..., Any],
    hooks: Any,
    agent_registry: Any,
    data_dir: Any,
    append_audit_event: Callable[..., Any],
    memory_service: Any = None,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build platform services, automation services, and hook bindings.

    The returned names intentionally match the explicit composition-root
    bindings consumed by the typed application stages.
    """
    from latticeai.runtime.automation_runtime import build_automation_runtime
    from latticeai.services.platform_runtime import PlatformRuntime

    def _llm_generate_sync(
        message: str,
        context: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str:
        # Synchronous model bridge for the orchestrator's role runner. Safe
        # because the agents run endpoint executes start() in a worker thread
        # (asyncio.to_thread), where no event loop is running.
        import asyncio as _asyncio

        return str(_asyncio.run(model_router.generate(
            message,
            context=context,
            max_tokens=max_tokens,
            temperature=temperature,
        )))

    platform = PlatformRuntime(
        store=workspace_store,
        workspace_service=workspace_service,
        plugin_registry=plugin_registry,
        get_current_user=get_current_user,
        workspace_graph=workspace_graph,
        workspace_scope_from_request=workspace_scope_from_request,
        get_tool_permission=get_tool_permission,
        hooks=hooks,
        llm_generate=_llm_generate_sync,
        llm_available=lambda: bool(getattr(model_router, "current_model_id", None)),
        agent_registry=agent_registry,
        memory_recall=memory_service.recall if memory_service is not None else None,
    )

    automation_runtime = build_automation_runtime(
        store=workspace_store,
        platform=platform,
        data_dir=data_dir,
        workspace_graph=workspace_graph,
        append_audit_event=append_audit_event,
        hooks=hooks,
        tz_name=tz_name,
    )

    return {
        "_llm_generate_sync": _llm_generate_sync,
        "PLATFORM": platform,
        "_automation_runtime": automation_runtime,
        "REVIEW_QUEUE": automation_runtime["REVIEW_QUEUE"],
        "TRIGGER_SERVICE": automation_runtime["TRIGGER_SERVICE"],
        "AGENT_RUNTIME": automation_runtime["AGENT_RUNTIME"],
        "RUN_EXECUTOR": automation_runtime["RUN_EXECUTOR"],
    }
