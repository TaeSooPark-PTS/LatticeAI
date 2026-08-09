"""wp23 coverage — vector index fingerprint, status, freshness, and search.

Real ``KnowledgeGraphStore`` instances over SQLite in ``tmp_path`` with the
deterministic offline hash embedder, so every score is reproducible. Degraded
paths come from crafted ``graph_meta`` / ``vector_embeddings`` rows or from
failing the storage-capability probe — never from timing or a real model.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.retrieval_vector import (
    VECTOR_MAX_CANDIDATES_CEILING,
    KnowledgeGraphVectorMixin,
)
from lattice_brain.graph.store import KnowledgeGraphStore


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _write_fingerprint_blob(store: KnowledgeGraphStore, raw: str) -> None:
    with store._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            (store._EMBEDDER_FINGERPRINT_KEY, raw),
        )


def _boom_connect():
    raise sqlite3.OperationalError("database is locked")


# ── embedder fingerprint ─────────────────────────────────────────────────────


def test_a_fingerprint_without_a_model_id_is_not_a_fingerprint(tmp_path) -> None:
    store = _store(tmp_path)
    _write_fingerprint_blob(store, json.dumps({"dim": 384}))

    with store._connect() as conn:
        assert store._embedder_fingerprint_record(conn) is None

    status = store.embedder_fingerprint_status()
    assert status["recorded"] is None
    assert status["stale_embedder"] is False


def test_an_unparseable_fingerprint_dimension_reads_as_zero(tmp_path) -> None:
    store = _store(tmp_path)
    _write_fingerprint_blob(
        store, json.dumps({"model_id": "some-other-model", "dim": "not-a-number"})
    )

    with store._connect() as conn:
        assert store._embedder_fingerprint_record(conn) == {
            "model_id": "some-other-model",
            "dim": 0,
        }

    # a different recorded model IS a stale index, honestly reported
    assert store.embedder_fingerprint_status()["stale_embedder"] is True


def test_recording_the_fingerprint_persists_the_current_embedder(tmp_path) -> None:
    store = _store(tmp_path)

    recorded = store.record_embedder_fingerprint()

    assert recorded == {
        "model_id": store._embedding_model.model_id,
        "dim": int(store._embedding_model.dim),
    }
    assert store.embedder_fingerprint_status()["recorded"] == recorded
    assert store.embedder_fingerprint_status()["stale_embedder"] is False


def test_fingerprint_status_degrades_when_the_database_is_unreadable(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(store, "_connect", _boom_connect)

    status = store.embedder_fingerprint_status()

    assert status["recorded"] is None
    assert status["stale_embedder"] is False
    assert status["current"]["model_id"] == store._embedding_model.model_id


# ── rebuild failure bookkeeping ──────────────────────────────────────────────


def test_a_failed_rebuild_records_the_operation_and_re_raises(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)

    def _boom(conn, **kwargs):
        raise RuntimeError("embedding provider disappeared")

    monkeypatch.setattr(store, "_iter_vector_source_items", _boom)

    with pytest.raises(RuntimeError, match="embedding provider disappeared"):
        store.rebuild_vector_index(full=True)

    with store._connect() as conn:
        row = conn.execute(
            "SELECT operation, status, error_message, completed_at "
            "FROM vector_index_operations ORDER BY requested_at DESC LIMIT 1"
        ).fetchone()

    assert row["status"] == "failed"
    assert row["operation"] == "rebuild_full"
    assert row["error_message"] == "embedding provider disappeared"
    assert row["completed_at"]


# ── index status ─────────────────────────────────────────────────────────────


def test_index_status_reports_an_unprobeable_storage_engine(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)

    def _boom():
        raise RuntimeError("capability probe failed")

    monkeypatch.setattr(store.storage_engine, "capabilities", _boom)

    status = store.index_status()

    assert status["storage"]["engine"] == {
        "engine": "sqlite",
        "available": False,
        "reason": "capability probe failed",
    }
    assert status["storage"]["vector_search_backend"] is None


def test_backlog_samples_are_capped_at_twenty(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        for index in range(25):
            store._upsert_node(
                conn, f"doc:{index:02d}", "Document", f"Document number {index}"
            )
        # drop the derived index so every source item reads as missing
        conn.execute("DELETE FROM vector_embeddings")

    status = store.index_status()

    assert status["missing_items"] == 25
    assert status["scale"]["backlog_reasons"] == {"missing_vector": 25}
    assert len(status["scale"]["backlog_samples"]) == 20


def test_a_row_embedded_at_another_dimension_reads_as_dimension_changed(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:dim", "Document", "Dimension drift")
        conn.execute(
            "UPDATE vector_embeddings SET embedding_dim=? WHERE item_id=?",
            (int(store._embedding_model.dim) + 1, "doc:dim"),
        )

    status = store.index_status()

    assert status["stale_items"] == 1
    assert status["scale"]["backlog_reasons"] == {"dimension_changed": 1}


# ── freshness ────────────────────────────────────────────────────────────────


def test_freshness_keeps_the_pending_status_when_the_old_row_count_fails(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "index_status",
        lambda: {
            "pending_items": 3,
            "source_items": 10,
            "embedder": {
                "stale_embedder": True,
                "recorded": {"model_id": "previous-model"},
            },
        },
    )
    monkeypatch.setattr(store, "_connect", _boom_connect)

    report = store.vector_freshness()

    # no old-model rows could be proven, so no stale_embedder claim is made
    assert report["status"] == "pending"
    assert report["pending_items"] == 3
    assert report["total_items"] == 10


# ── backend + candidate cap ──────────────────────────────────────────────────


def test_backend_falls_back_to_brute_force_when_the_probe_fails(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)

    def _boom():
        raise RuntimeError("capability probe failed")

    monkeypatch.setattr(store.storage_engine, "capabilities", _boom)

    assert store._vector_search_backend() == "bruteforce-cosine"


def test_candidate_cap_honours_an_explicit_request(tmp_path) -> None:
    store = _store(tmp_path)

    assert store._vector_candidate_cap(50, limit=30) == 50
    # never scan fewer rows than the caller intends to receive
    assert store._vector_candidate_cap(5, limit=30) == 30
    assert (
        store._vector_candidate_cap(VECTOR_MAX_CANDIDATES_CEILING * 2, limit=30)
        == VECTOR_MAX_CANDIDATES_CEILING
    )


def test_candidate_cap_of_zero_means_scan_everything(tmp_path) -> None:
    store = _store(tmp_path)

    assert store._vector_candidate_cap(0, limit=30) is None
    assert store._vector_candidate_cap(-1, limit=30) is None


def test_recall_report_is_honest_about_a_truncated_scan() -> None:
    report = KnowledgeGraphVectorMixin._recall_report(
        backend="bruteforce-cosine",
        cap=1,
        candidates_total=9,
        candidates_scanned=1,
    )

    assert report["truncated"] is True
    assert "partial recall" in report["detail"]


# ── vector search ────────────────────────────────────────────────────────────


def test_vector_search_of_an_empty_query_returns_an_empty_recall_block(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:any", "Document", "Anything at all")

    result = store.vector_search("   ")

    assert result == {
        "query": "",
        "matches": [],
        "recall": {
            "backend": "bruteforce-cosine",
            "max_candidates": 10_000,
            "candidates_total": 0,
            "candidates_scanned": 0,
            "truncated": False,
            "detail": None,
        },
    }


def test_vector_search_drops_every_row_below_the_score_floor(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:one", "Document", "Retrieval design notes")
        store._upsert_node(conn, "doc:two", "Document", "Unrelated release notes")

    # 1.5 is above the cosine ceiling, so nothing can clear it
    result = store.vector_search("retrieval design", min_score=1.5)

    assert result["matches"] == []
    # the scan still happened and is reported as complete
    assert result["recall"]["candidates_scanned"] == 2
    assert result["recall"]["truncated"] is False


def test_vector_search_scans_the_whole_index_when_uncapped(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        for index in range(3):
            store._upsert_node(
                conn, f"doc:{index}", "Document", f"Retrieval design note {index}"
            )

    result = store.vector_search("retrieval design", max_candidates=0)

    assert result["recall"]["max_candidates"] is None
    assert result["recall"]["candidates_scanned"] == 3
    assert result["recall"]["truncated"] is False
    assert len(result["matches"]) == 3
