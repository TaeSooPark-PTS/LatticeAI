"""Folder ingestion + web ingestion seam tests (IngestionPipeline).

Covers: directory walking with .latticeignore (globs, dir/ patterns,
comments), hard skip-list dirs, hidden/extension/size filtering, inline vs
background scheduling, idempotent re-runs, error reporting, and the
ingest_web_page convenience wrapper (extraction happens upstream — the
pipeline refuses to fetch).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline


def _pipeline(tmp_path: Path) -> IngestionPipeline:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    return IngestionPipeline(store)


def _build_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".latticeignore").write_text("# comment line\n\n*.log\nsecret/\n", encoding="utf-8")
    (root / "notes.txt").write_text("Folder ingestion notes for Lattice AI hybrid retrieval.", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text("# Readme\nLattice AI folder ingestion readme content.", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main():\n    return 'lattice'\n", encoding="utf-8")
    (root / "data.log").write_text("log line that must be ignored", encoding="utf-8")
    (root / "secret").mkdir()
    (root / "secret" / "key.txt").write_text("super secret material", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg").mkdir()
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1", encoding="utf-8")
    (root / ".hidden.txt").write_text("hidden file", encoding="utf-8")
    (root / "big.txt").write_text("x" * 5000, encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")


def test_ingest_folder_honors_latticeignore_and_filters(tmp_path):
    root = tmp_path / "corpus"
    _build_tree(root)
    pipe = _pipeline(tmp_path)

    summary = pipe.ingest_folder(root, max_file_bytes=1000)

    assert summary["status"] == "ok"
    assert summary["ingested"] == 3           # notes.txt, docs/readme.md, src/app.py
    assert summary["failed"] == 0
    assert summary["duplicate"] == 0
    assert summary["matched"] == 3
    assert summary["skipped"]["ignored"] == 2  # data.log (glob) + secret/ (dir pattern)
    assert summary["skipped"]["hidden"] == 1   # .hidden.txt
    assert summary["skipped"]["extension"] == 1  # image.png
    assert summary["skipped"]["too_large"] == 1  # big.txt
    # node_modules is hard-pruned: its files never even get scanned.
    assert summary["scanned"] == 7

    stats = pipe._kg.stats()
    assert stats["nodes"].get("Document", 0) == 3
    # Inline-read text produced chunks, so folder content is retrievable.
    docs = pipe._kg.list_documents()["documents"]
    assert all(doc["indexed"] for doc in docs)


def test_ingest_folder_rerun_reports_duplicates(tmp_path):
    root = tmp_path / "corpus"
    _build_tree(root)
    pipe = _pipeline(tmp_path)
    first = pipe.ingest_folder(root, max_file_bytes=1000)
    second = pipe.ingest_folder(root, max_file_bytes=1000)
    assert first["ingested"] == 3
    assert second["ingested"] == 0
    assert second["duplicate"] == 3
    assert second["status"] == "ok"


def test_ingest_folder_non_recursive_stays_top_level(tmp_path):
    root = tmp_path / "corpus"
    _build_tree(root)
    pipe = _pipeline(tmp_path)
    summary = pipe.ingest_folder(root, recursive=False, max_file_bytes=1000)
    assert summary["ingested"] == 1  # only notes.txt survives at the top level


def test_ingest_folder_background_schedules_without_ingesting(tmp_path):
    root = tmp_path / "corpus"
    _build_tree(root)
    pipe = _pipeline(tmp_path)

    summary = pipe.ingest_folder(root, background=True, max_file_bytes=1000)

    assert summary["status"] == "scheduled"
    assert summary["scheduled"] == 3
    job = pipe.get_background_job(summary["job_id"])
    assert job is not None and job.total == 3
    assert pipe._kg.stats()["nodes"].get("Document", 0) == 0  # nothing ingested yet


def test_ingest_folder_rejects_non_directory(tmp_path):
    pipe = _pipeline(tmp_path)
    summary = pipe.ingest_folder(tmp_path / "missing")
    assert summary["status"] == "failed"
    assert "not a directory" in summary["detail"]


def test_ingest_folder_unavailable_when_graph_disabled(tmp_path):
    root = tmp_path / "corpus"
    _build_tree(root)
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipe = IngestionPipeline(store, enable_graph=False)
    summary = pipe.ingest_folder(root)
    assert summary["status"] == "unavailable"


def test_ingest_folder_workspace_scope_propagates(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "plan.md").write_text("Workspace scoped folder ingestion plan.", encoding="utf-8")
    pipe = _pipeline(tmp_path)
    summary = pipe.ingest_folder(root, workspace_id="org:acme", owner="a@x.com")
    assert summary["ingested"] == 1
    docs = pipe._kg.list_documents()["documents"]
    scopes = pipe._kg.workspaces_of([d["id"] for d in docs])
    assert set(scopes.values()) == {"org:acme"}


def test_ingest_web_page_wraps_extracted_text(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest_web_page(
        "https://example.com/lattice",
        "Lattice AI web capture: the graph layer receives extracted text only.",
        title="Lattice AI",
        metadata={"capture": "browser-extension"},
        owner="user@example.com",
    )
    assert res.status == "ok"
    assert res.source_type == "web_url"
    assert res.node_id and res.node_id.startswith("webdoc:")
    prov = pipe._kg.get_provenance(res.node_id)
    assert prov["source_type"] == "web_url"
    assert prov["source_uri"] == "https://example.com/lattice"


def test_ingest_web_page_requires_upstream_extraction(tmp_path):
    pipe = _pipeline(tmp_path)
    res = pipe.ingest_web_page("https://example.com/x", "   ")
    assert res.status == "failed"
    assert "upstream" in (res.detail or "")

    no_url = pipe.ingest_web_page("", "some text")
    assert no_url.status == "failed"
    assert "url required" in (no_url.detail or "")
