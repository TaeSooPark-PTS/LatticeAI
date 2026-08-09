"""wp35: RunExecutor failure, cancellation and reconciliation paths.

``RunExecutor`` takes its store, agent runtime, workflow-runner factory, graph
accessor and audit sink as keyword collaborators, so every scenario here is
built by injecting a fake at that constructor seam. Async work is driven with
``asyncio.run`` (repo idiom — no pytest-asyncio mode is configured).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from latticeai.services.run_executor import RunExecutor


class FakeStore:
    """In-memory stand-in for the durable workspace store."""

    def __init__(self):
        self.agent_runs: Dict[str, Dict[str, Any]] = {}
        self.workflow_runs: Dict[str, Dict[str, Any]] = {}
        self.get_agent_error: Optional[Exception] = None
        self._sequence = 0

    # ── agent runs ────────────────────────────────────────────────────────
    def seed_agent_run(self, run_id: str, status: str = "running") -> Dict[str, Any]:
        self.agent_runs[run_id] = {"id": run_id, "status": status, "timeline": []}
        return self.agent_runs[run_id]

    def get_agent_run(self, run_id, *, workspace_id=None):
        if self.get_agent_error is not None:
            raise self.get_agent_error
        if run_id not in self.agent_runs:
            raise FileNotFoundError(run_id)
        return dict(self.agent_runs[run_id])

    def update_agent_run(self, run_id, *, workspace_id=None, **fields):
        run = self.agent_runs.setdefault(run_id, {"id": run_id, "timeline": []})
        run.update(fields)
        return dict(run)

    # ── workflow runs ─────────────────────────────────────────────────────
    def record_workflow_run(self, **fields):
        self._sequence += 1
        run_id = f"wfrun-{self._sequence}"
        run = {"id": run_id, "timeline": list(fields.get("timeline") or []), **fields}
        run["id"] = run_id
        self.workflow_runs[run_id] = run
        return dict(run)

    def get_workflow_run(self, run_id, *, workspace_id=None):
        if run_id not in self.workflow_runs:
            raise FileNotFoundError(run_id)
        return dict(self.workflow_runs[run_id])

    def update_workflow_run(self, run_id, *, workspace_id=None, **fields):
        run = self.workflow_runs.setdefault(run_id, {"id": run_id, "timeline": []})
        run.update(fields)
        return dict(run)

    def reconcile_interrupted_runs(self, *, reason):
        return {"count": 0, "reason": reason}


class FakeAgentRuntime:
    def __init__(self, complete):
        self._complete = complete
        self.reserved: List[str] = []

    def reserve_run(self, goal, *, user_email=None, scope=None, roles=None, inputs=None, max_retries=2):
        run_id = f"agent-{len(self.reserved) + 1}"
        self.reserved.append(run_id)
        return {"run": {"id": run_id, "status": "queued"}}

    def complete_reserved_run(self, run_id, goal, **kwargs):
        return self._complete(run_id, goal, **kwargs)


def _executor(store, complete, **overrides) -> RunExecutor:
    kwargs: Dict[str, Any] = {
        "store": store,
        "agent_runtime": FakeAgentRuntime(complete),
        "build_workflow_runners": lambda user, scope: {},
        "workspace_graph": lambda: {"nodes": []},
        "append_audit_event": lambda *a, **k: None,
    }
    kwargs.update(overrides)
    return RunExecutor(**kwargs)


def _workflow(*, review_queue: bool = False) -> Dict[str, Any]:
    return {
        "id": "wf-1",
        "name": "nightly digest",
        "nodes": [
            {
                "id": "t",
                "type": "trigger",
                "config": {"trigger": "manual", "review_queue": review_queue},
                "next": "o",
            },
            {"id": "o", "type": "output", "config": {}, "next": None},
        ],
    }


# ── startup reconciliation ───────────────────────────────────────────────────


def test_reconcile_startup_delegates_to_the_store():
    store = FakeStore()

    assert _executor(store, lambda *a, **k: {}).reconcile_startup() == {
        "count": 0,
        "reason": "server_startup",
    }


# ── agent runs ───────────────────────────────────────────────────────────────


def test_agent_cancelled_after_the_final_result_was_persisted():
    store = FakeStore()
    holder: Dict[str, Any] = {}

    def complete(run_id, goal, **kwargs):
        store.seed_agent_run(run_id, status="running")
        holder["executor"].cancel(run_id, kind="agent", scope="personal")
        return {"run": {"id": run_id, "status": "ok"}, "result": {"status": "ok"}}

    executor = _executor(store, complete)
    holder["executor"] = executor

    async def scenario():
        accepted = await executor.start_agent("ship it", user_email="u@e.co", scope="personal")
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return run_id

    run_id = asyncio.run(scenario())

    assert store.agent_runs[run_id]["status"] == "cancelled"
    assert "final result was persisted" in store.agent_runs[run_id]["cancel_reason"]
    assert executor._results[run_id]["result"]["status"] == "cancelled"


def test_agent_failure_records_a_failed_run_from_the_store():
    store = FakeStore()

    def complete(run_id, goal, **kwargs):
        store.seed_agent_run(run_id, status="running")
        raise RuntimeError("model exploded")

    executor = _executor(store, complete)

    async def scenario():
        accepted = await executor.start_agent("ship it", user_email="u@e.co", scope="personal")
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return run_id

    run_id = asyncio.run(scenario())

    stored = store.agent_runs[run_id]
    assert stored["status"] == "failed"
    assert stored["output_text"] == "model exploded"
    assert stored["timeline"][-1]["event"] == "execution_failed"
    assert executor._results[run_id]["result"]["error"] == "model exploded"


def test_agent_failure_degrades_when_the_store_and_audit_also_fail():
    store = FakeStore()
    store.get_agent_error = RuntimeError("store offline")

    def complete(run_id, goal, **kwargs):
        raise RuntimeError("model exploded")

    def boom_audit(*args, **kwargs):
        raise RuntimeError("audit sink offline")

    executor = _executor(store, complete, append_audit_event=boom_audit)

    async def scenario():
        accepted = await executor.start_agent("ship it", user_email=None, scope=None)
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return run_id

    run_id = asyncio.run(scenario())

    assert executor._results[run_id] == {
        "run": {"id": run_id, "status": "failed"},
        "result": {"status": "failed", "error": "model exploded"},
    }


# ── workflow runs ────────────────────────────────────────────────────────────


def test_workflow_cancelled_before_execution_started():
    store = FakeStore()
    executor = _executor(store, lambda *a, **k: {})

    async def scenario():
        accepted = await executor.start_workflow(
            _workflow(), workflow_id="wf-1", user_email=None, scope="personal"
        )
        run_id = accepted["run"]["id"]
        stopped = executor.cancel(run_id, kind="workflow", scope="personal")
        await executor.wait(run_id, timeout=5)
        return run_id, stopped

    run_id, stopped = asyncio.run(scenario())

    assert stopped["stopped"] is True
    assert stopped["status"] == "cancelling"
    stored = store.workflow_runs[run_id]
    assert stored["status"] == "cancelled"
    assert "before execution started" in stored["cancel_reason"]
    assert stored["timeline"][-1]["event"] == "execution_cancelled"


def test_workflow_cancelled_after_the_synchronous_step_completed():
    store = FakeStore()
    holder: Dict[str, Any] = {}

    def build_runners(user_email, scope):
        holder["executor"].cancel(holder["run_id"], kind="workflow", scope=scope)
        return {}

    executor = _executor(store, lambda *a, **k: {}, build_workflow_runners=build_runners)
    holder["executor"] = executor

    async def scenario():
        accepted = await executor.start_workflow(
            _workflow(), workflow_id="wf-1", user_email=None, scope="personal"
        )
        holder["run_id"] = accepted["run"]["id"]
        await executor.wait(holder["run_id"], timeout=5)
        return holder["run_id"]

    run_id = asyncio.run(scenario())

    stored = store.workflow_runs[run_id]
    assert stored["status"] == "cancelled"
    assert "current synchronous step completed" in stored["cancel_reason"]


def test_workflow_failure_records_the_error_in_outputs():
    store = FakeStore()

    def build_runners(user_email, scope):
        raise RuntimeError("runner wiring broken")

    executor = _executor(store, lambda *a, **k: {}, build_workflow_runners=build_runners)

    async def scenario():
        accepted = await executor.start_workflow(
            _workflow(), workflow_id="wf-1", user_email="u@e.co", scope="personal"
        )
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return run_id

    run_id = asyncio.run(scenario())

    stored = store.workflow_runs[run_id]
    assert stored["status"] == "failed"
    assert stored["outputs"] == {"error": "runner wiring broken"}
    assert stored["timeline"][-1]["detail"] == "runner wiring broken"
    assert executor._results[run_id]["result"]["error"] == "runner wiring broken"


def test_review_sink_failure_never_breaks_a_finished_workflow():
    class BrokenSink:
        def create(self, **kwargs):
            raise RuntimeError("review queue offline")

    store = FakeStore()
    executor = _executor(store, lambda *a, **k: {}, review_sink=BrokenSink())

    async def scenario():
        accepted = await executor.start_workflow(
            _workflow(review_queue=True),
            workflow_id="wf-1",
            user_email="u@e.co",
            scope="personal",
        )
        run_id = accepted["run"]["id"]
        await executor.wait(run_id, timeout=5)
        return run_id

    run_id = asyncio.run(scenario())

    assert store.workflow_runs[run_id]["status"] != "failed"
    assert executor._results[run_id]["result"]["status"] != "failed"


# ── cancellation edge cases ──────────────────────────────────────────────────


def test_cancel_reports_a_missing_run():
    executor = _executor(FakeStore(), lambda *a, **k: {})

    assert executor.cancel("ghost") == {
        "stopped": False,
        "reason": "run not found",
        "run_id": "ghost",
    }


def test_cancel_refuses_an_already_finished_run():
    store = FakeStore()
    store.seed_agent_run("agent-9", status="ok")

    result = _executor(store, lambda *a, **k: {}).cancel("agent-9")

    assert result == {
        "stopped": False,
        "reason": "run already finished",
        "run_id": "agent-9",
        "status": "ok",
    }


@pytest.mark.parametrize("kind", ["workflow", "agent"])
def test_cancel_without_a_worker_finalizes_the_record_directly(kind):
    store = FakeStore()
    if kind == "workflow":
        run = store.record_workflow_run(name="orphan", status="running", timeline=[])
        store.workflow_runs[run["id"]]["status"] = "running"
        run_id = run["id"]
        stored = store.workflow_runs
    else:
        run_id = "agent-orphan"
        store.seed_agent_run(run_id, status="running")
        stored = store.agent_runs

    result = _executor(store, lambda *a, **k: {}).cancel(run_id, kind=kind, scope=None)

    assert result["stopped"] is True
    assert result["status"] == "cancelled"
    assert stored[run_id]["status"] == "cancelled"
    assert "no active worker owned this run" in stored[run_id]["cancel_reason"]
