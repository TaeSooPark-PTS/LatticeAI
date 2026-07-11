"""Hooks platform — a persisted registry of lifecycle extension points.

(lattice_brain/runtime/hooks.py)
책임: HookContext/Result, HooksRegistry (persist+order+enable+builtin), dispatch_tool,
      fire_hook (pre/post_run/tool/workflow), BUILTIN_HOOKS 등록.
의존성: threading, subprocess (user hooks). dispatch_tool은 tool path 단일화.
상위: agent_runtime.py (pre/post_run), tool_dispatch.py, api/tools, core/agent (shared).

Lattice AI runs several behaviours at well-defined points in the agent / tool /
workflow lifecycle (audit logging, secret redaction, sensitive-data
classification, tool-permission gating, memory snapshots, workflow replay
logging). v3.2.0 makes those points *first-class and inspectable*: every hook is
listed, ordered, and individually enable/disable-able, and users can register
their own custom hooks alongside the built-ins.

The registry owns metadata, ordering and the enabled flag (persisted to
``data_dir/hooks.json``). Built-in hooks carry ``source="builtin"`` and map onto
behaviour the platform already performs; ``managed`` records whether the
behaviour is enforced by the platform (``platform``) or is an advisory hook the
user has registered (``user``). The registry never silently drops a hook: the
full set is always returned so the UI can show exactly what runs and in which
order.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..utils import now_iso as _now


LOGGER = logging.getLogger(__name__)


HOOK_KINDS = (
    "pre_run",
    "post_run",
    "pre_tool",
    "post_tool",
    "pre_workflow",
    "post_workflow",
    "pre_upload",
    "post_upload",
    "pre_index",
    "post_index",
    "agent",
)

# Kinds retired in v3.4.1 in favour of the explicit pre_/post_ lifecycle pairs.
# Accepted on input and mapped forward so older callers / persisted custom hooks
# never break.
LEGACY_KIND_ALIASES = {
    "workflow": "post_workflow",
    "pipeline": "post_index",
}

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


def hook_context(kind: str, event: str = "", **kwargs: Any) -> HookContext:
    """Factory for a :class:`HookContext` (matches the public hook vocabulary)."""
    return HookContext(kind, event, **kwargs)


def hook_result(**kwargs: Any) -> HookResult:
    """Factory for a :class:`HookResult`."""
    return HookResult(**kwargs)


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


# Built-in hooks describe lifecycle points the platform already exercises. They
# are honest reflections of existing behaviour (see the `binding` field), made
# visible and orderable here. Disabling a `managed="platform"` hook is recorded
# and surfaced, but core safety behaviours remain enforced by their owning
# subsystem — the UI states this explicitly so nothing is misrepresented.
BUILTIN_HOOKS: List[Dict[str, Any]] = [
    {
        "id": "builtin:redact-secrets",
        "name": "Redact secrets",
        "kind": "pre_run",
        "order": 10,
        "description": "Strip secret-like fields (token, password, api_key…) from agent context packets before a run.",
        "binding": "lattice_brain.runtime.multi_agent._redact",
        "managed": "platform",
    },
    {
        "id": "builtin:research-memory-snapshot",
        "name": "Research memory snapshot",
        "kind": "agent",
        "order": 20,
        "description": "Capture a short-term memory snapshot after the researcher stage gathers context.",
        "binding": "lattice_brain.runtime.multi_agent.default_role_runner",
        "managed": "platform",
    },
    {
        "id": "builtin:tool-permission-gate",
        "name": "Tool permission gate",
        "kind": "pre_tool",
        "order": 10,
        "description": "Require explicit approval for tools whose governance policy is not auto-approve.",
        "binding": "latticeai.core.tool_registry.ToolRegistry.permission",
        "managed": "platform",
    },
    {
        "id": "builtin:sensitive-data-guard",
        "name": "Sensitive-data guard",
        "kind": "pre_tool",
        "order": 20,
        "description": "Classify outgoing content for sensitive data before tool execution.",
        "binding": "server_app.classify_sensitive_message",
        "managed": "platform",
    },
    {
        "id": "builtin:audit-agent-run",
        "name": "Audit agent run",
        "kind": "post_run",
        "order": 10,
        "description": "Append every completed agent run to the workspace audit log.",
        "binding": "lattice_brain.runtime.agent_runtime.AgentRuntime.start",
        "managed": "platform",
    },
    {
        "id": "builtin:workflow-replay-log",
        "name": "Workflow replay log",
        "kind": "workflow",
        "order": 10,
        "description": "Record each workflow run's timeline so it can be replayed step by step.",
        "binding": "latticeai.api.workflow_designer",
        "managed": "platform",
    },
    {
        "id": "builtin:pipeline-index-status",
        "name": "Pipeline index status",
        "kind": "pipeline",
        "order": 10,
        "description": "Publish ingest / embed / graph-build pipeline state to the retrieval index status.",
        "binding": "latticeai.api.search",
        "managed": "platform",
    },
]

# Built-in hooks now bucket onto the v3.4.1 lifecycle pairs.
for _hook in BUILTIN_HOOKS:
    _hook["kind"] = LEGACY_KIND_ALIASES.get(_hook["kind"], _hook["kind"])
del _hook


class HooksRegistry:
    """Persisted registry of lifecycle hooks (built-in + user-registered)."""

    def __init__(self, path: Path, *, command_timeout: float = 20.0, run_log_limit: int = 100):
        self.path = Path(path)
        self._state: Dict[str, Any] = self._load()
        # Runtime dispatch state: in-process runners bound to hook ids, plus a
        # bounded, persisted log of recent executions so the UI can show that
        # hooks actually ran (not just that they are registered).
        self._runtime_runners: Dict[str, Callable[[HookContext], Any]] = {}
        self._command_timeout = float(command_timeout)
        self._run_lock = threading.Lock()
        self._runs_path = self.path.parent / "hooks_runs.json"
        self._runs: deque = deque(self._load_runs(), maxlen=int(run_log_limit))

    # ── persistence ───────────────────────────────────────────────────────
    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data.setdefault("custom", [])
                    data.setdefault("overrides", {})
                    return data
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                LOGGER.warning("hook registry state is unreadable at %s: %s", self._path, exc)
        return {"custom": [], "overrides": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── views ─────────────────────────────────────────────────────────────
    def _materialize(self) -> List[Dict[str, Any]]:
        overrides: Dict[str, Any] = self._state.get("overrides", {})
        hooks: List[Dict[str, Any]] = []
        for base in BUILTIN_HOOKS:
            ov = overrides.get(base["id"], {})
            hook = dict(base)
            hook["source"] = "builtin"
            hook["enabled"] = bool(ov.get("enabled", True))
            if "order" in ov:
                hook["order"] = ov["order"]
            hook["removable"] = False
            hooks.append(hook)
        for custom in self._state.get("custom", []):
            hook = dict(custom)
            hook["source"] = "user"
            hook.setdefault("managed", "user")
            hook.setdefault("binding", "advisory")
            hook["enabled"] = bool(custom.get("enabled", True))
            hook["removable"] = True
            hooks.append(hook)
        # Honest execution flag: a hook actually runs only if a runner is bound
        # (built-ins) or it carries a command (user hooks); otherwise it is
        # advisory (listed + ordered, but a no-op when fired).
        for hook in hooks:
            hook["executable"] = self.has_runner(hook["id"]) or bool(str(hook.get("command") or "").strip())
            hook["advisory"] = not hook["executable"]
        hooks.sort(key=lambda h: (HOOK_KINDS.index(h["kind"]) if h["kind"] in HOOK_KINDS else 99, h.get("order", 100), h["id"]))
        return hooks

    def list(self, kind: Optional[str] = None) -> Dict[str, Any]:
        hooks = self._materialize()
        if kind:
            hooks = [h for h in hooks if h["kind"] == kind]
        counts: Dict[str, Dict[str, int]] = {}
        for h in self._materialize():
            bucket = counts.setdefault(h["kind"], {"total": 0, "enabled": 0})
            bucket["total"] += 1
            if h["enabled"]:
                bucket["enabled"] += 1
        return {
            "hooks": hooks,
            "kinds": list(HOOK_KINDS),
            "counts": counts,
            "total": len(hooks),
            "enabled": sum(1 for h in hooks if h["enabled"]),
            "generated_at": _now(),
        }

    def get(self, hook_id: str) -> Optional[Dict[str, Any]]:
        return next((h for h in self._materialize() if h["id"] == hook_id), None)

    def inspect(self, hook_id: str) -> Dict[str, Any]:
        hook = self.get(hook_id)
        if hook is None:
            raise KeyError(hook_id)
        detail = dict(hook)
        detail["advisory"] = hook.get("managed") != "platform"
        detail["note"] = (
            "Enforced by its owning subsystem; the registry controls visibility and ordering."
            if hook.get("managed") == "platform"
            else "User-registered hook: listed, ordered and inspectable; runs advisory in this build."
        )
        return detail

    # ── mutations ─────────────────────────────────────────────────────────
    def set_enabled(self, hook_id: str, enabled: bool) -> Dict[str, Any]:
        if self.get(hook_id) is None:
            raise KeyError(hook_id)
        if hook_id.startswith("builtin:"):
            self._state.setdefault("overrides", {}).setdefault(hook_id, {})["enabled"] = bool(enabled)
        else:
            for custom in self._state.get("custom", []):
                if custom["id"] == hook_id:
                    custom["enabled"] = bool(enabled)
        self._save()
        return self.get(hook_id)  # type: ignore[return-value]

    def set_order(self, hook_id: str, order: int) -> Dict[str, Any]:
        if self.get(hook_id) is None:
            raise KeyError(hook_id)
        order = int(order)
        if hook_id.startswith("builtin:"):
            self._state.setdefault("overrides", {}).setdefault(hook_id, {})["order"] = order
        else:
            for custom in self._state.get("custom", []):
                if custom["id"] == hook_id:
                    custom["order"] = order
        self._save()
        return self.get(hook_id)  # type: ignore[return-value]

    def reorder(self, kind: str, ordered_ids: List[str]) -> Dict[str, Any]:
        for idx, hook_id in enumerate(ordered_ids):
            try:
                self.set_order(hook_id, (idx + 1) * 10)
            except KeyError:
                continue
        return self.list(kind=kind)

    def register(
        self,
        *,
        name: str,
        kind: str,
        description: str = "",
        command: str = "",
        order: Optional[int] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        if not str(name).strip():
            raise ValueError("name is required")
        kind = LEGACY_KIND_ALIASES.get(kind, kind)
        if kind not in HOOK_KINDS:
            raise ValueError(f"kind must be one of {', '.join(HOOK_KINDS)}")
        slug = str(name).strip().lower().replace(" ", "-")
        hook_id = f"user:{slug}"
        existing = {c["id"] for c in self._state.get("custom", [])}
        if hook_id in existing:
            hook_id = f"user:{slug}-{len(existing) + 1}"
        entry = {
            "id": hook_id,
            "name": str(name).strip(),
            "kind": kind,
            "description": str(description or "").strip(),
            "command": str(command or "").strip(),
            "order": int(order) if order is not None else 100,
            "enabled": bool(enabled),
            "managed": "user",
            "binding": "advisory",
            "created_at": _now(),
        }
        self._state.setdefault("custom", []).append(entry)
        self._save()
        return entry

    def remove(self, hook_id: str) -> Dict[str, Any]:
        if hook_id.startswith("builtin:"):
            raise ValueError("Built-in hooks cannot be removed; disable them instead.")
        before = len(self._state.get("custom", []))
        self._state["custom"] = [c for c in self._state.get("custom", []) if c["id"] != hook_id]
        if len(self._state["custom"]) == before:
            raise KeyError(hook_id)
        self._save()
        return {"removed": hook_id}

    # ── execution engine ──────────────────────────────────────────────────
    # The registry above owns *what* runs and in *what order*. The methods below
    # own *running* it: a hook executes either via an in-process runner bound by
    # its owning subsystem (built-ins) or, for user hooks, by running their
    # ``command`` as a subprocess. ``pre_*`` hooks can block the pending action.

    def register_hook(self, hook_id: str, runner: Callable[[HookContext], Any]) -> "HooksRegistry":
        """Bind a callable that *executes* when ``hook_id`` fires.

        Built-in hooks bind their owning subsystem's real behaviour here at app
        startup so dispatch performs actual work rather than a placeholder.
        Returns ``self`` so registrations can be chained.
        """
        if not callable(runner):
            raise TypeError("runner must be callable")
        self._runtime_runners[hook_id] = runner
        return self

    # Alias — descriptive name used by the wiring layer.
    register_runtime_hook = register_hook

    def unregister_hook(self, hook_id: str) -> None:
        self._runtime_runners.pop(hook_id, None)

    def has_runner(self, hook_id: str) -> bool:
        return hook_id in self._runtime_runners

    def run_hooks(
        self,
        kind: str,
        context: Optional[HookContext] = None,
        *,
        event: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run every enabled hook of ``kind``, in order, against one context.

        Returns the dispatch record: which hooks ran, whether the pending action
        was blocked, and a per-hook result list. A ``pre_*`` hook that blocks
        short-circuits the remaining hooks (fail-closed gate semantics).
        """
        kind = LEGACY_KIND_ALIASES.get(kind, kind)
        if kind not in HOOK_KINDS:
            raise ValueError(f"kind must be one of {', '.join(HOOK_KINDS)}")
        if context is None:
            context = HookContext(
                kind, event=event or kind, payload=payload,
                user_email=user_email, workspace_id=workspace_id, metadata=metadata,
            )
        hooks = [h for h in self._materialize() if h["kind"] == kind and h.get("enabled")]
        results: List[HookResult] = []
        for hook in hooks:
            res = self._run_one(hook, context)
            results.append(res)
            self._record_run(res, context)
            if res.blocked:
                context.block(res.detail or f"{hook['id']} blocked {context.event}")
                break
        return {
            "kind": kind,
            "event": context.event,
            "ran": len(results),
            "blocked": context.blocked,
            "block_reason": context.block_reason,
            "results": [r.as_dict() for r in results],
            "generated_at": _now(),
        }

    def run_hook(
        self,
        hook_id: str,
        context: Optional[HookContext] = None,
        *,
        event: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a single hook by id (regardless of kind ordering)."""
        hook = self.get(hook_id)
        if hook is None:
            raise KeyError(hook_id)
        if context is None:
            context = HookContext(
                hook["kind"], event=event or hook_id, payload=payload,
                user_email=user_email, workspace_id=workspace_id, metadata=metadata,
            )
        if not hook.get("enabled"):
            res = HookResult(
                hook_id=hook_id, name=hook.get("name", hook_id), kind=hook["kind"],
                status="skipped", detail="hook disabled",
                source=hook.get("source", ""), binding=hook.get("binding", ""),
            )
        else:
            res = self._run_one(hook, context)
        self._record_run(res, context)
        return res.as_dict()

    def fire_hook(
        self,
        kind: str,
        event: str = "",
        *,
        payload: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[HookContext] = None,
    ) -> Dict[str, Any]:
        """Fire-and-forget convenience over :meth:`run_hooks`.

        Never raises — a dispatch failure is captured in the returned record so
        a hook misconfiguration can never crash the lifecycle point that fired
        it. Callers that need to *gate* on a block should read ``["blocked"]``.
        """
        try:
            return self.run_hooks(
                kind, context, event=event or kind, payload=payload,
                user_email=user_email, workspace_id=workspace_id, metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("hook dispatch failed before execution: %s", kind)
            return {"kind": kind, "event": event or kind, "ran": 0, "blocked": False,
                    "block_reason": "", "error": str(exc), "results": [], "generated_at": _now()}

    # ── single-hook execution ─────────────────────────────────────────────
    def _run_one(self, hook: Dict[str, Any], context: HookContext) -> HookResult:
        hook_id = hook["id"]
        start = time.perf_counter()
        runner = self._runtime_runners.get(hook_id)
        try:
            if runner is not None:
                out = runner(context)
                status, detail, output, blocked = self._interpret_runner_output(out, context)
            elif str(hook.get("command") or "").strip():
                status, detail, output, blocked = self._run_command(hook, context)
            else:
                # No bound runner and no command → advisory (listed + ordered only).
                status, detail, output, blocked = "advisory", "", "", False
        except Exception as exc:  # a misbehaving hook never breaks the dispatch
            status, detail, output, blocked = "error", str(exc)[:500], "", False
        duration_ms = int((time.perf_counter() - start) * 1000)
        return HookResult(
            hook_id=hook_id,
            name=hook.get("name", hook_id),
            kind=hook["kind"],
            status=status,
            detail=detail,
            output=output,
            duration_ms=duration_ms,
            blocked=blocked,
            source=hook.get("source", ""),
            binding=hook.get("binding", ""),
        )

    @staticmethod
    def _interpret_runner_output(out: Any, context: HookContext):
        """Normalize a runner's return value into (status, detail, output, blocked).

        A runner may return ``None`` (ok), a ``str`` (ok + output text), or a
        dict ``{status?, detail?, output?, block?}``. Calling ``context.block()``
        also blocks, regardless of the return value.
        """
        blocked = bool(getattr(context, "blocked", False))
        if isinstance(out, dict):
            status = str(out.get("status") or ("blocked" if (out.get("block") or blocked) else "ok"))
            block = bool(out.get("block")) or blocked or status == "blocked"
            detail = str(out.get("detail") or context.block_reason or "")[:500]
            return status, detail, str(out.get("output") or "")[:4000], block
        if isinstance(out, str):
            return ("blocked" if blocked else "ok", context.block_reason if blocked else "", out[:4000], blocked)
        # None or anything else → ok (or blocked if the runner called context.block()).
        return ("blocked" if blocked else "ok", context.block_reason if blocked else "", "", blocked)

    def _run_command(self, hook: Dict[str, Any], context: HookContext):
        """Run a user hook's ``command`` as a subprocess with the context on stdin.

        The full context is provided both as the ``LATTICE_HOOK_CONTEXT`` env var
        and on stdin (JSON). Exit code 0 = ok. A non-zero exit from a ``pre_*``
        hook gates (blocks) the pending action; from any other kind it is
        recorded as an error without blocking. A timeout fails closed for gates.
        """
        cmd = str(hook.get("command") or "").strip()
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            return "error", f"invalid command: {exc}", "", False
        if not argv:
            return "skipped", "empty command", "", False
        ctx_json = json.dumps(context.as_dict(), ensure_ascii=False)
        # Command hooks run with an intentionally small environment. In
        # particular, provider keys, database credentials, session secrets,
        # and arbitrary PYTHON*/NODE* injection variables from the server
        # process must never be inherited by a child process. The retained
        # entries are the minimum needed for executable lookup, home-relative
        # tools, locale handling, temporary files, and Windows process startup.
        allowed_env_keys = {
            "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
            "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "PATHEXT",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed_env_keys
        }
        env.update({
            "LATTICE_HOOK_KIND": context.kind,
            "LATTICE_HOOK_EVENT": context.event,
            "LATTICE_HOOK_ID": str(hook.get("id", "")),
            "LATTICE_HOOK_CONTEXT": ctx_json,
        })
        is_gate = context.kind.startswith("pre_")
        try:
            proc = subprocess.run(
                argv, input=ctx_json, capture_output=True, text=True,
                timeout=self._command_timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return ("blocked" if is_gate else "error",
                    f"timed out after {self._command_timeout:.0f}s", "", is_gate)
        except Exception as exc:
            return "error", str(exc)[:500], "", False
        output = (proc.stdout or "")[:4000]
        if proc.returncode == 0:
            return "ok", "", output, False
        detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()[:500]
        return ("blocked" if is_gate else "error", detail, output, is_gate)

    # ── run log ────────────────────────────────────────────────────────────
    def _load_runs(self) -> List[Dict[str, Any]]:
        try:
            if self._runs_path.exists():
                with open(self._runs_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    return data
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.warning("hook run history is unreadable at %s: %s", self._runs_path, exc)
        return []

    def _save_runs(self) -> None:
        try:
            self._runs_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._runs_path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(list(self._runs), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._runs_path)
        except OSError as exc:  # pragma: no cover - the run log is best-effort
            LOGGER.warning("hook run history write failed at %s: %s", self._runs_path, exc)

    def _record_run(self, result: HookResult, context: HookContext) -> None:
        entry = result.as_dict()
        entry["target_event"] = context.event
        entry["target_kind"] = context.kind
        with self._run_lock:
            self._runs.appendleft(entry)
            self._save_runs()

    def recent_runs(self, limit: int = 50, kind: Optional[str] = None) -> Dict[str, Any]:
        with self._run_lock:
            runs = list(self._runs)
        if kind:
            runs = [r for r in runs if r.get("target_kind") == kind or r.get("kind") == kind]
        return {
            "runs": runs[: max(0, int(limit))],
            "total": len(runs),
            "generated_at": _now(),
        }
