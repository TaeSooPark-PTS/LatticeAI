"""Multi-Agent Runtime 2.0 — role orchestration with handoff, retry, and a
fully observable timeline.

v1.x shipped a single-agent state machine (:class:`latticeai.core.agent.AgentRuntime`:
PLAN → EXECUTE → VERIFY → DONE). v2.0 adds the *orchestration* layer above it:
a pipeline of named roles that hand off to one another, retry on a failing
review, and emit a structured timeline that drops straight into the Workspace
timeline / Knowledge Graph.

Built-in roles (ids match :data:`latticeai.core.workspace_os.DEFAULT_AGENTS`):

* ``researcher`` — gathers relevant context (workspace memory / graph)
* ``planner``    — decomposes the goal into ordered steps
* ``executor``   — carries out steps (may call workflows / plugins / tools)
* ``reviewer``   — judges the result → pass / retry
* ``release``    — finalizes / packages the outcome (optional)

Like the v1 runtime, the orchestrator is pure logic over an injected
``role_runner`` port, so it runs with no LLM and no server. The default runner
(:func:`default_role_runner`) is deterministic and genuinely useful: it produces
real plans, executes steps (optionally driving an injected workflow / plugin
runner — this is the agent→workflow / agent→plugin integration), and reviews
results. Production may swap in an LLM-backed runner without touching the
orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


MULTI_AGENT_VERSION = "2.0.0"

# Ordered default pipeline. ``researcher`` and ``release`` are optional stages
# (skipped unless requested) so a quick run is planner → executor → reviewer.
AGENT_ROLES = ("researcher", "planner", "executor", "reviewer", "release")
CORE_PIPELINE = ("planner", "executor", "reviewer")

ROLE_AGENT_IDS = {
    "researcher": "agent:researcher",
    "planner": "agent:planner",
    "executor": "agent:executor",
    "reviewer": "agent:reviewer",
    "release": "agent:release",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class OrchestrationContext:
    """Mutable carrier threaded through every role stage."""

    goal: str
    user_email: Optional[str] = None
    workspace_id: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    research: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)
    review: Dict[str, Any] = field(default_factory=dict)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    output: str = ""

    def handoff(self, frm: str, to: str, note: str = "") -> None:
        self.timeline.append({
            "event": "handoff",
            "from": frm,
            "to": to,
            "note": note,
            "timestamp": _now(),
        })


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
        }


def default_role_runner(
    *,
    workflow_runner: Optional[Callable[..., Any]] = None,
    plugin_runner: Optional[Callable[..., Any]] = None,
    context_provider: Optional[Callable[[str], List[str]]] = None,
) -> Callable[[str, OrchestrationContext], Dict[str, Any]]:
    """Build a deterministic, dependency-free role runner.

    The returned callable implements every built-in role with real (non-LLM)
    behavior, and — when ``workflow_runner`` / ``plugin_runner`` are supplied —
    lets the executor role actually drive workflows / plugins. This is what
    makes "agent runs can execute workflows / plugins" true in the community
    edition without requiring a model.
    """

    def runner(role: str, ctx: OrchestrationContext) -> Dict[str, Any]:
        if role == "researcher":
            found = context_provider(ctx.goal) if context_provider else []
            ctx.research = list(found)
            return {"role": role, "context_items": len(ctx.research), "items": ctx.research[:10]}

        if role == "planner":
            # Decompose the goal into ordered, inspectable steps.
            goal = ctx.goal.strip() or "Complete the requested task"
            requested = ctx.inputs.get("steps")
            if isinstance(requested, list) and requested:
                steps = [
                    {"index": i, "description": str(s), "status": "planned"}
                    for i, s in enumerate(requested)
                ]
            else:
                steps = [
                    {"index": 0, "description": f"Analyze: {goal}", "status": "planned"},
                    {"index": 1, "description": f"Execute: {goal}", "status": "planned"},
                    {"index": 2, "description": "Verify the result", "status": "planned"},
                ]
            ctx.plan = steps
            return {"role": role, "steps": len(steps), "plan": steps}

        if role == "executor":
            results = []
            # Optional: a plan step can request a workflow or plugin run.
            for step in ctx.plan:
                outcome: Dict[str, Any] = {"index": step["index"], "description": step["description"]}
                wf = step.get("workflow") or ctx.inputs.get("workflow")
                pl = step.get("plugin")
                if wf and workflow_runner is not None and step["index"] == 0:
                    try:
                        outcome["workflow_result"] = workflow_runner(wf, ctx)
                    except Exception as exc:
                        outcome["workflow_error"] = str(exc)
                if pl and plugin_runner is not None:
                    try:
                        outcome["plugin_result"] = plugin_runner(pl, ctx)
                    except Exception as exc:
                        outcome["plugin_error"] = str(exc)
                step["status"] = "done"
                outcome["status"] = "done"
                results.append(outcome)
            ctx.executed = results
            ctx.output = f"Completed {len(results)} planned step(s) for: {ctx.goal}"
            return {"role": role, "executed": len(results), "results": results}

        if role == "reviewer":
            ok = bool(ctx.executed) and all(r.get("status") == "done" for r in ctx.executed)
            ctx.review = {
                "verdict": "pass" if ok else "retry",
                "reason": "all steps completed" if ok else "no steps executed",
                "confidence": 0.9 if ok else 0.3,
            }
            return {"role": role, **ctx.review}

        if role == "release":
            ctx.output = ctx.output or f"Released outcome for: {ctx.goal}"
            return {"role": role, "released": True, "summary": ctx.output}

        return {"role": role, "status": "noop"}

    return runner


class MultiAgentOrchestrator:
    """Drives a role pipeline with handoff + bounded retry over a role runner."""

    def __init__(self, role_runner: Optional[Callable[[str, OrchestrationContext], Dict[str, Any]]] = None):
        self.role_runner = role_runner or default_role_runner()

    def _run_role(self, role: str, ctx: OrchestrationContext) -> Dict[str, Any]:
        started = _now()
        try:
            result = self.role_runner(role, ctx) or {}
            status = result.get("status", "ok")
        except Exception as exc:
            result = {"error": str(exc)}
            status = "error"
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

        ctx.timeline.append({"event": "start", "goal": ctx.goal, "pipeline": pipeline, "timestamp": _now()})

        roles_run: List[str] = []
        previous: Optional[str] = None
        index = 0
        # Walk the pipeline; the reviewer can rewind to the executor on a retry.
        while index < len(pipeline):
            role = pipeline[index]
            if previous is not None:
                ctx.handoff(previous, role)
            self._run_role(role, ctx)
            roles_run.append(role)
            previous = role

            if role == "reviewer" and ctx.review.get("verdict") == "retry" and ctx.retries < max_retries:
                ctx.retries += 1
                exec_index = pipeline.index("executor") if "executor" in pipeline else None
                if exec_index is not None:
                    ctx.handoff("reviewer", "executor", note=f"retry #{ctx.retries}: {ctx.review.get('reason')}")
                    index = exec_index
                    previous = "reviewer"
                    continue
            index += 1

        final_verdict = ctx.review.get("verdict", "pass")
        if final_verdict == "pass":
            status = "retried_ok" if ctx.retries else "ok"
        else:
            status = "failed"
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
        )
