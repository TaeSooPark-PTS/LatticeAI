"""Opt-in folder watch mode tests (backlog #8).

Covers: default-off behavior (no polling without stored opt-in), explicit
enable with a baseline snapshot (no bulk re-ingest), incremental ingest of
new/changed files through the normal pipeline (filters + provenance +
workspace scope respected), deleted files counted but never removed from the
graph, config persistence across service restarts with restore() honoring
only the stored opt-in, disable, and the API surface (approval dance + auth).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from latticeai.api.local_files import create_local_files_router
from latticeai.services.folder_watch import FolderWatchService


def _pipeline(tmp_path: Path) -> IngestionPipeline:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    return IngestionPipeline(store)


def _service(tmp_path: Path, pipeline=None) -> FolderWatchService:
    return FolderWatchService(
        pipeline=pipeline or _pipeline(tmp_path),
        config_path=tmp_path / "state" / "folder_watch.json",
        interval_seconds=3600,  # tests drive scan_once synchronously
    )


def _corpus(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("첫 노트: 폴더 watch 테스트 문서.", encoding="utf-8")
    (root / "readme.md").write_text("# Readme\nwatch mode baseline.", encoding="utf-8")


def test_watch_is_off_by_default(tmp_path):
    service = _service(tmp_path)
    status = service.status()
    assert status["enabled_count"] == 0
    assert status["polling"] is False
    assert status["watches"] == []
    # restore() with no stored opt-in never starts anything.
    assert service.restore() == {"restored": 0, "polling": False}
    assert service.status()["polling"] is False


def test_enable_snapshots_baseline_without_reingesting(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _pipeline(tmp_path)
    pipeline.ingest_folder(root)  # the folder was previously ingested
    docs_before = pipeline._kg.stats()["nodes"].get("Document", 0)

    service = _service(tmp_path, pipeline)
    result = service.enable(root, owner="user@example.com", workspace_id="personal")
    assert result["status"] == "ok"
    assert result["watch"]["enabled"] is True
    assert result["watch"]["tracked_files"] == 2

    # Baseline scan: nothing changed → nothing ingested.
    scan = service.scan_once(result["watch"]["id"])
    assert scan["status"] == "ok"
    assert scan["new"] == scan["changed"] == scan["ingested"] == 0
    assert pipeline._kg.stats()["nodes"].get("Document", 0) == docs_before


def test_scan_ingests_new_and_changed_files_with_scope(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _pipeline(tmp_path)
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com", workspace_id="personal")["watch"]

    (root / "new-note.md").write_text("새 파일: watch가 이것을 수집해야 한다.", encoding="utf-8")
    (root / "notes.txt").write_text("첫 노트: 내용이 바뀌었다 — 재수집 대상.", encoding="utf-8")
    (root / "skipped.log").write_text("extension filter must skip this", encoding="utf-8")

    scan = service.scan_once(watch["id"])
    assert scan["status"] == "ok"
    assert scan["new"] == 1
    assert scan["changed"] == 1
    assert scan["ingested"] == 2
    assert scan["failed"] == 0

    # Both files landed through the normal pipeline with provenance + scope.
    store = pipeline._kg
    docs = store.find_documents_by_uri_prefix(str(root))
    by_uri = {doc["source_uri"]: doc for doc in docs}
    assert str(root / "new-note.md") in by_uri
    scopes = store.workspaces_of([doc["id"] for doc in docs])
    assert all(scope == "personal" for scope in scopes.values())

    # Idempotent: a second scan with no edits ingests nothing.
    again = service.scan_once(watch["id"])
    assert again["new"] == again["changed"] == again["ingested"] == 0


def test_deleted_files_are_counted_but_not_removed_from_graph(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _pipeline(tmp_path)
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com")["watch"]
    (root / "new-note.md").write_text("잠깐 존재하는 파일", encoding="utf-8")
    service.scan_once(watch["id"])
    docs_before = pipeline._kg.stats()["nodes"].get("Document", 0)

    (root / "new-note.md").unlink()
    scan = service.scan_once(watch["id"])
    assert scan["removed"] == 1
    assert pipeline._kg.stats()["nodes"].get("Document", 0) == docs_before


def test_config_persists_and_restore_honors_stored_opt_in(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _pipeline(tmp_path)
    first = _service(tmp_path, pipeline)
    first.enable(root, owner="user@example.com")
    first.stop_all()

    # New process: config survives, restore resumes the stored opt-in.
    second = FolderWatchService(
        pipeline=pipeline,
        config_path=tmp_path / "state" / "folder_watch.json",
        interval_seconds=3600,
    )
    status = second.status()
    assert status["enabled_count"] == 1
    assert status["polling"] is False  # constructing never starts polling
    restored = second.restore()
    assert restored == {"restored": 1, "polling": True}
    assert second.status()["polling"] is True
    second.stop_all()


def test_disable_removes_watch_and_stops(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    service = _service(tmp_path)
    watch = service.enable(root, owner="user@example.com")["watch"]
    assert service.status()["enabled_count"] == 1

    result = service.disable(watch_id=watch["id"])
    assert result["status"] == "ok"
    assert service.status()["enabled_count"] == 0
    assert service.disable(watch_id=watch["id"]) == {"status": "not_found"}
    # The stored consent record is gone → a restart cannot resume it.
    assert service.restore() == {"restored": 0, "polling": False}
    service.stop_all()


# ── API surface ──────────────────────────────────────────────────────────────

class _Gateway:
    """Permissive permission-gateway stub recording the approval dance."""

    def __init__(self):
        self.approvals = []

    def require_local_user(self, request):
        return "user@example.com"

    def local_permission_response(self, path, action, user, content=None):
        return {"status": "permission_required", "path": path, "action": action,
                "approval_token": "tok"}

    def require_local_approval(self, *, token, path, action, user_email, content=None):
        if token != "tok":
            raise Exception("bad token")
        self.approvals.append((path, action, user_email))


def _client(tmp_path):
    pipeline = _pipeline(tmp_path)
    service = _service(tmp_path, pipeline)
    gateway = _Gateway()
    app = FastAPI()
    app.include_router(create_local_files_router(
        require_user=lambda request: "user@example.com",
        tool_response=lambda fn, *a, **k: fn(*a, **k),
        permission_gateway=gateway,
        knowledge_graph=pipeline._kg,
        require_graph=lambda: None,
        static_dir=tmp_path,
        local_kg_watcher=None,
        ingestion_pipeline=pipeline,
        data_dir=tmp_path / "data",
        folder_watch=service,
    ))
    return TestClient(app), service, gateway


def test_watch_api_enable_requires_approval_dance(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    client, service, gateway = _client(tmp_path)

    # Without approval: permission_required payload, nothing enabled.
    r = client.post("/api/ingestion/watch", json={"path": str(root)})
    assert r.status_code == 200
    assert r.json()["status"] == "permission_required"
    assert service.status()["enabled_count"] == 0

    r = client.post("/api/ingestion/watch", json={
        "path": str(root), "approved": True, "approval_token": "tok",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["watch"]["enabled"] is True
    assert gateway.approvals == [(str(root), "read", "user@example.com")]

    status = client.get("/api/ingestion/watch").json()
    assert status["enabled_count"] == 1
    assert status["watches"][0]["path"] == str(root.resolve())

    r = client.delete("/api/ingestion/watch", params={"watch_id": body["watch"]["id"]})
    assert r.status_code == 200
    assert client.get("/api/ingestion/watch").json()["enabled_count"] == 0
    service.stop_all()


def test_watch_api_delete_unknown_watch_404(tmp_path):
    client, service, _ = _client(tmp_path)
    assert client.delete("/api/ingestion/watch", params={"watch_id": "nope"}).status_code == 404
    assert client.delete("/api/ingestion/watch").status_code == 400
    service.stop_all()
