"""Wire PermissionMode into SingleAgentRuntime without rewriting agent.py.

``apply_permission_mode_to_runtime(runtime)`` replaces approval + gate
methods with mode-aware versions. Idempotent. Called from
``build_agent_runtime`` so every production/test runtime that goes through
the dispatch service gets the behaviour.
"""

from __future__ import annotations

from typing import Any, Optional

from latticeai.core.agent_permission import (
    approval_requirements_for,
    block_reason_for_tool,
    filter_governor_verdict,
    resolve_deps_mode,
)
from latticeai.core.permission_mode import normalize_mode


def apply_permission_mode_to_runtime(runtime: Any) -> Any:
    """Monkey-patch gate methods on an existing SingleAgentRuntime instance."""
    if getattr(runtime, "_permission_mode_patched", False):
        return runtime

    original_approval = runtime.approval_requirements
    original_blocked = runtime._blocked_by_gates
    original_governor = runtime._governor_review

    def approval_requirements(ctx: Any) -> dict:
        d = runtime.deps
        mode = resolve_deps_mode(d, ctx)
        governed = (
            getattr(d.change_governor, "governed_tools", frozenset())
            if getattr(d, "change_governor", None) is not None
            else frozenset()
        )
        result = approval_requirements_for(
            mode,
            ctx.plan or {},
            d.tool_governance or {},
            governed_tools=governed,
        )
        # Preserve any extra keys original might have added in future.
        try:
            legacy = original_approval(ctx)
            for key, value in legacy.items():
                result.setdefault(key, value)
        except Exception:
            pass
        return result

    def _blocked_by_gates(
        ctx: Any, req: Any, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, governor_allows_additive: bool,
    ) -> bool:
        mode = resolve_deps_mode(runtime.deps, ctx)
        reason = block_reason_for_tool(
            mode, name, policy, args,
            approved_by_human=bool(getattr(ctx, "approved_by_human", False)),
            governor_allows_additive=governor_allows_additive,
        )
        if reason is None:
            return False
        # Reuse original path for transcript/audit when blocked for destructive.
        if "destructive" in reason:
            return original_blocked(
                ctx, req, name, thoughts, args, policy, risk,
                current_user, governor_allows_additive,
            )
        d = runtime.deps
        d.audit(
            "agent_exec", user_email=current_user,
            source=getattr(req, "source", None) or "agent",
            state="EXECUTING", action=name, risk=risk,
            shell=policy.get("shell"), network=policy.get("network"),
            destructive=policy.get("destructive"), sandbox=policy.get("sandbox"),
            rollback=policy.get("rollback"),
            permission_mode=normalize_mode(mode).value,
            args={k: v for k, v in args.items() if k != "content"},
        )
        ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
        runtime._emit_step(ctx, "execute", "blocked", action=name, reason="approval")
        ctx.transcript.append({
            "state": "EXECUTING", "action": name,
            "thoughts": thoughts, "args": args, "risk": risk,
            "governance": dict(policy),
            "permission_mode": normalize_mode(mode).value,
            "error": reason,
        })
        return True

    def _governor_review(
        ctx: Any, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ):
        proposed, allows = original_governor(
            ctx, name, thoughts, args, policy, risk, current_user,
            request_workspace, conversation_id=conversation_id,
        )
        if not proposed:
            return proposed, allows
        mode = resolve_deps_mode(runtime.deps, ctx)
        # Last transcript entry is the proposal result — rewrite when mode says so.
        if not ctx.transcript:
            return proposed, allows
        last = ctx.transcript[-1]
        if not isinstance(last.get("result"), dict) or not last["result"].get("proposed"):
            return proposed, allows
        filtered = filter_governor_verdict(mode, {"decision": "proposed", "classification": {}})
        if filtered and filtered.get("decision") == "allow_additive":
            # Drop the proposal transcript entry and allow execution.
            ctx.transcript.pop()
            d = runtime.deps
            d.audit(
                "agent_change_auto_applied",
                user_email=current_user,
                action=name,
                permission_mode=normalize_mode(mode).value,
                note="mode skipped proposal staging",
            )
            return False, True
        return proposed, allows

    runtime.approval_requirements = approval_requirements  # type: ignore[method-assign]
    runtime._blocked_by_gates = _blocked_by_gates  # type: ignore[method-assign]
    runtime._governor_review = _governor_review  # type: ignore[method-assign]
    runtime._permission_mode_patched = True
    return runtime


__all__ = ["apply_permission_mode_to_runtime"]
