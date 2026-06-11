"""T3b-2: owner/workspace/visibility threading into nodes_v2 at the write door.

NULL workspace_id = legacy-global; unscoped rows get visibility='legacy' (the
'private' column default must never silently privatize machine-shared data).
Scoped re-upserts may enrich a row; unscoped re-upserts must not strip scope.
"""

import sqlite3

from knowledge_graph import KnowledgeGraphStore
from kg_schema import KGStoreV2


def _store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _v2_row(store, node_id):
    with store._connect() as conn:
        return conn.execute(
            "SELECT owner_id, workspace_id, visibility FROM nodes_v2 WHERE id=?", (node_id,)
        ).fetchone()


def test_unscoped_write_is_legacy_global(tmp_path):
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "n1", "Concept", "unscoped", "", {})
    row = _v2_row(store, "n1")
    assert row["workspace_id"] is None
    assert row["visibility"] == "legacy"


def test_scoped_write_populates_columns(tmp_path):
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "n2", "Concept", "scoped", "", {},
            owner="alice@x.com", workspace_id="org-acme", visibility="workspace",
        )
    row = _v2_row(store, "n2")
    assert (row["owner_id"], row["workspace_id"], row["visibility"]) == (
        "alice@x.com", "org-acme", "workspace",
    )


def test_metadata_hints_resolve_scope(tmp_path):
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "n3", "Concept", "hinted", "",
            {"user_email": "bob@x.com", "workspace_id": "org-acme"},
        )
    row = _v2_row(store, "n3")
    assert (row["owner_id"], row["workspace_id"], row["visibility"]) == (
        "bob@x.com", "org-acme", "workspace",
    )


def test_unscoped_reupsert_does_not_strip_scope(tmp_path):
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "n4", "Concept", "first", "", {},
            owner="alice@x.com", workspace_id="org-acme", visibility="workspace",
        )
        store._upsert_node(conn, "n4", "Concept", "renamed", "", {})
    row = _v2_row(store, "n4")
    assert (row["owner_id"], row["workspace_id"], row["visibility"]) == (
        "alice@x.com", "org-acme", "workspace",
    )


def test_old_db_heals_workspace_id_column_in_place(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE kg_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE nodes_v2 (id TEXT PRIMARY KEY, type TEXT NOT NULL, legacy_type TEXT,
         label TEXT NOT NULL, summary TEXT, attrs TEXT NOT NULL DEFAULT '{}', embedding BLOB,
         owner_id TEXT, visibility TEXT NOT NULL DEFAULT 'private', created_at TEXT NOT NULL,
         updated_at TEXT NOT NULL, style TEXT, tone TEXT,
         importance_score REAL NOT NULL DEFAULT 0.0, last_used TEXT);
        INSERT INTO nodes_v2 (id, type, label, created_at, updated_at)
         VALUES ('keepme', 'CONCEPT', 'data', 't', 't');
        """
    )
    conn.commit()
    conn.close()
    KGStoreV2(db).init_schema()
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes_v2)").fetchall()}
    assert "workspace_id" in cols, "additive column must heal in place"
    assert conn.execute("SELECT count(*) FROM nodes_v2").fetchone()[0] == 1, "data preserved"
