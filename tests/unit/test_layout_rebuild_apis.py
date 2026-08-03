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

    attention = _build_router(disabled_users=True, network_exposed=True).get(
        "/admin/health-summary"
    ).json()
    assert attention["status"] == "attention"
    assert attention["issue_count"] >= 1
    areas = {issue["area"] for issue in attention["issues"]}
    assert "users" in areas
    assert "runtime_trust" in areas
