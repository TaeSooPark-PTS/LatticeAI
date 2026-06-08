"""Multi-Agent Runtime 2.1.

The runtime remains a small, dependency-injected orchestrator, but v2.1 makes
the operational objects first-class: handoffs, context packets, review/retry
history, replayable timeline events, and explicit planning records. The default
runner is still deterministic and LLM-free so tests, local demos, and Community
installations can exercise the full Planner -> Executor -> Reviewer loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


MULTI_AGENT_VERSION = "3.4.1"

AGENT_ROLES = ("researcher", "planner", "executor", "reviewer", "release")
CORE_PIPELINE = ("planner", "executor", "reviewer")

ROLE_AGENT_IDS = {
    "researcher": "agent:researcher",
    "planner": "agent:planner",
    "executor": "agent:executor",
    "reviewer": "agent:reviewer",
    "release": "agent:release",
}

HANDOFF_STATUSES = (
    "created",
    "accepted",
    "running",
    "blocked",
    "completed",
    "rejected",
    "retry_requested",
    "cancelled",
)

REVIEW_OUTCOMES = ("approve", "reject", "retry")

_SECRET_KEYS = ("secret", "token", "password", "api_key", "apikey", "credential")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _redact(value: Any) -> Any:
    """Return a JSON-safe value with obvious secret fields redacted."""
    if isinstance(value, dict):
        clean: Dict[str, Any] = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SECRET_KEYS):
                clean[key] = "[redacted]"
            else:
                clean[key] = _redact(item)
        return clean
    if isinstance(value, list):
        return [_redact(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_redact(item) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _review_outcome(review: Dict[str, Any]) -> str:
    raw = str(review.get("outcome") or review.get("verdict") or "").lower().strip()
    if raw in {"approve", "approved", "pass", "passed", "ok"}:
        return "approve"
    if raw in {"reject", "rejected", "fail", "failed"}:
        return "reject"
    if raw == "retry":
        return "retry"
    return "approve"


@dataclass
class AgentContextPacket:
    """Structured, replay-safe context transferred between agent roles."""

    packet_id: str
    objective: str
    task_summary: str
    workspace_context: Dict[str, Any] = field(default_factory=dict)
    graph_context: Dict[str, Any] = field(default_factory=dict)
    memory_context: List[Any] = field(default_factory=list)
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    plugin_outputs: List[Any] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    reviewer_notes: List[str] = field(default_factory=list)
    retry_metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def as_dict(self) -> Dict[str, Any]:
        return _redact({
            "packet_id": self.packet_id,
            "objective": self.objective,
            "task_summary": self.task_summary,
            "workspace_context": self.workspace_context,
            "graph_context": self.graph_context,
            "memory_context": self.memory_context,
            "workflow_context": self.workflow_context,
            "plugin_outputs": self.plugin_outputs,
            "constraints": self.constraints,
            "reviewer_notes": self.reviewer_notes,
            "retry_metadata": self.retry_metadata,
            "created_at": self.created_at,
        })


@dataclass
class AgentHandoff:
    """Inspectable handoff between two agent roles."""

    handoff_id: str
    source_agent: str
    target_agent: str
    reason: str
    task_summary: str
    context_packet: Dict[str, Any]
    status: str = "created"
    created_at: str = field(default_factory=_now)
    accepted_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "reason": self.reason,
            "task_summary": self.task_summary,
            "context_packet": self.context_packet,
            "status": self.status,
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class OrchestrationContext:
    """Mutable carrier threaded through every role stage."""

    goal: str
    user_email: Optional[str] = None
    workspace_id: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    plan_id: str = ""
    plan_review: Dict[str, Any] = field(default_factory=dict)
    research: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)
    plugin_outputs: List[Any] = field(default_factory=list)
    workflow_outputs: List[Any] = field(default_factory=list)
    review: Dict[str, Any] = field(default_factory=dict)
    review_history: List[Dict[str, Any]] = field(default_factory=list)
    retry_history: List[Dict[str, Any]] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    handoffs: List[Dict[str, Any]] = field(default_factory=list)
    context_packets: List[Dict[str, Any]] = field(default_factory=list)
    memory_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    output: str = ""

    def build_context_packet(
        self,
        *,
        target_agent: Optional[str] = None,
        reviewer_notes: Optional[List[str]] = None,
        retry_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        packet = AgentContextPacket(
            packet_id=f"context-packet-{len(self.context_packets) + 1}",
            objective=self.goal,
            task_summary=(self.output or self.goal or "Agent task")[:500],
            workspace_context={
                "workspace_id": self.workspace_id,
                "user_email": self.user_email,
                "target_agent": target_agent,
            },
            graph_context=_redact(self.inputs.get("graph_context") or {}),
            memory_context=list(self.research[:20]),
            workflow_context={
                "requested_workflow": self.inputs.get("workflow"),
                "workflow_outputs": self.workflow_outputs[-10:],
            },
            plugin_outputs=self.plugin_outputs[-10:],
            constraints=list(self.inputs.get("constraints") or []),
            reviewer_notes=reviewer_notes or [],
            retry_metadata=retry_metadata or {"retry_count": self.retries},
        ).as_dict()
        self.context_packets.append(packet)
        return packet

    def handoff(self, frm: str, to: str, note: str = "", *, status: str = "completed") -> Dict[str, Any]:
        if status not in HANDOFF_STATUSES:
            status = "completed"
        handoff_id = f"handoff-{len(self.handoffs) + 1}"
        packet = self.build_context_packet(target_agent=to)
        now = _now()
        record = AgentHandoff(
            handoff_id=handoff_id,
            source_agent=ROLE_AGENT_IDS.get(frm, f"agent:{frm}"),
            target_agent=ROLE_AGENT_IDS.get(to, f"agent:{to}"),
            reason=note or f"{frm} completed work for {to}",
            task_summary=(self.output or self.goal or "Agent handoff")[:500],
            context_packet=packet,
            status=status,
            created_at=now,
            accepted_at=now if status in {"accepted", "running", "completed", "retry_requested"} else None,
            started_at=now if status in {"running", "completed", "retry_requested"} else None,
            completed_at=now if status in {"completed", "retry_requested"} else None,
        ).as_dict()
        self.handoffs.append(record)

        self.timeline.append({
            "event": "handoff_created",
            "handoff_id": handoff_id,
            "from": frm,
            "to": to,
            "source_agent": record["source_agent"],
            "target_agent": record["target_agent"],
            "reason": record["reason"],
            "context_packet": packet,
            "status": "created",
            "timestamp": now,
        })
        if record["accepted_at"]:
            self.timeline.append({
                "event": "handoff_accepted",
                "handoff_id": handoff_id,
                "from": frm,
                "to": to,
                "status": "accepted",
                "timestamp": record["accepted_at"],
            })
        if status in {"completed", "retry_requested"}:
            self.timeline.append({
                "event": "handoff_completed",
                "handoff_id": handoff_id,
                "from": frm,
                "to": to,
                "status": status,
                "timestamp": record["completed_at"],
            })

        # Backward-compatible compact event used by v2.0 UI/tests.
        self.timeline.append({
            "event": "handoff",
            "handoff_id": handoff_id,
            "from": frm,
            "to": to,
            "note": note,
            "status": status,
            "timestamp": now,
        })
        return record


@dataclass
class AgentRunResult:
    agent_id: str
    status: str  # ok | failed | retried_ok
    output: str
    timeline: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]
    review: Dict[str, Any]
    roles_run: List[str]
    retries: int = 0
    handoffs: List[Dict[str, Any]] = field(default_factory=list)
    context_packets: List[Dict[str, Any]] = field(default_factory=list)
    review_history: List[Dict[str, Any]] = field(default_factory=list)
    retry_history: List[Dict[str, Any]] = field(default_factory=list)
    plan_review: Dict[str, Any] = field(default_factory=dict)
    memory_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "output": self.output,
            "timeline": self.timeline,
            "plan": self.plan,
            "review": self.review,
            "roles_run": self.roles_run,
            "retries": self.retries,
            "handoffs": self.handoffs,
            "context_packets": self.context_packets,
            "review_history": self.review_history,
            "retry_history": self.retry_history,
            "plan_review": self.plan_review,
            "memory_snapshots": self.memory_snapshots,
        }


def default_role_runner(
    *,
    workflow_runner: Optional[Callable[..., Any]] = None,
    plugin_runner: Optional[Callable[..., Any]] = None,
    context_provider: Optional[Callable[[str], List[str]]] = None,
) -> Callable[[str, OrchestrationContext], Dict[str, Any]]:
    """Build a deterministic, dependency-free role runner."""

    def runner(role: str, ctx: OrchestrationContext) -> Dict[str, Any]:
        if role == "researcher":
            found = context_provider(ctx.goal) if context_provider else []
            ctx.research = list(found)
            snapshot = {
                "snapshot_id": f"memory-snapshot-{len(ctx.memory_snapshots) + 1}",
                "scope": "short_term",
                "items": ctx.research[:10],
                "created_at": _now(),
            }
            ctx.memory_snapshots.append(snapshot)
            return {"role": role, "context_items": len(ctx.research), "items": ctx.research[:10], "memory_snapshot": snapshot}

        if role == "planner":
            goal = ctx.goal.strip() or "Complete the requested task"
            requested = ctx.inputs.get("steps")
            steps: List[Dict[str, Any]]
            if isinstance(requested, list) and requested:
                steps = []
                for i, step in enumerate(requested):
                    if isinstance(step, dict):
                        item = dict(step)
                        item.setdefault("index", i)
                        item.setdefault("description", str(step.get("description") or step.get("name") or f"Step {i + 1}"))
                        item.setdefault("status", "planned")
                    else:
                        item = {"index": i, "description": str(step), "status": "planned"}
                    steps.append(item)
            else:
                steps = [
                    {"index": 0, "description": f"Analyze: {goal}", "status": "planned"},
                    {"index": 1, "description": f"Execute: {goal}", "status": "planned"},
                    {"index": 2, "description": "Verify the result", "status": "planned"},
                ]
            if ctx.inputs.get("workflow") and steps:
                steps[0]["workflow"] = ctx.inputs.get("workflow")
            if ctx.inputs.get("plugin") and steps:
                steps[0]["plugin"] = ctx.inputs.get("plugin")
            ctx.plan = steps
            ctx.plan_id = f"plan-{abs(hash((ctx.goal, len(steps)))) % 10_000_000}"
            ctx.plan_review = {
                "plan_id": ctx.plan_id,
                "outcome": "approve",
                "reason": "deterministic plan is bounded and executable",
                "reviewed_at": _now(),
            }
            return {"role": role, "plan_id": ctx.plan_id, "steps": len(steps), "plan": steps, "plan_review": ctx.plan_review}

        if role == "executor":
            results = []
            for step in ctx.plan:
                outcome: Dict[str, Any] = {"index": step["index"], "description": step["description"]}
                wf = step.get("workflow") or (ctx.inputs.get("workflow") if step["index"] == 0 else None)
                pl = step.get("plugin") or (ctx.inputs.get("plugin") if step["index"] == 0 else None)
                if wf and workflow_runner is not None:
                    try:
                        workflow_result = workflow_runner(wf, ctx)
                        outcome["workflow_result"] = workflow_result
                        ctx.workflow_outputs.append(workflow_result)
                    except Exception as exc:
                        outcome["workflow_error"] = str(exc)
                if pl and plugin_runner is not None:
                    try:
                        plugin_result = plugin_runner(pl, ctx)
                        outcome["plugin_result"] = plugin_result
                        ctx.plugin_outputs.append(plugin_result)
                    except Exception as exc:
                        outcome["plugin_error"] = str(exc)
                if outcome.get("workflow_error") or outcome.get("plugin_error"):
                    step["status"] = "failed"
                    outcome["status"] = "error"
                else:
                    step["status"] = "done"
                    outcome["status"] = "done"
                results.append(outcome)
            ctx.executed = results
            done = sum(1 for item in results if item.get("status") == "done")
            ctx.output = f"Completed {done}/{len(results)} planned step(s) for: {ctx.goal}"
            return {"role": role, "executed": len(results), "results": results, "plugin_outputs": ctx.plugin_outputs[-10:]}

        if role == "reviewer":
            ok = bool(ctx.executed) and all(r.get("status") == "done" for r in ctx.executed)
            ctx.review = {
                "outcome": "approve" if ok else "retry",
                "verdict": "pass" if ok else "retry",
                "reason": "all steps completed" if ok else "one or more steps failed or no steps executed",
                "confidence": 0.9 if ok else 0.3,
                "notes": [] if ok else ["executor should retry with preserved context"],
                "reviewed_at": _now(),
            }
            return {"role": role, **ctx.review}

        if role == "release":
            ctx.output = ctx.output or f"Released outcome for: {ctx.goal}"
            return {"role": role, "released": True, "summary": ctx.output}

        return {"role": role, "status": "noop"}

    return runner


class MultiAgentOrchestrator:
    """Drives a role pipeline with handoff, planning, review, and retry."""

    def __init__(self, role_runner: Optional[Callable[[str, OrchestrationContext], Dict[str, Any]]] = None):
        self.role_runner = role_runner or default_role_runner()

    def _run_role(self, role: str, ctx: OrchestrationContext) -> Dict[str, Any]:
        started = _now()
        if role == "reviewer":
            ctx.timeline.append({
                "event": "review_requested",
                "role": role,
                "agent_id": ROLE_AGENT_IDS.get(role, f"agent:{role}"),
                "timestamp": started,
            })
        try:
            result = self.role_runner(role, ctx) or {}
            status = result.get("status", "ok")
        except Exception as exc:
            result = {"error": str(exc)}
            status = "error"
        if role == "reviewer":
            review = dict(ctx.review or result)
            outcome = _review_outcome(review)
            event = {
                "approve": "review_approved",
                "reject": "review_rejected",
                "retry": "retry_requested",
            }[outcome]
            ctx.timeline.append({
                "event": event,
                "role": role,
                "agent_id": ROLE_AGENT_IDS.get(role, f"agent:{role}"),
                "outcome": outcome,
                "reason": review.get("reason", ""),
                "review": review,
                "timestamp": _now(),
            })
        ctx.timeline.append({
            "event": "role",
            "role": role,
            "agent_id": ROLE_AGENT_IDS.get(role, f"agent:{role}"),
            "status": status,
            "result": result,
            "started_at": started,
            "timestamp": _now(),
        })
        return result

    def run(
        self,
        goal: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        roles: Optional[List[str]] = None,
        max_retries: int = 2,
    ) -> AgentRunResult:
        ctx = OrchestrationContext(
            goal=goal or "",
            user_email=user_email,
            workspace_id=workspace_id,
            inputs=inputs or {},
        )
        pipeline = [r for r in (roles or list(CORE_PIPELINE)) if r in AGENT_ROLES]
        if not pipeline:
            pipeline = list(CORE_PIPELINE)
        max_retries = max(0, int(max_retries or 0))

        ctx.timeline.append({"event": "start", "goal": ctx.goal, "pipeline": pipeline, "timestamp": _now()})
        ctx.timeline.append({
            "event": "agent_started",
            "agent_id": ROLE_AGENT_IDS.get(pipeline[0], "agent:planner"),
            "goal": ctx.goal,
            "pipeline": pipeline,
            "workspace_id": workspace_id,
            "timestamp": _now(),
        })

        roles_run: List[str] = []
        previous: Optional[str] = None
        index = 0
        while index < len(pipeline):
            role = pipeline[index]
            if previous is not None:
                ctx.handoff(previous, role)
            self._run_role(role, ctx)
            roles_run.append(role)

            if role == "reviewer":
                review = dict(ctx.review or {})
                outcome = _review_outcome(review)
                review_entry = {
                    "index": len(ctx.review_history),
                    "outcome": outcome,
                    "verdict": review.get("verdict") or ("pass" if outcome == "approve" else outcome),
                    "reason": review.get("reason", ""),
                    "notes": review.get("notes") or review.get("reviewer_notes") or [],
                    "retry_count": ctx.retries,
                    "timestamp": _now(),
                }
                ctx.review_history.append(review_entry)
                if outcome == "retry" and ctx.retries < max_retries:
                    ctx.retries += 1
                    retry_entry = {
                        "retry": ctx.retries,
                        "limit": max_retries,
                        "reason": review_entry["reason"],
                        "reviewer_notes": review_entry["notes"],
                        "timestamp": _now(),
                    }
                    ctx.retry_history.append(retry_entry)
                    exec_index = pipeline.index("executor") if "executor" in pipeline else None
                    if exec_index is not None:
                        ctx.handoff("reviewer", "executor", note=f"retry #{ctx.retries}: {review_entry['reason']}", status="retry_requested")
                        index = exec_index
                        previous = "reviewer"
                        continue
                if outcome == "reject":
                    ctx.timeline.append({"event": "execution_failed", "reason": review_entry["reason"], "timestamp": _now()})
                    break

            previous = role
            index += 1

        final_outcome = _review_outcome(ctx.review or {})
        if final_outcome == "approve":
            status = "retried_ok" if ctx.retries else "ok"
        else:
            status = "failed"
        if status == "failed":
            ctx.timeline.append({"event": "execution_failed", "status": status, "retries": ctx.retries, "timestamp": _now()})
        ctx.timeline.append({"event": "end", "status": status, "retries": ctx.retries, "timestamp": _now()})

        return AgentRunResult(
            agent_id=ROLE_AGENT_IDS.get("executor", "agent:executor"),
            status=status,
            output=ctx.output or f"Processed goal: {ctx.goal}",
            timeline=ctx.timeline,
            plan=ctx.plan,
            review=ctx.review,
            roles_run=roles_run,
            retries=ctx.retries,
            handoffs=ctx.handoffs,
            context_packets=ctx.context_packets,
            review_history=ctx.review_history,
            retry_history=ctx.retry_history,
            plan_review=ctx.plan_review,
            memory_snapshots=ctx.memory_snapshots,
        )
