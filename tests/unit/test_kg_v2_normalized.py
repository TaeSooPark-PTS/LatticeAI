"""Normalized v2 schema contract.

Locks in the KGStoreV2 redesign:
  * no ``attrs._kg`` passthrough — summary/metadata are first-class columns,
  * legacy free-string types are normalized into a NodeType/EdgeType superset
    while the raw string is preserved losslessly in ``legacy_type``,
  * edge metadata lives in its own ``metadata`` column (``evidence`` freed),
  * edge identity follows the raw legacy type (no collapse under normalization),
  * a projection-layout bump migrates an old-shape v2 in place, non-destructively.
"""

import json
import sqlite3

import pytest

kg = pytest.importorskip("knowledge_graph")
ks = pytest.importorskip("kg_schema")


def _insert_node(conn, nid, ntype, title, summary, meta):
    conn.execute(
        "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (nid, ntype, title, summary, json.dumps(meta, ensure_ascii=False), "{}",
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )


def _insert_edge(conn, eid, a, b, etype, w, meta):
    conn.execute(
        "INSERT INTO edges(id,from_node,to_node,type,weight,metadata_json,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (eid, a, b, etype, w, json.dumps(meta, ensure_ascii=False), "2026-02-01T00:00:00"),
    )


@pytest.fixture()
def store(tmp_path):
    return kg.KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def test_no_attrs_kg_passthrough(store):
    """The projection stores summary/metadata as columns, never in attrs._kg."""
    with store._connect() as conn:
        _insert_node(conn, "computer:1", "Computer", "My Mac", "the workstation", {"os": "darwin"})
    store._backfill_v2_if_needed(force=True)

    with store._connect() as conn:
        row = conn.execute(
            "SELECT type, legacy_type, summary, attrs FROM nodes_v2 WHERE id='computer:1'"
        ).fetchone()
    attrs = json.loads(row["attrs"])
    assert "_kg" not in attrs, "attrs._kg passthrough must be gone"
    assert attrs == {"os": "darwin"}, "attrs now holds the real metadata directly"
    assert row["summary"] == "the workstation", "summary promoted to a first-class column"


def test_type_normalized_legacy_preserved(store):
    """Known legacy types normalize into the superset; the raw string is kept."""
    with store._connect() as conn:
        _insert_node(conn, "computer:1", "Computer", "Mac", "", {})
        _insert_node(conn, "evt:1", "checkout_event", "Bought", "", {})   # dynamic/unknown
    store._backfill_v2_if_needed(force=True)

    with store._connect() as conn:
        rows = {r["id"]: r for r in conn.execute(
            "SELECT id, type, legacy_type FROM nodes_v2")}
    # known legacy type → first-class superset member, raw preserved
    assert rows["computer:1"]["type"] == "COMPUTER"
    assert rows["computer:1"]["legacy_type"] == "Computer"
    # unknown/dynamic type → CONCEPT fallback, but raw string is lossless
    assert rows["evt:1"]["type"] == "CONCEPT"
    assert rows["evt:1"]["legacy_type"] == "checkout_event"

    # the read view reconstructs the exact legacy type string
    store._read_from_v2 = True
    types = {n["id"]: n["type"] for n in store.graph(limit=50)["nodes"]}
    # Computer is graph-visible; the view must surface the legacy label
    assert types.get("computer:1") == "Computer"


def test_edge_metadata_in_metadata_column(store):
    with store._connect() as conn:
        _insert_node(conn, "a", "Concept", "A", "", {})
        _insert_node(conn, "b", "Concept", "B", "", {})
        _insert_edge(conn, "e1", "a", "b", "related_to", 1.0, {"source": "scan", "confidence": 0.7})
    store._backfill_v2_if_needed(force=True)

    with store._connect() as conn:
        row = conn.execute(
            "SELECT type, legacy_type, evidence, metadata, confidence FROM edges_v2 WHERE id='e1'"
        ).fetchone()
    assert row["type"] == "RELATED_TO" and row["legacy_type"] == "related_to"
    assert row["evidence"] == "[]", "evidence is no longer abused to carry metadata"
    assert json.loads(row["metadata"]) == {"source": "scan", "confidence": 0.7}
    assert row["confidence"] == pytest.approx(0.7)


def test_edge_identity_survives_normalization_collision(store):
    """Two legacy edge types between the same pair that normalize to the same
    EdgeType must remain two distinct edges (identity = raw legacy type)."""
    with store._connect() as conn:
        _insert_node(conn, "a", "Concept", "A", "", {})
        _insert_node(conn, "b", "Concept", "B", "", {})
        _insert_edge(conn, "e1", "a", "b", "mentions", 1.0, {})   # → MENTIONS
        _insert_edge(conn, "e2", "a", "b", "관련됨", 1.0, {})       # → MENTIONS too
    store._backfill_v2_if_needed(force=True)

    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM edges_v2 WHERE source='a' AND target='b'").fetchone()[0]
        legacy_types = sorted(
            r[0] for r in conn.execute(
                "SELECT legacy_type FROM edges_v2 WHERE source='a' AND target='b'")
        )
    assert n == 2, "distinct legacy edges must not collapse under normalization"
    assert legacy_types == sorted(["mentions", "관련됨"])

    # legacy and v2 reads agree on the edge set
    def edge_pairs():
        return sorted((e["from"], e["to"], e["type"]) for e in store.graph(limit=50)["edges"])
    store._read_from_v2 = False
    legacy = edge_pairs()
    store._read_from_v2 = True
    v2 = edge_pairs()
    assert legacy == v2


def test_edge_reupsert_tracks_confidence_and_updates_in_place(store):
    """Re-upserting the same (source,target,legacy_type) edge updates in place and
    refreshes the dedicated confidence column (kept in sync with metadata)."""
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Concept", "A", "", {})
        store._upsert_node(conn, "b", "Concept", "B", "", {})
        store._upsert_edge(conn, "a", "b", "related_to", 1.0, {"confidence": 0.9})
    with store._connect() as conn:
        store._upsert_edge(conn, "a", "b", "related_to", 1.0, {"confidence": 0.3})

    with store._connect() as conn:
        rows = conn.execute(
            "SELECT confidence, metadata FROM edges_v2 WHERE source='a' AND target='b'"
        ).fetchall()
    assert len(rows) == 1, "same legacy edge must update in place, not duplicate"
    assert rows[0]["confidence"] == pytest.approx(0.3), "confidence column refreshed on re-upsert"
    assert json.loads(rows[0]["metadata"])["confidence"] == pytest.approx(0.3)


def test_edge_distinct_legacy_type_adds_row_on_reupsert(store):
    """A different raw legacy type between the same pair is a NEW edge even when it
    normalizes to the same EdgeType as an existing one."""
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Concept", "A", "", {})
        store._upsert_node(conn, "b", "Concept", "B", "", {})
        store._upsert_edge(conn, "a", "b", "mentions", 1.0, {})   # → MENTIONS
        store._upsert_edge(conn, "a", "b", "관련됨", 1.0, {})       # → MENTIONS, distinct legacy
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM edges_v2 WHERE source='a' AND target='b'").fetchone()[0]
    assert n == 2


def test_view_is_byte_faithful_to_legacy(store):
    """The kgv2_* views reproduce the legacy row strings verbatim — including
    unsorted multi-key metadata (no key re-sorting), NULL summary, and
    over-length title/summary that bypassed the _upsert_* truncation."""
    with store._connect() as conn:
        # direct inserts (not via _upsert_*) with awkward values
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("n1", "Concept", "T" * 300, None, '{"z":1,"a":2,"m":3}', "{}", "2026-01-01", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO edges(id,from_node,to_node,type,weight,metadata_json,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("n1", "n1", "n1", "self", 1.0, '{"k2":"b","k1":"a"}', "2026-02-01"),
        )
    store._backfill_v2_if_needed(force=True)

    with store._connect() as conn:
        ln = conn.execute("SELECT title,summary,metadata_json FROM nodes WHERE id='n1'").fetchone()
        vn = conn.execute("SELECT title,summary,metadata_json FROM kgv2_nodes WHERE id='n1'").fetchone()
        le = conn.execute("SELECT type,metadata_json FROM edges WHERE id='n1'").fetchone()
        ve = conn.execute("SELECT type,metadata_json FROM kgv2_edges WHERE id='n1'").fetchone()
    assert vn["title"] == ln["title"], "title verbatim (no projection-side truncation divergence)"
    assert vn["summary"] is None and ln["summary"] is None, "NULL summary preserved, not coerced to ''"
    assert vn["metadata_json"] == ln["metadata_json"] == '{"z":1,"a":2,"m":3}', "metadata key order preserved"
    assert ve["type"] == le["type"] == "self", "edge legacy type round-trips"
    assert ve["metadata_json"] == le["metadata_json"] == '{"k2":"b","k1":"a"}', "edge metadata verbatim"


def test_dual_write_invariant_holds_through_writes_and_deletes(store):
    """Every legacy write goes through _upsert_* (dual-write) and every delete is
    mirrored, so legacy and v2 id-sets stay identical. _v2_sync_report is the
    runtime guard for a bypassed write path."""
    with store._connect() as conn:
        store._upsert_node(conn, "conv:1", "Conversation", "c1", "", {})
        store._upsert_node(conn, "concept:x", "Concept", "X", "about x", {"k": "v"})
        store._upsert_node(conn, "concept:y", "Concept", "Y", "about y", {})
        store._upsert_edge(conn, "conv:1", "concept:x", "contains", 1.0, {})
        store._upsert_edge(conn, "concept:x", "concept:y", "mentions", 0.8, {})

    rep = store._v2_sync_report()
    assert rep["in_sync"], f"dual-write drift: {rep}"
    assert rep["nodes_legacy"] == rep["nodes_v2"] == 3
    assert rep["edges_legacy"] == rep["edges_v2"] == 2

    # deletes are mirrored too → still in sync
    store.clear_all()
    rep = store._v2_sync_report()
    assert rep["in_sync"], f"delete mirror drift: {rep}"
    assert rep["nodes_v2"] == 0 and rep["edges_v2"] == 0


def test_migration_is_atomic_on_failure(tmp_path, monkeypatch):
    """A crash mid-migration rolls back: legacy intact, projection_version stays
    stale so the next startup retries, and a clean restart self-heals."""
    db = tmp_path / "kg.sqlite"
    blobs = tmp_path / "blobs"
    store = kg.KnowledgeGraphStore(db, blobs)
    with store._connect() as conn:
        _insert_node(conn, "c1", "Concept", "Alpha", "body", {"k": "v"})
    # force the projection stale, then make the rebuild explode mid-flight
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE kg_meta SET value='0' WHERE key='projection_version'")

    def boom(self, conn, *, force=False):
        conn.execute("DELETE FROM nodes_v2")          # partial work inside the txn
        raise RuntimeError("simulated mid-migration crash")

    monkeypatch.setattr(kg.KnowledgeGraphStore, "_backfill_v2_on", boom)
    kg.KnowledgeGraphStore(db, blobs)                 # _init_v2_schema must roll back
    monkeypatch.undo()

    with sqlite3.connect(str(db)) as conn:
        legacy_n = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        version = conn.execute("SELECT value FROM kg_meta WHERE key='projection_version'").fetchone()[0]
    assert legacy_n == 1, "legacy data intact after rolled-back migration"
    assert version == "0", "projection_version stayed stale (rolled back) so next startup retries"

    healed = kg.KnowledgeGraphStore(db, blobs)         # clean restart heals
    assert healed._v2_sync_report()["in_sync"]


def test_projection_version_migration_rebuilds_old_shape(tmp_path):
    """An old-shape v2 projection (pre-normalization columns) is migrated in
    place on construction: dropped, rebuilt with the new columns, legacy intact."""
    db = tmp_path / "kg.sqlite"
    blobs = tmp_path / "blobs"

    # 1) first construction lays down legacy + (current) v2 schema
    store = kg.KnowledgeGraphStore(db, blobs)
    with store._connect() as conn:
        _insert_node(conn, "c1", "Concept", "Alpha", "alpha body", {"k": "v"})

    # 2) simulate an OLD projection: replace nodes_v2 with a pre-normalization
    #    shape (no legacy_type/summary/metadata cols, summary in attrs._kg) and
    #    roll the recorded projection_version back so the next init sees it stale.
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(
            "DROP VIEW IF EXISTS kgv2_nodes;"
            "DROP VIEW IF EXISTS kgv2_edges;"
            "DROP TABLE IF EXISTS edges_v2;"
            "DROP TABLE IF EXISTS nodes_v2;"
            "CREATE TABLE nodes_v2 (id TEXT PRIMARY KEY, type TEXT, label TEXT,"
            " attrs TEXT, created_at TEXT, updated_at TEXT);"
        )
        conn.execute(
            "INSERT INTO nodes_v2 VALUES ('c1','Concept','Alpha',?,?,?)",
            (json.dumps({"_kg": {"summary": "alpha body", "metadata_json": "{}"}}),
             "2026-01-01", "2026-01-01"),
        )
        conn.execute("INSERT OR REPLACE INTO kg_meta(key,value) VALUES ('projection_version','1')")

    # 3) reconstruct → version gate fires, old projection dropped + rebuilt
    store = kg.KnowledgeGraphStore(db, blobs)
    with store._connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes_v2)")}
        row = conn.execute("SELECT type, legacy_type, summary, attrs FROM nodes_v2 WHERE id='c1'").fetchone()
        legacy_n = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        version = conn.execute("SELECT value FROM kg_meta WHERE key='projection_version'").fetchone()[0]

    assert {"legacy_type", "summary", "metadata"} <= cols or "summary" in cols, "rebuilt with new columns"
    assert row["type"] == "CONCEPT" and row["legacy_type"] == "Concept"
    assert row["summary"] == "alpha body"
    assert "_kg" not in json.loads(row["attrs"])
    assert legacy_n == 1, "legacy table left intact (non-destructive)"
    assert version == str(kg._PROJECTION_VERSION)


def test_migration_keeps_reads_equivalent_after_rebuild(tmp_path):
    """After the version-gated rebuild, the v2 read path still matches legacy."""
    db = tmp_path / "kg.sqlite"
    store = kg.KnowledgeGraphStore(db, tmp_path / "blobs")
    with store._connect() as conn:
        _insert_node(conn, "concept:rag", "Concept", "RAG", "retrieval augmented", {"s": "doc"})
        _insert_node(conn, "topic:db", "Topic", "Storage", "sqlite storage", {})
        _insert_edge(conn, "e1", "concept:rag", "topic:db", "discusses", 1.0, {})
    # force a stale projection then reconstruct
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT OR REPLACE INTO kg_meta(key,value) VALUES ('projection_version','0')")
    store = kg.KnowledgeGraphStore(db, tmp_path / "blobs")

    for q in ["rag", "storage", "retrieval"]:
        store._read_from_v2 = False
        legacy = [m["id"] for m in store.search(q, limit=20)["matches"]]
        store._read_from_v2 = True
        v2 = [m["id"] for m in store.search(q, limit=20)["matches"]]
        assert legacy == v2, f"search({q!r}) diverges after migration"
