"""T3b prerequisite: edges_v2 identity rebuild + canonical enum round-trips.

The pre-v4 UNIQUE(source, target, legacy_type) would silently merge two
distinct canonical edge types between the same node pair once native writes
stop minting legacy strings (legacy_type=''). v4 rebuilds the table to
UNIQUE(source, target, type, legacy_type) — data-preserving and re-entrant.
"""

import sqlite3
from pathlib import Path

from kg_schema import EdgeType, KGStoreV2, NodeType

_OLD_SCHEMA = """
CREATE TABLE kg_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE nodes_v2 (id TEXT PRIMARY KEY, type TEXT NOT NULL, legacy_type TEXT, label TEXT NOT NULL,
 summary TEXT, attrs TEXT NOT NULL DEFAULT '{}', embedding BLOB, owner_id TEXT,
 visibility TEXT NOT NULL DEFAULT 'private', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 style TEXT, tone TEXT, importance_score REAL NOT NULL DEFAULT 0.0, last_used TEXT);
CREATE TABLE edges_v2 (id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
 type TEXT NOT NULL, legacy_type TEXT NOT NULL DEFAULT '', weight REAL NOT NULL DEFAULT 1.0,
 confidence REAL NOT NULL DEFAULT 1.0, evidence TEXT NOT NULL DEFAULT '[]',
 metadata TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL,
 UNIQUE(source, target, legacy_type),
 FOREIGN KEY(source) REFERENCES nodes_v2(id) ON DELETE CASCADE,
 FOREIGN KEY(target) REFERENCES nodes_v2(id) ON DELETE CASCADE);
INSERT INTO nodes_v2 (id,type,label,created_at,updated_at)
 VALUES ('a','CONCEPT','A','t','t'),('b','CONCEPT','B','t','t');
INSERT INTO edges_v2 (id,source,target,type,legacy_type,created_at)
 VALUES ('e1','a','b','MENTIONS','언급함','t'),
        ('e2','a','b','MENTIONS','mentions','t');
"""


def _old_db(tmp_path: Path) -> Path:
    db = tmp_path / "kg.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    conn.close()
    return db


def test_rebuild_upgrades_constraint_preserving_rows(tmp_path):
    db = _old_db(tmp_path)
    KGStoreV2(db).init_schema()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='edges_v2'").fetchone()["sql"]
    assert "UNIQUE(source, target, type, legacy_type)" in sql
    rows = {(r["id"], r["legacy_type"]) for r in conn.execute("SELECT id, legacy_type FROM edges_v2")}
    assert rows == {("e1", "언급함"), ("e2", "mentions")}, "distinct legacy strings must survive"


def test_rebuild_is_reentrant(tmp_path):
    db = _old_db(tmp_path)
    store = KGStoreV2(db)
    store.init_schema()
    store.init_schema()  # second run must be a no-op, not a failure
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM edges_v2").fetchone()[0] == 2


def test_canonical_edge_types_coexist_between_one_pair(tmp_path):
    db = _old_db(tmp_path)
    KGStoreV2(db).init_schema()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO edges_v2 (id,source,target,type,created_at) VALUES ('n1','a','b','MENTIONS','t')")
    conn.execute("INSERT INTO edges_v2 (id,source,target,type,created_at) VALUES ('n2','a','b','CONTAINS','t')")
    assert conn.execute("SELECT count(*) FROM edges_v2").fetchone()[0] == 4


def test_fresh_db_gets_new_identity(tmp_path):
    db = tmp_path / "fresh.sqlite"
    KGStoreV2(db).init_schema()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='edges_v2'").fetchone()["sql"]
    assert "UNIQUE(source, target, type, legacy_type)" in sql


def test_canonical_enum_values_round_trip():
    assert not [t for t in NodeType if NodeType.from_legacy(t.value) is not t]
    assert not [t for t in EdgeType if EdgeType.from_legacy(t.value) is not t]
    # Legacy aliases keep their mappings.
    assert NodeType.from_legacy("codefile") is NodeType.CODE_FILE
    assert NodeType.from_legacy("Topic") is NodeType.TOPIC
    assert EdgeType.from_legacy("언급함") is EdgeType.MENTIONS
