"""T3c: temporal dimension — edge occurrences + node revision chains.

Repeated observations of a relationship must be recoverable (when it was
learned, how often), and replaced nodes point at their successor instead of
being silently overwritten or deleted.
"""

import pytest

from lattice_brain.graph.store import KnowledgeGraphStore


def _store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _seed_pair(store):
    with store._connect() as conn:
        store._upsert_node(conn, "a", "Concept", "A", "", {})
        store._upsert_node(conn, "b", "Concept", "B", "", {})


def test_repeated_observations_accumulate(tmp_path):
    store = _store(tmp_path)
    _seed_pair(store)
    with store._connect() as conn:
        store._upsert_edge(conn, "a", "b", "mentions", 0.5, {"source": "doc1"})
        store._upsert_edge(conn, "a", "b", "mentions", 0.9, {"source": "doc2"})
        store._upsert_edge(conn, "a", "b", "mentions", 0.7, {"source": "chat"})
    with store._connect() as conn:
        edge = conn.execute(
            "SELECT id, weight FROM edges_v2 WHERE source='a' AND target='b'"
        ).fetchone()
        occ = conn.execute(
            "SELECT weight, source, observed_at FROM edge_occurrences WHERE edge_id=? ORDER BY id",
            (edge["id"],),
        ).fetchall()
    assert edge["weight"] == 0.9, "edge row keeps max weight as before"
    assert [(o["weight"], o["source"]) for o in occ] == [
        (0.5, "doc1"), (0.9, "doc2"), (0.7, "chat"),
    ], "every observation is recorded with its own weight/source"
    assert all(o["observed_at"] for o in occ)


def test_occurrences_cascade_with_edge(tmp_path):
    store = _store(tmp_path)
    _seed_pair(store)
    with store._connect() as conn:
        store._upsert_edge(conn, "a", "b", "mentions", 1.0, {})
        conn.execute("DELETE FROM edges_v2 WHERE source='a' AND target='b'")
        left = conn.execute("SELECT count(*) FROM edge_occurrences").fetchone()[0]
    assert left == 0


def test_mark_superseded_records_revision_chain(tmp_path):
    store = _store(tmp_path)
    _seed_pair(store)
    result = store.mark_superseded("a", "b")
    assert result["superseded_by"] == "b"
    with store._connect() as conn:
        row = conn.execute("SELECT superseded_by FROM nodes_v2 WHERE id='a'").fetchone()
        successor = conn.execute("SELECT superseded_by FROM nodes_v2 WHERE id='b'").fetchone()
    assert row["superseded_by"] == "b"
    assert successor["superseded_by"] is None
    # The old node is NOT deleted — knowledge is durable.
    with store._connect() as conn:
        assert conn.execute("SELECT 1 FROM nodes_v2 WHERE id='a'").fetchone()


def test_mark_superseded_requires_both_nodes(tmp_path):
    store = _store(tmp_path)
    _seed_pair(store)
    with pytest.raises(FileNotFoundError):
        store.mark_superseded("a", "ghost")
    with pytest.raises(FileNotFoundError):
        store.mark_superseded("ghost", "a")
