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
    should_stage_proposal,
)


def resolve_deps_mode(deps: Any, ctx: Any = None) -> PermissionMode:
    """Read mode from deps (value or zero-arg callable) or context override."""
    if ctx is not None:
        override = getattr(ctx, "permission_mode", None)
        if override is not None:
            return normalize_mode(override)
    raw = getattr(deps, "permission_mode", None)
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            raw = DEFAULT_MODE
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
        if mode == PermissionMode.STRICT and name in governed:
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


def filter_governor_verdict(
    mode: PermissionMode | str,
    verdict: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Drop proposal staging under trusted/bypass so mutations auto-apply."""
    if verdict is None:
        return None
    if verdict.get("decision") != "proposed":
        return verdict
    if should_stage_proposal(mode, proposal_required=True):
        return verdict
    # Reinterpret as allow so the classic execute path applies the change.
    return {
        "decision": "allow_additive",
        "classification": verdict.get("classification") or {},
        "mode_override": normalize_mode(mode).value,
        "note": "permission mode auto-applies mutation with audit",
    }


__all__ = [
    "resolve_deps_mode",
    "non_auto_plan_steps",
    "approval_requirements_for",
    "block_reason_for_tool",
    "filter_governor_verdict",
]
