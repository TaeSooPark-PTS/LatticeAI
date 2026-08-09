"""wp11: the /knowledge-graph/local/* routes, gated by the real approval flow.

Every route that touches a user's disk goes through the genuine
:class:`PermissionGateway`: the first (unapproved) request mints a token, an
administrator approves it through ``POST /permissions/approve/{token}``, and
only the second request reaches the graph. Nothing here hand-writes an
approval record, so the tests fail if the dance itself regresses.

What was never exercised before: the roots/sources/health/watch-status reads,
both the with-watcher and without-watcher shapes of every payload, the 404 for
stopping an unknown source, the 400s that ``ValueError`` becomes, the
workspace-less write scope, the source-reuse lookup on re-index, the failed
index (with its error hook), and the watch start/stop that follows a
successful index.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.permissions import create_permissions_router
from latticeai.services.local_knowledge import create_local_knowledge_router

USER = "owner@example.com"
ADMIN_HEADERS = {"X-Test-Admin": "true"}


class _Graph:
    """Fake knowledge graph: records calls, replays scripted answers."""

    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls: List[Dict[str, Any]] = []

    def _maybe_raise(self, name: str) -> None:
        error = self.behaviour.get(name + "_error")
        if error is not None:
            raise error

    def discover_local_roots(self):
        return {"roots": [{"path": "/Users/tester/Documents", "label": "Documents"}]}

    def local_sources(self):
        return {"sources": list(self.behaviour.get("sources", []))}

    def local_source_health(self, error_samples: int = 3):
        self.calls.append({"call": "health", "error_samples": error_samples})
        return {"folders": [{"id": "src-1"}, {"id": "src-2"}]}

    def vector_freshness(self):
        self._maybe_raise("vector_freshness")
        return {"status": "fresh", "pending_items": 0, "total_items": 12}

    def set_local_source_watch(self, source_id: str, enabled: bool):
        self._maybe_raise("set_watch")
        self.calls.append({"call": "set_watch", "source_id": source_id, "enabled": enabled})

    def preview_local_tree(self, root: Path, max_items: int = 200):
        self._maybe_raise("tree")
        self.calls.append({"call": "tree", "root": root, "max_items": max_items})
        return {"path": str(root), "items": [], "max_items": max_items}

    def audit_local_folder(self, root: Path, include_ocr: bool = False, max_files: int = 0):
        self._maybe_raise("audit")
        self.calls.append({"call": "audit", "root": root, "include_ocr": include_ocr,
                           "max_files": max_files})
        return {"summary": {"total_files": 0}}

    def index_local_folder(self, root: Path, **kwargs: Any):
        self._maybe_raise("index")
        self.calls.append({"call": "index", "root": root, **kwargs})
        return dict(self.behaviour.get("index_result", {"status": "ok"}))


class _Watcher:
    def __init__(self, active: Optional[Dict[str, Any]] = None) -> None:
        self.active = active or {}
        self.started: List[Dict[str, Any]] = []
        self.stopped: List[str] = []

    def status(self):
        return {"available": True, "error": "", "debounce_seconds": 5.0, "active": self.active}

    def start_source(self, source):
        self.started.append(source)
        return {"watching": True, "source_id": source.get("id")}

    def stop_source(self, source_id):
        self.stopped.append(source_id)
        return {"stopped": True, "source_id": source_id}


class _Hooks:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def fire_hook(self, kind, event, **kwargs):
        self.events.append((kind, event, kwargs))
        return {"blocked": False}

    def payloads(self, event: str) -> List[Dict[str, Any]]:
        return [item[2].get("payload", {}) for item in self.events if item[1] == event]


def _require_admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def _client(tmp_path: Path, graph: _Graph, **kwargs: Any):
    config = SimpleNamespace(
        discord_permission_webhook="",
        discord_bot_token="",
        discord_permission_channel="",
        permission_monitor_secret="",
        port=4825,
    )
    permissions_router, gateway = create_permissions_router(
        config=config,
        data_dir=tmp_path / "perm",
        require_user=lambda request: USER,
        require_admin=_require_admin,
        get_current_user=lambda request: USER,
    )
    app = FastAPI()
    app.include_router(permissions_router)
    app.include_router(create_local_knowledge_router(
        get_graph=lambda: graph,
        require_graph=lambda: None,
        require_user=lambda request: USER,
        require_local_user=gateway.require_local_user,
        local_permission_response=gateway.local_permission_response,
        require_local_approval=gateway.require_local_approval,
        **kwargs,
    ))
    return TestClient(app)


def _approve(client: TestClient, url: str, body: Dict[str, Any]) -> str:
    """Run the real dance: request → permission_required → admin approval."""
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["permission_required"] is True
    token = payload["approval_token"]
    approved = client.post("/permissions/approve/" + token, headers=ADMIN_HEADERS)
    assert approved.status_code == 200, approved.text
    return token


# ── reads ────────────────────────────────────────────────────────────────────

def test_roots_and_sources_report_watch_state(tmp_path):
    graph = _Graph(sources=[{"id": "src-1"}, {"id": "src-2"}])
    watcher = _Watcher(active={"src-1": {"root_path": "/data", "last_error": None}})
    client = _client(tmp_path, graph, watcher=watcher)

    roots = client.get("/knowledge-graph/local/roots")
    assert roots.status_code == 200
    assert roots.json()["roots"][0]["label"] == "Documents"

    sources = client.get("/knowledge-graph/local/sources").json()
    assert sources["watch"]["available"] is True
    by_id = {item["id"]: item for item in sources["sources"]}
    assert by_id["src-1"]["watch_active"] is True
    assert by_id["src-1"]["watch_status"]["root_path"] == "/data"
    assert by_id["src-2"]["watch_active"] is False
    assert by_id["src-2"]["watch_status"] is None

    status = client.get("/knowledge-graph/local/watch/status").json()
    assert status["available"] is True
    assert list(status["active"]) == ["src-1"]


def test_sources_and_watch_status_without_a_watcher(tmp_path):
    graph = _Graph(sources=[{"id": "src-1"}])
    client = _client(tmp_path, graph)

    sources = client.get("/knowledge-graph/local/sources").json()
    assert sources["watch"] == {"available": False, "active": {}}
    assert sources["sources"][0]["watch_active"] is False

    status = client.get("/knowledge-graph/local/watch/status").json()
    assert status == {"available": False, "active": {}, "error": "watcher unavailable"}


def test_health_marks_watched_folders_and_carries_global_vector_freshness(tmp_path):
    graph = _Graph()
    watcher = _Watcher(active={"src-2": {"root_path": "/data"}})
    client = _client(tmp_path, graph, watcher=watcher)

    payload = client.get("/knowledge-graph/local/health", params={"error_samples": 5}).json()

    assert graph.calls[0] == {"call": "health", "error_samples": 5}
    folders = {item["id"]: item for item in payload["folders"]}
    assert folders["src-2"]["watch_active"] is True
    assert folders["src-1"]["watch_active"] is False
    assert payload["watch"]["available"] is True
    assert payload["vector_freshness_global"]["status"] == "fresh"


def test_health_degrades_instead_of_500_when_vector_freshness_fails(tmp_path):
    graph = _Graph(vector_freshness_error=RuntimeError("vector index missing"))
    client = _client(tmp_path, graph)

    payload = client.get("/knowledge-graph/local/health").json()

    assert payload["watch"] == {"available": False, "active": {}}
    assert payload["vector_freshness_global"] == {
        "status": "unavailable", "detail": "vector index missing",
        "pending_items": 0, "total_items": 0,
    }


# ── watch stop ───────────────────────────────────────────────────────────────

def test_watch_stop_clears_the_stored_flag_and_the_live_watch(tmp_path):
    graph = _Graph()
    watcher = _Watcher()
    client = _client(tmp_path, graph, watcher=watcher)

    body = client.post("/knowledge-graph/local/watch/stop", json={"source_id": "src-1"}).json()

    assert body == {"status": "ok", "watch": {"stopped": True, "source_id": "src-1"}}
    assert graph.calls[0] == {"call": "set_watch", "source_id": "src-1", "enabled": False}
    assert watcher.stopped == ["src-1"]


def test_watch_stop_without_a_watcher_still_clears_the_flag(tmp_path):
    graph = _Graph()
    client = _client(tmp_path, graph)

    body = client.post("/knowledge-graph/local/watch/stop", json={"source_id": "src-1"}).json()

    assert body == {"status": "ok", "watch": {"stopped": False, "source_id": "src-1"}}


def test_watch_stop_reports_an_unknown_source_as_404(tmp_path):
    graph = _Graph(set_watch_error=ValueError("unknown local source: src-x"))
    client = _client(tmp_path, graph, watcher=_Watcher())

    response = client.post("/knowledge-graph/local/watch/stop", json={"source_id": "src-x"})

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown local source: src-x"


# ── tree + audit ─────────────────────────────────────────────────────────────

def test_tree_requires_an_approval_before_listing(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    graph = _Graph()
    client = _client(tmp_path, graph)
    body = {"path": str(root), "max_items": 5}

    token = _approve(client, "/knowledge-graph/local/tree", body)
    assert graph.calls == []  # the unapproved call never reached the graph

    approved = client.post(
        "/knowledge-graph/local/tree",
        json={**body, "approved": True, "approval_token": token},
    )
    assert approved.status_code == 200
    assert approved.json()["max_items"] == 5
    assert graph.calls[0] == {"call": "tree", "root": Path(str(root)), "max_items": 5}


def test_tree_turns_a_rejected_path_into_a_400(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    graph = _Graph(tree_error=ValueError("folder is outside the allowed roots"))
    client = _client(tmp_path, graph)
    body = {"path": str(root)}
    token = _approve(client, "/knowledge-graph/local/tree", body)

    response = client.post(
        "/knowledge-graph/local/tree",
        json={**body, "approved": True, "approval_token": token},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "folder is outside the allowed roots"


def test_audit_requires_an_approval_and_reports_bad_paths(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    graph = _Graph()
    client = _client(tmp_path, graph)
    body = {"path": str(root), "include_ocr": True, "max_files": 7}
    token = _approve(client, "/knowledge-graph/local/audit", body)

    approved = client.post(
        "/knowledge-graph/local/audit",
        json={**body, "approved": True, "approval_token": token},
    )
    assert approved.status_code == 200
    assert approved.json()["summary"]["total_files"] == 0
    assert graph.calls[0]["include_ocr"] is True
    assert graph.calls[0]["max_files"] == 7

    failing = _Graph(audit_error=ValueError("unreadable folder"))
    failing_client = _client(tmp_path, failing)
    failing_token = _approve(failing_client, "/knowledge-graph/local/audit", body)
    response = failing_client.post(
        "/knowledge-graph/local/audit",
        json={**body, "approved": True, "approval_token": failing_token},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "unreadable folder"


# ── index ────────────────────────────────────────────────────────────────────

def test_index_reuses_the_existing_source_for_the_same_folder_and_scope(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    graph = _Graph(
        sources=[
            {"id": "src-blank", "root_path": "   ", "consent": {}},
            {"id": "src-other-scope", "root_path": str(root),
             "consent": {"workspace_id": "org:acme"}},
            {"id": "src-other-path", "root_path": str(other), "consent": {}},
            {"id": "src-match", "root_path": str(root), "consent": {"workspace_id": "personal"}},
        ],
        index_result={"status": "ok", "source": {"id": "src-match"},
                      "counts": {"indexed": 0, "deleted": 0}},
    )
    client = _client(tmp_path, graph)
    body = {"path": str(root)}
    token = _approve(client, "/knowledge-graph/local/index", body)

    response = client.post(
        "/knowledge-graph/local/index",
        json={**body, "approved": True, "approval_token": token},
    )

    assert response.status_code == 200
    index_call = next(call for call in graph.calls if call["call"] == "index")
    assert index_call["source_id_override"] == "src-match"
    # No workspace_service is wired, so the requested scope passes through as
    # the personal Brain rather than being resolved.
    assert index_call["workspace_id"] == "personal"
    assert index_call["consent"] == {"approved_by": USER, "workspace_id": "personal"}
    assert "watch" not in response.json()


def test_index_failure_becomes_a_400_and_fires_the_error_hook(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    graph = _Graph(index_error=ValueError("folder belongs to another workspace"))
    hooks = _Hooks()
    client = _client(tmp_path, graph, hooks=hooks)
    body = {"path": str(root)}
    token = _approve(client, "/knowledge-graph/local/index", body)

    response = client.post(
        "/knowledge-graph/local/index",
        json={**body, "approved": True, "approval_token": token},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "folder belongs to another workspace"
    started, finished = hooks.payloads("folder.index")
    assert started["trigger"] == "connect"
    assert finished["status"] == "error"
    assert finished["error"] == "folder belongs to another workspace"


def test_index_starts_and_stops_the_watch_with_the_consent_scope(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    watcher = _Watcher()

    class _WorkspaceService:
        @staticmethod
        def resolve_write_scope(requested, user):
            assert user == USER
            return requested or "personal"

    graph = _Graph(
        index_result={"status": "ok", "source": {"id": "src-1"},
                      "index": {"indexed": 3}, "counts": {"indexed": 3, "deleted": 0}},
    )
    hooks = _Hooks()
    client = _client(tmp_path, graph, watcher=watcher, hooks=hooks,
                     workspace_service=_WorkspaceService())
    body = {"path": str(root), "watch_enabled": True, "consent": {"scope": "folder"}}
    token = _approve(client, "/knowledge-graph/local/index", body)

    watched = client.post(
        "/knowledge-graph/local/index",
        headers={"X-Workspace-Id": "org:acme"},
        json={**body, "approved": True, "approval_token": token},
    )

    assert watched.status_code == 200
    assert watched.json()["watch"] == {"watching": True, "source_id": "src-1"}
    assert watcher.started[0]["workspace_id"] == "org:acme"
    assert watcher.started[0]["consent"] == {
        "scope": "folder", "approved_by": USER, "workspace_id": "org:acme",
    }
    ingest_events = [item for item in hooks.events if item[1] == "tool.kg_ingest.local_folder"]
    assert ingest_events[0][2]["payload"]["source_id"] == "src-1"

    # The same route turns the watch back off when the caller opts out.
    stop_body = {"path": str(root), "watch_enabled": False}
    stop_token = _approve(client, "/knowledge-graph/local/index", stop_body)
    stopped = client.post(
        "/knowledge-graph/local/index",
        json={**stop_body, "approved": True, "approval_token": stop_token},
    )
    assert stopped.json()["watch"] == {"stopped": True, "source_id": "src-1"}
    assert watcher.stopped == ["src-1"]


def test_index_refuses_a_workspace_the_user_cannot_write(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()

    class _WorkspaceService:
        @staticmethod
        def resolve_write_scope(_requested, _user):
            raise PermissionError("workspace write denied")

    graph = _Graph()
    client = _client(tmp_path, graph, workspace_service=_WorkspaceService())

    response = client.post(
        "/knowledge-graph/local/index",
        headers={"X-Workspace-Id": "org:forbidden"},
        json={"path": str(root), "approved": True, "approval_token": "irrelevant"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace write denied"
    assert graph.calls == []  # refused before the approval check and the scan
