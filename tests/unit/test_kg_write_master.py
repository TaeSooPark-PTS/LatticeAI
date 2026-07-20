import json
import sqlite3

import pytest

from lattice_brain.graph.store import KnowledgeGraphStore


def _legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE graph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE nodes (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          title TEXT NOT NULL,
          summary TEXT,
          metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
          raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE edges (
          id TEXT PRIMARY KEY,
          from_node TEXT NOT NULL,
          to_node TEXT NOT NULL,
          type TEXT NOT NULL,
          weight REAL NOT NULL DEFAULT 1.0,
          metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
          created_at TEXT NOT NULL,
          UNIQUE(from_node, to_node, type)
        );
        INSERT INTO nodes VALUES
          ('legacy:one', 'Concept', 'Legacy One', 'before flip', '{}', '{}', 't', 't');
        """
    )
    conn.commit()
    conn.close()


def test_v2_write_master_stamps_format_and_creates_preflip_backup(tmp_path):
    db = tmp_path / "kg.sqlite"
    _legacy_db(db)

    store = KnowledgeGraphStore(db, tmp_path / "blobs")

    with store._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            conn.execute(
                "SELECT value FROM kg_meta WHERE key='db_format_version'"
            ).fetchone()[0]
            == "4"
        )
        assert conn.execute(
            "SELECT value FROM kg_meta WHERE key='v2_write_mastered_at'"
        ).fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0] == 1

    backups = list((tmp_path / "backups").glob("kg.pre-v2-write-master.*.sqlite"))
    assert len(backups) == 1


def test_newer_db_format_refuses_to_open(tmp_path):
    db = tmp_path / "kg.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version=999")
    conn.close()

    with pytest.raises(RuntimeError, match="newer than this build"):
        KnowledgeGraphStore(db, tmp_path / "blobs")


def test_v2_write_failure_does_not_create_legacy_only_node(tmp_path, monkeypatch):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("v2 unavailable")

    monkeypatch.setattr(store, "_v2_project_node", fail)

    with pytest.raises(sqlite3.OperationalError):
        with store._connect() as conn:
            store._upsert_node(conn, "concept:bad", "Concept", "Bad", metadata={"x": 1})

    with store._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE id='concept:bad'"
            ).fetchone()[0]
            == 0
        )


def test_v2_first_write_keeps_legacy_projection(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with store._connect() as conn:
        store._upsert_node(
            conn,
            "concept:scoped",
            "Concept",
            "Scoped",
            "summary",
            metadata={"workspace_id": "org-one"},
        )
        store._upsert_node(conn, "concept:target", "Concept", "Target")
        store._upsert_edge(conn, "concept:scoped", "concept:target", "포함함")

    with store._connect() as conn:
        legacy = conn.execute(
            "SELECT type, title, metadata_json FROM nodes WHERE id='concept:scoped'"
        ).fetchone()
        v2 = conn.execute(
            "SELECT type, legacy_type, workspace_id FROM nodes_v2 WHERE id='concept:scoped'"
        ).fetchone()
        edge = conn.execute(
            "SELECT type, metadata_json FROM edges WHERE from_node='concept:scoped'"
        ).fetchone()

    assert legacy["type"] == "Concept"
    assert json.loads(legacy["metadata_json"])["workspace_id"] == "org-one"
    assert (v2["type"], v2["legacy_type"], v2["workspace_id"]) == (
        "CONCEPT",
        "Concept",
        "org-one",
    )
    assert edge["type"] == "CONTAINS"
    assert json.loads(edge["metadata_json"])["legacy_label"] == "포함함"
