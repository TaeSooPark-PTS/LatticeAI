"""AgentRuntime — the single boundary for agent execution and observability.

Before this module the agent concern was spread across three places: the
:class:`~latticeai.core.multi_agent.MultiAgentOrchestrator` (role pipeline),
the :class:`~latticeai.services.platform_runtime.PlatformRuntime` (cross-system
wiring + an ad-hoc ``run_agent``), and ``api/agents.py`` (HTTP transport that
also owned orchestration + persistence + audit). The frontend reached past all
of them into the workspace store via ``/workspace/agents``.

``AgentRuntime`` collapses that into one façade with a small, stable surface:

* **configuration** — :meth:`config`, :meth:`roles`
* **status / health** — :meth:`status`, :meth:`health`
* **execution**       — :meth:`start`, :meth:`stop`
* **events / state**  — :meth:`list_runs`, :meth:`get_run`, :meth:`events`, :meth:`replay`

It *wraps* the existing orchestrator and run store rather than reimplementing
them — execution semantics are unchanged, but every caller (HTTP router and, via
it, the frontend) now depends on this boundary instead of internal paths.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from latticeai.core.multi_agent import (
    AGENT_ROLES,
    CORE_PIPELINE,
    MULTI_AGENT_VERSION,
    ROLE_AGENT_IDS,
)

ROLE_DESCRIPTIONS = {
    "researcher": "Gathers workspace context and memory for the goal.",
    "planner": "Decomposes the goal into an ordered, bounded plan.",
    "executor": "Executes each planned step, invoking tools and workflows.",
    "reviewer": "Reviews the executed work and approves, rejects, or retries.",
    "release": "Finalizes and summarizes the approved outcome.",
}

# Run statuses the orchestrator can emit that mean "still working". The default
# orchestrator runs synchronously, so persisted runs are always terminal; this
# set lets the runtime report live work if a future async runner lands.
_ACTIVE_STATUSES = {"running", "in_progress", "queued", "retrying"}
_TERMINAL_STATUSES = {"ok", "retried_ok", "failed", "rejected", "cancelled"}


class AgentRuntime:
    def __init__(
        self,
        *,
        store: Any,
        orchestrator_factory: Callable[[Optional[str], Optional[str]], Any],
        workspace_graph: Callable[[], Any],
        append_audit_event: Callable[..., None],
        max_retries_cap: int = 5,
    ):
        self._store = store
        self._orchestrator_factory = orchestrator_factory
        self._workspace_graph = workspace_graph
        self._append_audit_event = append_audit_event
        self._max_retries_cap = int(max_retries_cap)

    # ── configuration ─────────────────────────────────────────────────────
    def config(self) -> Dict[str, Any]:
        return {
            "version": MULTI_AGENT_VERSION,
            "roles": list(AGENT_ROLES),
            "default_pipeline": list(CORE_PIPELINE),
            "max_retries_cap": self._max_retries_cap,
            "execution_mode": "synchronous",
        }

    def roles(self) -> List[Dict[str, Any]]:
        return [
            {
                "role": role,
                "agent_id": ROLE_AGENT_IDS.get(role, f"agent:{role}"),
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "terminal": role not in {"researcher", "planner", "executor", "reviewer"},
            }
            for role in AGENT_ROLES
        ]

    # ── health ────────────────────────────────────────────────────────────
    def health(self) -> Dict[str, Any]:
        checks: Dict[str, Any] = {}
        ok = True
        try:
            self._store.list_agents(workspace_id=None)
            checks["run_store"] = {"status": "ok"}
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            checks["run_store"] = {"status": "error", "detail": str(exc)}
        try:
            self._orchestrator_factory(None, None)
            checks["orchestrator"] = {"status": "ok"}
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            checks["orchestrator"] = {"status": "error", "detail": str(exc)}
        return {"status": "ok" if ok else "degraded", "checks": checks}

    # ── roster + status ───────────────────────────────────────────────────
    def _roster(self, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Canonical role roster enriched with real run statistics."""
        by_agent: Dict[str, Dict[str, Any]] = {}
        for run in runs:
            aid = str(run.get("agent_id") or "")
            entry = by_agent.setdefault(aid, {"runs": 0, "last_status": None, "last_at": None})
            entry["runs"] += 1
            if entry["last_at"] is None:  # runs are newest-first
                entry["last_status"] = run.get("status")
                entry["last_at"] = run.get("created_at") or run.get("completed_at")

        roster: List[Dict[str, Any]] = []
        order = list(CORE_PIPELINE)  # planner, executor, reviewer first
        ordered_roles = order + [r for r in AGENT_ROLES if r not in order]
        for role in ordered_roles:
            agent_id = ROLE_AGENT_IDS.get(role, f"agent:{role}")
            stats = by_agent.get(agent_id, {"runs": 0, "last_status": None, "last_at": None})
            handoffs = []
            if role == "planner":
                handoffs = [ROLE_AGENT_IDS["executor"]]
            elif role == "executor":
                handoffs = [ROLE_AGENT_IDS["reviewer"]]
            roster.append({
                "id": agent_id,
                "name": role.capitalize(),
                "role": ROLE_DESCRIPTIONS.get(role, ""),
                "state": "available" if role != "release" else "idle",
                "runs": stats["runs"],
                "last_status": stats["last_status"],
                "last_at": stats["last_at"],
                "handoffs": handoffs,
            })
        return roster

    def status(self, *, scope: Optional[str] = None) -> Dict[str, Any]:
        try:
            listing = self._store.list_agents(workspace_id=scope)
        except Exception as exc:  # pragma: no cover - defensive
            listing = {"agents": [], "runs": [], "error": str(exc)}
        runs = list(listing.get("runs") or [])
        active = sum(1 for r in runs if str(r.get("status")) in _ACTIVE_STATUSES)
        return {
            "runtime": {
                "ready": True,
                "version": MULTI_AGENT_VERSION,
                "execution_mode": "synchronous",
                "default_pipeline": list(CORE_PIPELINE),
                "total_runs": len(runs),
                "active_runs": active,
            },
            "health": self.health(),
            "roles": self.roles(),
            "agents": self._roster(runs),
            "runs": runs[:25],
        }

    # ── events / state ────────────────────────────────────────────────────
    def list_runs(self, *, scope: Optional[str] = None) -> Dict[str, Any]:
        return self._store.list_agents(workspace_id=scope)

    def get_run(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        return {"run": self._store.get_agent_run(run_id, workspace_id=scope)}

    def replay(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        return {"replay": self._store.replay_agent_run(run_id, workspace_id=scope)}

    def events(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        run = self._store.get_agent_run(run_id, workspace_id=scope)
        status = str(run.get("status") or "")
        return {
            "run_id": run_id,
            "status": status,
            "is_final": status in _TERMINAL_STATUSES or status not in _ACTIVE_STATUSES,
            "current_role": run.get("current_role"),
            "timeline": run.get("timeline") or [],
            "handoffs": run.get("handoffs") or [],
        }

    # ── execution ─────────────────────────────────────────────────────────
    def start(
        self,
        goal: str,
        *,
        user_email: Optional[str],
        scope: Optional[str],
        roles: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        if not str(goal or "").strip():
            raise ValueError("goal is required")
        orchestrator = self._orchestrator_factory(user_email or None, scope)
        result = orchestrator.run(
            goal,
            user_email=user_email or None,
            workspace_id=scope,
            inputs=inputs or {},
            roles=roles or None,
            max_retries=max(0, min(int(max_retries or 0), self._max_retries_cap)),
        )
        run = self._store.record_agent_run(
            agent_id=result.agent_id,
            status=result.status,
            input_text=goal,
            output_text=result.output,
            timeline=result.timeline,
            relationships=[ROLE_AGENT_IDS.get(r, f"agent:{r}") for r in result.roles_run],
            handoffs=result.handoffs,
            context_packets=result.context_packets,
            plan=result.plan,
            plan_review=result.plan_review,
            review_history=result.review_history,
            retry_history=result.retry_history,
            memory_snapshots=result.memory_snapshots,
            user_email=user_email or None,
            graph=self._workspace_graph(),
            workspace_id=scope,
        )
        self._append_audit_event(
            "multi_agent_run",
            user_email=user_email,
            agent_id=result.agent_id,
            status=result.status,
            retries=result.retries,
        )
        return {"run": run, "result": result.as_dict()}

    def stop(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        """Best-effort stop.

        The default runtime executes synchronously, so by the time a run id
        exists the run has already completed. Report that honestly rather than
        pretending a cancellation occurred.
        """
        try:
            run = self._store.get_agent_run(run_id, workspace_id=scope)
        except FileNotFoundError:
            return {"stopped": False, "reason": "run not found", "run_id": run_id}
        status = str(run.get("status") or "")
        if status in _ACTIVE_STATUSES:
            return {"stopped": False, "reason": "asynchronous cancellation is not supported by the synchronous runtime", "run_id": run_id, "status": status}
        return {"stopped": False, "reason": "run already finished", "run_id": run_id, "status": status}
