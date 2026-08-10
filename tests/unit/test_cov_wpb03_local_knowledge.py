"""wpb03: local-folder indexing when the folder has already moved on.

A watched folder can be disconnected while a debounced reindex is in flight —
the user hits "stop watching" (or the source is re-registered) between the
timer firing and the index returning.  Both the success and the failure tail of
``_run_index`` then have nothing left to write status onto, and on an install
with no hook manager the failure tail has nothing to report to either.  The
route half covers the same "no hooks configured" wiring plus an index that
found nothing new, which is what every re-index of an unchanged folder returns.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.permissions import create_permissions_router
from latticeai.services.local_knowledge import (
    LocalKnowledgeWatcher,
    create_local_knowledge_router,
)

USER = "owner@example.com"
ADMIN_HEADERS = {"X-Test-Admin": "true"}


# ── fake watchdog ───────────────────────────────────────────────────────────


class _FakeObserver:
    def __init__(self) -> None:
        self.scheduled: List[Any] = []
        self.started = False
        self.stopped = False

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        return None


class _FakeHandlerBase:
    def __init__(self):
        self.base_initialised = True


def _watcher(monkeypatch, graph: Any, *, hooks: Any = None) -> LocalKnowledgeWatcher:
    events = ModuleType("watchdog.events")
    events.FileSystemEventHandler = _FakeHandlerBase
    observers = ModuleType("watchdog.observers")
    observers.Observer = _FakeObserver
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers)
    return LocalKnowledgeWatcher(lambda: graph, debounce_seconds=3600, hooks=hooks)


class _Hooks:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    def fire_hook(self, kind, event, **kwargs):
        self.events.append((kind, event, kwargs))
        return {"blocked": False}

    def payloads(self, event: str) -> List[Dict[str, Any]]:
        return [item[2].get("payload", {}) for item in self.events if item[1] == event]


class _DisconnectingGraph:
    """Indexes a folder, but the source is disconnected mid-flight."""

    def __init__(self, *, error: Optional[Exception] = None,
                 result: Optional[Dict[str, Any]] = None) -> None:
        self.error = error
        self.result = result or {"counts": {"indexed": 0, "deleted": 0}}
        self.watcher: Any = None
        self.indexed: List[Dict[str, Any]] = []
        self.sources: List[Dict[str, Any]] = []

    def local_sources(self) -> Dict[str, Any]:
        return {"sources": list(self.sources)}

    def index_local_folder(self, root: Path, **kwargs: Any) -> Dict[str, Any]:
        self.indexed.append({"root": root, **kwargs})
        # The user disconnects the folder while the index is running.
        self.watcher.stop_source(str(kwargs.get("source_id_override") or ""))
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _start(watcher: LocalKnowledgeWatcher, root: Path, source_id: str = "src-1") -> Dict[str, Any]:
    return watcher.start_source({
        "id": source_id,
        "root_path": str(root),
        "consent": {"approved_by": USER, "workspace_id": "org:one"},
    })


# ── watcher ─────────────────────────────────────────────────────────────────


def test_restoring_a_source_whose_folder_is_gone_counts_no_restore(monkeypatch, tmp_path):
    graph = _DisconnectingGraph()
    graph.sources = [
        {"id": "src-missing", "root_path": str(tmp_path / "deleted"), "watch_enabled": True},
        {"id": "src-off", "root_path": str(tmp_path), "watch_enabled": False},
    ]
    watcher = _watcher(monkeypatch, graph)

    restored = watcher.restore_enabled_sources()

    assert restored == {"restored": 0, "available": True}
    assert watcher.status()["active"] == {}


def test_an_index_that_finishes_after_a_disconnect_writes_no_stale_status(monkeypatch, tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    hooks = _Hooks()
    graph = _DisconnectingGraph(result={"counts": {"indexed": 3, "deleted": 0}})
    watcher = _watcher(monkeypatch, graph, hooks=hooks)
    graph.watcher = watcher
    assert _start(watcher, root)["watching"] is True

    watcher._run_index("src-1")

    assert watcher.status()["active"] == {}, "the disconnected source is not resurrected"
    assert graph.indexed[0]["workspace_id"] == "org:one"
    statuses = [payload.get("status") for payload in hooks.payloads("folder.reindex")]
    assert statuses == [None, "ok"], "pre-index then a completion that still reports ok"
    assert [item[1] for item in hooks.events if item[0] == "post_tool"] == [
        "tool.kg_ingest.local_folder"
    ]


def test_a_failed_index_after_a_disconnect_is_silent_without_hooks(monkeypatch, tmp_path, caplog):
    root = tmp_path / "notes"
    root.mkdir()
    graph = _DisconnectingGraph(error=ValueError("folder no longer readable"))
    watcher = _watcher(monkeypatch, graph, hooks=None)
    graph.watcher = watcher
    assert _start(watcher, root)["watching"] is True

    with caplog.at_level("WARNING"):
        watcher._run_index("src-1")

    assert watcher.status()["active"] == {}
    assert "folder no longer readable" in caplog.text


# ── routes ──────────────────────────────────────────────────────────────────


class _RouteGraph:
    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls: List[Dict[str, Any]] = []

    def local_sources(self) -> Dict[str, Any]:
        return {"sources": []}

    def index_local_folder(self, root: Path, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"root": root, **kwargs})
        error = self.behaviour.get("index_error")
        if error is not None:
            raise error
        return dict(self.behaviour.get("index_result", {"status": "ok"}))


def _require_admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def _client(tmp_path: Path, graph: Any, **kwargs: Any) -> TestClient:
    permissions_router, gateway = create_permissions_router(
        config=SimpleNamespace(
            discord_permission_webhook="",
            discord_bot_token="",
            discord_permission_channel="",
            permission_monitor_secret="",
            port=4825,
        ),
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


def _approved_index(client: TestClient, body: Dict[str, Any]):
    """Run the real approval dance, then re-post with the minted token."""
    first = client.post("/knowledge-graph/local/index", json=body)
    assert first.status_code == 200, first.text
    token = first.json()["approval_token"]
    approved = client.post("/permissions/approve/" + token, headers=ADMIN_HEADERS)
    assert approved.status_code == 200, approved.text
    return client.post(
        "/knowledge-graph/local/index",
        json={**body, "approved": True, "approval_token": token},
    )


def test_an_index_failure_without_hooks_is_still_a_400(tmp_path):
    graph = _RouteGraph(index_error=ValueError("path is outside the approved root"))
    client = _client(tmp_path, graph)

    response = _approved_index(client, {"path": str(tmp_path / "docs")})

    assert response.status_code == 400
    assert response.json()["detail"] == "path is outside the approved root"


def test_an_index_that_changed_nothing_fires_no_ingestion_hook(tmp_path):
    graph = _RouteGraph(index_result={
        "status": "ok",
        "source": {"id": "src-1"},
        "counts": {"indexed": 0, "deleted": 0},
    })
    hooks = _Hooks()
    client = _client(tmp_path, graph, hooks=hooks)

    response = _approved_index(client, {"path": str(tmp_path / "docs")})

    assert response.status_code == 200
    assert response.json()["counts"] == {"indexed": 0, "deleted": 0}
    assert [payload.get("status") for payload in hooks.payloads("folder.index")] == [None, "ok"]
    assert [item[1] for item in hooks.events if item[0] == "post_tool"] == []
