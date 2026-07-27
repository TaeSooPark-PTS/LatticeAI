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
    resolve_deps_mode,
)
from latticeai.core.permission_mode import should_stage_proposal


def _governed_tools(deps: Any) -> frozenset:
    governor = getattr(deps, "change_governor", None)
    if governor is None:
        return frozenset()
    return frozenset(getattr(governor, "governed_tools", frozenset()))


def apply_permission_mode_to_runtime(runtime: Any) -> Any:
    """Monkey-patch gate methods on an existing SingleAgentRuntime instance."""
    if getattr(runtime, "_permission_mode_patched", False):
        return runtime

    original_approval = runtime.approval_requirements
    original_blocked = runtime._blocked_by_gates
    original_governor = runtime._governor_review

    def resolve_permission_mode(
        ctx: Any = None,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        """Public helper so the HTTP layer can stamp a run-scoped mode once."""
        return resolve_deps_mode(
            runtime.deps, ctx, user_email=user_email, workspace_id=workspace_id,
        )

    def approval_requirements(ctx: Any) -> dict:
        d = runtime.deps
        mode = resolve_deps_mode(d, ctx)
        result = approval_requirements_for(
            mode,
            ctx.plan or {},
            d.tool_governance or {},
            governed_tools=_governed_tools(d),
        )
        # Preserve any extra keys the original might add in future.
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
        mode = resolve_deps_mode(
            runtime.deps, ctx,
            user_email=current_user,
            workspace_id=getattr(req, "workspace_id", None),
        )
        reason = block_reason_for_tool(
            mode, name, policy, args,
            approved_by_human=bool(getattr(ctx, "approved_by_human", False)),
            governor_allows_additive=governor_allows_additive,
        )
        if reason is None:
            return False
        # Reuse the original path for transcript/audit when blocked as destructive.
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
            permission_mode=mode.value,
            args={k: v for k, v in args.items() if k != "content"},
        )
        ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
        runtime._emit_step(ctx, "execute", "blocked", action=name, reason="approval")
        ctx.transcript.append({
            "state": "EXECUTING", "action": name,
            "thoughts": thoughts, "args": args, "risk": risk,
            "governance": dict(policy),
            "permission_mode": mode.value,
            "error": reason,
        })
        return True

    def _governor_review(
        ctx: Any, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ):
        mode = resolve_deps_mode(
            runtime.deps, ctx,
            user_email=current_user, workspace_id=request_workspace,
        )
        if should_stage_proposal(mode, proposal_required=True):
            return original_governor(
                ctx, name, thoughts, args, policy, risk, current_user,
                request_workspace, conversation_id=conversation_id,
            )

        # trusted / bypass: the user raised autonomy, so governed mutations
        # apply directly. The decision is made *before* delegating because
        # ``ChangeGovernor.review`` persists a proposal as a side effect —
        # calling it and then discarding the verdict would leave an orphan
        # proposal in the Review Center for a change that was already applied.
        d = runtime.deps
        if d.change_governor is None or name not in _governed_tools(d):
            return False, False
        if policy.get("destructive") or policy.get("risk") == "destructive":
            # Let the downstream destructive gate own the block + transcript.
            return False, False
        d.audit(
            "agent_change_auto_applied",
            user_email=current_user,
            workspace_id=request_workspace,
            action=name,
            path=str(args.get("path") or "") or None,
            permission_mode=mode.value,
            note="permission mode auto-applies mutation with audit",
        )
        return False, True

    runtime.approval_requirements = approval_requirements  # type: ignore[method-assign]
    runtime._blocked_by_gates = _blocked_by_gates  # type: ignore[method-assign]
    runtime._governor_review = _governor_review  # type: ignore[method-assign]
    runtime.resolve_permission_mode = resolve_permission_mode  # type: ignore[attr-defined]
    runtime._permission_mode_patched = True
    return runtime


__all__ = ["apply_permission_mode_to_runtime"]
