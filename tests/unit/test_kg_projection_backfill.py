"""The v2 migration is no longer a half-migration: legacy data is backfilled
into the v2 tables, non-destructively and idempotently."""

import sqlite3

import pytest

kg = pytest.importorskip("lattice_brain.graph.store")
pytest.importorskip("lattice_brain.graph.schema")


def _seed_legacy(db_path):
    """Insert two legacy nodes + one legacy edge directly (no LLM needed)."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("n1", "Concept", "Alpha", "", "{}", "{}", "2026-01-01", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("n2", "Concept", "Beta", "", "{}", "{}", "2026-01-01", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO edges(id,from_node,to_node,type,weight,metadata_json,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("e1", "n1", "n2", "related_to", 1.0, "{}", "2026-01-01"),
        )


def _counts(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0]
        e = conn.execute("SELECT COUNT(*) FROM edges_v2").fetchone()[0]
        ln = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    return n, e, ln


def test_backfill_populates_v2_and_preserves_legacy(tmp_path):
    db = tmp_path / "kg.sqlite"
    blobs = tmp_path / "blobs"

    # 1) first construction creates schema; v2 starts empty
    kg.KnowledgeGraphStore(db, blobs)
    n, e, _ = _counts(db)
    assert (n, e) == (0, 0)

    # 2) seed legacy tables
    _seed_legacy(db)

    # 3) reconstructing triggers the guarded backfill
    kg.KnowledgeGraphStore(db, blobs)
    n, e, legacy_n = _counts(db)
    assert n == 2, "legacy nodes copied into nodes_v2"
    assert e == 1, "legacy edge copied into edges_v2"
    assert legacy_n == 2, "legacy table left intact (non-destructive)"


def test_init_schema_heals_stale_empty_v2_table(tmp_path):
    """A v2 table left behind by an older schema (missing columns) is recreated."""
    from lattice_brain.graph.schema import KGStoreV2
    db = tmp_path / "kg.sqlite"
    # simulate an old, empty nodes_v2 without the style/tone columns
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE nodes_v2 (id TEXT PRIMARY KEY, type TEXT, label TEXT)")
    KGStoreV2(str(db)).init_schema()
    with sqlite3.connect(str(db)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes_v2)").fetchall()}
    assert "style" in cols and "importance_score" in cols, "stale table was recreated with current columns"


def test_init_schema_never_drops_rows(tmp_path):
    """Safety contract: a stale v2 table holding rows is never dropped.

    (This case cannot occur in practice — v2 has never been written to — but the
    heal must refuse to destroy data even if that leaves the upgrade incomplete.)
    """
    from lattice_brain.graph.schema import KGStoreV2
    db = tmp_path / "kg.sqlite"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE nodes_v2 (id TEXT PRIMARY KEY, type TEXT, label TEXT)")
        conn.execute("INSERT INTO nodes_v2 VALUES ('x','t','l')")
    try:
        KGStoreV2(str(db)).init_schema()
    except sqlite3.OperationalError:
        pass  # incomplete upgrade surfaces loudly; that's acceptable — data is safe
    with sqlite3.connect(str(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0]
    assert n == 1, "row preserved — heal refused to drop a non-empty table"


def test_backfill_is_idempotent(tmp_path):
    db = tmp_path / "kg.sqlite"
    blobs = tmp_path / "blobs"
    kg.KnowledgeGraphStore(db, blobs)
    _seed_legacy(db)
    kg.KnowledgeGraphStore(db, blobs)   # backfill #1
    kg.KnowledgeGraphStore(db, blobs)   # backfill #2 should no-op
    n, e, _ = _counts(db)
    assert (n, e) == (2, 1), "re-running does not duplicate rows"
