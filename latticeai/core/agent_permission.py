"""Agent-loop permission-mode gates (v9.9.8).

Kept separate from ``agent.py`` so the large state-machine module stays
stable; ``SingleAgentRuntime`` and ``build_agent_runtime`` call these
helpers instead of inlining mode tables.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from latticeai.core.permission_mode import (
    DEFAULT_MODE,
    PermissionMode,
    effective_auto_approve,
    is_circuit_breaker,
    normalize_mode,
    plan_requires_approval,
)


def call_mode_source(
    raw: Any,
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Any:
    """Resolve a mode source that may be a value, or a callable that either
    accepts ``user_email``/``workspace_id`` scope kwargs or takes no arguments.

    Scoped resolution is what makes per-user and per-workspace overrides real:
    an unscoped call always collapses to the process-wide default.
    """
    if not callable(raw):
        return raw
    try:
        return raw(user_email=user_email, workspace_id=workspace_id)
    except TypeError:
        # Legacy zero-arg resolver (or a static callable) — fall back.
        try:
            return raw()
        except Exception:
            return DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def resolve_deps_mode(
    deps: Any,
    ctx: Any = None,
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> PermissionMode:
    """Mode for this run: explicit context stamp wins, else the scoped resolver
    on ``deps``, else strict."""
    if ctx is not None:
        override = getattr(ctx, "permission_mode", None)
        if override is not None:
            return normalize_mode(override)
    raw = call_mode_source(
        getattr(deps, "permission_mode", None),
        user_email=user_email,
        workspace_id=workspace_id,
    )
    return normalize_mode(raw if raw is not None else DEFAULT_MODE)


def non_auto_plan_steps(
    mode: PermissionMode | str,
    steps: Sequence[Mapping[str, Any]],
    tool_governance: Mapping[str, Mapping[str, Any]],
    *,
    governed_tools: Optional[Any] = None,
) -> List[Any]:
    """Plan steps that still need approval under the active mode."""
    mode = normalize_mode(mode)
    governed = frozenset(governed_tools or ())
    non_auto: List[Any] = []
    for step in steps:
        name = step.get("action")
        if not name:
            continue
        if name in governed and mode == PermissionMode.STRICT:
            # Strict keeps governor tools out of the plan-level block (per-call).
            continue
        policy = dict(tool_governance.get(name) or {
            "auto_approve": False, "risk": "write", "destructive": False,
            "shell": False, "network": False, "sandbox": "workspace", "rollback": "none",
        })
        if effective_auto_approve(mode, str(name), policy):
            continue
        non_auto.append(name)
    return non_auto


def approval_requirements_for(
    mode: PermissionMode | str,
    plan: Mapping[str, Any],
    tool_governance: Mapping[str, Mapping[str, Any]],
    *,
    governed_tools: Optional[Any] = None,
) -> Dict[str, Any]:
    steps = plan.get("steps") or []
    non_auto = non_auto_plan_steps(
        mode, steps, tool_governance, governed_tools=governed_tools,
    )
    requires = plan_requires_approval(
        mode,
        non_auto_steps=non_auto,
        plan_flag=bool(plan.get("requires_approval", False)),
    )
    lines = [
        f"{index}. {step.get('description') or step.get('action') or '?'}"
        for index, step in enumerate(steps, start=1)
        if isinstance(step, dict)
    ]
    summary = str(plan.get("goal") or "").strip()
    if lines:
        summary = (summary + "\n" if summary else "") + "\n".join(lines)
    return {
        "requires_approval": requires,
        "non_auto_steps": non_auto,
        "plan_summary": summary,
        "permission_mode": normalize_mode(mode).value,
    }


def block_reason_for_tool(
    mode: PermissionMode | str,
    name: str,
    policy: Mapping[str, Any],
    args: Mapping[str, Any],
    *,
    approved_by_human: bool = False,
    governor_allows_additive: bool = False,
) -> Optional[str]:
    """Return a block reason, or None when the call may proceed."""
    mode = normalize_mode(mode)
    breaker = is_circuit_breaker(name, policy, args)
    if breaker:
        return f"BLOCKED: {breaker}"
    if policy.get("risk") == "destructive" or policy.get("destructive"):
        return f"BLOCKED: destructive action '{name}' not permitted in agent mode."
    if approved_by_human or governor_allows_additive:
        return None
    if effective_auto_approve(mode, name, policy, args=args):
        return None
    if policy.get("auto_approve"):
        return None
    return f"BLOCKED: action '{name}' requires explicit approval (mode={mode.value})."


__all__ = [
    "call_mode_source",
    "resolve_deps_mode",
    "non_auto_plan_steps",
    "approval_requirements_for",
    "block_reason_for_tool",
]
