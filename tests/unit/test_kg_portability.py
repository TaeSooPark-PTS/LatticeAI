"""v3.6.0 Knowledge Graph portability tests — export/import + backup/restore.

The graph is the user's durable asset, so it must round-trip locally with no
cloud. Covers: versioned logical export, dry-run, merge import equivalence,
binary backup -> clear -> restore recovery, integrity check, and route auth
(export = user, import/backup/restore = admin).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.portability as portability_module
from knowledge_graph import GRAPH_SCHEMA_VERSION, KnowledgeGraphStore
from latticeai.api.portability import create_portability_router
from lattice_brain.ingestion import IngestionItem, IngestionPipeline
from lattice_brain.portability import KGPortabilityService


def _seeded(tmp_path: Path, tag: str) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / f"{tag}.sqlite", tmp_path / f"{tag}-blobs")
    pipe = IngestionPipeline(store)
    pipe.ingest(IngestionItem(source_type="web_url", title="A", text="alpha body about graphs", source_uri="u1"))
    pipe.ingest(IngestionItem(source_type="note", title="B", text="beta body with a TODO decide"))
    return store


def _node_totals(store: KnowledgeGraphStore) -> int:
    return sum(store.stats().get("nodes", {}).values())


def test_export_has_versioned_header(tmp_path):
    svc = KGPortabilityService(knowledge_graph=_seeded(tmp_path, "kg"), data_dir=tmp_path / "data")
    art = svc.export()
    h = art["header"]
    assert h["format"] == "latticeai.kg.export"
    assert h["graph_schema_version"] == GRAPH_SCHEMA_VERSION
    assert "kg_v2_schema_version" in h and "projection_version" in h and "embed_dim" in h
    assert h["exported_at"]
    assert art["counts"]["nodes"] >= 1
    assert len(art["provenance"]) >= 2


def test_logical_export_import_roundtrip(tmp_path):
    src = _seeded(tmp_path, "src")
    art = KGPortabilityService(knowledge_graph=src, data_dir=tmp_path / "d1").export()

    dst = KnowledgeGraphStore(tmp_path / "dst.sqlite", tmp_path / "dst-blobs")
    svc2 = KGPortabilityService(knowledge_graph=dst, data_dir=tmp_path / "d2")

    # dry-run writes nothing
    plan = svc2.import_data(art, dry_run=True)
    assert plan["nodes"] == art["counts"]["nodes"]
    assert _node_totals(dst) == 0

    res = svc2.import_data(art, mode="merge")
    assert res.get("imported") is True
    assert _node_totals(dst) == _node_totals(src)
    # provenance survived the round-trip
    src_node = src.export_graph_data()["provenance"][0]["node_id"]
    assert dst.get_provenance(src_node) is not None


def test_import_refuses_newer_schema(tmp_path):
    src = _seeded(tmp_path, "src")
    art = KGPortabilityService(knowledge_graph=src, data_dir=tmp_path / "d1").export()
    art["header"]["graph_schema_version"] = GRAPH_SCHEMA_VERSION + 5
    dst = KnowledgeGraphStore(tmp_path / "dst.sqlite", tmp_path / "dst-blobs")
    svc = KGPortabilityService(knowledge_graph=dst, data_dir=tmp_path / "d2")
    with pytest.raises(ValueError, match="newer"):
        svc.import_data(art)


def test_replace_import_rolls_back_on_mid_import_failure(tmp_path):
    original = _seeded(tmp_path, "original")
    before = _node_totals(original)
    replacement = _seeded(tmp_path, "replacement")
    art = KGPortabilityService(knowledge_graph=replacement, data_dir=tmp_path / "export").export()
    art["nodes"].append({"type": "Broken", "title": "missing id"})

    svc = KGPortabilityService(knowledge_graph=original, data_dir=tmp_path / "import")

    with pytest.raises(KeyError):
        svc.import_data(art, mode="replace")

    assert _node_totals(original) == before


def test_backup_clear_restore_recovers_graph(tmp_path):
    store = _seeded(tmp_path, "kg")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")
    before = _node_totals(store)
    assert before > 0

    out = svc.backup()
    assert Path(out["path"]).exists()
    assert out["manifest"]["db_sha256"]

    store.clear_all()
    assert _node_totals(store) == 0

    dry_run = svc.restore(out["path"], dry_run=True)
    assert dry_run["dry_run"] is True
    assert _node_totals(store) == 0

    with pytest.raises(ValueError, match="confirmation"):
        svc.restore(out["path"])

    restored = svc.restore(out["path"], confirm=True)
    assert restored["restored"] is True
    assert _node_totals(store) == before


def test_restore_integrity_check_rejects_tampered_archive(tmp_path):
    store = _seeded(tmp_path, "kg")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")
    out = svc.backup()
    # Tamper: rewrite the zip's db member so its sha no longer matches manifest.
    import zipfile
    archive = Path(out["path"])
    raw = {}
    with zipfile.ZipFile(archive) as zf:
        for n in zf.namelist():
            raw[n] = zf.read(n)
    raw["knowledge_graph.sqlite"] = b"corrupted-db-bytes"
    with zipfile.ZipFile(archive, "w") as zf:
        for n, b in raw.items():
            zf.writestr(n, b)
    with pytest.raises(ValueError, match="integrity"):
        svc.restore(out["path"], confirm=True)


def test_restore_tolerates_wal_sibling_vanishing_mid_swap(tmp_path, monkeypatch):
    """A live connection can checkpoint away the -wal/-shm sibling between the
    exists() probe and the copy. Restore must treat that as 'nothing to
    preserve', not crash with FileNotFoundError (regression: TOCTOU race)."""
    store = _seeded(tmp_path, "kg")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")
    out = svc.backup()
    store.clear_all()

    # Force a -wal sibling to exist at probe time, then vanish before copy2.
    wal = Path(str(store.db_path) + "-wal")
    wal.write_bytes(b"transient-wal")
    real_copy2 = portability_module.shutil.copy2

    def vanish_then_copy(src, dst, *a, **k):
        if Path(src) == wal:
            wal.unlink(missing_ok=True)  # checkpoint removed it underneath us
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(portability_module.shutil, "copy2", vanish_then_copy)

    restored = svc.restore(out["path"], confirm=True)
    assert restored["restored"] is True
    assert _node_totals(store) > 0


def test_restore_failure_preserves_current_brain_and_pre_restore_backup(tmp_path, monkeypatch):
    store = _seeded(tmp_path, "kg")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")
    out = svc.backup()

    store.clear_all()
    assert _node_totals(store) == 0

    original_replace = portability_module.os.replace

    def fail_db_swap(src, dst):
        if Path(dst) == Path(store.db_path):
            raise OSError("simulated restore swap failure")
        return original_replace(src, dst)

    monkeypatch.setattr(portability_module.os, "replace", fail_db_swap)

    with pytest.raises(OSError, match="swap failure"):
        svc.restore(out["path"], confirm=True)

    assert _node_totals(store) == 0
    backups = list(Path(store.db_path).parent.glob(f"{Path(store.db_path).name}.pre-restore-*"))
    assert backups
    assert (backups[0] / Path(store.db_path).name).exists()


def test_restore_blob_failure_rolls_back_database_and_blobs(tmp_path, monkeypatch):
    store = _seeded(tmp_path, "kg")
    Path(store.blob_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.blob_dir) / "note.txt").write_text("archived blob", encoding="utf-8")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")
    out = svc.backup()

    store.clear_all()
    Path(store.blob_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.blob_dir) / "note.txt").write_text("current blob", encoding="utf-8")

    original_replace = portability_module.os.replace

    def fail_blob_swap(src, dst):
        if Path(dst) == Path(store.blob_dir):
            raise OSError("simulated blob restore swap failure")
        return original_replace(src, dst)

    monkeypatch.setattr(portability_module.os, "replace", fail_blob_swap)

    with pytest.raises(OSError, match="blob restore"):
        svc.restore(out["path"], confirm=True)

    assert _node_totals(store) == 0
    assert (Path(store.blob_dir) / "note.txt").read_text(encoding="utf-8") == "current blob"


# ── route auth ───────────────────────────────────────────────────────────────
def _app(tmp_path, *, admin_ok=True):
    store = _seeded(tmp_path, "kg")
    svc = KGPortabilityService(knowledge_graph=store, data_dir=tmp_path / "data")

    def require_admin(request: Request):
        if not admin_ok:
            raise HTTPException(status_code=403, detail="admin only")
        return ("admin@example.com", {})

    app = FastAPI()
    app.include_router(create_portability_router(
        service=svc,
        require_user=lambda request: "user@example.com",
        require_admin=require_admin,
    ))
    return TestClient(app)


def test_export_route_requires_admin(tmp_path):
    denied = _app(tmp_path, admin_ok=False)
    assert denied.post("/api/knowledge-graph/export").status_code == 403

    client = _app(tmp_path)
    r = client.post("/api/knowledge-graph/export")
    assert r.status_code == 200
    assert r.json()["header"]["format"] == "latticeai.kg.export"


def test_import_route_requires_admin(tmp_path):
    client = _app(tmp_path, admin_ok=False)
    r = client.post("/api/knowledge-graph/import", json={"artifact": {"nodes": []}, "mode": "merge"})
    assert r.status_code == 403


def test_portability_status_route(tmp_path):
    client = _app(tmp_path)
    r = client.get("/api/knowledge-graph/portability")
    assert r.status_code == 200
    assert r.json()["available"] is True


def test_recent_provenance_route(tmp_path):
    assert _app(tmp_path, admin_ok=False).get("/api/knowledge-graph/provenance?limit=10").status_code == 403
    client = _app(tmp_path)
    r = client.get("/api/knowledge-graph/provenance?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and body["count"] >= 2
    assert all("source_type" in it for it in body["items"])


def test_archive_routes_verify_and_require_confirmed_restore(tmp_path):
    client = _app(tmp_path)
    archive = client.post(
        "/api/knowledge-graph/archive",
        json={"passphrase": "archive passphrase"},
    )
    assert archive.status_code == 200
    path = archive.json()["path"]

    inspected = client.post(
        "/api/knowledge-graph/archive/inspect",
        json={"path": path, "passphrase": "archive passphrase"},
    )
    assert inspected.status_code == 200
    assert inspected.json()["verified"] is True

    verified = client.post(
        "/api/knowledge-graph/archive/verify",
        json={"path": path, "passphrase": "archive passphrase"},
    )
    assert verified.status_code == 200
    assert verified.json()["ok"] is True

    blocked = client.post(
        "/api/knowledge-graph/archive/restore",
        json={"path": path, "passphrase": "archive passphrase"},
    )
    assert blocked.status_code == 400
    assert "confirmation" in blocked.json()["detail"]

    planned = client.post(
        "/api/knowledge-graph/archive/import",
        json={"path": path, "passphrase": "archive passphrase", "dry_run": True},
    )
    assert planned.status_code == 200
    assert planned.json()["operation"] == "import"
    assert planned.json()["dry_run"] is True


def test_import_preserves_legacy_edge_collisions(tmp_path):
    """Logical import must keep distinct v2 rows for legacy labels that normalize
    to the same EdgeType (lossless collision case for hardening)."""
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    # minimal seed
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("a", "Concept", "A", "", "{}", "{}", "2026-01-01", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("b", "Concept", "B", "", "{}", "{}", "2026-01-01", "2026-01-01"),
        )
        # legacy table may only keep one, but import artifact can carry both
    art = {
        "header": {"graph_schema_version": 1},
        "nodes": [
            {
                "id": "a",
                "type": "Concept",
                "title": "A",
                "summary": "",
                "metadata_json": "{}",
                "raw_json": "{}",
            },
            {
                "id": "b",
                "type": "Concept",
                "title": "B",
                "summary": "",
                "metadata_json": "{}",
                "raw_json": "{}",
            },
        ],
        "edges": [
            {
                "id": "e1",
                "from_node": "a",
                "to_node": "b",
                "type": "mentions",
                "weight": 1.0,
                "metadata_json": "{}",
            },
            {
                "id": "e2",
                "from_node": "a",
                "to_node": "b",
                "type": "관련됨",
                "weight": 1.0,
                "metadata_json": "{}",
            },
        ],
        "chunks": [],
        "knowledge_sources": [],
        "provenance": [],
    }
    res = store.import_graph_data(art, mode="replace")
    assert res.get("imported")
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges_v2 WHERE source='a' AND target='b'"
        ).fetchone()[0]
        legs = sorted(
            r[0]
            for r in conn.execute(
                "SELECT legacy_type FROM edges_v2 WHERE source='a' AND target='b'"
            )
        )
    assert n == 2, "v2 keeps colliding legacy labels as distinct projected rows"
    # v2 must retain the distinction even if legacy collapsed
    # (import now passes legacy_label to force distinct legacy_type)
    assert legs == ["mentions", "관련됨"]
