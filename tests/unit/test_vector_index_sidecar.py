"""HNSW sidecar query: missing/stale honesty and add_items refresh."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from latticeai.core.vector_index import (
    VECTOR_QUERY_K_CAP,
    decode_f32le,
    hnswlib_available,
    query_sidecar,
    reset_sidecar_cache,
    resolve_graph_db,
    sidecar_fingerprint,
    sidecar_freshness,
)


def _blob(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _seed_db(path: Path, rows: list[tuple[str, list[float]]], model: str = "hash", dim: int = 4) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vector_embeddings("
        "item_id TEXT PRIMARY KEY, item_type TEXT, source_node TEXT, "
        "embedding BLOB, embedding_dim INT, embedding_model TEXT, "
        "metadata_json TEXT, indexed_at TEXT)"
    )
    for index, (item_id, vector) in enumerate(rows):
        conn.execute(
            "INSERT INTO vector_embeddings VALUES (?,?,?,?,?,?,?,?)",
            (
                item_id,
                "chunk",
                item_id,
                _blob(vector),
                dim,
                model,
                "{}",
                f"2026-01-01T00:00:{index:02d}",
            ),
        )
    conn.commit()
    conn.close()


def test_decode_f32le_matches_rust_layout():
    blob = _blob([1.0, -2.5, 0.0])
    assert decode_f32le(blob, 3) == [1.0, -2.5, 0.0]
    assert decode_f32le(b"", 3) == []
    assert decode_f32le(blob, 8) == [1.0, -2.5, 0.0]


def test_resolve_graph_db_prefers_an_explicit_sqlite_file(tmp_path, monkeypatch):
    db = tmp_path / "brain.sqlite"
    db.write_bytes(b"")
    assert resolve_graph_db(str(db)) == db
    assert resolve_graph_db(str(tmp_path)) == tmp_path / "knowledge_graph.sqlite"
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    assert resolve_graph_db(None) == tmp_path / "knowledge_graph.sqlite"


def test_missing_db_is_index_none(tmp_path):
    reset_sidecar_cache()
    reply = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[1.0, 0.0, 0.0, 0.0],
        k=4,
        db_path=tmp_path / "missing.sqlite",
    )
    assert reply["index"] == "none"
    assert reply["ids"] == []
    assert reply["size"] == 0


@pytest.mark.skipif(
    not hnswlib_available(),
    reason="the width-mismatch detail is produced by the real index; without hnswlib the reply is the availability message",
)
def test_identity_mismatch_is_index_none(tmp_path):
    reset_sidecar_cache()
    db = tmp_path / "kg.sqlite"
    _seed_db(db, [("a", [1.0, 0.0, 0.0, 0.0])])
    reply = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[1.0, 0.0],
        k=4,
        db_path=db,
    )
    assert reply["index"] == "none"
    assert "width" in (reply["detail"] or "")


def test_query_builds_a_sidecar_from_store_vectors(tmp_path):
    reset_sidecar_cache()
    db = tmp_path / "kg.sqlite"
    target = [1.0, 0.0, 0.0, 0.0]
    other = [0.0, 1.0, 0.0, 0.0]
    _seed_db(db, [("hit", target), ("miss", other)])
    reply = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=target,
        k=2,
        db_path=db,
    )
    if not hnswlib_available():
        assert reply["index"] == "none"
        assert "hnswlib" in (reply["detail"] or "")
        return
    assert reply["index"] == "hnsw"
    assert reply["size"] == 2
    assert reply["store_size"] == 2
    assert reply["stale"] is False
    assert reply["ids"][0] == "hit"
    assert (tmp_path / "kg.hnsw").is_file()


def test_append_after_ingest_keeps_the_new_id(tmp_path):
    reset_sidecar_cache()
    db = tmp_path / "kg.sqlite"
    _seed_db(db, [("a", [1.0, 0.0, 0.0, 0.0]), ("b", [0.0, 1.0, 0.0, 0.0])])
    first = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[1.0, 0.0, 0.0, 0.0],
        k=3,
        db_path=db,
    )
    if not hnswlib_available():
        assert first["index"] == "none"
        return
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO vector_embeddings VALUES (?,?,?,?,?,?,?,?)",
        ("c", "chunk", "c", _blob([0.0, 0.0, 1.0, 0.0]), 4, "hash", "{}", "2026-01-01T00:01:00"),
    )
    conn.commit()
    conn.close()
    second = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[0.0, 0.0, 1.0, 0.0],
        k=3,
        db_path=db,
    )
    assert second["index"] == "hnsw"
    assert second["size"] == 3
    assert second["stale"] is False
    assert "c" in second["ids"]
    assert second["refreshed"] in {"append", "rebuild"}


def test_empty_model_id_and_zero_dim_are_none():
    reset_sidecar_cache()
    assert query_sidecar(
        workspace=None, embedding_model="", embedding_dim=4, vector=[0.0] * 4, k=1
    )["index"] == "none"
    assert query_sidecar(
        workspace=None, embedding_model="hash", embedding_dim=0, vector=[], k=1
    )["index"] == "none"


def test_wrong_width_store_row_is_skipped(tmp_path):
    reset_sidecar_cache()
    db = tmp_path / "kg.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE vector_embeddings("
        "item_id TEXT PRIMARY KEY, item_type TEXT, source_node TEXT, "
        "embedding BLOB, embedding_dim INT, embedding_model TEXT, "
        "metadata_json TEXT, indexed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO vector_embeddings VALUES (?,?,?,?,?,?,?,?)",
        ("bad", "chunk", "bad", _blob([1.0, 0.0]), 4, "hash", "{}", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    reply = query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[1.0, 0.0, 0.0, 0.0],
        k=2,
        db_path=db,
    )
    assert reply["index"] == "none"


def test_corrupt_sidecar_meta_is_treated_as_absent(tmp_path):
    from latticeai.core.vector_index.sidecar import sidecar_meta_size

    db = tmp_path / "kg.sqlite"
    db.write_bytes(b"")
    (tmp_path / "kg.hnsw.meta.json").write_text("not-json", encoding="utf-8")
    assert sidecar_meta_size(db) is None


def test_freshness_after_a_built_sidecar(tmp_path):
    reset_sidecar_cache()
    db = tmp_path / "kg.sqlite"
    _seed_db(db, [("a", [1.0, 0.0, 0.0, 0.0])])
    query_sidecar(
        workspace=None,
        embedding_model="hash",
        embedding_dim=4,
        vector=[1.0, 0.0, 0.0, 0.0],
        k=1,
        db_path=db,
    )
    report = sidecar_freshness(embedding_model="hash", embedding_dim=4, db_path=db)
    if hnswlib_available():
        assert report["index"] == "hnsw"
        assert report["stale"] is False
    else:
        assert report["index"] == "none"


def test_freshness_reports_a_missing_sidecar(tmp_path):
    db = tmp_path / "kg.sqlite"
    _seed_db(db, [("a", [1.0, 0.0, 0.0, 0.0])])
    report = sidecar_freshness(embedding_model="hash", embedding_dim=4, db_path=db)
    assert report["index"] == "none"
    assert report["store_size"] == 1
    assert report["stale"] is True
    assert sidecar_fingerprint("hash", 4, 1) == "hash|4|1"
    assert VECTOR_QUERY_K_CAP == 200
