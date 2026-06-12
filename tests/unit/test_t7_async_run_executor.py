"""T7c: durable async run engine, cooperative cancellation, SSE feed events."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from latticeai.core.multi_agent import MultiAgentOrchestrator
from latticeai.core.realtime import RealtimeBus
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.agent_runtime import AgentRuntime
from latticeai.services.run_executor import RunExecutor


def _runtime(store, orchestrator_factory=None):
    runtime = AgentRuntime(
        store=store,
        orchestrator_factory=orchestrator_factory or (lambda user, scope: MultiAgentOrchestrator()),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
    )
    executor = RunExecutor(
        store=store,
        agent_runtime=runtime,
        build_workflow_runners=lambda user, scope: {},
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
    )
    runtime.attach_executor(executor)
    return runtime, executor


def test_startup_reconciliation_marks_active_runs_interrupted(tmp_path: Path):
    bus = RealtimeBus()
    store = WorkspaceOSStore(tmp_path, event_sink=bus)
    agent = store.record_agent_run(
        agent_id="agent:executor",
        status="queued",
        input_text="g",
        output_text="",
        user_email=None,
        workspace_id="personal",
    )
    workflow = store.record_workflow_run(
        workflow_id="wf-1",
        name="wf",
        status="running",
        timeline=[],
        user_email=None,
        workspace_id="personal",
        mode="live",
    )
    paused = store.record_workflow_run(
        workflow_id="wf-2",
        name="pause",
        status="awaiting_approval",
        timeline=[],
        user_email=None,
        workspace_id="personal",
        mode="live",
        pause={"node": "n1"},
    )

    result = store.reconcile_interrupted_runs(reason="restart-test")

    assert result["count"] == 2
    assert store.get_agent_run(agent["id"], workspace_id="personal")["status"] == "interrupted"
    assert store.get_workflow_run(workflow["id"], workspace_id="personal")["status"] == "interrupted"
    assert store.get_workflow_run(paused["id"], workspace_id="personal")["status"] == "awaiting_approval"
    event_types = {event["event_type"] for event in bus.recent(limit=20)}
    assert "execution_interrupted" in event_types
    assert "startup_reconciliation" in event_types


def test_async_agent_run_completes_single_durable_record(tmp_path: Path):
    async def scenario():
        bus = RealtimeBus()
        store = WorkspaceOSStore(tmp_path, event_sink=bus)
        runtime, executor = _runtime(store)
        accepted = await executor.start_agent("ship it", user_email="u@example.com", scope="personal")
        run_id = accepted["run"]["id"]
        assert accepted["execution_mode"] == "async"
        assert runtime.config()["execution_mode"] == "async"
        await executor.wait(run_id, timeout=5)
        return store, bus, run_id

    store, bus, run_id = asyncio.run(scenario())
    run = store.get_agent_run(run_id, workspace_id="personal")
    assert run["status"] in {"ok", "retried_ok"}
    assert len(store.list_agents(workspace_id="personal")["runs"]) == 1
    assert run["timeline"][0]["event"] == "agent_started"
    assert any(item.get("event") == "handoff_created" for item in run["timeline"])
    event_types = {event["event_type"] for event in bus.recent(limit=50)}
    assert {"agent_started", "handoff_created"}.issubset(event_types)


def test_async_agent_cancellation_is_persisted(tmp_path: Path):
    class _Result:
        agent_id = "agent:executor"
        status = "ok"
        output = "late output"
        timeline = [{"event": "role", "role": "executor", "status": "ok"}]
        plan = []
        plan_review = {}
        roles_run = ["executor"]
        retries = 0
        handoffs = []
        context_packets = []
        review_history = []
        retry_history = []
        memory_snapshots = []
        mode = "llm"

        def as_dict(self):
            return {"status": self.status, "output": self.output, "timeline": self.timeline}

    class _SlowOrchestrator:
        mode = "llm"

        def run(self, *args, **kwargs):
            time.sleep(0.05)
            return _Result()

    async def scenario():
        store = WorkspaceOSStore(tmp_path)
        _, executor = _runtime(store, orchestrator_factory=lambda user, scope: _SlowOrchestrator())
        accepted = await executor.start_agent("slow", user_email=None, scope="personal")
        run_id = accepted["run"]["id"]
        stopped = executor.cancel(run_id, kind="agent", scope="personal")
        await executor.wait(run_id, timeout=5)
        return store, run_id, stopped

    store, run_id, stopped = asyncio.run(scenario())
    run = store.get_agent_run(run_id, workspace_id="personal")
    assert stopped["stopped"] is True
    assert run["status"] == "cancelled"
    assert any(item.get("event") == "execution_cancelled" for item in run["timeline"])


def test_async_workflow_run_completes_and_emits_realtime(tmp_path: Path):
    async def scenario():
        bus = RealtimeBus()
        store = WorkspaceOSStore(tmp_path, event_sink=bus)
        runtime, executor = _runtime(store)
        workflow = {
            "id": "wf-1",
            "name": "async wf",
            "nodes": [
                {"id": "t", "type": "trigger", "next": "out"},
                {"id": "out", "type": "output", "config": {"value": "done"}},
            ],
        }
        accepted = await executor.start_workflow(
            workflow,
            workflow_id="wf-1",
            user_email="u@example.com",
            scope="personal",
            inputs={},
        )
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return store, bus, run_id, runtime

    store, bus, run_id, _ = asyncio.run(scenario())
    run = store.get_workflow_run(run_id, workspace_id="personal")
    assert run["status"] == "ok"
    assert run["outputs"]["out"] == "done"
    assert len(store.list_workflow_runs(workspace_id="personal")["runs"]) == 1
    event_types = {event["event_type"] for event in bus.recent(limit=50)}
    assert {"workflow_started", "workflow_completed"}.issubset(event_types)
