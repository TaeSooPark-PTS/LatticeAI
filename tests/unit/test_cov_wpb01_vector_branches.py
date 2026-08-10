"""wpb01 branch coverage — ``lattice_brain.graph.retrieval_vector``.

The vector index is derived data, so the interesting directions are the ones
where an item is *not* embeddable or a section is *not* requested:

* ``_iter_vector_source_items`` with nodes or chunks switched off, and rows
  whose embeddable text cleans down to nothing,
* ``index_node_incremental`` pointed at a Chunk node, a textless node, and a
  node whose chunk is blank,
* ``rebuild_vector_index(full=True)`` with neither section selected (no
  ``DELETE`` is issued at all),
* ``index_status`` when the newest operation is still running and the newest
  completed one recorded no duration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402

STAMP = "2026-08-01T00:00:00Z"


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _insert_node(
    store: KnowledgeGraphStore,
    node_id: str,
    *,
    node_type: str = "Note",
    title: str = "",
    summary: str = "",
    metadata: dict | None = None,
) -> None:
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (node_id, node_type, title, summary, json.dumps(metadata or {}), STAMP, STAMP),
        )


def _insert_chunk(store: KnowledgeGraphStore, chunk_id: str, text: str) -> None:
    """A chunk row plus the Chunk node the vector query joins against."""
    _insert_node(store, chunk_id, node_type="Chunk", title="chunk")
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO chunks(id, source_node, text, metadata_json, created_at)
            VALUES (?, ?, ?, '{}', ?)
            """,
            (chunk_id, chunk_id, text, STAMP),
        )


# ── _iter_vector_source_items ───────────────────────────────────────────────


def test_index_status_ignores_rows_with_no_embeddable_text(
    store: KnowledgeGraphStore,
) -> None:
    """A titleless node and a whitespace-only chunk are not index candidates."""
    _insert_node(store, "node:blank")
    _insert_node(store, "node:real", title="Retrieval policy notes")
    _insert_chunk(store, "chunk:blank", "   ")

    status = store.index_status()

    ids = {sample["item_id"] for sample in status["scale"]["backlog_samples"]}
    assert ids == {"node:real"}
    assert status["source_items"] == 1


def test_full_rebuild_with_no_sections_selected_touches_nothing(
    store: KnowledgeGraphStore,
) -> None:
    """Neither ``item_type`` filter is built, so no DELETE and no items."""
    _insert_node(store, "node:real", title="Retrieval policy notes")
    _insert_chunk(store, "chunk:real", "a chunk with real prose in it")
    store.rebuild_vector_index(full=True)
    with store._connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM vector_embeddings").fetchone()["c"]
    assert before > 0

    result = store.rebuild_vector_index(
        full=True, include_nodes=False, include_chunks=False
    )

    assert result["items_total"] == 0
    assert result["items_indexed"] == 0
    with store._connect() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM vector_embeddings").fetchone()["c"]
    # The wipe was skipped entirely because no item_type was selected.
    assert after == before


# ── index_node_incremental ──────────────────────────────────────────────────


def test_incremental_index_of_a_chunk_node_skips_the_node_item(
    store: KnowledgeGraphStore,
) -> None:
    _insert_chunk(store, "chunk:solo", "some indexable chunk prose")

    result = store.index_node_incremental("chunk:solo")

    # Only the chunk row is an item — the Chunk node itself is never one.
    assert result["items_total"] == 1
    assert result["items_indexed"] == 1
    with store._connect() as conn:
        types = [
            row["item_type"]
            for row in conn.execute("SELECT item_type FROM vector_embeddings")
        ]
    assert types == ["chunk"]


def test_incremental_index_of_a_textless_node_is_a_noop(
    store: KnowledgeGraphStore,
) -> None:
    _insert_node(store, "node:blank")

    result = store.index_node_incremental("node:blank")

    assert result["items_total"] == 0
    assert result["status"] == "noop"


def test_incremental_index_skips_a_blank_chunk_of_a_real_node(
    store: KnowledgeGraphStore,
) -> None:
    _insert_node(store, "node:doc", title="Quarterly plan")
    _insert_node(store, "chunk:blank", node_type="Chunk", title="chunk")
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO chunks(id, source_node, text, metadata_json, created_at)
            VALUES ('chunk:blank', 'node:doc', '   ', '{}', ?)
            """,
            (STAMP,),
        )

    result = store.index_node_incremental("node:doc")

    # The node is indexed; its blank chunk never becomes an item.
    assert result["items_total"] == 1
    with store._connect() as conn:
        ids = [
            row["item_id"]
            for row in conn.execute("SELECT item_id FROM vector_embeddings")
        ]
    assert ids == ["node:doc"]


# ── index_status operation history ──────────────────────────────────────────


def test_index_status_scans_past_a_running_operation_for_the_last_completed(
    store: KnowledgeGraphStore,
) -> None:
    """The newest row is still running and the completed one logged no duration."""
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO vector_index_operations(
              id, operation, status, requested_at, items_total, metadata_json)
            VALUES ('op:done', 'rebuild_full', 'completed', '2026-08-01T00:00:00Z',
                    12, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO vector_index_operations(
              id, operation, status, requested_at, items_total, metadata_json)
            VALUES ('op:running', 'rebuild_full', 'running', '2026-08-02T00:00:00Z',
                    0, '{}')
            """
        )

    status = store.index_status()

    assert [row["id"] for row in status["operations"]] == ["op:running", "op:done"]
    # No duration_ms was recorded, so the latency budget stays honestly unknown.
    budget = status["scale"]["latency_budget"]
    assert budget["last_rebuild_duration_ms"] is None
    assert budget["last_items_per_second"] is None
    assert budget["within_target"] is None
