"""Layout-rebuild run timelines (pipeline status, combined runs, admin health).

These endpoints power Capture journey counts, the Act unified timeline, and
the calm admin header. They aggregate existing stores only — no schema change.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.admin import create_admin_router
from latticeai.api.automation_intelligence import create_automation_intelligence_router
from latticeai.api.knowledge_graph import _pipeline_stage_view
from latticeai.core.workspace_runs import WorkspaceRuns
from latticeai.services.automation_intelligence import AutomationIntelligenceService

from ._layout_rebuild_common import (
    _assert_stage_invariants,
    _kg_client,
    _MemoryStore,
    _mixed_workspace_state,
    _PipelineGraph,
)


def test_pipeline_stage_view_never_waiting_when_pending_zero_and_count_positive():
    done = _pipeline_stage_view(count=4, pending=0)
    assert done == {"count": 4, "pending": 0, "status": "done"}
    _assert_stage_invariants(done)

    waiting = _pipeline_stage_view(count=0, pending=0)
    assert waiting["status"] == "waiting"
    _assert_stage_invariants(waiting)

    working = _pipeline_stage_view(count=2, pending=3)
    assert working == {"count": 2, "pending": 3, "status": "working"}
    _assert_stage_invariants(working)


def test_pipeline_status_counts_documents_and_edges():
    graph = _PipelineGraph(
        documents=[
            {"id": "doc:1", "indexed": True, "chunks": 3},
            {"id": "doc:2", "indexed": True, "chunks": 1},
            {"id": "doc:3", "indexed": False, "chunks": 0},
        ],
        edges={"mentions": 5, "based_on": 4},
    )
    client = _kg_client(graph)

    response = client.get("/knowledge-graph/pipeline/status")
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 3
    assert body["extracted"] == 2
    assert body["connected"] == 9
    assert "updated_at" in body
    stages = body["stages"]
    assert stages["received"]["count"] == 3
    assert stages["received"]["pending"] == 0
    assert stages["received"]["status"] == "done"
    # One doc not yet extracted → pending=1, status=working
    assert stages["extracted"]["count"] == 2
    assert stages["extracted"]["pending"] == 1
    assert stages["extracted"]["status"] == "working"
    assert stages["connected"]["count"] == 9
    assert stages["connected"]["status"] == "done"
    for stage in stages.values():
        _assert_stage_invariants(stage)


def test_pipeline_status_empty_store_returns_zeros():
    client = _kg_client(_PipelineGraph(documents=[], edges={}))
    body = client.get("/knowledge-graph/pipeline/status").json()
    assert body["received"] == 0
    assert body["extracted"] == 0
    assert body["connected"] == 0
    stages = body["stages"]
    assert stages["received"]["status"] == "waiting"
    assert stages["extracted"]["status"] == "waiting"
    assert stages["connected"]["status"] == "waiting"
    for stage in stages.values():
        _assert_stage_invariants(stage)


def test_pipeline_status_uses_index_pending_as_single_source():
    """index_status pending must drive stages.extracted — not a second story."""
    graph = _PipelineGraph(
        documents=[
            {"id": "doc:1", "indexed": True, "chunks": 2},
            {"id": "doc:2", "indexed": True, "chunks": 1},
        ],
        edges={"mentions": 3},
        index_status={
            "source_items": 2,
            "ready_items": 2,
            "pending_items": 0,
        },
    )
    body = _kg_client(graph).get("/knowledge-graph/pipeline/status").json()
    assert body["stages"]["extracted"]["pending"] == 0
    assert body["stages"]["extracted"]["status"] == "done"
    _assert_stage_invariants(body["stages"]["extracted"])

    backlog = _PipelineGraph(
        documents=[
            {"id": "doc:1", "indexed": True, "chunks": 2},
            {"id": "doc:2", "indexed": False, "chunks": 0},
        ],
        edges={"mentions": 1},
        index_status={
            "source_items": 2,
            "ready_items": 1,
            "pending_items": 4,
        },
    )
    body = _kg_client(backlog).get("/knowledge-graph/pipeline/status").json()
    assert body["stages"]["extracted"]["pending"] == 4
    assert body["stages"]["extracted"]["status"] == "working"
    # Never: pending=0 with waiting while count>0
    for stage in body["stages"].values():
        _assert_stage_invariants(stage)


def test_activity_run_row_is_public_api():
    """B3: routers must call the public activity_run_row, not a private member."""
    row = WorkspaceRuns.activity_run_row(
        {
            "id": "run-1",
            "status": "awaiting_approval",
            "workflow_name": "Needs eyes",
            "created_at": "2026-06-06T12:00:00",
            "workflow_id": "wf-1",
        },
        source="workflow",
    )
    assert row["id"] == "run-1"
    assert row["source"] == "workflow"
    assert row["title"] == "Needs eyes"
    assert row["can_resume"] is True
    assert row["can_stop"] is False
    assert not hasattr(WorkspaceRuns, "_activity_run_row") or callable(
        getattr(WorkspaceRuns, "activity_run_row", None)
    )


def test_activity_run_row_legacy_agent_without_ids_keeps_source_agent():
    """Legacy agent rows may omit both agent_id and workflow_id.

    Failure mode: activity_run_row passes through agent_id=None and a weak
    source normalizer reclassifies the row, collapsing the 목표 badge into
    레시피 (or vice versa). Pin source="agent" for bare legacy agent history.
    """
    legacy = {
        "id": "legacy-agent-run",
        "status": "ok",
        "goal": "Summarize last week",
        "created_at": "2026-05-01T09:00:00",
        # deliberately no agent_id, no workflow_id
    }
    row = WorkspaceRuns.activity_run_row(legacy, source="agent")
    assert row["source"] == "agent"
    assert row.get("agent_id") is None
    assert row.get("workflow_id") is None
    assert row["title"] == "Summarize last week"
    # Frontend badge: source === "agent" OR (!workflow_id && source !== "workflow")
    is_goal = row.get("source") == "agent" or (
        not row.get("workflow_id") and row.get("source") != "workflow"
    )
    is_recipe = row.get("source") == "workflow" or bool(row.get("workflow_id"))
    assert is_goal
    assert not is_recipe

    # Invalid/blank source still lands on agent when no workflow_id is present.
    recovered = WorkspaceRuns.activity_run_row(legacy, source="")
    assert recovered["source"] == "agent"

    # Combined list path: bare agent history must not lose source="agent".
    store = _MemoryStore({
        "agent_runs": [
            {
                "id": "legacy-bare",
                "status": "ok",
                "goal": "Bare legacy goal",
                "created_at": "2026-06-01T10:00:00",
                "workspace_id": "personal",
            }
        ],
        "workflow_runs": [],
    })
    payload = WorkspaceRuns(store).list_combined_runs(limit=10, workspace_id="personal")
    assert len(payload["runs"]) == 1
    bare = payload["runs"][0]
    assert bare["id"] == "legacy-bare"
    assert bare["source"] == "agent"
    assert bare.get("agent_id") is None
    assert bare.get("workflow_id") is None


def test_list_combined_runs_sorts_and_normalizes():
    store = _MemoryStore({
        "agent_runs": [
            {
                "id": "agent-run-old",
                "status": "ok",
                "input": "Old agent goal",
                "created_at": "2026-06-01T10:00:00",
                "workspace_id": "personal",
            },
            {
                "id": "agent-run-new",
                "status": "running",
                "goal": "Newest agent goal",
                "created_at": "2026-06-06T12:30:00",
                "workspace_id": "personal",
            },
        ],
        "workflow_runs": [
            {
                "id": "wf-run-approval",
                "workflow_id": "wf-1",
                "workflow_name": "Needs approval",
                "status": "awaiting_approval",
                "created_at": "2026-06-06T12:05:00",
                "workspace_id": "personal",
            },
        ],
    })
    runs = WorkspaceRuns(store)
    payload = runs.list_combined_runs(limit=10, workspace_id="personal")
    ids = [row["id"] for row in payload["runs"]]
    assert ids[0] == "agent-run-new"
    assert ids[1] == "wf-run-approval"
    assert ids[2] == "agent-run-old"
    assert payload["total"] == 3
    assert payload["truncated"] is False

    capped = runs.list_combined_runs(limit=2, workspace_id="personal")
    assert len(capped["runs"]) == 2
    assert capped["total"] == 3
    assert capped["truncated"] is True

    approval = next(row for row in payload["runs"] if row["id"] == "wf-run-approval")
    assert approval["source"] == "workflow"
    assert approval["title"] == "Needs approval"
    assert approval["can_resume"] is True
    assert approval["can_stop"] is False

    active = next(row for row in payload["runs"] if row["id"] == "agent-run-new")
    assert active["source"] == "agent"
    assert active["title"] == "Newest agent goal"
    assert active["can_stop"] is True


def test_activity_and_automations_combined_routes():
    class _Store:
        def list_combined_runs(self, *, limit=20, workspace_id=None):
            # Legacy shape: runs only. Router must still surface total/truncated.
            return {
                "runs": [
                    {
                        "id": "wf-run-approval",
                        "source": "workflow",
                        "title": "Needs approval",
                        "status": "awaiting_approval",
                        "started_at": "2026-06-06T12:05:00",
                        "finished_at": None,
                        "can_stop": False,
                        "can_resume": True,
                    }
                ]
            }

        def list_workflows(self, workspace_id=None):
            return {"workflows": []}

    store = _Store()
    service = AutomationIntelligenceService(store=store)
    app = FastAPI()
    app.include_router(
        create_automation_intelligence_router(
            service=service,
            store=store,
            require_user=lambda _request: "user@example.com",
            gate_read=lambda _request: "personal",
            gate_write=lambda _request: "personal",
            append_audit_event=lambda *args, **kwargs: None,
            workspace_graph=lambda: None,
        )
    )
    client = TestClient(app)

    for path in ("/api/activity/runs", "/automations/runs/combined"):
        response = client.get(path, params={"limit": 5})
        assert response.status_code == 200, path
        body = response.json()
        assert len(body["runs"]) == 1
        assert body["runs"][0]["status"] == "awaiting_approval"
        assert body["runs"][0]["source"] == "workflow"
        assert body["total"] == 1
        assert body["truncated"] is False


def test_activity_runs_always_carry_source_for_recipe_goal_badge():
    """Capture 09 badge: source === 'workflow' || Boolean(workflow_id).

    Failure mode: a store returns runs with neither ``source`` nor
    ``workflow_id``. The merged Act timeline then paints every workflow
    execution as "목표", silently undoing the two-panel merge.
    """
    from latticeai.api.automation_intelligence import (
        _ensure_activity_run_source,
        _with_run_truncation_meta,
    )

    # Pure helper: missing source + workflow_id ⇒ workflow.
    inferred = _ensure_activity_run_source(
        {"id": "wf-1", "workflow_id": "recipe-9", "title": "Nightly digest", "status": "ok"}
    )
    assert inferred["source"] == "workflow"
    assert inferred["workflow_id"] == "recipe-9"

    # Missing source + agent_id ⇒ agent.
    agentish = _ensure_activity_run_source(
        {"id": "ag-1", "agent_id": "agent-x", "title": "Goal", "status": "running"}
    )
    assert agentish["source"] == "agent"

    # Explicit source wins over ids.
    pinned = _ensure_activity_run_source(
        {"id": "x", "source": "workflow", "agent_id": "a", "title": "t", "status": "ok"}
    )
    assert pinned["source"] == "workflow"

    # Meta wrapper must re-inject source even when the store omitted it.
    legacy_payload = {
        "runs": [
            {
                "id": "wf-run-no-source",
                "workflow_id": "wf-1",
                "title": "Needs eyes",
                "status": "awaiting_approval",
                "started_at": "2026-06-06T12:05:00",
            },
            {
                "id": "agent-run-no-source",
                "agent_id": "agent-1",
                "title": "A goal",
                "status": "ok",
                "started_at": "2026-06-06T12:00:00",
            },
        ],
        "total": 2,
        "truncated": False,
    }
    fixed = _with_run_truncation_meta(legacy_payload, capped=20)
    by_id = {row["id"]: row for row in fixed["runs"]}
    assert by_id["wf-run-no-source"]["source"] == "workflow"
    assert by_id["agent-run-no-source"]["source"] == "agent"
    # Contract keys the frontend badge depends on.
    for row in fixed["runs"]:
        assert row.get("source") in {"agent", "workflow"}, row
        if row["source"] == "workflow":
            assert row.get("workflow_id"), row

    # HTTP surface: store that strips source must still advertise it.
    class _StoreMissingSource:
        def list_combined_runs(self, *, limit=20, workspace_id=None):
            return {
                "runs": [
                    {
                        "id": "wf-run-approval",
                        # deliberately no "source"
                        "workflow_id": "wf-1",
                        "title": "Needs approval",
                        "status": "awaiting_approval",
                        "started_at": "2026-06-06T12:05:00",
                        "finished_at": None,
                        "can_stop": False,
                        "can_resume": True,
                    },
                    {
                        "id": "agent-run-1",
                        "agent_id": "agent-1",
                        "title": "Newest agent goal",
                        "status": "running",
                        "started_at": "2026-06-06T12:30:00",
                        "finished_at": None,
                        "can_stop": True,
                        "can_resume": False,
                    },
                ],
                "total": 2,
                "truncated": False,
            }

        def list_workflows(self, workspace_id=None):
            return {"workflows": []}

    store = _StoreMissingSource()
    service = AutomationIntelligenceService(store=store)
    app = FastAPI()
    app.include_router(
        create_automation_intelligence_router(
            service=service,
            store=store,
            require_user=lambda _request: "user@example.com",
            gate_read=lambda _request: "personal",
            gate_write=lambda _request: "personal",
            append_audit_event=lambda *args, **kwargs: None,
            workspace_graph=lambda: None,
        )
    )
    client = TestClient(app)
    body = client.get("/api/activity/runs", params={"limit": 10}).json()
    assert len(body["runs"]) == 2
    sources = {row["id"]: row["source"] for row in body["runs"]}
    assert sources["wf-run-approval"] == "workflow"
    assert sources["agent-run-1"] == "agent"
    workflow_row = next(r for r in body["runs"] if r["id"] == "wf-run-approval")
    assert workflow_row.get("workflow_id") == "wf-1"
    # Frontend badge predicate must hold for every workflow row.
    for row in body["runs"]:
        is_recipe = row.get("source") == "workflow" or bool(row.get("workflow_id"))
        is_goal = row.get("source") == "agent" or (
            not row.get("workflow_id") and row.get("source") != "workflow"
        )
        assert is_recipe or is_goal
        if row["id"].startswith("wf-"):
            assert is_recipe
        if row["id"].startswith("agent-"):
            assert row["source"] == "agent"

    # activity_run_row itself must always emit source (unit of the public API).
    row = WorkspaceRuns.activity_run_row(
        {"id": "x", "status": "ok", "workflow_id": "wf-z", "workflow_name": "Z"},
        source="workflow",
    )
    assert "source" in row and row["source"] == "workflow"
    assert row.get("workflow_id") == "wf-z"
    # Removing source from the row dict is a contract break — pin the key set.
    required = {"id", "source", "title", "status", "started_at", "workflow_id"}
    assert required.issubset(row.keys()), row.keys()


def test_list_combined_runs_workspace_scope_isolation():
    """Workspace A must never see workspace B agent or workflow runs."""
    runs = WorkspaceRuns(_MemoryStore(_mixed_workspace_state()))
    payload = runs.list_combined_runs(limit=50, workspace_id="ws-a")
    ids = {row["id"] for row in payload["runs"]}
    assert ids == {"agent-a-1", "wf-a-1"}
    assert "agent-b-1" not in ids
    assert "wf-b-1" not in ids
    for row in payload["runs"]:
        assert row["id"].startswith(("agent-a", "wf-a"))


def test_list_combined_runs_total_is_workspace_scoped_after_limit():
    """``total`` must count only the gated workspace — never the global store.

    Failure mode: Act renders "N of total" from this field. If total is the
    unscoped store size (4) while workspace A only owns 2 rows, a ws-a user
    sees "1 of 4" / "50건 중 20건" and other workspaces leak into the sentence
    even when ``runs`` itself is correctly filtered.
    """
    runs = WorkspaceRuns(_MemoryStore(_mixed_workspace_state()))
    global_all = runs.list_combined_runs(limit=50, workspace_id=None)
    # Mixed fixture has 4 runs across two workspaces when unscoped.
    assert global_all["total"] == 4

    full = runs.list_combined_runs(limit=50, workspace_id="ws-a")
    assert full["total"] == 2
    assert len(full["runs"]) == 2
    assert full["truncated"] is False
    assert {row["id"] for row in full["runs"]} == {"agent-a-1", "wf-a-1"}

    # Cap must shrink the page, not the scoped total.
    slim = runs.list_combined_runs(limit=1, workspace_id="ws-a")
    assert len(slim["runs"]) == 1
    assert slim["total"] == 2, (
        f"total must stay workspace-scoped after limit; got {slim['total']} "
        f"(global would be {global_all['total']})"
    )
    assert slim["total"] != global_all["total"]
    assert slim["truncated"] is True
    assert slim["runs"][0]["id"] in {"agent-a-1", "wf-a-1"}

    other = runs.list_combined_runs(limit=1, workspace_id="ws-b")
    assert other["total"] == 2
    assert len(other["runs"]) == 1
    assert other["truncated"] is True
    assert other["runs"][0]["id"] in {"agent-b-1", "wf-b-1"}


def test_combined_runs_route_total_is_workspace_scoped_via_list_combined_runs():
    """Router path that calls store.list_combined_runs must keep scoped total.

    automation_intelligence._combined_runs caps with limit=capped and trusts
    the store's total. A store that returned global total would surface other
    workspaces in the Act sentence even after gate_read scoped the listing.
    """
    store = WorkspaceRuns(_MemoryStore(_mixed_workspace_state()))
    service = AutomationIntelligenceService(store=store)
    app = FastAPI()
    app.include_router(
        create_automation_intelligence_router(
            service=service,
            store=store,
            require_user=lambda _request: "user@example.com",
            gate_read=lambda _request: "ws-a",
            gate_write=lambda _request: "ws-a",
            append_audit_event=lambda *args, **kwargs: None,
            workspace_graph=lambda: None,
        )
    )
    client = TestClient(app)

    full = client.get("/api/activity/runs", params={"limit": 50}).json()
    assert {row["id"] for row in full["runs"]} == {"agent-a-1", "wf-a-1"}
    assert full["total"] == 2
    assert full["truncated"] is False

    slim = client.get("/api/activity/runs", params={"limit": 1}).json()
    assert len(slim["runs"]) == 1
    assert slim["total"] == 2, (
        "router must not report unscoped total after list_combined_runs cap"
    )
    assert slim["truncated"] is True
    assert slim["runs"][0]["id"] in {"agent-a-1", "wf-a-1"}

    alias = client.get("/automations/runs/combined", params={"limit": 1}).json()
    assert alias["total"] == 2
    assert len(alias["runs"]) == 1
    assert alias["truncated"] is True


def test_combined_runs_fallback_workspace_scope_isolation():
    """Router fallback (no list_combined_runs) must still honor workspace scope."""
    state = _mixed_workspace_state()

    class _FallbackStore:
        """Mirrors the router fallback path: list_agents + list_workflow_runs only."""

        def list_agents(self, workspace_id=None):
            items = [
                run
                for run in state["agent_runs"]
                if str(run.get("workspace_id") or "personal") == str(workspace_id or "personal")
            ]
            return {"agents": [], "runs": items}

        def list_workflow_runs(self, limit=50, workspace_id=None):
            items = [
                run
                for run in state["workflow_runs"]
                if str(run.get("workspace_id") or "personal") == str(workspace_id or "personal")
            ]
            return {"runs": items[:limit]}

        # Intentionally no list_combined_runs — forces automation_intelligence fallback.

    store = _FallbackStore()
    service = AutomationIntelligenceService(store=store)
    app = FastAPI()
    app.include_router(
        create_automation_intelligence_router(
            service=service,
            store=store,
            require_user=lambda _request: "user@example.com",
            gate_read=lambda _request: "ws-a",
            gate_write=lambda _request: "ws-a",
            append_audit_event=lambda *args, **kwargs: None,
            workspace_graph=lambda: None,
        )
    )
    client = TestClient(app)
    body = client.get("/api/activity/runs", params={"limit": 50}).json()
    ids = {row["id"] for row in body["runs"]}
    assert ids == {"agent-a-1", "wf-a-1"}
    assert "agent-b-1" not in ids
    assert "wf-b-1" not in ids
    assert body["total"] == 2
    assert body["truncated"] is False

    slim = client.get("/api/activity/runs", params={"limit": 1}).json()
    assert len(slim["runs"]) == 1
    assert slim["total"] == 2
    assert slim["truncated"] is True


def test_admin_health_summary_ok_and_attention():
    users = {
        "admin@example.com": {"role": "admin", "disabled": False},
        "member@example.com": {"role": "user", "disabled": True},
    }

    def _build_router(*, disabled_users: bool, network_exposed: bool) -> TestClient:
        live_users = {
            email: {**user, "disabled": disabled_users and email.startswith("member")}
            for email, user in users.items()
        }
        app = FastAPI()
        app.include_router(
            create_admin_router(
                require_admin=lambda _request: ("admin@example.com", live_users),
                require_user=lambda _request: "admin@example.com",
                load_users=lambda: live_users,
                save_users=lambda _users: None,
                get_user_role=lambda email, _users: (_users.get(email) or {}).get("role", "user"),
                get_history=lambda: [],
                get_audit_log=lambda: [],
                public_user=lambda email, user, _users: {"email": email, **user},
                load_vpc_config=lambda: {},
                save_vpc_config=lambda _cfg: None,
                build_admin_audit_report=lambda _users, events: {"recent_events": events},
                build_sensitivity_report=lambda _history: {
                    "summary": {"severity_counts": {"high": 0}, "risky_messages": 0}
                },
                append_audit_event=lambda *args, **kwargs: None,
                public_sso_config=lambda: {},
                save_sso_config=lambda _cfg: None,
                get_graph_stats=lambda: {"nodes": 1},
                enable_graph=True,
                invite_code="x",
                invite_gate_enabled=False,
                default_port=4825,
                product_hardening_status=lambda: {
                    "startup": {"network_exposed": network_exposed}
                },
            )
        )
        return TestClient(app)

    ok_body = _build_router(disabled_users=False, network_exposed=False).get(
        "/admin/health-summary"
    ).json()
    assert ok_body["status"] == "ok"
    assert ok_body["issue_count"] == 0
    assert ok_body["issue_count"] == len(ok_body["issues"])
    assert "issue_count" in ok_body  # first-class field, not client-derived

    attention = _build_router(disabled_users=True, network_exposed=True).get(
        "/admin/health-summary"
    ).json()
    assert attention["status"] == "attention"
    assert attention["issue_count"] >= 1
    # Explicit count must match issues[] length (AdminConsole reads issue_count).
    assert attention["issue_count"] == len(attention["issues"])
    areas = {issue["area"] for issue in attention["issues"]}
    assert "users" in areas
    assert "runtime_trust" in areas
