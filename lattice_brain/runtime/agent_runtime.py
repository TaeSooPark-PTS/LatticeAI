"""AgentRuntime — the single boundary for agent execution and observability.

(lattice_brain/runtime/agent_runtime.py)
책임: 퍼사드. store/orchestrator/hooks/audit 주입 받아 start/reserve/complete,
      status/health/config/events/replay/stop, pre/post_run hook firing.
      RunExecutor와 /agents 라우터의 유일한 의존 대상.
의존: .multi_agent (orchestrator), .hooks, store (WORKSPACE_OS).
진입점: app_factory.py:AGENT_RUNTIME (wiring root), api/agents.py, RunExecutor.

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

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .multi_agent import (
    AGENT_ROLES,
    CORE_PIPELINE,
    MULTI_AGENT_VERSION,
    ROLE_AGENT_IDS,
)
from .contracts import contract_view, contract_views, extract_contract, multi_agent_contract

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
_ACTIVE_STATUSES = {"running", "in_progress", "queued", "retrying", "cancelling"}
_TERMINAL_STATUSES = {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class AgentRuntimeUnavailable(RuntimeError):
    """Raised when a product run would otherwise persist simulation output."""


class AgentRuntime:
    def __init__(
        self,
        *,
        store: Any,
        orchestrator_factory: Callable[[Optional[str], Optional[str]], Any],
        workspace_graph: Callable[[], Any],
        append_audit_event: Callable[..., None],
        max_retries_cap: int = 5,
        hooks: Any = None,
        allow_simulation_runs: bool = False,
    ):
        self._store = store
        self._orchestrator_factory = orchestrator_factory
        self._workspace_graph = workspace_graph
        self._append_audit_event = append_audit_event
        self._max_retries_cap = int(max_retries_cap)
        # Lifecycle hooks registry (optional). When present, ``start`` fires the
        # ``pre_run`` / ``post_run`` hooks; a blocking ``pre_run`` aborts the run.
        self._hooks = hooks
        self._allow_simulation_runs = bool(allow_simulation_runs)
        self._run_executor: Any = None

    def attach_executor(self, executor: Any) -> None:
        self._run_executor = executor

    def _execution_mode(self) -> str:
        return "async" if self._run_executor is not None else "synchronous"

    # ── configuration ─────────────────────────────────────────────────────
    def config(self) -> Dict[str, Any]:
        return {
            "version": MULTI_AGENT_VERSION,
            "roles": list(AGENT_ROLES),
            "default_pipeline": list(CORE_PIPELINE),
            "max_retries_cap": self._max_retries_cap,
            "execution_mode": self._execution_mode(),
            "simulation_runs_allowed": self._allow_simulation_runs,
            "cancellation": (
                "cooperative; running synchronous model/tool calls finish their current step before a cancelled status is persisted"
                if self._run_executor is not None else
                "not supported for the synchronous runtime"
            ),
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
        ready = True
        try:
            self._store.list_agents(workspace_id=None)
            checks["run_store"] = {"status": "ok"}
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            checks["run_store"] = {"status": "error", "detail": str(exc)}
        try:
            orchestrator = self._orchestrator_factory(None, None)
            mode = getattr(orchestrator, "mode", "simulation")
            if mode == "simulation":
                if self._allow_simulation_runs:
                    checks["orchestrator"] = {
                        "status": "ok",
                        "mode": mode,
                        "detail": "Simulation runs are explicitly enabled for this non-product runtime.",
                    }
                else:
                    ready = False
                    checks["orchestrator"] = {
                        "status": "unavailable",
                        "mode": mode,
                        "detail": "No LLM-backed model is loaded; product execution API refuses simulation runs.",
                    }
            else:
                checks["orchestrator"] = {"status": "ok", "mode": mode}
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            checks["orchestrator"] = {"status": "error", "detail": str(exc)}
        return {
            "status": "ok" if ok and ready else "unavailable" if ok else "degraded",
            "ready": bool(ok and ready),
            "checks": checks,
        }

    def _live_orchestrator(self, user_email: Optional[str], scope: Optional[str]) -> Any:
        orchestrator = self._orchestrator_factory(user_email or None, scope)
        mode = getattr(orchestrator, "mode", "simulation")
        if mode == "simulation" and not self._allow_simulation_runs:
            raise AgentRuntimeUnavailable(
                "Agent execution is unavailable because no LLM-backed model is loaded. "
                "Simulation mode is disabled in the product execution API so it cannot be recorded as real success."
            )
        return orchestrator

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
        health = self.health()
        orchestrator_status = (health.get("checks") or {}).get("orchestrator") or {}
        ready = bool(health.get("ready"))
        return {
            "runtime": {
                "ready": ready,
                "version": MULTI_AGENT_VERSION,
                "execution_mode": self._execution_mode(),
                "mode": orchestrator_status.get("mode", "unknown"),
                "unavailable_reason": None if ready else orchestrator_status.get("detail"),
                "default_pipeline": list(CORE_PIPELINE),
                "total_runs": len(runs),
                "active_runs": active,
            },
            "health": health,
            "roles": self.roles(),
            "agents": self._roster(runs),
            "runs": runs[:25],
            "contracts": contract_views(runs[:25]),
        }

    def preview(
        self,
        goal: str,
        *,
        roles: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the execution contract without reserving or starting a run.

        The preview is the product-facing readiness gate for agent execution:
        clients can show whether a real LLM-backed run can start, which roles
        will execute, how retry limits are clamped, and why the run is blocked.
        """

        requested_roles = list(roles or CORE_PIPELINE)
        unknown_roles = [role for role in requested_roles if role not in AGENT_ROLES]
        health = self.health()
        goal_ready = bool(str(goal or "").strip())
        retry_budget = max(0, min(int(max_retries or 0), self._max_retries_cap))
        blocking_reasons: List[str] = []
        if not goal_ready:
            blocking_reasons.append("goal is required")
        if unknown_roles:
            blocking_reasons.append(f"unknown roles: {', '.join(unknown_roles)}")
        if not health.get("ready"):
            orchestrator = (health.get("checks") or {}).get("orchestrator") or {}
            blocking_reasons.append(str(orchestrator.get("detail") or "agent runtime is unavailable"))
        can_start = not blocking_reasons
        return {
            "ready": can_start,
            "can_start": can_start,
            "blocking_reasons": blocking_reasons,
            "goal": str(goal or "").strip(),
            "roles": requested_roles,
            "unknown_roles": unknown_roles,
            "inputs_keys": sorted((inputs or {}).keys()),
            "max_retries": retry_budget,
            "max_retries_requested": max_retries,
            "scope": scope,
            "execution_mode": self._execution_mode(),
            "runtime": {
                "version": MULTI_AGENT_VERSION,
                "default_pipeline": list(CORE_PIPELINE),
                "max_retries_cap": self._max_retries_cap,
                "simulation_runs_allowed": self._allow_simulation_runs,
            },
            "health": health,
        }

    # ── events / state ────────────────────────────────────────────────────
    def list_runs(self, *, scope: Optional[str] = None) -> Dict[str, Any]:
        listing = self._store.list_agents(workspace_id=scope)
        runs = list(listing.get("runs") or [])
        payload = dict(listing)
        payload["contracts"] = contract_views(runs)
        return payload

    def get_run(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        run = self._store.get_agent_run(run_id, workspace_id=scope)
        payload = {"run": run}
        contract = extract_contract(run)
        if contract is not None:
            payload["contract"] = contract_view(contract)
        return payload

    def replay(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        replay = self._store.replay_agent_run(run_id, workspace_id=scope)
        payload = {"replay": replay}
        contract = extract_contract(replay)
        if contract is not None:
            payload["contract"] = contract_view(contract)
        return payload

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
            "contract": contract_view(run),
        }

    # ── execution ─────────────────────────────────────────────────────────
    def _fire_pre_run(
        self,
        *,
        goal: str,
        roles: Optional[List[str]],
        max_retries: int,
        user_email: Optional[str],
        scope: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        pre_dispatch: Optional[Dict[str, Any]] = None
        if self._hooks is not None:
            pre_dispatch = self._hooks.fire_hook(
                "pre_run", "agent.run",
                payload={"goal": goal, "roles": roles or None, "max_retries": max_retries},
                user_email=user_email, workspace_id=scope,
            )
            if pre_dispatch.get("blocked"):
                self._append_audit_event(
                    "multi_agent_run_blocked",
                    user_email=user_email,
                    reason=pre_dispatch.get("block_reason"),
                )
                raise PermissionError(pre_dispatch.get("block_reason") or "Agent run blocked by a pre_run hook.")
        return pre_dispatch

    @staticmethod
    def _result_patch(result: Any, goal: str) -> Dict[str, Any]:
        return {
            "agent_id": result.agent_id,
            "status": result.status,
            "input": goal,
            "output_text": result.output,
            "timeline": result.timeline,
            "relationships": [ROLE_AGENT_IDS.get(r, f"agent:{r}") for r in result.roles_run],
            "handoffs": result.handoffs,
            "context_packets": result.context_packets,
            "plan": result.plan,
            "plan_review": result.plan_review,
            "review_history": result.review_history,
            "retry_history": result.retry_history,
            "memory_snapshots": result.memory_snapshots,
            "mode": getattr(result, "mode", "simulation"),
            "contract": multi_agent_contract(result=result, goal=goal),
            "current_role": None,
        }

    def _post_run_hooks(
        self,
        *,
        run_id: Optional[str],
        result: Any,
        user_email: Optional[str],
        scope: Optional[str],
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._hooks is None:
            return None
        return self._hooks.fire_hook(
            "post_run", "agent.run",
            payload={
                "run_id": run_id,
                "agent_id": result.agent_id,
                "status": status or result.status,
                "retries": result.retries,
            },
            user_email=user_email, workspace_id=scope,
        )

    def reserve_run(
        self,
        goal: str,
        *,
        user_email: Optional[str],
        scope: Optional[str],
        roles: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Create the durable queued row used by the async executor."""
        if not str(goal or "").strip():
            raise ValueError("goal is required")
        pre_dispatch = self._fire_pre_run(
            goal=goal,
            roles=roles,
            max_retries=max_retries,
            user_email=user_email,
            scope=scope,
        )
        orchestrator = self._live_orchestrator(user_email, scope)
        mode = getattr(orchestrator, "mode", "llm")
        run = self._store.record_agent_run(
            agent_id=ROLE_AGENT_IDS.get("executor", "agent:executor"),
            status="queued",
            input_text=goal,
            output_text="",
            timeline=[{"event": "agent_started", "status": "queued", "timestamp": _now()}],
            relationships=[],
            handoffs=[],
            context_packets=[],
            plan=[],
            plan_review={},
            review_history=[],
            retry_history=[],
            memory_snapshots=[],
            user_email=user_email or None,
            graph=None,
            workspace_id=scope,
            mode=mode,
        )
        run = self._store.update_agent_run(
            run.get("id"),
            workspace_id=scope,
            execution_mode="async",
            requested_roles=roles or None,
            inputs=inputs or {},
            max_retries=max_retries,
        )
        payload: Dict[str, Any] = {"run": run}
        if pre_dispatch is not None:
            payload["pre_run_hooks"] = pre_dispatch
        return payload

    def complete_reserved_run(
        self,
        run_id: str,
        goal: str,
        *,
        user_email: Optional[str],
        scope: Optional[str],
        roles: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
        pre_dispatch: Optional[Dict[str, Any]] = None,
        cancel_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute orchestration and update an existing durable async row."""
        run = self._store.get_agent_run(run_id, workspace_id=scope)
        base_timeline = list(run.get("timeline") or [])
        self._store.update_agent_run(
            run_id,
            workspace_id=scope,
            status="running",
            current_role=(roles or list(CORE_PIPELINE))[0] if (roles or CORE_PIPELINE) else None,
            started_at=run.get("started_at") or _now(),
        )
        try:
            orchestrator = self._live_orchestrator(user_email, scope)
            result = orchestrator.run(
                goal,
                user_email=user_email or None,
                workspace_id=scope,
                inputs=inputs or {},
                roles=roles or None,
                max_retries=max(0, min(int(max_retries or 0), self._max_retries_cap)),
            )
        except Exception as exc:
            failed = self._store.update_agent_run(
                run_id,
                workspace_id=scope,
                graph=self._workspace_graph(),
                status="failed",
                current_role=None,
                error=str(exc),
                output_text=str(exc),
                timeline=base_timeline + [{
                    "event": "execution_failed",
                    "status": "failed",
                    "detail": str(exc),
                    "timestamp": _now(),
                }],
            )
            self._append_audit_event("multi_agent_run", user_email=user_email, agent_id=failed.get("agent_id"), status="failed", retries=0)
            return {"run": failed, "result": {"status": "failed", "error": str(exc)}}

        patch = self._result_patch(result, goal)
        patch["timeline"] = base_timeline + list(result.timeline or [])
        if cancel_requested is not None and cancel_requested():
            patch["status"] = "cancelled"
            patch["current_role"] = None
            patch["cancel_reason"] = "cancelled after the current synchronous step completed"
            patch["cancelled_at"] = _now()
            patch["timeline"] = patch["timeline"] + [{
                "event": "execution_cancelled",
                "status": "cancelled",
                "reason": patch["cancel_reason"],
                "timestamp": _now(),
            }]
        updated = self._store.update_agent_run(
            run_id,
            workspace_id=scope,
            graph=self._workspace_graph(),
            patch=patch,
        )
        self._append_audit_event(
            "multi_agent_run",
            user_email=user_email,
            agent_id=result.agent_id,
            status=updated.get("status") or result.status,
            retries=result.retries,
        )
        post_dispatch = self._post_run_hooks(
            run_id=run_id,
            result=result,
            user_email=user_email,
            scope=scope,
            status=updated.get("status") or result.status,
        )
        result_payload = result.as_dict()
        result_payload["contract"] = multi_agent_contract(
            result=result,
            goal=goal,
            run_id=run_id,
            current_role=updated.get("current_role") if isinstance(updated, dict) else None,
        )
        if updated.get("status") == "cancelled":
            result_payload = {"status": "cancelled", "reason": updated.get("cancel_reason"), "completed_result": result_payload}
        payload = {"run": updated, "result": result_payload}
        if pre_dispatch is not None:
            payload["pre_run_hooks"] = pre_dispatch
        if post_dispatch is not None:
            payload["post_run_hooks"] = post_dispatch
        return payload

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

        # ── pre_run hooks ─────────────────────────────────────────────────
        # A blocking pre_run hook (e.g. a policy gate) aborts the run before any
        # orchestration happens. Hook failures never crash the run (fire_hook
        # swallows them); only an explicit block stops it.
        pre_dispatch: Optional[Dict[str, Any]] = None
        if self._hooks is not None:
            pre_dispatch = self._hooks.fire_hook(
                "pre_run", "agent.run",
                payload={"goal": goal, "roles": roles or None, "max_retries": max_retries},
                user_email=user_email, workspace_id=scope,
            )
            if pre_dispatch.get("blocked"):
                self._append_audit_event(
                    "multi_agent_run_blocked",
                    user_email=user_email,
                    reason=pre_dispatch.get("block_reason"),
                )
                raise PermissionError(pre_dispatch.get("block_reason") or "Agent run blocked by a pre_run hook.")

        orchestrator = self._live_orchestrator(user_email, scope)
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
            mode=getattr(result, "mode", "simulation"),
        )
        self._append_audit_event(
            "multi_agent_run",
            user_email=user_email,
            agent_id=result.agent_id,
            status=result.status,
            retries=result.retries,
        )

        # ── post_run hooks ────────────────────────────────────────────────
        post_dispatch: Optional[Dict[str, Any]] = None
        if self._hooks is not None:
            run_id = run.get("id") or run.get("run_id") if isinstance(run, dict) else None
            post_dispatch = self._hooks.fire_hook(
                "post_run", "agent.run",
                payload={
                    "run_id": run_id,
                    "agent_id": result.agent_id,
                    "status": result.status,
                    "retries": result.retries,
                },
                user_email=user_email, workspace_id=scope,
            )

        result_payload = result.as_dict()
        result_payload["contract"] = multi_agent_contract(result=result, goal=goal, run_id=run.get("id") if isinstance(run, dict) else None)
        payload = {"run": run, "result": result_payload}
        if pre_dispatch is not None:
            payload["pre_run_hooks"] = pre_dispatch
        if post_dispatch is not None:
            payload["post_run_hooks"] = post_dispatch
        return payload

    def stop(self, run_id: str, *, scope: Optional[str] = None) -> Dict[str, Any]:
        """Best-effort stop.

        The default runtime executes synchronously, so by the time a run id
        exists the run has already completed. Report that honestly rather than
        pretending a cancellation occurred.
        """
        if self._run_executor is not None:
            return self._run_executor.cancel(run_id, kind="agent", scope=scope)
        try:
            run = self._store.get_agent_run(run_id, workspace_id=scope)
        except FileNotFoundError:
            return {"stopped": False, "reason": "run not found", "run_id": run_id}
        status = str(run.get("status") or "")
        if status in _ACTIVE_STATUSES:
            return {"stopped": False, "reason": "asynchronous cancellation is not supported by the synchronous runtime", "run_id": run_id, "status": status}
        return {"stopped": False, "reason": "run already finished", "run_id": run_id, "status": status}
