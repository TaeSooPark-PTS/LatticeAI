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
    from local_knowledge_api import LocalKnowledgeWatcher

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
