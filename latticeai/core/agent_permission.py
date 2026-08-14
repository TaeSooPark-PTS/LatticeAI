"""Agent-loop permission-mode gates (v9.9.8) — the two the parity fixtures pin.

These were the Python loop's mode tables, called by ``SingleAgentRuntime`` and
``build_agent_runtime``. Orchestration moved to ``lattice-agent`` in v11.6.0 and
the Python loop is gone, so **nothing in the worker imports this module**.

It survives for one reason, stated so it is not read as a leftover:
``scripts/generate_agent_parity_fixtures.py`` produces the committed goldens
``rust/fixtures`` holds for the Rust loop's permission gates, and it derives
them from :func:`block_reason_for_tool` and :func:`non_auto_plan_steps` here.
Deleting these two would leave the Rust side asserting against fixtures no
Python could regenerate — the fixtures would still pass, and would stop meaning
anything. The rest of the module (``call_mode_source``, ``resolve_deps_mode``,
``approval_requirements_for``) went with the loop that called it.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from latticeai.core.permission_mode import (
    PermissionMode,
    effective_auto_approve,
    is_circuit_breaker,
    normalize_mode,
)


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
    "block_reason_for_tool",
    "non_auto_plan_steps",
]
