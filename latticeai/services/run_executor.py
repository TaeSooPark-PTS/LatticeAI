"""Durable asyncio run executor for v4 Act runtimes.

The executor owns server-loop tasks for agent and workflow runs while the
workspace store remains the durable source of truth. Work is persisted before it
starts, updated as it moves through queued/running/cancelling/final states, and
reconciled on startup so orphaned active rows never masquerade as live work.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from lattice_brain.workflow import WorkflowEngine


ACTIVE_STATUSES = {"queued", "running", "in_progress", "retrying", "cancelling"}
TERMINAL_STATUSES = {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class _RunHandle:
    run_id: str
    kind: str
    scope: Optional[str]
    task: Optional[asyncio.Task] = None
    cancel_requested: bool = False
    started: bool = False


class RunExecutor:
    """Async task manager for persisted agent/workflow executions."""

    def __init__(
        self,
        *,
        store: Any,
        agent_runtime: Any,
        build_workflow_runners: Callable[[Optional[str], Optional[str]], Dict[str, Callable[..., Any]]],
        workspace_graph: Callable[[], Any],
        append_audit_event: Callable[..., None],
        hooks: Any = None,
        review_sink: Any = None,
    ) -> None:
        self.store = store
        self.agent_runtime = agent_runtime
        self.build_workflow_runners = build_workflow_runners
        self.workspace_graph = workspace_graph
        self.append_audit_event = append_audit_event
        self.hooks = hooks
        # Optional review-queue seam (5.6.0). Default None → no behavior change.
        self.review_sink = review_sink
        self._handles: Dict[str, _RunHandle] = {}
        self._results: Dict[str, Dict[str, Any]] = {}

    # ── startup reconciliation ───────────────────────────────────────────

    def reconcile_startup(self) -> Dict[str, Any]:
        return self.store.reconcile_interrupted_runs(reason="server_startup")

    # ── agent runs ───────────────────────────────────────────────────────

    async def start_agent(
        self,
        goal: str,
        *,
        user_email: Optional[str],
        scope: Optional[str],
        roles: Optional[list[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        reserved = self.agent_runtime.reserve_run(
            goal,
            user_email=user_email,
            scope=scope,
            roles=roles,
            inputs=inputs or {},
            max_retries=max_retries,
        )
        run_id = reserved["run"]["id"]
        handle = _RunHandle(run_id=run_id, kind="agent", scope=scope)
        handle.task = asyncio.create_task(
            self._run_agent(handle, goal, user_email=user_email, roles=roles, inputs=inputs or {}, max_retries=max_retries)
        )
        self._handles[run_id] = handle
        return {
            **reserved,
            "execution_mode": "async",
            "accepted": True,
            "events_url": f"/agents/api/runs/{run_id}/events",
            "stop_url": f"/agents/api/runs/{run_id}/stop",
        }

    async def _run_agent(
        self,
        handle: _RunHandle,
        goal: str,
        *,
        user_email: Optional[str],
        roles: Optional[list[str]],
        inputs: Dict[str, Any],
        max_retries: int,
    ) -> None:
        run_id = handle.run_id
        try:
            if handle.cancel_requested:
                self._cancel_agent_record(run_id, handle.scope, "cancelled before execution started")
                return
            handle.started = True
            payload = await asyncio.to_thread(
                self.agent_runtime.complete_reserved_run,
                run_id,
                goal,
                user_email=user_email,
                scope=handle.scope,
                roles=roles,
                inputs=inputs,
                max_retries=max_retries,
                cancel_requested=lambda: handle.cancel_requested,
            )
            if handle.cancel_requested and (payload.get("run") or {}).get("status") != "cancelled":
                self._cancel_agent_record(run_id, handle.scope, "cancelled after the final result was persisted")
            else:
                self._results[run_id] = payload
        except Exception as exc:
            try:
                run = self.store.get_agent_run(run_id, workspace_id=handle.scope)
                timeline = list(run.get("timeline") or [])
                timeline.append({"event": "execution_failed", "status": "failed", "detail": str(exc), "timestamp": _now()})
                failed = self.store.update_agent_run(
                    run_id,
                    workspace_id=handle.scope,
                    status="failed",
                    current_role=None,
                    output_text=str(exc),
                    timeline=timeline,
                    graph=self.workspace_graph(),
                )
                self._results[run_id] = {"run": failed, "result": {"status": "failed", "error": str(exc)}}
            except Exception:
                self._results[run_id] = {"run": {"id": run_id, "status": "failed"}, "result": {"status": "failed", "error": str(exc)}}
            try:
                self.append_audit_event("agent_run_failed", user_email=user_email, run_id=run_id, error=str(exc))
            except Exception:
                pass
        finally:
            self._handles.pop(run_id, None)

    def _cancel_agent_record(self, run_id: str, scope: Optional[str], reason: str) -> Dict[str, Any]:
        run = self.store.get_agent_run(run_id, workspace_id=scope)
        timeline = list(run.get("timeline") or [])
        timeline.append({"event": "execution_cancelled", "status": "cancelled", "reason": reason, "timestamp": _now()})
        cancelled = self.store.update_agent_run(
            run_id,
            workspace_id=scope,
            status="cancelled",
            current_role=None,
            cancel_reason=reason,
            cancelled_at=_now(),
            output_text=run.get("output_preview") or reason,
            timeline=timeline,
            graph=self.workspace_graph(),
        )
        payload = {"run": cancelled, "result": {"status": "cancelled", "reason": reason}}
        self._results[run_id] = payload
        return cancelled

    # ── workflow runs ────────────────────────────────────────────────────

    async def start_workflow(
        self,
        workflow: Dict[str, Any],
        *,
        workflow_id: str,
        user_email: Optional[str],
        scope: Optional[str],
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        run = self.store.record_workflow_run(
            workflow_id=workflow_id,
            name=workflow.get("name") or "workflow",
            status="queued",
            timeline=[{"event": "workflow_started", "status": "queued", "timestamp": _now()}],
            outputs={},
            user_email=user_email,
            graph=None,
            workspace_id=scope,
            mode="live",
        )
        run = self.store.update_workflow_run(
            run["id"],
            workspace_id=scope,
            execution_mode="async",
            inputs=inputs or {},
        )
        handle = _RunHandle(run_id=run["id"], kind="workflow", scope=scope)
        handle.task = asyncio.create_task(
            self._run_workflow(handle, workflow, user_email=user_email, inputs=inputs or {})
        )
        self._handles[run["id"]] = handle
        return {
            "run": run,
            "execution_mode": "async",
            "accepted": True,
            "events_url": f"/workflows/api/runs/{run['id']}/replay",
            "stop_url": f"/workflows/api/runs/{run['id']}/stop",
        }

    async def _run_workflow(
        self,
        handle: _RunHandle,
        workflow: Dict[str, Any],
        *,
        user_email: Optional[str],
        inputs: Dict[str, Any],
    ) -> None:
        run_id = handle.run_id
        try:
            if handle.cancel_requested:
                self._cancel_workflow_record(run_id, handle.scope, "cancelled before execution started")
                return
            handle.started = True
            run = self.store.get_workflow_run(run_id, workspace_id=handle.scope)
            base_timeline = list(run.get("timeline") or [])
            self.store.update_workflow_run(
                run_id,
                workspace_id=handle.scope,
                status="running",
                started_at=run.get("started_at") or _now(),
            )
            result = await asyncio.to_thread(self._execute_workflow_sync, workflow, user_email, handle.scope, inputs)
            if handle.cancel_requested:
                self._cancel_workflow_record(run_id, handle.scope, "cancelled after the current synchronous step completed")
                return
            pause = (
                {"node": result.paused_node, "pending": result.pending_approval, "context": result.paused_context}
                if result.status == "awaiting_approval" else None
            )
            updated = self.store.update_workflow_run(
                run_id,
                workspace_id=handle.scope,
                graph=self.workspace_graph(),
                status=result.status,
                timeline=base_timeline + list(result.timeline or []),
                outputs=result.outputs,
                pause=pause,
            )
            self.append_audit_event(
                "workflow_run",
                user_email=user_email,
                workflow_id=workflow.get("id"),
                status=result.status,
            )
            self._maybe_enqueue_review(
                workflow,
                run_result={"run": updated, "result": result.as_dict()},
                user_email=user_email,
                workspace_id=handle.scope,
            )
            self._results[run_id] = {"run": updated, "result": result.as_dict()}
        except Exception as exc:
            run = self.store.get_workflow_run(run_id, workspace_id=handle.scope)
            timeline = list(run.get("timeline") or [])
            timeline.append({"event": "execution_failed", "status": "failed", "detail": str(exc), "timestamp": _now()})
            failed = self.store.update_workflow_run(
                run_id,
                workspace_id=handle.scope,
                graph=self.workspace_graph(),
                status="failed",
                timeline=timeline,
                outputs={"error": str(exc)},
                pause=None,
            )
            self._results[run_id] = {"run": failed, "result": {"status": "failed", "error": str(exc)}}
        finally:
            self._handles.pop(run_id, None)

    def _maybe_enqueue_review(
        self,
        workflow: Dict[str, Any],
        *,
        run_result: Dict[str, Any],
        user_email: Optional[str],
        workspace_id: Optional[str],
    ) -> None:
        if self.review_sink is None:
            return
        try:
            from latticeai.services.review_queue import enqueue_from_automation

            enqueue_from_automation(
                self.review_sink,
                workflow=workflow,
                source="workflow_run",
                run_result=run_result,
                user_email=user_email,
                workspace_id=workspace_id,
            )
        except Exception:
            pass

    def _execute_workflow_sync(
        self,
        workflow: Dict[str, Any],
        user_email: Optional[str],
        scope: Optional[str],
        inputs: Dict[str, Any],
    ) -> Any:
        runners = self.build_workflow_runners(user_email, scope)
        return WorkflowEngine(runners, hooks=self.hooks).run(workflow, inputs=inputs)

    def _cancel_workflow_record(self, run_id: str, scope: Optional[str], reason: str) -> Dict[str, Any]:
        run = self.store.get_workflow_run(run_id, workspace_id=scope)
        timeline = list(run.get("timeline") or [])
        timeline.append({"event": "execution_cancelled", "status": "cancelled", "reason": reason, "timestamp": _now()})
        cancelled = self.store.update_workflow_run(
            run_id,
            workspace_id=scope,
            status="cancelled",
            cancel_reason=reason,
            cancelled_at=_now(),
            timeline=timeline,
            pause=None,
            graph=self.workspace_graph(),
        )
        payload = {"run": cancelled, "result": {"status": "cancelled", "reason": reason}}
        self._results[run_id] = payload
        return cancelled

    # ── cancellation/status ──────────────────────────────────────────────

    def cancel(self, run_id: str, *, kind: Optional[str] = None, scope: Optional[str] = None) -> Dict[str, Any]:
        handle = self._handles.get(run_id)
        try:
            run = (
                self.store.get_workflow_run(run_id, workspace_id=scope)
                if kind == "workflow" or (handle and handle.kind == "workflow")
                else self.store.get_agent_run(run_id, workspace_id=scope)
            )
        except FileNotFoundError:
            return {"stopped": False, "reason": "run not found", "run_id": run_id}

        status = str(run.get("status") or "")
        if status not in ACTIVE_STATUSES:
            return {"stopped": False, "reason": "run already finished", "run_id": run_id, "status": status}

        if handle is not None:
            handle.cancel_requested = True
        target_kind = (kind or (handle.kind if handle else "agent"))
        updater = self.store.update_workflow_run if target_kind == "workflow" else self.store.update_agent_run
        updater(
            run_id,
            workspace_id=scope,
            status="cancelling",
            cancel_requested=True,
            cancel_requested_at=_now(),
        )
        if handle is None:
            if target_kind == "workflow":
                self._cancel_workflow_record(run_id, scope, "cancelled; no active worker owned this run")
            else:
                self._cancel_agent_record(run_id, scope, "cancelled; no active worker owned this run")
        return {
            "stopped": True,
            "run_id": run_id,
            "status": "cancelling" if handle is not None else "cancelled",
            "cancellation": "cooperative",
            "reason": "cancellation requested; synchronous work finishes its current step before the final cancelled status is stored",
        }

    async def wait(self, run_id: str, *, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        handle = self._handles.get(run_id)
        if handle and handle.task:
            await asyncio.wait_for(handle.task, timeout=timeout)
        return self._results.get(run_id)
