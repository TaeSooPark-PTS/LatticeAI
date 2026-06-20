"""Shared runtime contracts for agent execution surfaces.

These contracts are deliberately small and serializable.  The single-agent
state machine and the multi-agent facade can evolve independently, but callers
should see the same run identity, mode, status, role, timeline and artifact
shape across both paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def runtime_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class AgentRunContract:
    run_id: Optional[str]
    agent_id: str
    runtime: str
    mode: str
    status: str
    goal: str
    roles: List[str] = field(default_factory=list)
    current_role: Optional[str] = None
    retries: int = 0
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    schema_version: str = "agent-run-contract/v1"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial", "done"}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "mode": self.mode,
            "status": self.status,
            "goal": self.goal,
            "roles": list(self.roles),
            "current_role": self.current_role,
            "retries": self.retries,
            "timeline": list(self.timeline),
            "artifacts": list(self.artifacts),
            "blocking_reasons": list(self.blocking_reasons),
            "is_terminal": self.is_terminal,
        }


def multi_agent_contract(
    *,
    result: Any,
    goal: str,
    run_id: Optional[str] = None,
    current_role: Optional[str] = None,
    blocking_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return AgentRunContract(
        run_id=run_id,
        agent_id=getattr(result, "agent_id", "agent:executor"),
        runtime="multi_agent",
        mode=getattr(result, "mode", "simulation"),
        status=getattr(result, "status", "unknown"),
        goal=goal,
        roles=list(getattr(result, "roles_run", []) or []),
        current_role=current_role,
        retries=int(getattr(result, "retries", 0) or 0),
        timeline=list(getattr(result, "timeline", []) or []),
        artifacts=[],
        blocking_reasons=list(blocking_reasons or []),
    ).as_dict()


def single_agent_contract(
    *,
    ctx: Any,
    goal: str,
    run_id: Optional[str] = None,
    agent_id: str = "agent:single",
    mode: str = "llm",
    blocking_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    state = getattr(getattr(ctx, "state", None), "value", getattr(ctx, "state", "unknown"))
    status = "done" if state == "DONE" else "failed" if state == "FAILED" else str(state or "unknown").lower()
    return AgentRunContract(
        run_id=run_id,
        agent_id=agent_id,
        runtime="single_agent",
        mode=mode,
        status=status,
        goal=goal,
        roles=["planner", "executor", "critic"],
        current_role=str(state or ""),
        retries=int(getattr(ctx, "retry_count", 0) or 0),
        timeline=list(getattr(ctx, "transcript", []) or []),
        artifacts=[],
        blocking_reasons=list(blocking_reasons or []),
    ).as_dict()
