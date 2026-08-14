"""Tool lifecycle dispatch — the one path every tool call takes.

(lattice_brain/runtime/hooks.py)
책임: HookContext/Result, dispatch_tool (pre_tool → execute → post_tool).
상위: api/agent_worker_seam, api/tools.

v3.2.0 made lifecycle hooks first-class: a persisted registry under
``data_dir/hooks.json``, ordering, enable flags, built-in descriptions and a
subprocess runner for user hooks. All of that is platform state, and v11.6.0
moved platform state into ``lattice-host`` — the registry, its HTTP surface
(``/api/hooks/*``) and the built-in runners (which bound to the audit log and
the sensitivity classifier) went with it.

What a stateless compute worker still needs is the *shape* of a dispatch, so
the seam that runs a tool runs it through the same three steps the product path
does. :func:`dispatch_tool` with ``hooks=None`` — which is what the worker
passes, because it holds no registry — is a transparent pass-through, and
:class:`HookContext` / :class:`HookResult` remain the contract a bound runner
would be handed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..utils import now_iso as _now

LOGGER = logging.getLogger(__name__)

# Hook statuses a dispatch can record.
HOOK_STATUSES = ("ok", "blocked", "error", "skipped", "advisory")


class HookContext:
    """Mutable execution context handed to every hook in a dispatch.

    A bound hook runner may inspect or modify :attr:`payload` (e.g. redact
    secrets in place), attach data via :meth:`set`, or halt a pending action
    with :meth:`block`. Blocking is honoured for the ``pre_*`` kinds that gate
    real work (a blocked ``pre_run`` stops the agent run; a blocked ``pre_tool``
    stops the tool call).
    """

    __slots__ = (
        "kind", "event", "payload", "metadata",
        "user_email", "workspace_id", "blocked", "block_reason", "notes",
    )

    def __init__(
        self,
        kind: str,
        event: str = "",
        payload: Optional[Dict[str, Any]] = None,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.kind = kind
        self.event = event or kind
        self.payload = dict(payload or {})
        self.metadata = dict(metadata or {})
        self.user_email = user_email
        self.workspace_id = workspace_id
        self.blocked = False
        self.block_reason = ""
        self.notes: List[str] = []

    def set(self, key: str, value: Any) -> "HookContext":
        self.payload[key] = value
        return self

    def block(self, reason: str = "") -> "HookContext":
        self.blocked = True
        self.block_reason = reason or "blocked by hook"
        return self

    def note(self, message: str) -> "HookContext":
        self.notes.append(str(message))
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "event": self.event,
            "payload": self.payload,
            "metadata": self.metadata,
            "user_email": self.user_email,
            "workspace_id": self.workspace_id,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "notes": list(self.notes),
        }


class HookResult:
    """The outcome of running a single hook (one entry in a dispatch)."""

    __slots__ = (
        "hook_id", "name", "kind", "status", "detail", "output",
        "duration_ms", "blocked", "source", "binding", "started_at",
    )

    def __init__(
        self,
        *,
        hook_id: str,
        name: str = "",
        kind: str = "",
        status: str = "ok",
        detail: str = "",
        output: str = "",
        duration_ms: int = 0,
        blocked: bool = False,
        source: str = "",
        binding: str = "",
        started_at: Optional[str] = None,
    ):
        self.hook_id = hook_id
        self.name = name
        self.kind = kind
        self.status = status  # ok | blocked | error | skipped | advisory
        self.detail = detail
        self.output = output
        self.duration_ms = duration_ms
        self.blocked = blocked
        self.source = source
        self.binding = binding
        self.started_at = started_at or _now()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hook_id": self.hook_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
            "output": self.output,
            "duration_ms": self.duration_ms,
            "blocked": self.blocked,
            "source": self.source,
            "binding": self.binding,
            "started_at": self.started_at,
        }


def dispatch_tool(
    hooks: Any,
    tool_name: str,
    args: Any,
    run_fn: Callable[[], Any],
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
    source: str = "",
) -> Any:
    """Run a tool through the shared ``pre_tool`` → execute → ``post_tool`` lifecycle.

    This is the single tool-dispatch path so every caller (the HTTP ``/tools/*``
    routes, the single-agent runtime in :mod:`latticeai.core.agent`, and the
    workflow tool node) fires the same hooks. A blocking ``pre_tool`` hook raises
    :class:`PermissionError`; a tool error still fires ``post_tool`` (status
    ``error``) before re-raising. With ``hooks=None`` it is a transparent
    pass-through, so the tool path is unchanged when hooks are absent.
    """
    if hooks is None:
        return run_fn()
    try:
        arg_keys = list(args.keys()) if isinstance(args, dict) else []
    except Exception as exc:
        LOGGER.debug("tool argument metadata could not be inspected: %s", exc)
        arg_keys = []
    pre = hooks.fire_hook(
        "pre_tool", f"tool.{tool_name}",
        payload={"tool": tool_name, "args_keys": arg_keys, "source": source},
        user_email=user_email, workspace_id=workspace_id,
    )
    if pre.get("blocked"):
        raise PermissionError(pre.get("block_reason") or f"Tool '{tool_name}' blocked by a pre_tool hook.")
    try:
        result = run_fn()
    except Exception as exc:
        hooks.fire_hook(
            "post_tool", f"tool.{tool_name}",
            payload={"tool": tool_name, "status": "error", "detail": str(exc), "source": source},
            user_email=user_email, workspace_id=workspace_id,
        )
        raise
    hooks.fire_hook(
        "post_tool", f"tool.{tool_name}",
        payload={"tool": tool_name, "status": "ok", "source": source},
        user_email=user_email, workspace_id=workspace_id,
    )
    return result


__all__ = [
    "HOOK_STATUSES",
    "HookContext",
    "HookResult",
    "dispatch_tool",
]
