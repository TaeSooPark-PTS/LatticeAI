"""wpb01 branch coverage — ``lattice_brain.graph.retrieval``.

Covers the untaken sides of:

* ``search`` — a blank query (no lexical pass at all) and a query whose first
  pass already filled the requested limit (no topic-expansion pass),
* ``hybrid_search`` — an empty vector channel that is *not* explained by a
  stale embedder, and a truncated-recall payload that already has a
  ``vector_degraded`` reason (partial recall must not overwrite it),
* ``context_for_query`` — a query with neither matches nor topic candidates,
* ``delete_conversation`` / ``clear_all`` on a build with no v2 projection
  (``KGStoreV2 is None``), and ``clear_all`` with no blob directory on disk.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.graph.retrieval as retrieval_mod  # noqa: E402
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402

STAMP = "2026-08-01T00:00:00Z"


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _node(store: KnowledgeGraphStore, node_id: str, title: str, *, node_type: str = "Note") -> None:
    """Write through the store's own door so the v2 projection stays in sync."""
    with store._connect() as conn:
        store._upsert_node(conn, node_id, node_type, title, summary=title, metadata={})


def _edge(store: KnowledgeGraphStore, from_node: str, to_node: str, edge_type: str) -> None:
    with store._connect() as conn:
        store._upsert_edge(conn, from_node, to_node, edge_type, weight=1.0, metadata={})


# ── search ──────────────────────────────────────────────────────────────────


def test_search_with_a_blank_query_skips_the_lexical_pass(
    store: KnowledgeGraphStore,
) -> None:
    _node(store, "node:a", "Retrieval policy notes")

    result = store.search("   ")

    assert result["query"] == ""
    assert result["matches"] == []


def test_search_stops_expanding_once_the_limit_is_already_filled(
    store: KnowledgeGraphStore,
) -> None:
    """One row for limit=1 means the topic-expansion query never runs."""
    _node(store, "node:a", "Retrieval policy notes")
    _node(store, "node:b", "Retrieval policy appendix")

    result = store.search("Retrieval", 1)

    assert len(result["matches"]) == 1
    assert result["matches"][0]["id"] in {"node:a", "node:b"}


# ── hybrid_search ───────────────────────────────────────────────────────────


def test_empty_vector_channel_is_not_blamed_on_a_fresh_embedder(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _node(store, "node:a", "Retrieval policy notes")
    monkeypatch.setattr(
        store, "vector_search", lambda *a, **k: {"matches": []}, raising=False
    )
    monkeypatch.setattr(
        store,
        "embedder_fingerprint_status",
        lambda: {"current": {}, "recorded": None, "stale_embedder": False},
        raising=False,
    )

    result = store.hybrid_search("Retrieval policy")

    assert result["mode"] == "hybrid"
    assert result["sources"]["vector"] == 0
    # No honest cause to report, so the field stays absent.
    assert "vector_degraded" not in result


def test_partial_recall_does_not_overwrite_a_stale_embedder_reason(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both signals fire at once; the more specific cause wins."""
    _node(store, "node:a", "Retrieval policy notes")
    monkeypatch.setattr(
        store,
        "vector_search",
        lambda *a, **k: {"matches": [], "recall": {"truncated": True, "scanned": 10}},
        raising=False,
    )
    monkeypatch.setattr(
        store,
        "embedder_fingerprint_status",
        lambda: {"current": {}, "recorded": {}, "stale_embedder": True},
        raising=False,
    )

    result = store.hybrid_search("Retrieval policy")

    assert result["vector_recall"] == {"truncated": True, "scanned": 10}
    assert result["vector_degraded"] == "stale_embedder"


# ── context_for_query ───────────────────────────────────────────────────────


def test_context_for_a_query_with_no_topic_candidates_is_empty(
    store: KnowledgeGraphStore,
) -> None:
    """Punctuation yields no concepts and no tokens, so no fallback query runs."""
    _node(store, "node:a", "Retrieval policy notes")

    payload = store.context_for_query("!!! ???", with_meta=True)

    assert payload["context"] == ""
    assert payload["quality"]["mode"] == "none"
    assert payload["quality"]["nodes"] == 0


# ── delete_conversation / clear_all without the v2 projection ───────────────


def test_delete_conversation_without_the_v2_projection(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _node(store, "conversation:c1", "chat", node_type="Conversation")
    _node(store, "node:msg", "hello there")
    _node(store, "topic:orphan", "orphaned topic", node_type="Topic")
    _edge(store, "conversation:c1", "node:msg", "contains")
    monkeypatch.setattr(retrieval_mod, "KGStoreV2", None)

    result = store.delete_conversation("c1")

    assert result["status"] == "ok"
    assert result["removed_nodes"] == 2
    with store._connect() as conn:
        remaining = {
            row["id"] for row in conn.execute("SELECT id FROM nodes")
        }
    assert remaining == set()


def test_clear_all_without_the_v2_projection_or_a_blob_directory(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _node(store, "node:a", "Retrieval policy notes")
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO chunks(id, source_node, text, metadata_json, created_at)
            VALUES ('node:a', 'node:a', 'body', '{}', ?)
            """,
            (STAMP,),
        )
    shutil.rmtree(store.blob_dir)
    monkeypatch.setattr(retrieval_mod, "KGStoreV2", None)

    result = store.clear_all()

    assert result["status"] == "ok"
    assert result["removed"]["nodes"] == 1
    assert result["removed"]["chunks"] == 1
    # The blob directory was absent, so it was not recreated.
    assert not store.blob_dir.exists()
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"] == 0


def test_v2_rows_survive_when_the_projection_is_disabled(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof the KGStoreV2 guard is what gates the nodes_v2 delete."""
    _node(store, "node:a", "Retrieval policy notes")
    with store._connect() as conn:
        projected = conn.execute("SELECT COUNT(*) AS c FROM nodes_v2").fetchone()["c"]
    assert projected == 1
    monkeypatch.setattr(retrieval_mod, "KGStoreV2", None)

    store.clear_all()

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM nodes_v2").fetchone()["c"] == 1
