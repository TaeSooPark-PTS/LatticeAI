"""wp23 coverage — graph search, reads, and doc-gen traversal.

Real ``KnowledgeGraphStore`` instances over SQLite in ``tmp_path``. Two edge
shapes matter here and are exercised deliberately: the store emits edges with
``from``/``to`` keys (not ``source``/``target``), and a fused score of ``0`` is
falsy but valid, so the vector normalization compares explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import rerank as rerank_mod
from lattice_brain.graph import retrieval as retrieval_mod
from lattice_brain.graph import retrieval_policy as retrieval_policy_mod
from lattice_brain.graph import retrieval_reads as reads_mod
from lattice_brain.graph._kg_common import _slug
from lattice_brain.graph.store import KnowledgeGraphStore


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


# ── context quality signal ───────────────────────────────────────────────────


def test_unknown_retrieval_mode_is_reported_as_lexical_only() -> None:
    signal = retrieval_mod.context_quality_signal("teleportation", 3)

    assert signal == {
        "mode": "lexical_only",
        "nodes": 3,
        "limited": True,
        "reason": "벡터 검색을 사용할 수 없어 키워드 검색 결과만 사용했습니다",
    }


# ── graph(): topic importance metrics ────────────────────────────────────────


def test_graph_scores_topic_nodes_from_their_mentions_and_conversations(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    # Topic/Conversation are outside the shipped visible-type tuple, and the
    # kgv2_* read views collapse a write-door edge label to '', so this reads
    # the legacy tables with the visibility tuple widened — the only way the
    # topic-importance branch sees the rows it was written for.
    store._read_from_v2 = False
    monkeypatch.setattr(
        store,
        "_GRAPH_VISIBLE_TYPES",
        store._GRAPH_VISIBLE_TYPES + ("Topic", "Conversation"),
    )
    with store._connect() as conn:
        store._upsert_node(conn, "topic:alpha", "Topic", "Alpha Topic")
        store._upsert_node(conn, "conv:1", "Conversation", "Conversation one")
        store._upsert_node(
            conn, "doc:1", "Document", "Doc one", metadata={"conversation_id": "c9"}
        )
        store._upsert_node(conn, "doc:2", "Document", "Doc two")
        # pre-v4 free-string edge labels, which is what the metric block reads
        for from_node, edge_type in (
            ("conv:1", "mentions"),
            ("doc:1", "discusses"),
            ("doc:2", "related_to"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO edges(id, from_node, to_node, type, weight,"
                " metadata_json, created_at) VALUES (?, ?, ?, ?, ?, '{}', ?)",
                (
                    f"legacy:{from_node}",
                    from_node,
                    "topic:alpha",
                    edge_type,
                    2.0,
                    "2026-08-01T00:00:00",
                ),
            )

    graph = store.graph(limit=50)

    topic = next(node for node in graph["nodes"] if node["id"] == "topic:alpha")
    metrics = topic["metadata"]["graph_metrics"]
    assert metrics["degree"] == 3
    # only the mentions/discusses edges add mention weight (related_to does not)
    assert metrics["mention_count"] == 4.0
    # the Conversation node itself plus doc:1's conversation_id metadata
    assert metrics["conversation_count"] == 2
    assert topic["importance"] > max(
        node["importance"] for node in graph["nodes"] if node["id"] != "topic:alpha"
    )


# ── hybrid_search coercion + degradation ─────────────────────────────────────


def _seed(store: KnowledgeGraphStore) -> None:
    store.ingest_source(
        source_type="note",
        title="Lattice Retrieval Design",
        text="Hybrid retrieval fuses lexical and vector channels for recall.",
        source_uri="note:design",
    )
    store.ingest_source(
        source_type="note",
        title="Lattice Retrieval Operations",
        text="The vector index is rebuilt incrementally after ingestion.",
        source_uri="note:ops",
    )


def test_hybrid_search_coerces_an_unusable_top_k(tmp_path) -> None:
    store = _store(tmp_path)
    _seed(store)

    result = store.hybrid_search("lattice retrieval", top_k="not-a-number")

    assert result["top_k"] == 20


def test_hybrid_search_coerces_an_unusable_alpha(tmp_path) -> None:
    store = _store(tmp_path)
    _seed(store)

    result = store.hybrid_search("lattice retrieval", alpha="not-a-number")

    assert result["alpha"] == 0.6
    # an explicit alpha pins the vector share and disables the rewrite policy
    assert result["query_class"] is None


def test_hybrid_search_falls_back_to_the_default_alpha_when_policy_fails(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    _seed(store)

    def _boom(query, **kwargs):
        raise RuntimeError("policy table unreadable")

    monkeypatch.setattr(retrieval_policy_mod, "resolve_policy", _boom)

    result = store.hybrid_search("lattice retrieval")

    assert result["alpha"] == 0.6
    assert result["query_class"] is None
    assert result["policy"] == {"search_query": "lattice retrieval", "rewrite_rules": []}


def test_hybrid_search_reports_partial_vector_recall(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.rebuild_vector_index(full=True)
    monkeypatch.setenv("LATTICEAI_VECTOR_MAX_CANDIDATES", "1")

    result = store.hybrid_search("lattice retrieval", top_k=5, vector_limit=1)

    assert result["vector_recall"]["truncated"] is True
    assert result["vector_recall"]["candidates_scanned"] == 1
    assert result["vector_degraded"] == "partial_recall"


def test_hybrid_search_survives_a_failing_fingerprint_probe(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:plain", "Document", "Lattice Retrieval Design")

    def _boom():
        raise RuntimeError("graph_meta unreadable")

    monkeypatch.setattr(store, "embedder_fingerprint_status", _boom)

    # a floor above the cosine ceiling empties the vector channel, which is the
    # state the stale-embedder probe was written to explain
    result = store.hybrid_search("lattice retrieval", min_vector_score=1.5)

    assert result["mode"] == "hybrid"
    assert result["sources"]["vector"] == 0
    # the probe blew up, so no cause is claimed rather than a wrong one
    assert "vector_degraded" not in result


def test_hybrid_search_skips_matches_without_an_identifier(tmp_path) -> None:
    store = _store(tmp_path)
    _seed(store)
    with store._connect() as conn:
        # a legacy row whose id was never populated: it must not become a match
        store._upsert_node(conn, "", "Document", "Lattice Retrieval Orphan")
    store.rebuild_vector_index(full=True)

    result = store.hybrid_search("lattice retrieval", top_k=10)

    assert result["matches"]
    assert all(match["node_id"] for match in result["matches"])
    assert "Orphan" not in " ".join(str(m["title"]) for m in result["matches"])


def test_a_chunk_vector_hit_supplies_the_summary_its_parent_lacks(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:widget", "Document", "Widget Handbook", summary="")
        store._upsert_node(conn, "chunk:widget", "Chunk", "Widget Handbook chunk")
        store._upsert_chunk(
            conn,
            chunk_id="chunk:widget",
            source_node="doc:widget",
            text="Widget calibration is documented in section four.",
        )
    store.rebuild_vector_index(full=True)

    result = store.hybrid_search("widget handbook", top_k=5)

    parent = next(m for m in result["matches"] if m["node_id"] == "doc:widget")
    assert parent["summary"] == "Widget calibration is documented in section four."
    assert parent["fusion"] == "both"


def test_recency_decay_never_dampens_a_node_with_no_usable_timestamp(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:dated", "Document", "recent widget notes A")
        store._upsert_node(conn, "doc:undated", "Document", "recent widget notes B")
        for table, column in (("nodes", "updated_at"), ("nodes_v2", "updated_at")):
            conn.execute(
                f"UPDATE {table} SET {column}='' WHERE id=?", ("doc:undated",)
            )

    result = store.hybrid_search("recent widget", top_k=10)

    assert result["query_class"] == "recency"
    decays = {m["node_id"]: m["scores"]["age_decay"] for m in result["matches"]}
    assert decays["doc:undated"] == 1.0
    assert 0.5 <= decays["doc:dated"] <= 1.0


def test_hybrid_search_degrades_to_the_fused_order_when_rerank_explodes(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    _seed(store)

    def _boom(query, candidates, **kwargs):
        raise RuntimeError("rerank model is on fire")

    monkeypatch.setattr(rerank_mod, "rerank_matches", _boom)

    result = store.hybrid_search("lattice retrieval", top_k=1)

    assert result["rerank"] == {
        "mode": "identity",
        "model": None,
        "detail": "rerank model is on fire",
    }
    assert len(result["matches"]) == 1


# ── context_for_query ────────────────────────────────────────────────────────


def test_context_for_an_empty_query_is_an_empty_string(tmp_path) -> None:
    store = _store(tmp_path)

    assert store.context_for_query("   ") == ""


def test_topic_fallback_dedupes_rows_and_stops_at_the_limit(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "doc:a", "Document", "lattice retrieval overview")
        store._upsert_node(conn, "doc:b", "Document", "retrieval only summary")
    # the documented fallback exists for "lexical search found nothing"
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: {"matches": []})

    context = store.context_for_query("lattice retrieval", 2)

    lines = context.splitlines()
    assert len(lines) == 2
    assert len({line for line in lines}) == 2


def test_topic_fallback_scopes_its_rows_and_reports_lexical_only(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "doc:mine", "Document", "lattice retrieval mine", workspace_id="ws-1"
        )
        store._upsert_node(
            conn,
            "doc:theirs",
            "Document",
            "lattice retrieval theirs",
            workspace_id="ws-2",
        )
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: {"matches": []})

    payload = store.context_for_query(
        "lattice retrieval", 6, allowed_workspaces={"ws-1"}, with_meta=True
    )

    assert "doc:mine" in payload["context"]
    assert "doc:theirs" not in payload["context"]
    assert payload["quality"]["mode"] == "lexical_only"
    assert payload["quality"]["nodes"] == 1


# ── delete_conversation ──────────────────────────────────────────────────────


def test_delete_conversation_skips_an_empty_id(tmp_path) -> None:
    store = _store(tmp_path)

    assert store.delete_conversation("  ") == {"status": "skipped", "removed_nodes": 0}


def test_delete_conversation_removes_children_and_grandchildren(tmp_path) -> None:
    store = _store(tmp_path)
    conv_id = f"conversation:{_slug('thread-42')}"
    with store._connect() as conn:
        store._upsert_node(conn, conv_id, "Chat", "Thread 42")
        store._upsert_node(conn, "msg:1", "Message", "A message")
        store._upsert_node(conn, "chunk:1", "Chunk", "A chunk of the message")
        store._upsert_edge(conn, conv_id, "msg:1", "contains")
        store._upsert_edge(conn, "msg:1", "chunk:1", "has_chunk")

    result = store.delete_conversation("thread-42")

    assert result["status"] == "ok"
    assert result["removed_nodes"] == 3
    with store._connect() as conn:
        remaining = {
            row["id"] for row in conn.execute("SELECT id FROM nodes").fetchall()
        }
    assert remaining == set()


# ── reads mixin ──────────────────────────────────────────────────────────────


def test_unscoped_filter_returns_every_candidate(tmp_path) -> None:
    store = _store(tmp_path)
    items = [{"id": "a"}, {"id": "b"}]

    assert store.filter_scoped_nodes(items, None) == items


def test_scope_sql_can_include_legacy_global_rows() -> None:
    clause, params = reads_mod.KnowledgeGraphReadsMixin._workspace_scope_sql(
        {"ws-1"}, True
    )

    assert clause == "workspace_id IN (?) OR workspace_id IS NULL"
    assert params == ["ws-1"]


def _scoped_pair(store: KnowledgeGraphStore) -> None:
    with store._connect() as conn:
        store._upsert_node(
            conn, "n:mine", "Document", "Mine", workspace_id="ws-1"
        )
        store._upsert_node(
            conn, "n:theirs", "Document", "Theirs", workspace_id="ws-2"
        )
        store._upsert_node(
            conn, "n:also-mine", "Concept", "Also mine", workspace_id="ws-1"
        )
        store._upsert_edge(conn, "n:mine", "n:theirs", "related_to")
        store._upsert_edge(conn, "n:mine", "n:also-mine", "related_to")


def test_neighbors_refuses_a_node_outside_the_callers_scope(tmp_path) -> None:
    store = _store(tmp_path)
    _scoped_pair(store)

    with pytest.raises(ValueError, match="graph node not found"):
        store.neighbors("n:theirs", allowed_workspaces={"ws-1"})


def test_neighbors_drops_edges_into_another_workspace(tmp_path) -> None:
    store = _store(tmp_path)
    _scoped_pair(store)

    payload = store.neighbors("n:mine", allowed_workspaces={"ws-1"})

    assert {node["id"] for node in payload["neighbors"]} == {"n:also-mine"}
    assert [edge["to"] for edge in payload["edges"]] == ["n:also-mine"]


def test_get_node_requires_a_node_id(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="node_id required"):
        store.get_node("  ")


def test_relationship_search_keeps_only_fully_visible_relationships(tmp_path) -> None:
    store = _store(tmp_path)
    _scoped_pair(store)

    payload = store.relationship_search(
        node_id="n:mine", allowed_workspaces={"ws-1"}
    )

    assert [rel["target"]["id"] for rel in payload["relationships"]] == ["n:also-mine"]


def test_traverse_requires_a_node_id(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="node_id required"):
        store.traverse("")


def test_traverse_refuses_a_node_outside_the_callers_scope(tmp_path) -> None:
    store = _store(tmp_path)
    _scoped_pair(store)

    with pytest.raises(ValueError, match="graph node not found"):
        store.traverse("n:theirs", allowed_workspaces={"ws-1"})


def test_traverse_stops_early_when_the_frontier_is_exhausted(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "n:isolated", "Document", "No edges at all")

    payload = store.traverse("n:isolated", depth=3)

    assert payload["depth"] == 3
    assert [node["id"] for node in payload["nodes"]] == ["n:isolated"]
    assert payload["edges"] == []


def test_stats_reports_a_failing_v2_schema_probe(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)

    class _BrokenV2:
        def __init__(self, db_path):
            self.db_path = db_path

        def stats(self):
            raise RuntimeError("v2 schema is unreadable")

    monkeypatch.setattr(reads_mod, "KGStoreV2", _BrokenV2)

    payload = store.stats()

    assert payload["v2"] == {"available": False, "error": "v2 schema is unreadable"}


# ── doc-gen traversal ────────────────────────────────────────────────────────


def test_multi_hop_context_breaks_once_the_frontier_empties(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "n:lonely", "Document", "Nothing links here")

    payload = store.multi_hop_context(["n:lonely"], max_hops=3)

    assert [node["id"] for node in payload["nodes"]] == ["n:lonely"]
    assert payload["edges"] == []
