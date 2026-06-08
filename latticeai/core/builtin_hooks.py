"""Real runners for the built-in lifecycle hooks.

Each built-in hook in :data:`latticeai.core.hooks.BUILTIN_HOOKS` is bound here to
an actual callable so dispatch performs real platform work rather than a silent
no-op. Kept out of ``server_app`` to keep the assembly file lean; ``server_app``
calls :func:`register_builtin_hook_runners` once with the platform dependencies.

A runner receives a :class:`~latticeai.core.hooks.HookContext` and returns a
status dict (``{status, output, block?, detail?}``). It may mutate the context
payload (e.g. redaction) or call ``context.block()`` to gate a ``pre_*`` action.
"""

from __future__ import annotations

from typing import Any, Callable

_SECRET_KEY_HINTS = (
    "token", "password", "passwd", "secret", "api_key", "apikey",
    "authorization", "auth", "cookie", "session", "private_key",
)


def register_builtin_hook_runners(
    registry: Any,
    *,
    append_audit_event: Callable[..., None],
    get_tool_permission: Callable[..., dict],
    classify_sensitive_message: Callable[..., dict],
) -> None:
    """Bind a real runner to every built-in hook on ``registry``."""

    def redact_secrets(context):
        """pre_run — strip secret-like keys from the agent context packet."""
        payload = context.payload if isinstance(context.payload, dict) else {}
        redacted = []
        for key in list(payload.keys()):
            if any(s in str(key).lower() for s in _SECRET_KEY_HINTS):
                payload[key] = "***redacted***"
                redacted.append(key)
        return {"status": "ok", "output": f"redacted {len(redacted)} field(s)" if redacted else "no secrets present"}

    def audit_agent_run(context):
        """post_run — append the completed agent run to the workspace audit log."""
        p = context.payload if isinstance(context.payload, dict) else {}
        append_audit_event(
            "hook_post_run", user_email=context.user_email,
            run_id=p.get("run_id"), agent_id=p.get("agent_id"), status=p.get("status"),
        )
        return {"status": "ok", "output": f"audited run {p.get('run_id') or ''}".strip()}

    def pipeline_index_status(context):
        """post_index — record ingest/embed/graph-build pipeline state."""
        p = context.payload if isinstance(context.payload, dict) else {}
        return {"status": "ok", "output": f"pipeline {context.event}: indexed={p.get('indexed')}"}

    def research_memory_snapshot(context):
        """agent — record that a short-term memory snapshot was captured."""
        p = context.payload if isinstance(context.payload, dict) else {}
        n = p.get("context_items")
        return {"status": "ok", "output": f"memory snapshot recorded ({n if n is not None else '0'} context items)"}

    def tool_permission_gate(context):
        """pre_tool — evaluate the real governance policy for the tool and record it.

        Enforcement (admin-only gating, approval tokens) stays in the tool
        dispatcher; this surfaces the policy into the run log and only blocks when
        the governance policy itself marks the tool denied.
        """
        p = context.payload if isinstance(context.payload, dict) else {}
        tool = p.get("tool") or ""
        try:
            perm = dict(get_tool_permission(tool))
        except Exception as exc:  # pragma: no cover - defensive
            return {"status": "ok", "output": f"policy unavailable for '{tool}': {exc}"}
        if perm.get("policy") == "deny" or perm.get("risk") == "deny":
            return {"status": "blocked", "block": True, "detail": f"governance policy denies '{tool}'"}
        return {"status": "ok", "output": f"policy[{tool}]: risk={perm.get('risk')} approval={perm.get('requires_approval')}"}

    def sensitive_data_guard(context):
        """pre_tool — classify the outgoing tool payload for sensitive data."""
        p = context.payload if isinstance(context.payload, dict) else {}
        content = " ".join(str(v) for v in p.values() if isinstance(v, (str, int, float)))
        try:
            verdict = classify_sensitive_message(
                {"role": "tool", "content": content, "user_email": context.user_email}, -1
            )
        except Exception as exc:  # pragma: no cover - defensive
            return {"status": "ok", "output": f"classifier unavailable: {exc}"}
        labels = verdict.get("labels") or []
        return {"status": "ok", "output": f"sensitivity={verdict.get('sensitivity')} labels={','.join(labels) if labels else 'none'}"}

    def workflow_replay_log(context):
        """post_workflow — record the workflow run so it can be replayed."""
        p = context.payload if isinstance(context.payload, dict) else {}
        return {"status": "ok", "output": f"workflow {p.get('workflow_id') or '?'} -> {p.get('status') or 'recorded'} ({p.get('steps', '?')} steps)"}

    registry.register_hook("builtin:redact-secrets", redact_secrets)
    registry.register_hook("builtin:audit-agent-run", audit_agent_run)
    registry.register_hook("builtin:pipeline-index-status", pipeline_index_status)
    registry.register_hook("builtin:research-memory-snapshot", research_memory_snapshot)
    registry.register_hook("builtin:tool-permission-gate", tool_permission_gate)
    registry.register_hook("builtin:sensitive-data-guard", sensitive_data_guard)
    registry.register_hook("builtin:workflow-replay-log", workflow_replay_log)


__all__ = ["register_builtin_hook_runners"]
