"""wpb02 branch coverage — Knowledge Graph read paths.

Four read-side guards only fire on shapes a healthy write path never produces:
two legacy rows sharing an id (SQLite lets a TEXT PRIMARY KEY hold NULL, so
pre-scoping imports really can collide), a traversal seed whose node row is
gone, a self-edge that would otherwise re-queue a node already visited, and an
isolated node with no neighbours at all. The fifth is a build without the v2
schema module. All of them run against a real store on ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from lattice_brain.graph import retrieval_reads as reads_mod
from lattice_brain.graph.store import KnowledgeGraphStore


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _insert_orphan_row(store: KnowledgeGraphStore, title: str) -> None:
    """Insert a node row with no id (SQLite permits NULL in a TEXT PRIMARY KEY)."""
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json,
                              created_at, updated_at)
            VALUES (NULL, 'Document', ?, '', '{}', '{}',
                    '2026-08-01T00:00:00', '2026-08-01T00:00:00')
            """,
            (title,),
        )


# ── retrieval_docgen.search_for_document_generation ─────────────────────────


def test_two_legacy_rows_sharing_an_id_are_scored_once(store: KnowledgeGraphStore):
    # The legacy tables are the read path here (the store's documented test
    # toggle), so the rows below are exactly what a pre-v2 database can hold.
    store._read_from_v2 = False
    assert store._read_tables() == ("nodes", "edges")
    _insert_orphan_row(store, "Roadmap alpha")
    _insert_orphan_row(store, "Roadmap beta")

    results = store.search_for_document_generation("Roadmap")

    assert [r["id"] for r in results] == [None]
    assert results[0]["title"] == "Roadmap alpha"


# ── retrieval_docgen.multi_hop_context ──────────────────────────────────────


def test_a_traversal_seed_with_no_node_row_yields_no_node(store: KnowledgeGraphStore):
    result = store.multi_hop_context(["node:does-not-exist"])

    assert result["nodes"] == []
    assert result["edges"] == []


def test_a_self_edge_does_not_re_queue_the_node_it_starts_from(store: KnowledgeGraphStore):
    with store._connect() as conn:
        store._upsert_node(conn, "n:loop", "Concept", "Self referential")
        store._upsert_edge(conn, "n:loop", "n:loop", "RELATED_TO")

    result = store.multi_hop_context(["n:loop"], max_hops=2)

    assert [n["id"] for n in result["nodes"]] == ["n:loop"]
    assert len(result["edges"]) == 1
    assert result["edges"][0]["from"] == "n:loop"
    assert result["edges"][0]["to"] == "n:loop"


# ── retrieval_reads.neighbors ───────────────────────────────────────────────


def test_an_isolated_node_reports_no_neighbours(store: KnowledgeGraphStore):
    with store._connect() as conn:
        store._upsert_node(conn, "n:alone", "Concept", "Alone")

    result = store.neighbors("n:alone")

    assert result == {"node_id": "n:alone", "neighbors": [], "edges": []}


# ── retrieval_reads.stats ───────────────────────────────────────────────────


def test_stats_without_the_v2_schema_module_reports_it_unavailable(
    store: KnowledgeGraphStore, monkeypatch
):
    with store._connect() as conn:
        store._upsert_node(conn, "n:a", "Concept", "A")
    monkeypatch.setattr(reads_mod, "KGStoreV2", None)

    stats: Dict[str, Any] = store.stats()

    assert stats["v2_schema_available"] is False
    assert stats["v2"] is None
    assert stats["nodes"]["Concept"] == 1


def test_reads_module_still_exposes_the_neighbours_surface():
    surface: List[str] = [
        name for name in ("neighbors", "stats") if hasattr(reads_mod.KnowledgeGraphReadsMixin, name)
    ]
    assert surface == ["neighbors", "stats"]
