"""wp30 coverage — the unified ingestion door's degraded paths.

Everything here is a case where the pipeline must stay honest instead of
crashing or lying: an unusable upstream confidence value, an unreadable
``.latticeignore``, a provenance table that is gone, an audit sink that
raises, an older store with no incremental vector sync or workspace-scoped
search, and a folder walk that meets a broken symlink, an unreadable file, a
hidden directory and its own file cap.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.graph.proactive as proactive_mod
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import (
    IngestionItem,
    IngestionPipeline,
    _load_latticeignore,
    _matches_ignore,
    assess_extraction_quality,
    content_hash_text,
)

PROSE = (
    "Lattice AI keeps every source behind one ingestion door. The knowledge "
    "graph stores provenance for each capture so the user can answer why a "
    "document is in the brain. Extraction quality is advisory, never a gate."
)


class _OlderStore:
    """A store from before incremental vector sync and scoped search existed."""

    db_path = None
    blob_dir = None

    def __init__(self) -> None:
        self.provenance = []
        self.searches = []

    def ingest_source(self, **kwargs):
        return {"node_id": "node-1", "content_hash": "hash-1", "chunk_ids": ["c1"],
                "title": kwargs.get("title")}

    def node_is_embedded(self, node_id):
        return True

    def record_provenance(self, **kwargs):
        self.provenance.append(kwargs)
        return {"id": "prov-1"}

    def search(self, query, limit, **kwargs):
        if kwargs:
            raise TypeError("search() got an unexpected keyword argument")
        self.searches.append((query, limit))
        return {"matches": []}


class _Unreadable:
    """A persisted job value this build cannot normalize."""

    def __str__(self) -> str:
        raise RuntimeError("corrupt job item")


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _fail_read_text_for(monkeypatch, name: str) -> None:
    real = Path.read_text

    def fake(self, *args, **kwargs):
        if self.name == name:
            raise OSError("simulated read failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake)


# ── extraction quality heuristics ────────────────────────────────────────────

def test_unusable_upstream_confidence_falls_back_to_the_heuristic():
    quality = assess_extraction_quality(PROSE, upstream_confidence="very sure")
    assert quality["reasons"] != ["upstream_confidence"]
    assert quality["level"] == "high"


def test_repetitive_lines_and_whitespace_floods_are_flagged():
    repeated = "\n".join(["Read more", "Read more", "Read more", "a", "b", "c", "d", "e"])
    assert "repetitive_lines" in assess_extraction_quality(repeated)["reasons"]

    # A table that came out of the extractor as columns of padding.
    spaced = "data    point    here    \n" * 20
    reasons = assess_extraction_quality(spaced)["reasons"]
    assert "high_whitespace_ratio" in reasons


def test_content_hash_text_is_stable_and_encoding_safe():
    assert content_hash_text("hello") == content_hash_text("hello")
    assert content_hash_text("") == content_hash_text(None)
    assert len(content_hash_text("hello")) == 64


# ── .latticeignore parsing ───────────────────────────────────────────────────

def test_latticeignore_parses_globs_and_survives_an_unreadable_file(tmp_path, monkeypatch):
    assert _load_latticeignore(tmp_path) == []
    (tmp_path / ".latticeignore").write_text(
        "# comment\n\n*.log\nbuild/\n", encoding="utf-8"
    )
    assert _load_latticeignore(tmp_path) == ["*.log", "build/"]

    _fail_read_text_for(monkeypatch, ".latticeignore")
    assert _load_latticeignore(tmp_path) == []


def test_matches_ignore_skips_empty_and_directory_only_patterns():
    assert _matches_ignore("a.log", "a.log", is_dir=False, patterns=["*.log"]) is True
    # "build/" only prunes directories.
    assert _matches_ignore("build", "build", is_dir=False, patterns=["build/"]) is False
    assert _matches_ignore("build", "build", is_dir=True, patterns=["build/"]) is True
    # A bare separator degenerates to an empty pattern and matches nothing.
    assert _matches_ignore("sub", "sub", is_dir=True, patterns=["/"]) is False


# ── ingest(): degraded infrastructure ────────────────────────────────────────

def test_provenance_and_audit_failures_never_fail_a_landed_ingest(tmp_path):
    store = _store(tmp_path)

    def _no_table(**kwargs):
        raise RuntimeError("no such table: ingestion_provenance")

    def _audit(action, payload, user_email):
        raise RuntimeError("audit sink down")

    store.record_provenance = _no_table  # type: ignore[method-assign]
    pipe = IngestionPipeline(store, audit=_audit)
    result = pipe.ingest(IngestionItem(source_type="note", title="A", text=PROSE))

    assert result.status == "ok"
    assert result.node_id
    assert result.provenance_id is None
    assert "provenance capture failed" in (result.detail or "")


def test_older_stores_skip_incremental_sync_and_scoped_search(tmp_path):
    store = _OlderStore()
    pipe = IngestionPipeline(store)
    result = pipe.ingest(
        IngestionItem(source_type="note", title="A", text=PROSE, workspace_id="w1")
    )

    assert result.status == "ok"
    assert result.indexing_status == "indexed"  # inline embedding, nothing to sync
    assert result.detail is None
    # The scoped call raised TypeError and the pipeline retried unscoped.
    assert store.searches == [(PROSE[:400], 20)]
    assert store.provenance[0]["workspace_id"] == "w1"


def test_quality_gate_is_skipped_when_the_proactive_module_is_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(sys.modules, "lattice_brain.graph.proactive", None)
    result = IngestionPipeline(_store(tmp_path)).ingest(
        IngestionItem(source_type="note", title="A", text=PROSE)
    )
    assert result.status == "ok"
    assert result.quality_gate is None


def test_quality_gate_failure_never_blocks_the_ingest(tmp_path, monkeypatch):
    def _explode(text, search_fn, **kwargs):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(proactive_mod, "gate_ingest_candidate", _explode)
    result = IngestionPipeline(_store(tmp_path)).ingest(
        IngestionItem(source_type="note", title="A", text=PROSE)
    )
    assert result.status == "ok"
    assert result.quality_gate is None


def test_file_source_without_a_path_fails_as_data_not_an_exception(tmp_path):
    result = IngestionPipeline(_store(tmp_path)).ingest(IngestionItem(source_type="pdf"))
    assert result.status == "failed"
    assert result.indexing_status == "failed"
    assert "requires a path" in (result.detail or "")


def test_upstream_extraction_without_inline_text_is_scored_from_its_chunks(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4 binary body")
    result = IngestionPipeline(_store(tmp_path)).ingest(
        IngestionItem(
            source_type="pdf",
            title="report.pdf",
            path=str(document),
            metadata={"extracted": {"preview": PROSE}},
        )
    )
    assert result.status == "ok"
    assert result.chunk_count >= 1
    assert result.extraction_quality == {
        "score": 0.5,
        "level": "medium",
        "reasons": ["content_extracted_upstream_not_scored"],
    }


# ── background jobs driven by the pipeline ───────────────────────────────────

def test_a_running_job_is_reported_rather_than_started_twice(tmp_path):
    pipe = IngestionPipeline(_store(tmp_path))
    job = pipe.schedule_background([IngestionItem(source_type="note", title="A", text=PROSE)])
    job.status = "running"

    assert pipe.run_background_job(job.job_id) == job.as_dict()
    assert pipe.run_background_job("bg_ingest_9999") == {
        "status": "not_found",
        "job_id": "bg_ingest_9999",
    }


def test_per_item_exceptions_are_isolated_and_recorded(tmp_path):
    pipe = IngestionPipeline(_store(tmp_path))
    good = IngestionItem(source_type="note", title="A", text=PROSE)
    job = pipe.schedule_background([good])
    # A payload the ingest door cannot even normalize: the failure has to stay
    # inside this item instead of aborting the rest of the job.
    job.items.append(IngestionItem(source_type=_Unreadable(), title="corrupt"))
    job.total = 2

    outcome = pipe.run_background_job(job.job_id)

    assert outcome["status"] == "partial"
    assert outcome["processed"] == 1
    assert outcome["failed"] == 1
    assert outcome["errors"][0]["index"] == 1


# ── ingest_folder ────────────────────────────────────────────────────────────

def test_ingest_folder_rejects_an_unusable_root(tmp_path):
    pipe = IngestionPipeline(_store(tmp_path))
    summary = pipe.ingest_folder(12345)
    assert summary["status"] == "failed"
    assert "invalid root path" in summary["detail"]

    missing = pipe.ingest_folder(tmp_path / "absent")
    assert missing["status"] == "failed"
    assert "not a directory" in missing["detail"]


def test_ingest_folder_records_unreadable_entries_and_skips_hidden_dirs(
    tmp_path, monkeypatch
):
    root = tmp_path / "corpus"
    (root / ".hidden").mkdir(parents=True)
    (root / ".hidden" / "secret.md").write_text("hidden", encoding="utf-8")
    (root / "keep.md").write_text(PROSE, encoding="utf-8")
    (root / "unreadable.md").write_text("unreadable", encoding="utf-8")
    (root / "broken.md").symlink_to(root / "nowhere.md")

    _fail_read_text_for(monkeypatch, "unreadable.md")
    pipe = IngestionPipeline(_store(tmp_path))
    summary = pipe.ingest_folder(root)

    assert summary["status"] == "partial"
    assert summary["ingested"] == 1
    assert summary["failed"] == 2
    details = {Path(error["path"]).name: error["detail"] for error in summary["errors"]}
    assert "stat failed" in details["broken.md"]
    assert "read failed" in details["unreadable.md"]
    # The hidden directory was pruned, so its file was never scanned.
    assert "secret.md" not in details
    assert summary["scanned"] == 3


def test_ingest_folder_stops_at_the_file_cap_and_routes_pdfs_by_extension(tmp_path):
    root = tmp_path / "corpus"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text(PROSE, encoding="utf-8")
    (root / "b.pdf").write_bytes(b"%PDF-1.4 body")
    (root / "sub" / "c.md").write_text(PROSE, encoding="utf-8")

    pipe = IngestionPipeline(_store(tmp_path))
    summary = pipe.ingest_folder(root, max_files=2)

    assert summary["truncated"] is True  # sub/c.md never made it into the batch
    assert summary["matched"] == 2
    assert summary["ingested"] == 2
    assert summary["status"] == "ok"
    titles = {row["title"] for row in pipe._kg.list_provenance()["items"]}
    assert {"a.md", "b.pdf"} <= titles


def test_ingest_folder_records_failures_from_the_ingest_door(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.md").write_text(PROSE, encoding="utf-8")

    pipe = IngestionPipeline(_store(tmp_path))

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    # The store refuses the write, so the walk records it per file.
    pipe._kg.ingest_document = _boom  # type: ignore[method-assign]
    summary = pipe.ingest_folder(root)

    assert summary["status"] == "partial"
    assert summary["failed"] == 1
    assert summary["errors"][0]["status"] == "failed"
    assert "disk full" in summary["errors"][0]["detail"]
