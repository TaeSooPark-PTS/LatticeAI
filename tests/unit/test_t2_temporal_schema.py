"""Temporal schema migration + stamping primitives (v11.1.0 Track 2).

The riskiest part of the temporal model is not the query — it is arriving at an
existing Brain and adding columns to tables that already hold data. These tests
pin the three properties that make that safe:

* the migration is **additive and idempotent** (running it twice changes
  nothing, and no row is dropped or rewritten);
* ``NULL`` is the convention, not ``''`` — including through the ``kgv2_*``
  projection views, where a ``COALESCE`` would have turned "still valid" into
  a value (the 11.0.1 ``kgv2_edges`` observation);
* the stamping primitives only ever write the fields they were handed.
"""

from __future__ import annotations

import sqlite3

from lattice_brain.graph.schema import (
    TEMPORAL_PREDICATE_SQL,
    KGStoreV2,
)
from tests.unit.test_t2_support import make_store, seed

# The nodes_v2/edges_v2 shape shipped before 11.1.0 — no valid_from/valid_to.
PRE_TEMPORAL_SQL = """
CREATE TABLE nodes_v2 (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, legacy_type TEXT, label TEXT NOT NULL,
  summary TEXT, attrs TEXT NOT NULL DEFAULT '{}', embedding BLOB, owner_id TEXT,
  workspace_id TEXT, visibility TEXT NOT NULL DEFAULT 'private', superseded_by TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, style TEXT, tone TEXT,
  importance_score REAL NOT NULL DEFAULT 0.0, last_used TEXT
);
CREATE TABLE edges_v2 (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, type TEXT NOT NULL,
  legacy_type TEXT NOT NULL DEFAULT '', weight REAL NOT NULL DEFAULT 1.0,
  confidence REAL NOT NULL DEFAULT 1.0, evidence TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL, UNIQUE(source, target, type, legacy_type)
);
"""


def _legacy_db(tmp_path):
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(PRE_TEMPORAL_SQL)
    conn.execute(
        "INSERT INTO nodes_v2(id, type, label, created_at, updated_at) "
        "VALUES ('keep', 'CONCEPT', 'Kept fact', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO edges_v2(id, source, target, type, created_at) "
        "VALUES ('e-keep', 'keep', 'keep', 'MENTIONS', '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()
    return db


def _columns(db, table):
    conn = sqlite3.connect(db)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_existing_brain_gains_temporal_columns_without_losing_rows(tmp_path):
    db = _legacy_db(tmp_path)
    KGStoreV2(db).init_schema()

    assert {"valid_from", "valid_to", "superseded_by"} <= set(_columns(db, "nodes_v2"))
    assert {"valid_from", "valid_to", "superseded_by"} <= set(_columns(db, "edges_v2"))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM edges_v2").fetchone()[0] == 1
        # NULL, never '' — an empty string sorts before every timestamp and
        # would read as "valid since the beginning of time".
        assert conn.execute(
            "SELECT valid_from, valid_to FROM nodes_v2 WHERE id='keep'"
        ).fetchone() == (None, None)
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    db = _legacy_db(tmp_path)
    store = KGStoreV2(db)
    store.init_schema()
    first = (_columns(db, "nodes_v2"), _columns(db, "edges_v2"))
    store.init_schema()
    store.init_schema()
    assert (_columns(db, "nodes_v2"), _columns(db, "edges_v2")) == first
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0] == 1
    finally:
        conn.close()


def test_pre_v4_edge_identity_rebuild_still_lands_temporal_columns(tmp_path):
    """A DB old enough to need the edges_v2 identity rebuild also gets stamped."""
    db = tmp_path / "prev4.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        PRE_TEMPORAL_SQL.replace(
            "UNIQUE(source, target, type, legacy_type)", "UNIQUE(source, target, legacy_type)"
        )
    )
    conn.execute(
        "INSERT INTO nodes_v2(id, type, label, created_at, updated_at) "
        "VALUES ('a', 'CONCEPT', 'A', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO edges_v2(id, source, target, type, created_at) "
        "VALUES ('e1', 'a', 'a', 'MENTIONS', '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    KGStoreV2(db).init_schema()
    assert {"valid_from", "valid_to"} <= set(_columns(db, "edges_v2"))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM edges_v2").fetchone()[0] == 1
    finally:
        conn.close()


def test_projection_views_pass_temporal_columns_through_as_null(tmp_path):
    store = make_store(tmp_path)
    seed(store, [("n1", "Concept", "Alpha", "first")])
    with store._connect() as conn:
        node = conn.execute(
            "SELECT valid_from, valid_to, superseded_by FROM kgv2_nodes WHERE id='n1'"
        ).fetchone()
        assert tuple(node) == (None, None, None)
        edge_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(kgv2_edges)")
        }
    assert {"valid_from", "valid_to", "superseded_by"} <= edge_columns


def test_stamp_writes_only_the_supplied_fields(tmp_path):
    store = make_store(tmp_path)
    seed(store, [("n1", "Concept", "Alpha", "first")])
    v2 = KGStoreV2(store.db_path)

    assert v2.stamp_node_validity("n1") is False  # nothing supplied → no write
    assert v2.stamp_node_validity("missing", valid_to="2026-01-01T00:00:00") is False
    assert v2.stamp_node_validity("n1", valid_to="2026-01-01T00:00:00") is True
    assert v2.stamp_node_validity("n1", superseded_by="n2") is True

    with store._connect() as conn:
        row = conn.execute(
            "SELECT valid_from, valid_to, superseded_by FROM nodes_v2 WHERE id='n1'"
        ).fetchone()
    # valid_from was never supplied, so it stays NULL rather than being cleared
    # or defaulted — the second stamp did not undo the first.
    assert tuple(row) == (None, "2026-01-01T00:00:00", "n2")


def test_touch_node_counts_accesses_and_tolerates_a_missing_row(tmp_path):
    store = make_store(tmp_path)
    seed(store, [("n1", "Concept", "Alpha", "first")])
    v2 = KGStoreV2(store.db_path)

    assert v2.touch_node("n1", at="2026-05-05T05:05:05") is True
    assert v2.touch_node("n1") is True
    assert v2.touch_node("ghost") is False
    with store._connect() as conn:
        score, last_used = conn.execute(
            "SELECT importance_score, last_used FROM nodes_v2 WHERE id='n1'"
        ).fetchone()
    assert score == 2.0
    assert last_used != "2026-05-05T05:05:05"  # the second touch moved it


def test_temporal_predicate_uses_created_at_as_the_valid_from_fallback():
    # The fallback is the whole reason an existing Brain keeps answering after
    # the migration; assert the SQL says so rather than trusting a comment.
    assert "COALESCE(valid_from, created_at)" in TEMPORAL_PREDICATE_SQL
    assert "valid_to IS NULL" in TEMPORAL_PREDICATE_SQL
