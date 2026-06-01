"""v2.1 agent platform maturity coverage."""

from pathlib import Path

from latticeai.core.marketplace import TemplateCatalog
from latticeai.core.multi_agent import MultiAgentOrchestrator, OrchestrationContext
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.core.workflow_engine import WorkflowEngine


def test_agent_handoff_context_packet_and_replay_are_persisted(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    result = MultiAgentOrchestrator().run("Review the release", workspace_id="personal")

    run = store.record_agent_run(
        agent_id=result.agent_id,
        status=result.status,
        input_text="Review the release",
        output_text=result.output,
        user_email="user@example.com",
        timeline=result.timeline,
        relationships=[],
        handoffs=result.handoffs,
        context_packets=result.context_packets,
        plan=result.plan,
        plan_review=result.plan_review,
        review_history=result.review_history,
        retry_history=result.retry_history,
        memory_snapshots=result.memory_snapshots,
        workspace_id="personal",
    )

    handoffs = store.list_handoffs(workspace_id="personal", run_id=run["id"])["handoffs"]
    assert handoffs
    assert handoffs[0]["handoff_id"]
    assert handoffs[0]["context_packet"]["objective"] == "Review the release"

    replay = store.replay_agent_run(run["id"], workspace_id="personal")
    assert replay["replayable"] is True
    assert any(frame["event"] == "handoff_created" for frame in replay["frames"])


def test_review_retry_loop_records_history_and_limit():
    state = {"reviews": 0}

    def runner(role: str, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "step", "status": "planned"}]
        elif role == "executor":
            ctx.executed = [{"index": 0, "status": "done"}]
            ctx.output = "done"
        elif role == "reviewer":
            state["reviews"] += 1
            verdict = "retry" if state["reviews"] == 1 else "pass"
            ctx.review = {"verdict": verdict, "reason": "review cycle"}
        return {"role": role}

    result = MultiAgentOrchestrator(role_runner=runner).run("goal", max_retries=2)

    assert result.status == "retried_ok"
    assert result.retry_history[0]["limit"] == 2
    assert [item["outcome"] for item in result.review_history] == ["retry", "approve"]


def test_memory_snapshot_is_workspace_scoped(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    store.upsert_memory(kind="short_term", content="active run note", user_email="u@example.com", workspace_id="personal")
    store.upsert_memory(kind="long_term", content="durable note", user_email="u@example.com", workspace_id="personal")

    snapshot = store.create_memory_snapshot(label="agent review", user_email="u@example.com", workspace_id="personal")

    assert snapshot["memory_count"] == 2
    assert {item["kind"] for item in snapshot["memories"]} == {"short_term", "long_term"}
    assert store.list_memory_snapshots(workspace_id="personal")["snapshots"][0]["id"] == snapshot["id"]


def test_workflow_agent_plugin_output_enters_workflow_output():
    workflow = {
        "name": "agent plugin path",
        "nodes": [
            {"id": "trigger", "type": "trigger", "next": "agent"},
            {"id": "agent", "type": "agent", "config": {"goal": "run"}, "next": "plugin"},
            {"id": "plugin", "type": "plugin", "config": {"plugin": "demo"}, "next": "output"},
            {"id": "output", "type": "output", "config": {}, "next": None},
        ],
    }
    engine = WorkflowEngine({
        "agent": lambda node, context: {"agent_run_id": "ar-1", "output": "agent output"},
        "plugin": lambda node, context: {"plugin_id": "demo", "output": context.get("last_output")},
    })

    run = engine.run(workflow)

    assert run.status == "ok"
    assert run.outputs["output"]["output"]["agent_run_id"] == "ar-1"


def test_marketplace_template_export_import_and_install(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    catalog = TemplateCatalog()
    exported = catalog.export_template("workflow", "workflow-agent-plugin-review")
    imported = catalog.import_template(exported)
    installed = catalog.install_template(exported, store=store, user_email="u@example.com", workspace_id="personal")

    assert imported["kind"] == "workflow"
    assert installed["workflow_id"]
    assert store.list_template_registry()["workflow:workflow-agent-plugin-review"]["installed"] is True


def test_execution_events_flow_to_realtime_feed(tmp_path: Path):
    events = []
    store = WorkspaceOSStore(tmp_path, event_sink=lambda event: events.append(event))
    result = MultiAgentOrchestrator().run("observe me", workspace_id="personal")
    store.record_agent_run(
        agent_id=result.agent_id,
        status=result.status,
        input_text="observe me",
        output_text=result.output,
        user_email=None,
        timeline=result.timeline,
        relationships=[],
        handoffs=result.handoffs,
        context_packets=result.context_packets,
        workspace_id="personal",
    )

    event_types = {event["event_type"] for event in events}
    assert {"agent_started", "handoff_created", "review_approved"}.issubset(event_types)
