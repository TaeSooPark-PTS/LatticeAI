"""Layout-rebuild API contracts (pipeline status, combined runs, admin health).

These endpoints power Capture journey counts, the Act unified timeline, and
the calm admin header. They aggregate existing stores only — no schema change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.admin import create_admin_router
from latticeai.api.automation_intelligence import create_automation_intelligence_router
from latticeai.api.knowledge_graph import (
    _pipeline_stage_view,
    create_knowledge_graph_router,
)
from latticeai.core.workspace_runs import WorkspaceRuns
from latticeai.services.automation_intelligence import AutomationIntelligenceService


class _PipelineGraph:
    def __init__(
        self,
        documents: List[Dict[str, Any]],
        edges: Dict[str, int],
        *,
        index_status: Optional[Dict[str, Any]] = None,
    ):
        self._documents = documents
        self._edges = edges
        self._index_status = index_status

    def stats(self):
        return {
            "schema_version": 3,
            "v2_schema_available": True,
            "nodes": {"Document": len(self._documents), "Chunk": 4},
            "edges": self._edges,
            "v2": {"nodes": len(self._documents), "edges": sum(self._edges.values())},
        }

    def list_documents(self, limit: int = 200):
        docs = self._documents[:limit]
        return {"documents": docs, "total": len(docs)}

    def index_status(self):
        if self._index_status is None:
            raise AttributeError("index_status not configured")
        return self._index_status

    def graph(self, limit):
        return {"nodes": [], "edges": [], "limit": limit}

    def search(self, q, limit):
        return {"query": q, "matches": []}

    def context_for_query(self, q, limit):
        return ""

    def neighbors(self, node_id):
        return {"node": node_id, "neighbors": []}

    def ingest_message(self, role, content, **kwargs):
        return {"status": "ok"}

    def curate(self):
        return {"status": "ok"}

    def provenance_coverage(self):
        return {"total_nodes": 0, "nodes_with_provenance": 0, "coverage_ratio": 0}


class _MemoryStore:
    def __init__(self, state: Dict[str, Any]):
        self._state = state

    def load_state(self):
        return self._state

    def save_state(self, state):
        self._state = state

    def _scoped(self, items, workspace_id):
        if not workspace_id:
            return list(items)
        return [
            item
            for item in items
            if str(item.get("workspace_id") or "personal") == str(workspace_id)
        ]

    def _resolve_scope(self, workspace_id, state):
        return workspace_id or "personal"

    def _record_workspace(self, run):
        return str(run.get("workspace_id") or "personal")

    def _emit_execution_event(self, **kwargs):
        return None

    def record_timeline_event(self, *args, **kwargs):
        return None


def _kg_client(graph) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=lambda _request: "user@example.com",
            static_dir=Path("."),
        )
    )
    return TestClient(app)


def _assert_stage_invariants(stage: Dict[str, Any]) -> None:
    """pending=0 must never pair with waiting when count>0, or with working."""
    assert stage["pending"] >= 0
    assert stage["count"] >= 0
    if stage["pending"] == 0:
        assert stage["status"] != "working"
        if stage["count"] > 0:
            assert stage["status"] != "waiting"
            assert stage["status"] == "done"
    if stage["pending"] > 0:
        assert stage["status"] == "working"


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


def _mixed_workspace_state() -> Dict[str, Any]:
    """Agent runs in workspace A + workflow runs in workspace B (and one A)."""
    return {
        "agent_runs": [
            {
                "id": "agent-a-1",
                "status": "ok",
                "goal": "Workspace A agent",
                "created_at": "2026-06-06T12:30:00",
                "workspace_id": "ws-a",
            },
            {
                "id": "agent-b-1",
                "status": "running",
                "goal": "Workspace B agent",
                "created_at": "2026-06-06T12:40:00",
                "workspace_id": "ws-b",
            },
        ],
        "workflow_runs": [
            {
                "id": "wf-a-1",
                "workflow_id": "wf-a",
                "workflow_name": "Workspace A workflow",
                "status": "awaiting_approval",
                "created_at": "2026-06-06T12:05:00",
                "workspace_id": "ws-a",
            },
            {
                "id": "wf-b-1",
                "workflow_id": "wf-b",
                "workflow_name": "Workspace B workflow",
                "status": "ok",
                "created_at": "2026-06-06T12:00:00",
                "workspace_id": "ws-b",
            },
        ],
    }


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


def test_host_capacity_readiness_buckets():
    """System basic mode must not re-derive host capacity copy on the client."""
    from latticeai.api.static_routes import host_capacity_readiness

    assert host_capacity_readiness(cpu_pct=10, ram_pct=20, gpu_mem_pct=5) == "roomy"
    assert host_capacity_readiness(cpu_pct=55, ram_pct=40, gpu_mem_pct=10) == "roomy"
    assert host_capacity_readiness(cpu_pct=34, ram_pct=61, gpu_mem_pct=48) == "tight"
    assert host_capacity_readiness(cpu_pct=80, ram_pct=10, gpu_mem_pct=10) == "tight"
    assert host_capacity_readiness(cpu_pct=10, ram_pct=10, gpu_mem_pct=81) == "low"
    assert host_capacity_readiness(cpu_pct=99, ram_pct=99, gpu_mem_pct=99) == "low"


def _parse_mock_sysinfo() -> Dict[str, Any]:
    """Extract the /local/sysinfo payload fields from the visual mock server.

    Release capture (`08-system.png`) hits this mock in basic mode. The mock
    must ship the same readiness bucket the real API would compute for its
    percents — otherwise the published screenshot can say "넉넉합니다" while
    ram_pct is 61 (tight).

    Keys in the mock are unquoted JS identifiers, so we parse fields with
    regex rather than ``json.loads``.
    """
    import re

    mock_path = Path(__file__).resolve().parents[1] / "visual" / "mock_server.cjs"
    source = mock_path.read_text(encoding="utf-8")
    match = re.search(
        r'pathname === "/local/sysinfo"[^{]*return json\(res,\s*(\{.*?)\);',
        source,
        re.DOTALL,
    )
    assert match, "mock_server.cjs must define /local/sysinfo"
    body = match.group(1)

    def _num(name: str) -> float:
        m = re.search(rf"\b{name}\s*:\s*([0-9]+(?:\.[0-9]+)?)", body)
        assert m, f"mock /local/sysinfo missing numeric field {name}"
        return float(m.group(1))

    def _str(name: str) -> str:
        m = re.search(rf'\b{name}\s*:\s*"([^"]+)"', body)
        assert m, f"mock /local/sysinfo missing string field {name}"
        return m.group(1)

    return {
        "cpu_pct": _num("cpu_pct"),
        "ram_pct": _num("ram_pct"),
        "gpu_mem_pct": _num("gpu_mem_pct"),
        "gpu_mem_gb": _num("gpu_mem_gb"),
        "readiness": _str("readiness"),
    }


def _readiness_copy_from_workspace_i18n() -> Dict[str, str]:
    """Parse ko readiness phrases from frontend/src/i18n/workspace.ts.

    Hand-copied constants drift silently when i18n is edited. Reading the
    source of truth keeps mock bucket ↔ UI copy agreement honest.
    """
    import re

    i18n_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "i18n"
        / "workspace.ts"
    )
    source = i18n_path.read_text(encoding="utf-8")
    # Namespace file is ``{ ko: { ... }, en: { ... } }``; take only the first
    # (ko) block so en duplicates do not overwrite.
    ko_match = re.search(r"\bko\s*:\s*\{", source)
    assert ko_match, "workspace.ts must define a ko copy block"
    ko_start = ko_match.end()
    en_match = re.search(r"\ben\s*:\s*\{", source[ko_start:])
    ko_body = source[ko_start : ko_start + en_match.start()] if en_match else source[ko_start:]

    out: Dict[str, str] = {}
    for bucket in ("roomy", "tight", "low"):
        m = re.search(
            rf'"system\.readiness\.{bucket}"\s*:\s*"([^"]+)"',
            ko_body,
        )
        assert m, f"workspace.ts ko missing system.readiness.{bucket}"
        out[bucket] = m.group(1)
    return out


def test_mock_sysinfo_readiness_matches_capture_bucket():
    """Mock /local/sysinfo percents must derive the same readiness the mock pins.

    Failure mode this catches: mock still returns ram_pct=61 but readiness is
    missing or wrong (e.g. roomy). The System basic-mode UI keys off the
    readiness field via ``system.readiness.*`` in workspace.ts; a wrong bucket
    selects the roomy/"넉넉" sentence while the load profile is tight.

    This test does **not** OCR release screenshots. Stale 08-system.png is a
    separate evidence-binding gate (``scripts/check_release_evidence_bound.mjs``).
    """
    from latticeai.api.static_routes import host_capacity_readiness

    mock = _parse_mock_sysinfo()
    assert mock.get("ram_pct") == 61
    assert mock.get("cpu_pct") == 34
    assert mock.get("gpu_mem_pct") == 48
    assert mock.get("readiness") == "tight", (
        f"mock must pin readiness=tight for the capture load profile; got {mock!r}"
    )

    derived = host_capacity_readiness(
        cpu_pct=float(mock["cpu_pct"]),
        ram_pct=float(mock["ram_pct"]),
        gpu_mem_pct=float(mock["gpu_mem_pct"]),
    )
    assert derived == mock["readiness"] == "tight"

    copy = _readiness_copy_from_workspace_i18n()
    assert set(copy) == {"roomy", "tight", "low"}
    assert len(set(copy.values())) == 3, "ko readiness phrases must stay distinct"

    capture_text = copy[mock["readiness"]]
    roomy_text = copy["roomy"]
    assert capture_text != roomy_text
    assert "타이트" in capture_text
    assert "넉넉" not in capture_text
    assert "넉넉" in roomy_text


def test_system_settings_basic_branch_reads_sysinfo_readiness():
    """System.tsx basic branch must consume response ``readiness``, not a hardcode.

    Failure mode this catches: mock↔i18n agreement stays green while SettingsPanel
    basic mode goes back to always rendering ``system.readiness.plenty`` (or any
    fixed key) and never reads ``data.readiness``. Capture then shows "넉넉" even
    when /local/sysinfo correctly returns readiness=tight.
    """
    import re

    system_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "System.tsx"
    )
    source = system_path.read_text(encoding="utf-8")

    # SettingsPanel owns the host-capacity DataPanel; isolate its body so a
    # coincidental ``readiness`` mention elsewhere cannot satisfy the gate.
    panel_match = re.search(
        r"function SettingsPanel\b[\s\S]*?(?=\nfunction |\nexport |\Z)",
        source,
    )
    assert panel_match, "System.tsx must define SettingsPanel"
    panel = panel_match.group(0)
    assert re.search(r'mode\s*===\s*"basic"', panel), (
        "SettingsPanel must keep a mode === \"basic\" branch for readiness copy"
    )

    # The basic branch must pull readiness off the sysinfo payload object.
    assert re.search(
        r"(?:\?\.\s*readiness|\[['\"]readiness['\"]\]|\.readiness)\b",
        panel,
    ), "SettingsPanel must read data.readiness (or data?.readiness) from /local/sysinfo"

    # i18n key must be derived from that bucket: system.readiness.${readiness}
    # (or equivalent concat). A static system.readiness.plenty alone is the
    # regression the review called out.
    dynamic_key = (
        re.search(r"`system\.readiness\.\$\{[^}]+\}`", panel) is not None
        or re.search(r'["\']system\.readiness\.["\']\s*\+\s*\w+', panel) is not None
        or re.search(r"system\.readiness\.\$\{\s*readiness\s*\}", panel) is not None
    )
    assert dynamic_key, (
        "SettingsPanel basic branch must build system.readiness.<bucket> from the "
        "readiness field; hardcoding system.readiness.plenty alone is a regression"
    )


# ── mock_server.cjs ↔ real API shape parity (layout-rebuild capture surfaces) ──


def _mock_server_source() -> str:
    path = Path(__file__).resolve().parents[1] / "visual" / "mock_server.cjs"
    assert path.is_file(), f"missing visual mock: {path}"
    return path.read_text(encoding="utf-8")


def _extract_mock_json_object(source: str, pathname: str) -> Dict[str, Any]:
    """Best-effort extract of ``return json(res, {…})`` for a mock pathname.

    The visual mock is plain JS, not JSON. We locate the pathname branch and
    hand the object body to ``json.loads`` after a small identifier→string
    rewrite (unquoted keys, trailing commas, bare null/true/false stay valid
    enough for the three layout-rebuild endpoints we pin).
    """
    import json
    import re

    # Match both single-line and multi-line ``return json(res, {…})`` forms.
    # Non-greedy body up to the matching close is hard with nested braces, so
    # we brace-count from the first ``{`` after the pathname hit.
    path_idx = source.find(f'pathname === "{pathname}"')
    if path_idx < 0:
        path_idx = source.find(f"pathname === '{pathname}'")
    assert path_idx >= 0, f"mock_server.cjs missing branch for {pathname}"
    window = source[path_idx : path_idx + 4000]
    ret_idx = window.find("return json(res,")
    assert ret_idx >= 0, f"mock branch for {pathname} has no return json(res, …)"
    brace_start = window.find("{", ret_idx)
    assert brace_start >= 0, f"mock branch for {pathname} has no object body"
    depth = 0
    end = None
    for i, ch in enumerate(window[brace_start:], start=brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, f"unbalanced braces in mock branch for {pathname}"
    body = window[brace_start:end]
    # Quote bare identifiers used as object keys: ``action:`` → ``"action":``
    # but leave already-quoted keys and string values alone.
    quoted = re.sub(r"(?m)([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    # Drop trailing commas before } or ] (JS allows them; JSON does not).
    quoted = re.sub(r",\s*([}\]])", r"\1", quoted)
    try:
        return json.loads(quoted)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"failed to parse mock JSON for {pathname}: {exc}\n{quoted[:500]}"
        ) from exc


def _deep_key_set(value: Any, *, prefix: str = "") -> set[str]:
    """Collect dotted key paths for dict/list JSON-ish payloads."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            keys.add(path)
            keys |= _deep_key_set(v, prefix=path)
    elif isinstance(value, list) and value:
        # Sample first element only — mock arrays are homogeneous for these APIs.
        keys |= _deep_key_set(value[0], prefix=f"{prefix}[]" if prefix else "[]")
    return keys


def test_mock_permissions_pending_matches_real_api_shape(tmp_path: Path):
    """Mock /permissions/pending must match real action_label bridge values.

    Failure mode (capture 09): mock invents a label that does not tokenize to
    an i18n key (or drifts from _PERMISSION_ACTION_LABELS). Act.tsx prefers
    action_label for key derivation and t() has no defaultValue — raw keys ship.
    """
    import time

    from latticeai.api.permissions import (
        _PERMISSION_ACTION_LABELS,
        create_permissions_router,
    )

    mock = _extract_mock_json_object(_mock_server_source(), "/permissions/pending")
    assert "pending" in mock and "count" in mock
    assert isinstance(mock["pending"], dict) and mock["pending"]
    assert mock["count"] == len(mock["pending"])

    # Mock must include one mapped label (same string as the real map) AND one
    # unmapped fallback (raw English action).
    labels = {item.get("action_label") for item in mock["pending"].values()}
    actions = {item.get("action") for item in mock["pending"].values()}
    expected_read_label = _PERMISSION_ACTION_LABELS["read"]
    assert expected_read_label in labels, (
        f"mock must use real action_label for action=read "
        f"({expected_read_label!r}); got {labels!r}"
    )
    assert "read" in actions
    assert any(
        item.get("action") == "delete" and item.get("action_label") == "delete"
        for item in mock["pending"].values()
    ), "mock must include unmapped action=delete → action_label='delete' fallback"

    class _Cfg:
        discord_permission_webhook = ""
        discord_bot_token = ""
        discord_permission_channel = ""
        permission_monitor_secret = ""

    def require_admin(_request):
        return "admin@example.com", {}

    router, gateway = create_permissions_router(
        config=_Cfg(),
        data_dir=tmp_path,
        require_user=lambda _r: "admin@example.com",
        require_admin=require_admin,
        get_current_user=lambda _r: "admin@example.com",
    )
    # Seed mapped + unmapped actions exactly like the mock contract.
    now = time.time()
    gateway.local_approvals["hash-read"] = {
        "path": "/tmp/report.md",
        "action": "read",
        "user_email": "admin@example.com",
        "approved": False,
        "expires_at": now + 300,
        "token_hint": "perm-token",
    }
    gateway.local_approvals["hash-delete"] = {
        "path": "/tmp/legacy-cache.bin",
        "action": "delete",
        "user_email": "admin@example.com",
        "approved": False,
        "expires_at": now + 240,
        "token_hint": "perm-token-delete",
    }
    app = FastAPI()
    app.include_router(router)
    real = TestClient(app).get("/permissions/pending").json()

    mock_item_keys = set()
    for item in mock["pending"].values():
        mock_item_keys |= set(item.keys())
    real_item_keys = set()
    for item in real["pending"].values():
        real_item_keys |= set(item.keys())
    # Real response keys must be a superset of mock keys (mock ⊆ real).
    assert mock_item_keys.issubset(real_item_keys), (
        f"mock pending item keys {mock_item_keys} not ⊆ real {real_item_keys}"
    )
    assert {"pending", "count"}.issubset(real.keys())
    assert {"pending", "count"}.issubset(mock.keys())

    # Enum domain: mapped labels come from _PERMISSION_ACTION_LABELS; unmapped
    # fall back to the raw action string.
    for item in real["pending"].values():
        action = str(item.get("action") or "")
        expected = _PERMISSION_ACTION_LABELS.get(action, action)
        assert item.get("action_label") == expected, item
    read_item = next(v for v in real["pending"].values() if v.get("action") == "read")
    assert read_item["action_label"] == _PERMISSION_ACTION_LABELS["read"]
    delete_item = next(v for v in real["pending"].values() if v.get("action") == "delete")
    assert delete_item["action_label"] == "delete"
    # Contract: every pending item always carries a non-empty action string.
    for item in real["pending"].values():
        assert isinstance(item.get("action"), str) and item["action"], item


def _workspace_i18n_keys() -> set[str]:
    """Parse quoted keys from frontend/src/i18n/workspace.ts (ko + en blocks)."""
    import re

    i18n_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "i18n"
        / "workspace.ts"
    )
    source = i18n_path.read_text(encoding="utf-8")
    return set(re.findall(r'"((?:act\.approval\.action\.)[^"]+)"\s*:', source))


def test_permission_action_labels_have_matching_i18n_keys():
    """F1 contract: i18n keys are derived from ``action``, not action_label.

    Frontend (F1) builds:
      token = action.toLowerCase().replace(/[\\s-]+/g, '_')
      t(`act.approval.action.${token}`, { defaultValue: action_label || action })

    ``action_label`` is human-readable fallback only (Discord / missing key).
    Deriving keys from action_label fixed a pre-F1 bridge bug into the contract
    and left unmapped actions (e.g. delete) untested — capture 09 then showed
    the raw key ``act.approval.action.delete``.

    Cover every action PermissionGateway can surface: mapped labels (list /
    read / write) plus at least the unmapped ``delete`` exercised by the mock
    and real pending responses.
    """
    import re

    from latticeai.api.permissions import _PERMISSION_ACTION_LABELS

    i18n_keys = _workspace_i18n_keys()
    assert i18n_keys, "workspace.ts must define act.approval.action.* keys"

    # Mapped enum + unmapped actions that actually flow through pending.
    actions = set(_PERMISSION_ACTION_LABELS.keys()) | {"delete"}

    missing: list[str] = []
    for action in sorted(actions):
        # F1: key token comes from ``action``, not action_label.
        token = re.sub(r"[\s-]+", "_", str(action).lower())
        key = f"act.approval.action.{token}"
        if key not in i18n_keys:
            missing.append(f"action={action!r} → {key!r}")
    assert not missing, (
        "action-derived i18n keys missing from frontend/src/i18n/workspace.ts "
        "(F1 builds act.approval.action.<action>; t() has no defaultValue). "
        "Missing:\n  - "
        + "\n  - ".join(missing)
    )


def test_mock_activity_runs_and_health_summary_key_superset():
    """Mock keys for activity runs + health-summary ⊆ real router response keys.

    Failure mode: UI authored against a mock that invents fields (or omits
    issue_count) looks green in capture while production shows wrong counts.
    """
    mock_src = _mock_server_source()
    mock_runs = _extract_mock_json_object(mock_src, "/api/activity/runs")
    mock_health = _extract_mock_json_object(mock_src, "/admin/health-summary")

    # ── /api/activity/runs ──────────────────────────────────────────────
    class _Store:
        def list_combined_runs(self, *, limit=20, workspace_id=None):
            return {
                "runs": [
                    {
                        "id": "wf-run-approval",
                        "source": "workflow",
                        "title": "Agent Review Workflow",
                        "status": "awaiting_approval",
                        "started_at": "2026-06-06T12:05:00",
                        "finished_at": None,
                        "can_stop": False,
                        "can_resume": True,
                        "workflow_id": "wf-agent-review",
                    },
                    {
                        "id": "agent-run-1",
                        "source": "agent",
                        "title": "Summarize release",
                        "status": "ok",
                        "started_at": "2026-06-06T12:30:00",
                        "finished_at": "2026-06-06T12:31:00",
                        "can_stop": False,
                        "can_resume": False,
                        "agent_id": "agent:executor",
                    },
                ],
                "total": 2,
                "truncated": False,
            }

        def list_workflows(self, workspace_id=None):
            return {"workflows": []}

    store = _Store()
    service = AutomationIntelligenceService(store=store)
    runs_app = FastAPI()
    runs_app.include_router(
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
    real_runs = TestClient(runs_app).get("/api/activity/runs", params={"limit": 20}).json()

    mock_run_keys = _deep_key_set(mock_runs)
    real_run_keys = _deep_key_set(real_runs)
    # Top-level + first-row keys the capture UI reads must exist on both sides;
    # real may add fields the mock omits (superset).
    required_top = {"runs", "total", "truncated"}
    assert required_top.issubset(mock_runs.keys())
    assert required_top.issubset(real_runs.keys())
    mock_row_keys = set(mock_runs["runs"][0].keys()) if mock_runs.get("runs") else set()
    real_row_keys: set[str] = set()
    for row in real_runs.get("runs") or []:
        real_row_keys |= set(row.keys())
    assert mock_row_keys.issubset(real_row_keys), (
        f"mock activity-run row keys {mock_row_keys} not ⊆ real {real_row_keys}"
    )
    assert any(r.get("status") == "awaiting_approval" for r in mock_runs["runs"]), (
        "mock /api/activity/runs must include awaiting_approval for capture 09"
    )
    # Source domain must match product enum.
    for row in list(mock_runs["runs"]) + list(real_runs["runs"]):
        assert row.get("source") in {"agent", "workflow"}, row

    # ── /admin/health-summary ───────────────────────────────────────────
    live_users = {
        "admin@example.com": {"role": "admin", "disabled": False},
        "disabled@example.com": {"role": "user", "disabled": True},
    }
    health_app = FastAPI()
    health_app.include_router(
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
            product_hardening_status=lambda: {"startup": {"network_exposed": False}},
        )
    )
    real_health = TestClient(health_app).get("/admin/health-summary").json()

    mock_health_keys = set(mock_health.keys())
    real_health_keys = set(real_health.keys())
    assert mock_health_keys.issubset(real_health_keys), (
        f"mock health-summary keys {mock_health_keys} not ⊆ real {real_health_keys}"
    )
    assert {"status", "issue_count", "issues"}.issubset(real_health_keys)
    assert isinstance(real_health["issue_count"], int)
    assert real_health["issue_count"] == len(real_health["issues"])
    assert mock_health.get("status") in {"ok", "attention"}
    assert real_health.get("status") in {"ok", "attention"}
    # Mock is intentionally "attention" so capture 10 shows the non-ok layout.
    assert mock_health.get("status") == "attention"
    assert isinstance(mock_health.get("issue_count"), int)
    assert mock_health["issue_count"] == len(mock_health.get("issues") or [])

    # Silence unused helpers when only top-level keys matter for health.
    assert mock_run_keys  # parsed successfully
    assert real_run_keys


# ── orphan i18n key gate ────────────────────────────────────────────────────
#
# Runtime-assembled keys use ``t(`prefix.${...}`)``. Those prefixes are
# allowlisted so the gate does not false-positive on every dynamic key.
# Anything still unreferenced after the allowlist is an orphan.
#
# Baseline: tests/unit/fixtures/i18n_known_orphans.txt
#   - Section 1 = true legacy (orphans already present at git tag v10.6.3)
#   - Section 2 = layout-rebuild 2026-08 residual (NOT forever-frozen;
#     frontend should delete unused i18n entries, then remove them here).
#     Cap = 157 (legacy) + current Section 2 residual. Do NOT raise the cap
#     to bless new orphans — shrink Section 2 and lower the cap instead.

# Hard ceiling = 157 legacy + residual Section 2. Recounted after frontend
# round: Section 2 still has 11 keys (frontend did not delete them), so the
# honest ceiling is 168. Raising above that is a reject; lowering is required
# whenever Section 2 shrinks.
I18N_ORPHAN_FIXTURE_CAP = 168  # 157 legacy + 11 section-2 residual; lower when Section 2 shrinks

# Explicit allowlist for ``t(`prefix.${...}`)`` / concat assembly. Keep this
# list in the test file so reviews can see every runtime-prefix exception.
I18N_DYNAMIC_PREFIX_ALLOWLIST = (
    "act.approval.action.",
    "act.cadence.",
    "act.creates.",
    "act.recipe.",
    "act.runStatus.",
    "act.trigger.when.",
    "act.agentRole.",
    "brain.answerProof.confidence.",
    "brain.firstScreen.state.",
    "brain.garden.bed.",
    "brain.garden.empty.",
    "brain.headline.",
    "brain.ingest.",
    "brain.jobs.status.",
    "brain.living.state.",
    "brain.memoryTier.",
    "brain.proactive.status.",
    "brain.readiness.",
    "brain.depth.",
    "brain.depthTitle.",
    "brain.rings.",
    "capture.pipeline.step.",
    "flow.install.stage.",
    "flow.install.step.",
    "intelligence.action.",
    "intelligence.dim.",
    "intelligence.grade.",
    "library.model.status.",
    "shell.mode.",
    "shell.sync.",
    "system.permission.mode.",
    "system.permission.risk.",
    "system.readiness.",
    "ui.entity.",
    "ui.field.",
)


def _discover_defined_i18n_keys(repo: Path) -> set[str]:
    import re

    keys: set[str] = set()
    i18n_dir = repo / "frontend" / "src" / "i18n"
    for path in i18n_dir.glob("*.ts"):
        if path.stem in {"types", "registry"}:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'^\s+"([^"]+)":\s*"', text, re.M):
            keys.add(match.group(1))
    return keys


def _frontend_src_blob_excluding_i18n_defs(repo: Path) -> str:
    parts: List[str] = []
    src = repo / "frontend" / "src"
    for path in src.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        # Skip namespace definition tables — keys only appear there as defs.
        if path.parent.name == "i18n" and path.stem not in {"types", "registry"}:
            continue
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _discover_orphan_i18n_keys(repo: Path) -> set[str]:
    defined = _discover_defined_i18n_keys(repo)
    blob = _frontend_src_blob_excluding_i18n_defs(repo)
    orphans: set[str] = set()
    for key in defined:
        if any(key.startswith(prefix) for prefix in I18N_DYNAMIC_PREFIX_ALLOWLIST):
            continue
        if f'"{key}"' in blob or f"'{key}'" in blob or f"`{key}`" in blob:
            continue
        orphans.add(key)
    return orphans


def _load_known_orphan_baseline(repo: Path) -> set[str]:
    path = repo / "tests" / "unit" / "fixtures" / "i18n_known_orphans.txt"
    assert path.is_file(), f"missing orphan baseline fixture: {path}"
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line)
    return keys


def test_no_new_orphan_i18n_keys():
    """Keys defined in i18n but never referenced in frontend/src are orphans.

    Bidirectional freeze:
      - NEW orphans (panel deleted, keys left in i18n) fail.
      - STALE fixture entries (key re-wired or deleted from i18n) fail so the
        grandfather list must shrink — never only grow.
      - Fixture size is capped by I18N_ORPHAN_FIXTURE_CAP; raising the cap
        requires JUSTIFY comments in the fixture (see file header).
    """
    repo = Path(__file__).resolve().parents[2]
    orphans = _discover_orphan_i18n_keys(repo)
    known = _load_known_orphan_baseline(repo)

    new_orphans = sorted(orphans - known)
    assert not new_orphans, (
        "new orphan i18n keys (defined in frontend/src/i18n but unreferenced "
        "in frontend/src outside definition tables). Either wire them up, "
        "delete them from i18n, or — only when intentional — add them to "
        "tests/unit/fixtures/i18n_known_orphans.txt with a # JUSTIFY: line "
        "and raise I18N_ORPHAN_FIXTURE_CAP in this test file.\n"
        + "\n".join(f"  - {key}" for key in new_orphans)
    )

    stale = sorted(known - orphans)
    assert not stale, (
        "stale entries in tests/unit/fixtures/i18n_known_orphans.txt "
        "(no longer orphans — key was re-wired or removed from i18n). "
        "Remove them from the fixture (and lower I18N_ORPHAN_FIXTURE_CAP).\n"
        + "\n".join(f"  - {key}" for key in stale)
    )

    assert len(known) <= I18N_ORPHAN_FIXTURE_CAP, (
        f"orphan fixture has {len(known)} keys; cap is {I18N_ORPHAN_FIXTURE_CAP}. "
        "Do not grow the fixture without a # JUSTIFY: comment per key and an "
        "explicit raise of I18N_ORPHAN_FIXTURE_CAP in this file."
    )
    assert len(orphans) == len(known), (
        f"orphan set size {len(orphans)} != fixture size {len(known)}"
    )

    # Fixture header must document the growth policy (review gate).
    fixture_text = (
        repo / "tests" / "unit" / "fixtures" / "i18n_known_orphans.txt"
    ).read_text(encoding="utf-8")
    assert "GROWTH POLICY" in fixture_text
    assert "JUSTIFY" in fixture_text

    # Allowlist must stay explicit and non-empty so a wiped list is not silent.
    assert len(I18N_DYNAMIC_PREFIX_ALLOWLIST) >= 10
    assert all(p.endswith(".") for p in I18N_DYNAMIC_PREFIX_ALLOWLIST), (
        "dynamic prefixes must end with '.' so they only match assembled keys"
    )

    # Sanity: a clearly used key is never reported as orphan.
    assert "shell.route.brain" not in orphans
    assert "shell.localBadge" not in orphans


def test_orphan_gate_detects_deleted_panel_keys(tmp_path: Path):
    """Synthetic failure: drop all references to 8 keys → gate sees 8 new orphans."""
    repo = Path(__file__).resolve().parents[2]
    known = _load_known_orphan_baseline(repo)
    defined = _discover_defined_i18n_keys(repo)
    blob = _frontend_src_blob_excluding_i18n_defs(repo)

    # Only exact-referenced keys that are not dynamic-prefix-covered and not
    # already grandfathered. Dynamic-prefix keys never surface as orphans.
    def _is_exact_referenced(key: str) -> bool:
        return f'"{key}"' in blob or f"'{key}'" in blob or f"`{key}`" in blob

    def _is_dynamic(key: str) -> bool:
        return any(key.startswith(prefix) for prefix in I18N_DYNAMIC_PREFIX_ALLOWLIST)

    live = [
        key
        for key in sorted(defined)
        if _is_exact_referenced(key) and not _is_dynamic(key) and key not in known
    ]
    # Prefer a single panel namespace so the scenario matches "panel deleted".
    candidates = [k for k in live if k.startswith("act.")] or live
    assert len(candidates) >= 8, "need at least 8 live keys to simulate panel deletion"
    doomed = set(candidates[:8])

    scrubbed = blob
    for key in doomed:
        scrubbed = scrubbed.replace(f'"{key}"', '""').replace(f"'{key}'", "''")

    orphans: set[str] = set()
    for key in defined:
        if _is_dynamic(key):
            continue
        if f'"{key}"' in scrubbed or f"'{key}'" in scrubbed or f"`{key}`" in scrubbed:
            continue
        orphans.add(key)
    new_orphans = orphans - known
    assert doomed.issubset(new_orphans), (
        f"gate must flag scrubbed keys as new orphans; missing "
        f"{sorted(doomed - new_orphans)}"
    )


def test_prepare_stream_emits_load_before_smoke_test(monkeypatch):
    """Install UI stage order is install → download → load → validate.

    The stream must emit ``load`` before ``smoke_test`` (frontend maps
    smoke_test → validate). Without this gate the UI can reverse the order
    and every other suite still passes.
    """
    import asyncio
    import json
    import re

    from latticeai.services import model_loading

    class _Resolution:
        def __init__(self, model_id, engine=None, user_email=None, engine_aliases=None):
            self.load_id = model_id
            self.engine = engine
            self.user_email = user_email
            self.actual_current = None

        @classmethod
        def from_request(cls, model_id, *, engine=None, user_email=None, engine_aliases=None):
            return cls(model_id, engine=engine, user_email=user_email)

        def update_after_load(self, *, actual_current):
            self.actual_current = actual_current

        def to_dict(self):
            return {"load_id": self.load_id, "actual_current": self.actual_current}

    class _Router:
        def __init__(self):
            self.current_model_id = "local_mlx:some-model"
            self.load_calls = 0

        async def load_model(self, model_id, adapter_path, **kwargs):
            self.load_calls += 1
            self.current_model_id = model_id
            return f"loaded {model_id}"

    def _progress(stage, message, **kwargs):
        payload: Dict[str, Any] = {"stage": stage, "message": message}
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return payload

    async def _smoke(resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    router = _Router()
    deps = {
        "normalize_local_model_request": lambda mid, engine: mid,
        "_ModelResolution": _Resolution,
        "parse_model_ref": lambda mid: ("local_mlx", mid.split(":", 1)[-1])
        if ":" in mid
        else ("local_mlx", mid),
        "_model_runtime_compatibility": lambda model, engine=None: {"supported": True},
        "engine_installed": lambda provider: True,
        "_download_allowed": lambda allow: True,
        "_engine_install_block": lambda provider: None,
        "ensure_engine_ready": lambda provider: {"installed_now": False},
        "hf_model_ready": lambda model, engine: True,
        "_download_block": lambda provider, model: None,
        "download_hf_model": lambda model, engine, progress_emit=None: {
            "provider": engine,
            "model": model,
            "cached": True,
        },
        "ensure_ollama_server": lambda: None,
        "local_binary": lambda name: f"/usr/bin/{name}",
        "get_ollama_pulled_models": lambda: [],
        "ensure_vllm_server": lambda model: None,
        "ensure_llamacpp_server": lambda model: None,
        "get_lmstudio_models": lambda: [],
        "ensure_lmstudio_model": lambda model: {"instance_id": model},
        "get_current_user": lambda request: "me@local",
        "get_user_api_key": lambda email, provider: None,
        "router": router,
        "_smoke_test_loaded_model": _smoke,
        "MODEL_ENGINE_ALIASES": {},
        "_friendly_model_runtime_error": lambda exc, **kw: str(exc),
        "hf_model_dir": lambda model: Path("/tmp/models") / model,
        "model_download_progress_payload": _progress,
        "get_lmstudio_models_raw": lambda: [],
        "pull_ollama_model_with_progress": lambda *a, **k: None,
    }
    monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda state: deps)

    async def _collect() -> List[str]:
        stages: List[str] = []
        async for frame in model_loading.prepare_and_load_model_stream(
            "local_mlx:some-model",
            request=object(),
            runtime_state=object(),
            allow_download=True,
        ):
            # SSE frames: event: progress\ndata: {...}\n\n
            for match in re.finditer(r"data: (.+?)(?:\n\n|\n$)", frame, re.DOTALL):
                try:
                    payload = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                stage = payload.get("stage")
                if isinstance(stage, str) and stage:
                    stages.append(stage)
        return stages

    stages = asyncio.run(_collect())
    assert "load" in stages, f"stream must emit load; got {stages}"
    assert "smoke_test" in stages, f"stream must emit smoke_test; got {stages}"
    assert stages.index("load") < stages.index("smoke_test"), (
        f"load must precede smoke_test (frontend maps smoke_test→validate); got {stages}"
    )
    # After smoke_test comes done; never reverse load/validate again.
    assert stages.index("smoke_test") < stages.index("done") if "done" in stages else True
    assert router.load_calls == 1
