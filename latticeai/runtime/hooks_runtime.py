"""Hooks + local-knowledge watcher runtime assembly for app startup.

Extracted from ``app_factory._build`` as a composition seam: the hooks
registry must be constructed *ahead* of the local-knowledge watcher so
folder-watch reindexes can fire the ``pre_index``/``post_index`` lifecycle
hooks. Heavy imports stay inside the function so importing the module has no
side effects.
"""

from __future__ import annotations

from typing import Any, Callable, Dict


def build_hooks_runtime(
    *,
    data_dir: Any,
    enable_graph: bool,
    knowledge_graph_getter: Callable[[], Any],
) -> Dict[str, Any]:
    """Construct the hooks registry and local-knowledge watcher behind one seam."""

    from lattice_brain.runtime.hooks import HooksRegistry
    from latticeai.services.local_knowledge import LocalKnowledgeWatcher

    hooks_registry = HooksRegistry(data_dir / "hooks.json")
    local_kg_watcher = (
        LocalKnowledgeWatcher(knowledge_graph_getter, hooks=hooks_registry)
        if enable_graph
        else None
    )
    return {
        "HOOKS_REGISTRY": hooks_registry,
        "LOCAL_KG_WATCHER": local_kg_watcher,
    }


def bind_trigger_hook_runner(*, registry: Any, trigger_service: Any) -> str:
    """Ensure the brain-event trigger hook exists and bind its runtime runner."""

    from latticeai.services.triggers import TRIGGER_HOOK_NAME

    trigger_hook_id = next(
        (
            h.get("id")
            for h in registry._state.get("custom", [])
            if h.get("name") == TRIGGER_HOOK_NAME
        ),
        None,
    )
    if trigger_hook_id is None:
        trigger_hook_id = registry.register(
            name=TRIGGER_HOOK_NAME,
            kind="post_tool",
            description="Fires brain_event workflow triggers when knowledge enters the brain.",
        )["id"]
    registry.register_hook(trigger_hook_id, trigger_service.hook_runner())
    return trigger_hook_id


def bind_builtin_hook_runners(
    *,
    registry: Any,
    append_audit_event: Callable[..., Any],
    get_tool_permission: Callable[..., Any],
    classify_sensitive_message: Callable[..., Any],
) -> None:
    """Bind concrete platform runners for built-in hook definitions."""

    from latticeai.core.builtin_hooks import register_builtin_hook_runners

    register_builtin_hook_runners(
        registry,
        append_audit_event=append_audit_event,
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=classify_sensitive_message,
    )
