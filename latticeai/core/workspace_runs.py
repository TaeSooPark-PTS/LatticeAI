"""Agent and workflow run persistence extracted from WorkspaceOSStore."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from lattice_brain.runtime.contracts import run_record_contract, workflow_run_contract

from .workspace_os_utils import _json_hash, _listify, _now

RUN_ACTIVE_STATUSES = {"queued", "running", "in_progress", "retrying", "cancelling"}
RUN_TERMINAL_STATUSES = {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial"}


class WorkspaceRuns:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def list_agents(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        state = self.load_state()
        runs = self._scoped(_listify(state.get("agent_runs")), workspace_id)
        return {"agents": _listify(state.get("agents")), "runs": list(reversed(runs[-100:]))}

    def record_agent_run(
        self,
        *,
        agent_id: str,
        status: str,
        input_text: str,
        output_text: str,
        user_email: Optional[str],
        timeline: Optional[List[Dict[str, Any]]] = None,
        relationships: Optional[List[str]] = None,
        handoffs: Optional[List[Dict[str, Any]]] = None,
        context_packets: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[List[Dict[str, Any]]] = None,
        plan_review: Optional[Dict[str, Any]] = None,
        review_history: Optional[List[Dict[str, Any]]] = None,
        retry_history: Optional[List[Dict[str, Any]]] = None,
        memory_snapshots: Optional[List[Dict[str, Any]]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        mode: str = "simulation",
    ) -> Dict[str, Any]:
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        run = {
            "id": f"agent-run-{_json_hash([agent_id, input_text, output_text, _now()])[:16]}",
            "record_schema_version": 2,
            "agent_id": agent_id,
            "mode": mode,
            "status": status,
            "input": input_text,
            "output_preview": output_text[:1000],
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "relationships": relationships or [],
            "timeline": timeline or [],
            "handoffs": handoffs or [],
            "context_packets": context_packets or [],
            "plan": plan or [],
            "plan_review": plan_review or {},
            "review_history": review_history or [],
            "retry_history": retry_history or [],
            "memory_snapshots": memory_snapshots or [],
            "created_at": _now(),
        }
        if mode == "simulation":
            # Simulated runs are replay scaffolding, not experiences — they must
            # never enter the knowledge graph as real provenance.
            run["graph_node_id"] = None
            run["graph_skipped"] = "simulation runs are not recorded in the knowledge graph"
        elif graph is not None:
            try:
                ingested = graph.ingest_event(
                    "AgentRun",
                    f"{agent_id} {status}",
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"run_id": run["id"], "agent_id": agent_id, "status": status, "mode": mode},
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)
        if handoffs:
            stored_handoffs = state.setdefault("handoffs", [])
            for handoff in handoffs:
                stored = {
                    **handoff,
                    "run_id": run["id"],
                    "workspace_id": resolved_workspace,
                }
                stored_handoffs.append(stored)
            state["handoffs"] = stored_handoffs
        state.setdefault("agent_runs", []).append(run)
        self.save_state(state)
        self._emit_replayable_timeline_events(area="agent", run_id=run["id"], timeline=run["timeline"], workspace_id=resolved_workspace)
        if status == "failed":
            self._emit_execution_event(area="agent", event_type="execution_failed", payload={"run_id": run["id"], "agent_id": agent_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("agent", "agent_run", {"run_id": run["id"], "agent_id": agent_id, "status": status}, workspace_id=resolved_workspace)
        run["contract"] = run_record_contract(run)
        state = self.load_state()
        for item in _listify(state.get("agent_runs")):
            if item.get("id") == run["id"]:
                item["contract"] = run["contract"]
                break
        self.save_state(state)
        return run

    def update_agent_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        graph: Any = None,
        patch: Optional[Dict[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Patch a persisted agent run without changing its id.

        Async execution creates a durable queued/running row before work starts,
        then updates that same row as progress, cancellation, or a terminal
        result arrives. This keeps old run lists/read APIs compatible while
        avoiding duplicate "placeholder + final" records.
        """
        updates = {**(patch or {}), **fields}
        state = self.load_state()
        run = next((item for item in _listify(state.get("agent_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        resolved_workspace = self._record_workspace(run)
        old_timeline_len = len(run.get("timeline") or [])

        output_text = updates.pop("output_text", None)
        if output_text is not None:
            run["output_preview"] = str(output_text)[:1000]
        for key, value in updates.items():
            run[key] = value
        status = str(run.get("status") or "")
        run["updated_at"] = _now()
        if status in RUN_TERMINAL_STATUSES:
            run.setdefault("completed_at", _now())

        handoffs = updates.get("handoffs")
        if isinstance(handoffs, list):
            stored_handoffs = [
                item for item in _listify(state.get("handoffs"))
                if item.get("run_id") != run_id
            ]
            for handoff in handoffs:
                if isinstance(handoff, dict):
                    stored_handoffs.append({**handoff, "run_id": run_id, "workspace_id": resolved_workspace})
            state["handoffs"] = stored_handoffs

        if (
            status in RUN_TERMINAL_STATUSES
            and run.get("mode") != "simulation"
            and graph is not None
            and not run.get("graph_node_id")
        ):
            try:
                ingested = graph.ingest_event(
                    "AgentRun",
                    f"{run.get('agent_id')} {status}",
                    user_email=run.get("user_email"),
                    source="workspace_os",
                    metadata={
                        "run_id": run_id,
                        "agent_id": run.get("agent_id"),
                        "status": status,
                        "mode": run.get("mode"),
                    },
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)

        self.save_state(state)
        run["contract"] = run_record_contract(run)
        state = self.load_state()
        for item in _listify(state.get("agent_runs")):
            if item.get("id") == run_id:
                item["contract"] = run["contract"]
                break
        self.save_state(state)

        timeline = run.get("timeline") or []
        if len(timeline) > old_timeline_len:
            self._emit_replayable_timeline_events(
                area="agent",
                run_id=run_id,
                timeline=timeline[old_timeline_len:],
                workspace_id=resolved_workspace,
            )
        if status == "failed":
            self._emit_execution_event(area="agent", event_type="execution_failed", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        elif status == "cancelled":
            self._emit_execution_event(area="agent", event_type="execution_cancelled", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        elif status == "interrupted":
            self._emit_execution_event(area="agent", event_type="execution_interrupted", payload={"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("agent", "agent_run_update", {"run_id": run_id, "agent_id": run.get("agent_id"), "status": status}, workspace_id=resolved_workspace)
        return run

    def get_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = next((item for item in _listify(self.load_state().get("agent_runs")) if item.get("id") == run_id), None)
        if not run or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        return run

    def list_handoffs(self, workspace_id: Optional[str] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
        handoffs = self._scoped(_listify(self.load_state().get("handoffs")), workspace_id)
        if run_id:
            handoffs = [item for item in handoffs if item.get("run_id") == run_id]
        return {"handoffs": list(reversed(handoffs[-200:]))}

    def create_workflow(
        self,
        *,
        name: str,
        steps: List[Dict[str, Any]],
        user_email: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        workflow = {
            "id": f"workflow-{_json_hash([name, steps, user_email, _now()])[:16]}",
            "name": name or "Untitled workflow",
            "steps": steps,
            "user_email": user_email,
            "workspace_id": self._resolve_scope(workspace_id, state),
            "metadata": metadata or {},
            "events": [{"type": "created", "timestamp": _now()}],
            "created_at": _now(),
            "updated_at": _now(),
        }
        # Workflow Designer stores a typed-node graph alongside the legacy
        # ``steps`` list so older history keeps working and new editors get nodes.
        if nodes is not None:
            workflow["nodes"] = nodes
        if graph is not None:
            try:
                ingested = graph.ingest_event(
                    "Workflow",
                    workflow["name"],
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"workflow_id": workflow["id"], "steps": steps},
                )
                workflow["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                workflow["graph_error"] = str(exc)
        state.setdefault("workflows", []).append(workflow)
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_created", {"workflow_id": workflow["id"], "name": workflow["name"]})
        return workflow

    def record_workflow_run(
        self,
        *,
        workflow_id: Optional[str],
        name: str,
        status: str,
        timeline: List[Dict[str, Any]],
        outputs: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        graph: Any = None,
        workspace_id: Optional[str] = None,
        mode: str = "simulation",
        pause: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a Workflow Designer execution into local-first run history."""
        state = self.load_state()
        resolved_workspace = self._resolve_scope(workspace_id, state)
        run = {
            "id": f"workflow-run-{_json_hash([workflow_id, name, status, _now()])[:16]}",
            "record_schema_version": 2,
            "workflow_id": workflow_id,
            "name": name or "workflow",
            "mode": mode,
            "status": status,
            "timeline": timeline or [],
            "outputs": outputs or {},
            "user_email": user_email,
            "workspace_id": resolved_workspace,
            "created_at": _now(),
        }
        if pause:
            run["pause"] = pause
        if mode == "simulation":
            # Record-only node runners do no real work; their runs must not be
            # written into the knowledge graph as if they were real executions.
            run["graph_node_id"] = None
            run["graph_skipped"] = "simulation runs are not recorded in the knowledge graph"
        elif graph is not None:
            try:
                ingested = graph.ingest_event(
                    "WorkflowRun",
                    f"{run['name']} {status}",
                    user_email=user_email,
                    source="workspace_os",
                    metadata={"run_id": run["id"], "workflow_id": workflow_id, "status": status, "mode": mode},
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)
        state.setdefault("workflow_runs", []).append(run)
        # Attach the run id to the workflow's event log for cross-linking.
        for wf in _listify(state.get("workflows")):
            if wf.get("id") == workflow_id:
                wf.setdefault("events", []).append({"type": "run", "timestamp": _now(), "payload": {"run_id": run["id"], "status": status}})
                wf["updated_at"] = _now()
                break
        self.save_state(state)
        self._emit_execution_event(area="workflow", event_type="workflow_started", payload={"run_id": run["id"], "workflow_id": workflow_id, "name": name}, workspace_id=resolved_workspace)
        self._emit_replayable_timeline_events(area="workflow", run_id=run["id"], timeline=run["timeline"], workspace_id=resolved_workspace)
        if status == "failed":
            self._emit_execution_event(area="workflow", event_type="execution_failed", payload={"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status in {"ok", "partial"}:
            self._emit_execution_event(area="workflow", event_type="workflow_completed", payload={"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("workflow", "workflow_run", {"run_id": run["id"], "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        run["contract"] = workflow_run_contract(run)
        state = self.load_state()
        for item in _listify(state.get("workflow_runs")):
            if item.get("id") == run["id"]:
                item["contract"] = run["contract"]
                break
        self.save_state(state)
        return run

    def update_workflow_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        graph: Any = None,
        patch: Optional[Dict[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Patch a persisted workflow run in place for async execution."""
        updates = {**(patch or {}), **fields}
        state = self.load_state()
        run = next((item for item in _listify(state.get("workflow_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        resolved_workspace = self._record_workspace(run)
        old_timeline_len = len(run.get("timeline") or [])

        for key, value in updates.items():
            if value is None and key == "pause":
                run.pop("pause", None)
            else:
                run[key] = value
        status = str(run.get("status") or "")
        run["updated_at"] = _now()
        if status in RUN_TERMINAL_STATUSES:
            run.setdefault("completed_at", _now())

        workflow_id = run.get("workflow_id")
        for wf in _listify(state.get("workflows")):
            if wf.get("id") == workflow_id:
                wf.setdefault("events", []).append({"type": "run_update", "timestamp": _now(), "payload": {"run_id": run_id, "status": status}})
                wf["updated_at"] = _now()
                break

        if (
            status in RUN_TERMINAL_STATUSES
            and run.get("mode") != "simulation"
            and graph is not None
            and not run.get("graph_node_id")
        ):
            try:
                ingested = graph.ingest_event(
                    "WorkflowRun",
                    f"{run.get('name')} {status}",
                    user_email=run.get("user_email"),
                    source="workspace_os",
                    metadata={
                        "run_id": run_id,
                        "workflow_id": workflow_id,
                        "status": status,
                        "mode": run.get("mode"),
                    },
                )
                run["graph_node_id"] = ingested.get("node_id")
            except Exception as exc:
                run["graph_error"] = str(exc)

        self.save_state(state)
        run["contract"] = workflow_run_contract(run)
        state = self.load_state()
        for item in _listify(state.get("workflow_runs")):
            if item.get("id") == run_id:
                item["contract"] = run["contract"]
                break
        self.save_state(state)

        timeline = run.get("timeline") or []
        if len(timeline) > old_timeline_len:
            self._emit_replayable_timeline_events(
                area="workflow",
                run_id=run_id,
                timeline=timeline[old_timeline_len:],
                workspace_id=resolved_workspace,
            )
        if status == "failed":
            self._emit_execution_event(area="workflow", event_type="execution_failed", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status in {"ok", "partial"}:
            self._emit_execution_event(area="workflow", event_type="workflow_completed", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status == "cancelled":
            self._emit_execution_event(area="workflow", event_type="execution_cancelled", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        elif status == "interrupted":
            self._emit_execution_event(area="workflow", event_type="execution_interrupted", payload={"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        self.record_timeline_event("workflow", "workflow_run_update", {"run_id": run_id, "workflow_id": workflow_id, "status": status}, workspace_id=resolved_workspace)
        return run

    def list_workflow_runs(self, workflow_id: Optional[str] = None, limit: int = 50, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        runs = self._scoped(_listify(self.load_state().get("workflow_runs")), workspace_id)
        if workflow_id:
            runs = [run for run in runs if run.get("workflow_id") == workflow_id]
        return {"runs": list(reversed(runs[-max(1, min(limit, 300)):]))}

    def mark_workflow_run_resolved(
        self, run_id: str, *, resumed_run_id: str, approved: bool,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close out a paused run after its approval decision (one decision only)."""
        state = self.load_state()
        run = next((item for item in _listify(state.get("workflow_runs")) if item.get("id") == run_id), None)
        if run is None or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        run["status"] = "resumed" if approved else "denied"
        run["resolved_at"] = _now()
        run["resumed_run_id"] = resumed_run_id
        self.save_state(state)
        return run

    def get_workflow_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = next((item for item in _listify(self.load_state().get("workflow_runs")) if item.get("id") == run_id), None)
        if not run or (workspace_id and self._record_workspace(run) != str(workspace_id)):
            raise FileNotFoundError(run_id)
        return run

    def reconcile_interrupted_runs(self, *, reason: str = "server_startup") -> Dict[str, Any]:
        """Mark durable active runs as interrupted after a process restart.

        Queued/running/cancelling rows cannot have an owning asyncio task after
        startup. Paused approval runs are intentionally left untouched so their
        durable human decision cursor remains resumable.
        """
        state = self.load_state()
        interrupted: List[Dict[str, Any]] = []
        now = _now()
        collections = (("agent_runs", "agent"), ("workflow_runs", "workflow"))
        for key, area in collections:
            for run in _listify(state.get(key)):
                status = str(run.get("status") or "")
                if status not in RUN_ACTIVE_STATUSES:
                    continue
                run["status"] = "interrupted"
                run["interrupted_at"] = now
                run["interrupt_reason"] = reason
                run["updated_at"] = now
                run.setdefault("timeline", []).append({
                    "event": "execution_interrupted",
                    "status": "interrupted",
                    "reason": reason,
                    "timestamp": now,
                })
                interrupted.append({
                    "kind": area,
                    "run_id": run.get("id"),
                    "workspace_id": self._record_workspace(run),
                    "previous_status": status,
                })
        if not interrupted:
            return {"count": 0, "interrupted": []}
        self.save_state(state)
        for item in interrupted:
            area = item["kind"]
            run_id = item["run_id"]
            workspace = item.get("workspace_id")
            self._emit_execution_event(
                area=area,
                event_type="execution_interrupted",
                payload={"run_id": run_id, "reason": reason, "previous_status": item.get("previous_status")},
                workspace_id=workspace,
            )
        self.record_timeline_event(
            "system",
            "startup_reconciliation",
            {"interrupted_runs": len(interrupted), "reason": reason},
        )
        return {"count": len(interrupted), "interrupted": interrupted}

    @staticmethod
    def _replay_frames(run: Dict[str, Any], *, kind: str) -> List[Dict[str, Any]]:
        frames = []
        for index, item in enumerate(run.get("timeline") or []):
            event = item.get("event") or item.get("event_type") or item.get("type") or "event"
            actor = (
                item.get("agent_id")
                or item.get("role")
                or item.get("source_agent")
                or item.get("target_agent")
                or item.get("node")
                or kind
            )
            result = item.get("result") if "result" in item else item.get("output")
            decision = item.get("outcome") or item.get("verdict") or item.get("status")
            frames.append({
                "index": index,
                "event": event,
                "actor": actor,
                "when": item.get("timestamp") or item.get("started_at") or run.get("created_at"),
                "why": item.get("reason") or item.get("note") or item.get("name") or "",
                "input": item.get("context_packet") or item.get("trigger") or run.get("input"),
                "output": result,
                "decision": decision,
                "raw": item,
            })
        return frames

    def replay_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        return {
            "kind": "agent",
            "run_id": run_id,
            "status": run.get("status"),
            "workspace_id": self._record_workspace(run),
            "contract": run.get("contract") or run_record_contract(run),
            "replayable": True,
            "frames": self._replay_frames(run, kind="agent"),
            "handoffs": run.get("handoffs") or [],
            "context_packets": run.get("context_packets") or [],
            "review_history": run.get("review_history") or [],
            "retry_history": run.get("retry_history") or [],
        }

    def replay_workflow_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        run = self.get_workflow_run(run_id, workspace_id=workspace_id)
        return {
            "kind": "workflow",
            "run_id": run_id,
            "status": run.get("status"),
            "workspace_id": self._record_workspace(run),
            "contract": run.get("contract") or workflow_run_contract(run),
            "replayable": True,
            "frames": self._replay_frames(run, kind="workflow"),
            "outputs": run.get("outputs") or {},
        }

    def get_workflow(self, workflow_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        workflow = next((wf for wf in _listify(self.load_state().get("workflows")) if wf.get("id") == workflow_id), None)
        if not workflow or (workspace_id and self._record_workspace(workflow) != str(workspace_id)):
            raise FileNotFoundError(workflow_id)
        return workflow

    def update_workflow_definition(
        self,
        workflow_id: str,
        *,
        name: Optional[str] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit a stored workflow's node graph / name without losing its history."""
        state = self.load_state()
        workflow = next((wf for wf in _listify(state.get("workflows")) if wf.get("id") == workflow_id), None)
        if not workflow or (workspace_id and self._record_workspace(workflow) != str(workspace_id)):
            raise FileNotFoundError(workflow_id)
        if name is not None and str(name).strip():
            workflow["name"] = str(name).strip()
        if nodes is not None:
            workflow["nodes"] = nodes
        if metadata is not None:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        workflow.setdefault("events", []).append({"type": "edited", "timestamp": _now()})
        workflow["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_edited", {"workflow_id": workflow_id})
        return workflow

    def list_workflows(self, query: str = "", workspace_id: Optional[str] = None) -> Dict[str, Any]:
        workflows = list(reversed(self._scoped(_listify(self.load_state().get("workflows")), workspace_id)))
        q = str(query or "").lower().strip()
        if q:
            workflows = [
                wf for wf in workflows
                if q in str(wf.get("name") or "").lower()
                or q in json.dumps(wf.get("steps") or [], ensure_ascii=False).lower()
            ]
        return {"workflows": workflows}

    def record_workflow_event(self, workflow_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state()
        workflows = _listify(state.get("workflows"))
        workflow = next((item for item in workflows if item.get("id") == workflow_id), None)
        if not workflow:
            raise FileNotFoundError(workflow_id)
        event = {"type": event_type, "timestamp": _now(), "payload": payload or {}}
        workflow.setdefault("events", []).append(event)
        workflow["updated_at"] = _now()
        self.save_state(state)
        self.record_timeline_event("workflow", "workflow_event", {"workflow_id": workflow_id, "event_type": event_type})
        return workflow
