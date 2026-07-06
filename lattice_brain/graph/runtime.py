"""Runtime hooks for the knowledge graph package."""

from __future__ import annotations

from typing import Any

_llm_router_ref: Any = None


def set_llm_router(router_instance: Any) -> None:
    global _llm_router_ref
    _llm_router_ref = router_instance


def get_llm_router() -> Any:
    return _llm_router_ref
