"""wp23 coverage — v2 projection plumbing, store construction, sensitivity flag.

Everything here runs against a real ``KnowledgeGraphStore`` over SQLite in
``tmp_path``. The degraded paths (FTS5 missing, ``kg_meta``/``nodes_v2`` absent,
``KGStoreV2`` unavailable) are produced by pointing a single call at a
deliberately bare database or by blanking the module-level ``KGStoreV2`` /
``EdgeType`` seams — never by editing product code.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ``KGStoreV2`` / ``EdgeType`` are globals of the v2_schema submodule, which
# is where the projection code reads them from. Blanking them on the package
# would rebind a copy nothing looks at.
from lattice_brain.graph.projection import v2_schema as projection_mod
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.storage.base import StorageCapabilities


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


@contextmanager
def _bare_connection(path: Path, script: str = ""):
    """A connection to a database that knows nothing about the graph schema."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    if script:
        conn.executescript(script)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _redirect_connect(monkeypatch, store, path: Path, script: str = "") -> None:
    monkeypatch.setattr(store, "_connect", lambda: _bare_connection(path, script))


# ── store construction guards ────────────────────────────────────────────────


class _UnavailableEngine:
    def capabilities(self):
        return StorageCapabilities(
            engine="sqlite", available=False, reason="brain disk is read-only"
        )


class _PostgresEngine:
    def capabilities(self):
        return StorageCapabilities(engine="postgres", available=True)


def test_store_refuses_an_unavailable_storage_engine(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="brain disk is read-only"):
        KnowledgeGraphStore(
            tmp_path / "kg.sqlite",
            tmp_path / "blobs",
            storage_engine=_UnavailableEngine(),
        )


def test_store_refuses_a_non_sqlite_storage_engine(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="requires SQLiteEngine"):
        KnowledgeGraphStore(
            tmp_path / "kg.sqlite",
            tmp_path / "blobs",
            storage_engine=_PostgresEngine(),
        )


# ── FTS degradation ──────────────────────────────────────────────────────────


def test_init_fts_reports_an_unavailable_fts_build_instead_of_faking_it(
    tmp_path, caplog
) -> None:
    store = _store(tmp_path)
    store._FTS_SQL = "CREATE VIRTUAL TABLE bad_fts USING no_such_fts_module(x);"

    with caplog.at_level("INFO"):
        store._init_fts()

    assert store._fts_enabled is False
    assert any("FTS5 trigram index unavailable" in r.message for r in caplog.records)


def test_fts_match_ids_returns_nothing_when_the_index_table_is_absent(
    tmp_path,
) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        assert store._fts_match_ids(conn, "lattice", 5) == []


# ── v2 schema init / backup / backfill ───────────────────────────────────────


def test_v2_schema_init_is_a_no_op_without_the_v2_schema_module(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    assert store._v2_projection_available is True
    monkeypatch.setattr(projection_mod, "KGStoreV2", None)

    store._init_v2_schema()

    # the early return leaves the previously resolved availability untouched
    assert store._v2_projection_available is True


def test_backup_before_flip_skips_a_database_that_does_not_exist(tmp_path) -> None:
    store = _store(tmp_path)
    store.db_path = tmp_path / "never-created.sqlite"

    assert store._backup_before_v2_flip() is None


def test_backup_before_flip_treats_an_unreadable_node_count_as_empty(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    # kg_meta exists but carries no write-master stamp, and there is no `nodes`
    # table at all — the COUNT must degrade to "nothing to back up".
    _redirect_connect(
        monkeypatch,
        store,
        tmp_path / "stamp-only.sqlite",
        "CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT);",
    )

    assert store._backup_before_v2_flip() is None
    assert not (tmp_path / "backups").exists()


def test_backfill_wrapper_swallows_a_projection_failure(
    tmp_path, monkeypatch, caplog
) -> None:
    store = _store(tmp_path)

    def _boom(conn, *, force=False):
        raise RuntimeError("projection table vanished")

    monkeypatch.setattr(store, "_backfill_v2_on", _boom)

    with caplog.at_level("WARNING"):
        store._backfill_v2_if_needed(force=True)

    assert any("v2 backfill skipped" in r.message for r in caplog.records)


# ── node / edge projection guards ────────────────────────────────────────────


def test_node_projection_without_the_v2_schema(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(projection_mod, "KGStoreV2", None)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        assert store._v2_project_node(conn, "n1", "Document", "T", None, None) is None
        with pytest.raises(RuntimeError, match="v2 schema is unavailable"):
            store._v2_project_node(
                conn, "n1", "Document", "T", None, None, strict=True
            )


def test_node_projection_failure_is_debug_logged_unless_strict(
    tmp_path, caplog
) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        with caplog.at_level("DEBUG"):
            store._v2_project_node(conn, "n1", "Document", "T", "sum", '{"a": 1}')
        assert any("v2 node projection skipped" in r.message for r in caplog.records)

        with pytest.raises(sqlite3.OperationalError):
            store._v2_project_node(
                conn, "n1", "Document", "T", "sum", '{"a": 1}', strict=True
            )


def test_edge_projection_without_the_v2_schema(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(projection_mod, "KGStoreV2", None)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        assert store._v2_project_edge(conn, "a", "b", "MENTIONS", 1.0, None) is None
        with pytest.raises(RuntimeError, match="v2 schema is unavailable"):
            store._v2_project_edge(
                conn, "a", "b", "MENTIONS", 1.0, None, strict=True
            )


def test_canonical_legacy_label_collapses_to_the_empty_identity(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "src", "Document", "Source doc")
        store._upsert_node(conn, "dst", "Concept", "Target concept")
        store._v2_project_edge(
            conn, "src", "dst", "MENTIONS", 0.9, "{}", legacy_type="MENTIONS"
        )
        row = conn.execute(
            "SELECT legacy_type, type FROM edges_v2 WHERE source=? AND target=?",
            ("src", "dst"),
        ).fetchone()

    # An explicit legacy_type that IS the canonical value adds nothing, so the
    # effective identity key stays (source, target, type).
    assert row["type"] == "MENTIONS"
    assert row["legacy_type"] == ""


def test_unmappable_legacy_label_is_tolerated(tmp_path, monkeypatch) -> None:
    class _EdgeTypeStub:
        @staticmethod
        def from_legacy(label):
            if label == "unmappable-verb":
                raise ValueError("no mapping for this label")
            return SimpleNamespace(value="MENTIONS")

    store = _store(tmp_path)
    monkeypatch.setattr(projection_mod, "EdgeType", _EdgeTypeStub)

    with store._connect() as conn:
        store._upsert_node(conn, "src2", "Document", "Source doc")
        store._upsert_node(conn, "dst2", "Concept", "Target concept")
        store._v2_project_edge(
            conn,
            "src2",
            "dst2",
            "MENTIONS",
            0.5,
            "{}",
            legacy_type="unmappable-verb",
        )
        row = conn.execute(
            "SELECT legacy_type FROM edges_v2 WHERE source=? AND target=?",
            ("src2", "dst2"),
        ).fetchone()

    # the label survived verbatim instead of the projection blowing up
    assert row["legacy_type"] == "unmappable-verb"


def test_edge_projection_failure_is_debug_logged_unless_strict(
    tmp_path, caplog
) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        with caplog.at_level("DEBUG"):
            store._v2_project_edge(conn, "a", "b", "MENTIONS", 1.0, "{}")
        assert any("v2 edge projection skipped" in r.message for r in caplog.records)


# ── promotion writing / pending queue ────────────────────────────────────────


def test_promotion_links_only_sources_from_this_curate_run(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc-in", "Document", "Scanned document")
        store._upsert_node(conn, "doc-out", "Document", "Not scanned this run")
        result = store._write_promotion(
            conn,
            {
                "label": "Retrieval Policy",
                "importance": 2.5,
                "aliases": ["policy"],
                "sources": ["doc-in", "doc-out"],
            },
            valid_source_ids={"doc-in"},
        )

    assert result["linked_sources"] == 1
    assert result["node_id"].startswith("topic:")
    neighbors = store.neighbors(result["node_id"])
    assert {n["id"] for n in neighbors["neighbors"]} == {"doc-in"}


def test_promotion_after_review_skips_sources_deleted_since_the_proposal(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc-alive", "Document", "Still here")
        result = store._write_promotion(
            conn,
            {
                "id": "topic:reviewed",
                "label": "Reviewed Topic",
                "importance": 1.75,
                "sources": ["doc-alive", "doc-deleted"],
            },
        )

    assert result["node_id"] == "topic:reviewed"
    assert result["linked_sources"] == 1


def test_pending_promotions_degrade_on_a_missing_meta_table(tmp_path) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        assert store._read_pending_promotions(conn) == []


def test_pending_promotions_ignore_corrupt_and_non_list_payloads(tmp_path) -> None:
    store = _store(tmp_path)

    with store._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('pending_promotions', ?)",
            ("{not json",),
        )
        assert store._read_pending_promotions(conn) == []

        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('pending_promotions', ?)",
            (json.dumps({"id": "topic:x"}),),
        )
        assert store._read_pending_promotions(conn) == []

        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('pending_promotions', ?)",
            (json.dumps([{"id": "topic:x"}, {"no": "id"}, "junk"]),),
        )
        assert store._read_pending_promotions(conn) == [{"id": "topic:x"}]


def test_last_noise_curate_at_is_none_when_the_meta_table_is_unreadable(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    _redirect_connect(monkeypatch, store, tmp_path / "bare.sqlite")

    assert store.last_noise_curate_at() is None


# ── v2 delete mirrors + sync report ──────────────────────────────────────────


def test_delete_mirrors_are_no_ops_without_the_v2_schema(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(projection_mod, "KGStoreV2", None)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        assert store._v2_delete_nodes(conn, ["n1"]) is None
        assert store._v2_delete_edges_from(conn, "n1") is None


def test_delete_mirror_ignores_an_empty_id_list(tmp_path) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        # returns before touching the (absent) nodes_v2 table
        assert store._v2_delete_nodes(conn, []) is None


def test_delete_mirrors_are_debug_logged_when_the_projection_is_missing(
    tmp_path, caplog
) -> None:
    store = _store(tmp_path)

    with _bare_connection(tmp_path / "bare.sqlite") as conn:
        with caplog.at_level("DEBUG"):
            store._v2_delete_nodes(conn, ["n1"])
            store._v2_delete_edges_from(conn, "n1")

    messages = [r.message for r in caplog.records]
    assert any("v2 node delete mirror skipped" in m for m in messages)
    assert any("v2 edge delete mirror skipped" in m for m in messages)


# ── node sensitivity flag (write_master) ─────────────────────────────────────


def test_set_node_sensitivity_reports_a_missing_node(tmp_path) -> None:
    store = _store(tmp_path)

    assert store.set_node_sensitivity("ghost", local_only=True) == {
        "ok": False,
        "node_id": "ghost",
        "reason": "node not found",
    }


def _legacy_metadata(store: KnowledgeGraphStore, node_id: str) -> dict:
    """Read the row ``set_node_sensitivity`` actually writes.

    The flag is written straight to the legacy ``nodes`` table; the store's
    read path resolves through the ``kgv2_*`` projection, which this write does
    not update (reported as a product finding, not worked around here).
    """
    with store._connect() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
    return json.loads(row["metadata_json"])


def test_set_node_sensitivity_marks_and_unmarks_a_node(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "note-1", "Document", "Private note")

    marked = store.set_node_sensitivity(
        "note-1", local_only=True, reason="contains a home address"
    )

    assert marked["ok"] is True
    assert marked["local_only"] is True
    assert marked["reason"] == "contains a home address"
    assert _legacy_metadata(store, "note-1") == {
        "local_only": True,
        "local_only_reason": "contains a home address",
    }

    unmarked = store.set_node_sensitivity("note-1", local_only=False)

    assert unmarked["local_only"] is False
    assert unmarked["reason"] is None
    # unmarking clears the justification with the flag — no stale reason lingers
    assert _legacy_metadata(store, "note-1") == {}


def test_set_node_sensitivity_recovers_from_a_corrupt_metadata_row(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "note-2", "Document", "Corrupt metadata")

    # A row that predates the json_valid CHECK: written with the constraint
    # suspended so the reader's tolerance is exercised, not simulated.
    raw = sqlite3.connect(str(store.db_path))
    try:
        raw.execute("PRAGMA ignore_check_constraints=ON")
        raw.execute(
            "UPDATE nodes SET metadata_json='{corrupt' WHERE id=?", ("note-2",)
        )
        raw.commit()
    finally:
        raw.close()

    result = store.set_node_sensitivity("note-2", local_only=True, reason=None)

    assert result["ok"] is True
    assert result["reason"] == "marked by the user"
    # the unparseable metadata is replaced rather than propagated
    assert _legacy_metadata(store, "note-2") == {
        "local_only": True,
        "local_only_reason": "marked by the user",
    }
