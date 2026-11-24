"""Runtime hooks for the knowledge graph package.

Supports scoped dependency injection via :func:`llm_router_scope` (backed by a
``ContextVar``) for multi-instance and isolated testing, while keeping the
process-level :func:`set_llm_router` / :func:`get_llm_router` contract and
exposing :func:`require_llm_router` for callers that need an explicit
initialization guard.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_UNSET = object()
_llm_router_ref: Any = None
_scoped_llm_router: ContextVar[Any] = ContextVar("lattice_brain_llm_router", default=_UNSET)


def set_llm_router(router_instance: Any) -> None:
    global _llm_router_ref
    _llm_router_ref = router_instance


def get_llm_router() -> Optional[Any]:
    scoped = _scoped_llm_router.get()
    if scoped is not _UNSET:
        return scoped
    return _llm_router_ref


def require_llm_router() -> Any:
    """Return the active LLM router or raise ``RuntimeError`` if uninitialized."""
    router = get_llm_router()
    if router is None:
        raise RuntimeError(
            "LLM router is not initialized; bind one via llm_router_scope() or set_llm_router()."
        )
    return router


@contextmanager
def llm_router_scope(router_instance: Any) -> Iterator[Any]:
    """Bind a scoped LLM router for the duration of a ``with`` block."""
    token = _scoped_llm_router.set(router_instance)
    try:
        yield router_instance
    finally:
        _scoped_llm_router.reset(token)


__all__ = [
    "get_llm_router",
    "llm_router_scope",
    "require_llm_router",
    "set_llm_router",
]
