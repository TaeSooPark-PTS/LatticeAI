"""wp11: /api/local-agent/status and the approval-gated local file routes.

The status route claims every field it returns is *probed*. These tests hold it
to that: a working temp dir, a graph that answers, a graph that raises and a
data dir that cannot be created each produce a different mode, and the probe
file is asserted to be cleaned up afterwards.

The file routes use the genuine :class:`PermissionGateway` and the genuine
approval route — the first request mints a token, an administrator approves it,
and only then does the tool run. ``Accept-Language: en`` is sent so the
localized refusal messages are stable to assert on.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.local_files import create_local_files_router
from latticeai.api.permissions import create_permissions_router
from latticeai.services import folder_watch as folder_watch_module
from latticeai.tools import ToolError

USER = "owner@example.com"
ADMIN_HEADERS = {"X-Test-Admin": "true"}
EN = {"Accept-Language": "en"}


class _ToolCalls(list):
    """Records which tool a route dispatched to, mirroring the real envelope."""

    def __call__(self, fn, *args):
        self.append((fn.__name__, args))
        try:
            return {"status": "ok", "result": fn(*args)}
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


class _Graph:
    def __init__(self, *, stats_error: Optional[Exception] = None,
                 sources_error: Optional[Exception] = None,
                 sources: Optional[List[Dict[str, Any]]] = None) -> None:
        self._stats_error = stats_error
        self._sources_error = sources_error
        self._sources = sources or []

    def stats(self):
        if self._stats_error is not None:
            raise self._stats_error
        return {"nodes": {}, "edges": 0}

    def local_sources(self):
        if self._sources_error is not None:
            raise self._sources_error
        return {"sources": list(self._sources)}


class _Watcher:
    def __init__(self, active: Optional[Dict[str, Any]] = None) -> None:
        self.active = active or {}

    def status(self):
        return {"available": True, "error": "", "debounce_seconds": 5.0, "active": self.active}


class _Job:
    def __init__(self, job_id: str, status: str, remaining: int = 0) -> None:
        self.job_id = job_id
        self.status = status
        self._remaining = remaining

    def remaining_indices(self):
        return list(range(self._remaining))

    def as_dict(self):
        return {"job_id": self.job_id, "status": self.status}


class _Pipeline:
    def __init__(self, *, is_available: bool = True, job: Optional[_Job] = None,
                 summary: Optional[Dict[str, Any]] = None) -> None:
        self._available = is_available
        self._job = job
        self._summary = summary or {"status": "ok", "ingested": 0}
        self.folder_calls: List[Dict[str, Any]] = []
        self.background: List[Any] = []
        self.resumed: List[Any] = []

    def available(self):
        return self._available

    def list_background_jobs(self, limit: int = 20):
        return [{"job_id": "job-1", "limit": limit}]

    def get_background_job(self, job_id: str):
        return self._job if self._job and self._job.job_id == job_id else None

    def ingest_folder(self, path, **kwargs):
        self.folder_calls.append({"path": path, **kwargs})
        return dict(self._summary)

    def run_background_job(self, job_id, user_email=None):
        self.background.append((job_id, user_email))

    def resume_background_job(self, job_id, user_email=None):
        self.resumed.append((job_id, user_email))

    def multimodal_status(self):
        return {
            "enabled": True,
            "image": True,
            "audio": True,
            "video": False,
            "video_detail": "ffmpeg is not installed on this machine",
            "gates": {"multimodal": {"enabled": True}, "video": {"enabled": True}},
        }


def _require_admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def _client(tmp_path: Path, **kwargs: Any):
    """Build the router the way the app does, with the real permission gateway."""
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
    calls = _ToolCalls()
    options: Dict[str, Any] = {
        "require_user": lambda request: USER,
        "tool_response": calls,
        "permission_gateway": gateway,
        "knowledge_graph": None,
        "require_graph": lambda: None,
        "static_dir": tmp_path / "static",
        "local_kg_watcher": None,
    }
    options.update(kwargs)
    app = FastAPI()
    app.include_router(permissions_router)
    app.include_router(create_local_files_router(**options))
    return TestClient(app), calls


def _approve(client: TestClient, url: str, body: Dict[str, Any]) -> str:
    first = client.post(url, json=body, headers=EN)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["permission_required"] is True
    token = payload["approval_token"]
    approved = client.post("/permissions/approve/" + token, headers=ADMIN_HEADERS)
    assert approved.status_code == 200, approved.text
    return token


# ── /api/local-agent/status ──────────────────────────────────────────────────

def test_local_agent_status_probes_filesystem_graph_and_watcher(tmp_path):
    data_dir = tmp_path / "data"
    graph = _Graph(sources=[{"id": "src-1"}, {"id": "src-2"}])
    client, _ = _client(
        tmp_path,
        knowledge_graph=graph,
        local_kg_watcher=_Watcher(active={"src-1": {"root_path": "/data"}}),
        data_dir=data_dir,
    )

    body = client.get("/api/local-agent/status").json()

    assert body["mode"] == "online"
    assert body["online"] is True
    assert body["health"] == {
        "status": "online", "filesystem_access": True,
        "graph_reachable": True, "watcher_available": True,
    }
    assert body["handshake"]["ok"] is True
    assert body["handshake"]["latency_ms"] >= 0
    assert body["pid"] == os.getpid()
    assert body["folders"] == {"connected": 2, "watching": 1}
    assert body["connected_folders"] == 2
    assert body["watched_folders"] == 1
    assert body["error"] is None
    assert body["agent"]["python"]
    # The write→read→delete probe leaves nothing behind.
    assert list(data_dir.glob(".local_agent_probe_*")) == []


def test_local_agent_status_degrades_when_the_graph_raises(tmp_path):
    (tmp_path / "static").mkdir()
    graph = _Graph(stats_error=RuntimeError("database is locked"),
                   sources_error=RuntimeError("sources unavailable"))
    client, _ = _client(tmp_path, knowledge_graph=graph)

    body = client.get("/api/local-agent/status").json()

    assert body["mode"] == "degraded"
    assert body["online"] is False
    assert body["filesystem_access"] is True  # probed static_dir.parent instead
    assert body["health"]["graph_reachable"] is False
    assert body["watcher_available"] is False
    assert body["watch"] == {"available": False, "active": {}}
    assert body["sources"] == []
    assert "graph: database is locked" in body["error"]
    assert "sources: sources unavailable" in body["error"]
    assert body["handshake"]["ok"] is False


def test_local_agent_status_reports_error_when_the_probe_cannot_write(tmp_path):
    blocked = tmp_path / "data-is-a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    client, _ = _client(tmp_path, data_dir=blocked)

    body = client.get("/api/local-agent/status").json()

    assert body["mode"] == "error"
    assert body["filesystem_access"] is False
    assert body["health"]["graph_reachable"] is None  # no graph wired → not probed
    assert body["handshake"]["ok"] is False
    assert body["error"].startswith("filesystem:")


# ── /local/* approval dance ──────────────────────────────────────────────────

def test_local_list_runs_only_after_the_approval_is_granted(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "notes.md").write_text("노트", encoding="utf-8")
    client, calls = _client(tmp_path)

    # The GET variant only ever hands back a fresh permission request.
    prompt = client.get("/local/list", params={"path": str(root)}, headers=EN).json()
    assert prompt["permission_required"] is True
    assert prompt["action"] == "list"
    assert calls == []

    token = _approve(client, "/local/list", {"path": str(root)})
    approved = client.post(
        "/local/list",
        json={"path": str(root), "approved": True, "approval_token": token},
        headers=EN,
    )

    assert approved.status_code == 200
    names = [item["name"] for item in approved.json()["result"]["items"]]
    assert names == ["notes.md"]
    assert calls == [("local_list", (str(root),))]


def test_local_read_and_serve_share_the_read_approval(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("로컬 지식", encoding="utf-8")
    client, calls = _client(tmp_path)

    token = _approve(client, "/local/read", {"path": str(target)})
    read = client.post(
        "/local/read",
        json={"path": str(target), "approved": True, "approval_token": token},
        headers=EN,
    )
    assert read.status_code == 200
    assert read.json()["result"]["content"] == "로컬 지식"
    assert calls == [("local_read", (str(target),))]

    served = client.get(
        "/local/serve", params={"path": str(target), "approval_token": token}, headers=EN,
    )
    assert served.status_code == 200
    assert served.content.decode("utf-8") == "로컬 지식"


def test_local_serve_404s_for_a_path_that_is_not_a_file(tmp_path):
    missing = tmp_path / "gone.md"
    client, _ = _client(tmp_path)
    # Mint + approve a read for the path, so the 404 is about the file itself.
    token = _approve(client, "/local/read", {"path": str(missing)})

    response = client.get(
        "/local/serve", params={"path": str(missing), "approval_token": token}, headers=EN,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found."


def test_local_serve_refuses_without_an_approval(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("로컬 지식", encoding="utf-8")
    client, _ = _client(tmp_path)

    response = client.get("/local/serve", params={"path": str(target)}, headers=EN)

    assert response.status_code == 403


def test_local_write_requires_an_approval_bound_to_the_content(tmp_path):
    target = tmp_path / "written.md"
    client, calls = _client(tmp_path)
    body = {"path": str(target), "content": "승인된 내용"}

    token = _approve(client, "/local/write", body)
    tampered = client.post(
        "/local/write",
        json={**body, "content": "다른 내용", "approved": True, "approval_token": token},
        headers=EN,
    )
    assert tampered.status_code == 403
    assert not target.exists()

    approved = client.post(
        "/local/write",
        json={**body, "approved": True, "approval_token": token},
        headers=EN,
    )
    assert approved.status_code == 200
    assert target.read_text(encoding="utf-8") == "승인된 내용"
    assert calls == [("local_write", (str(target), "승인된 내용"))]


# ── ingestion routes ─────────────────────────────────────────────────────────

def test_ingestion_routes_report_503_without_a_pipeline(tmp_path):
    client, _ = _client(tmp_path)

    response = client.get("/api/ingestion/jobs", headers=EN)

    assert response.status_code == 503
    # This guard predates per-request language resolution and answers in the
    # product default language.
    assert response.json()["detail"] == "지식 그래프 수집이 꺼져 있습니다."


def test_multimodal_status_is_reachable_and_reports_the_refusal_reason(tmp_path):
    """FEATURE_STATUS promised this answer; until 11.5.2 nothing could ask for it.

    The pipeline has produced the payload since 11.2.0 — including *which* of
    the three reasons a video would be refused for — but no route called it, so
    "why was my video not indexed?" was unanswerable from the product.
    """
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline)

    response = client.get("/api/ingestion/multimodal", headers=EN)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["video"] is False
    assert body["video_detail"] == "ffmpeg is not installed on this machine"
    assert set(body["gates"]) == {"multimodal", "video"}


def test_multimodal_status_reports_503_without_a_pipeline(tmp_path):
    # Same guard as its sibling job routes: with ingestion off there is no
    # routing decision to describe, and saying "multi-modal is off" would be a
    # different, misleading claim.
    client, _ = _client(tmp_path)

    response = client.get("/api/ingestion/multimodal", headers=EN)

    assert response.status_code == 503


def test_ingestion_folder_refuses_conflicting_workspace_selectors(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline)

    response = client.post(
        "/api/ingestion/folder",
        headers={**EN, "X-Workspace-Id": "org:header"},
        json={"path": str(root), "workspace_id": "org:body"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace selectors must match."
    assert pipeline.folder_calls == []


def test_ingestion_folder_resolves_the_write_scope_and_schedules_the_job(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    resolved: List[Any] = []

    class _WorkspaceService:
        @staticmethod
        def resolve_write_scope(requested, user):
            resolved.append((requested, user))
            return "org:acme"

    pipeline = _Pipeline(summary={"status": "ok", "job_id": "job-9"})
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline,
                        workspace_service=_WorkspaceService())
    body = {"path": str(root), "background": True, "workspace_id": "org:acme"}

    token = _approve(client, "/api/ingestion/folder", body)
    response = client.post(
        "/api/ingestion/folder",
        json={**body, "approved": True, "approval_token": token},
        headers=EN,
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-9"
    # Resolved on both passes: the scope is settled before the approval gate.
    assert resolved == [("org:acme", USER), ("org:acme", USER)]
    assert pipeline.folder_calls[0]["workspace_id"] == "org:acme"
    assert pipeline.folder_calls[0]["owner"] == USER
    assert pipeline.background == [("job-9", USER)]


def test_ingestion_folder_refuses_a_workspace_the_user_cannot_write(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()

    class _WorkspaceService:
        @staticmethod
        def resolve_write_scope(_requested, _user):
            raise PermissionError("workspace write denied")

    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline,
                        workspace_service=_WorkspaceService())

    response = client.post(
        "/api/ingestion/folder",
        json={"path": str(root), "workspace_id": "org:forbidden"},
        headers=EN,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace write denied"
    assert pipeline.folder_calls == []


def test_ingestion_folder_requires_a_path(tmp_path):
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline)

    response = client.post("/api/ingestion/folder", json={"path": "   "}, headers=EN)

    assert response.status_code == 400
    assert response.json()["detail"] == "A path is required."


def test_resume_reports_a_job_that_is_already_running(tmp_path):
    pipeline = _Pipeline(job=_Job("job-1", "running", remaining=4))
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline)

    response = client.post("/api/ingestion/jobs/job-1/resume", headers=EN)

    assert response.status_code == 200
    assert response.json() == {
        "status": "already_running", "job_id": "job-1",
        "job": {"job_id": "job-1", "status": "running"},
    }
    assert pipeline.resumed == []


# ── watch-mode routes ────────────────────────────────────────────────────────

def test_watch_routes_report_503_when_the_service_fails_to_construct(tmp_path, monkeypatch):
    class _Exploding:
        def __init__(self, **_kwargs):
            raise RuntimeError("watch config is corrupt")

    monkeypatch.setattr(folder_watch_module, "FolderWatchService", _Exploding)
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline, data_dir=tmp_path / "data")

    response = client.get("/api/ingestion/watch", headers=EN)

    assert response.status_code == 503
    assert response.json()["detail"] == "The folder watch service is unavailable."


def test_watch_enable_requires_a_path(tmp_path):
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline, data_dir=tmp_path / "data")

    response = client.post("/api/ingestion/watch", json={"path": ""}, headers=EN)

    assert response.status_code == 400
    assert response.json()["detail"] == "A path is required."


def test_watch_enable_surfaces_a_failed_enable_as_a_400(tmp_path):
    not_a_folder = tmp_path / "notes.md"
    not_a_folder.write_text("파일", encoding="utf-8")
    pipeline = _Pipeline()
    client, _ = _client(tmp_path, ingestion_pipeline=pipeline, data_dir=tmp_path / "data")
    body = {"path": str(not_a_folder)}

    token = _approve(client, "/api/ingestion/watch", body)
    response = client.post(
        "/api/ingestion/watch",
        json={**body, "approved": True, "approval_token": token},
        headers=EN,
    )

    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"]
    assert client.get("/api/ingestion/watch", headers=EN).json()["enabled_count"] == 0
