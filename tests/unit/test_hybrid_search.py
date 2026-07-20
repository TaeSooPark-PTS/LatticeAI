"""Graph-layer unified hybrid search (lexical + vector fusion) tests.

Covers: alpha-weighted linear fusion with per-source scores + fusion
provenance, node_id dedupe (chunks roll up to their parent), graceful
lexical-only degradation when the vector index is unavailable, parameter
clamping, workspace scoping, and the opt-in hybrid path of
``context_for_query`` (default behavior unchanged).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _seed(store: KnowledgeGraphStore) -> None:
    store.ingest_source(
        source_type="note",
        title="Hybrid Retrieval Design",
        text=(
            "Hybrid retrieval fuses lexical keyword matching with vector "
            "cosine similarity so Lattice AI search covers both exact terms "
            "and semantic neighbors."
        ),
        source_uri="note:hybrid-design",
    )
    store.ingest_source(
        source_type="note",
        title="Vector Index Operations",
        text=(
            "The vector index stores embeddings in SQLite and is rebuilt "
            "incrementally after ingestion."
        ),
        source_uri="note:vector-ops",
    )
    store.ingest_source(
        source_type="note",
        title="Release Checklist",
        text="Ship notes, tag the build, and update the changelog.",
        source_uri="note:release",
    )


def test_hybrid_search_returns_fused_ranked_matches(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    result = store.hybrid_search("hybrid retrieval vector similarity", top_k=10)

    assert result["mode"] == "hybrid"
    assert result["detail"] is None
    assert result["sources"]["lexical"] > 0
    assert result["sources"]["vector"] > 0
    matches = result["matches"]
    assert matches
    # Ranked, deduped by node_id, with per-source scores + fusion provenance.
    node_ids = [m["node_id"] for m in matches]
    assert len(node_ids) == len(set(node_ids))
    scores = [m["score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    for rank, match in enumerate(matches, start=1):
        assert match["rank"] == rank
        assert set(match["scores"]) == {"lexical", "vector"}
        assert match["fusion"] in {"lexical", "vector", "both"}
    # A node hit by both channels reports fusion provenance "both".
    assert any(m["fusion"] == "both" for m in matches)


def test_hybrid_search_falls_back_to_lexical_only_on_vector_failure(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    def _broken(*args, **kwargs):
        raise RuntimeError("embedding provider down")

    store.vector_search = _broken

    result = store.hybrid_search("hybrid retrieval", top_k=5)
    assert result["mode"] == "lexical_only"
    assert "vector index unavailable" in (result["detail"] or "")
    assert result["matches"], "lexical results must survive the vector outage"
    assert all(m["fusion"] == "lexical" for m in result["matches"])
    assert all(m["scores"]["vector"] == 0.0 for m in result["matches"])


def test_hybrid_search_reports_lexical_only_when_vector_method_missing(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    store.vector_search = None  # simulate a store without the vector mixin

    result = store.hybrid_search("hybrid retrieval")
    assert result["mode"] == "lexical_only"
    assert "not available" in (result["detail"] or "")


def test_hybrid_search_clamps_alpha_and_top_k(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    high = store.hybrid_search("vector index", alpha=7.5, top_k=0)
    assert high["alpha"] == 1.0
    assert high["top_k"] == 1
    assert len(high["matches"]) <= 1

    low = store.hybrid_search("vector index", alpha=-3, top_k=5000)
    assert low["alpha"] == 0.0
    assert low["top_k"] == 100


def test_hybrid_search_empty_query_returns_no_matches(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    result = store.hybrid_search("   ")
    assert result["matches"] == []


def test_hybrid_search_respects_workspace_scope(tmp_path):
    store = _store(tmp_path)
    store.ingest_source(
        source_type="note",
        title="Alpha Plan",
        text="Workspace alpha hybrid retrieval planning document.",
        source_uri="note:a",
        workspace_id="org:a",
    )
    store.ingest_source(
        source_type="note",
        title="Beta Plan",
        text="Workspace beta hybrid retrieval planning document.",
        source_uri="note:b",
        workspace_id="org:b",
    )

    scoped = store.hybrid_search("hybrid retrieval planning", workspace_id="org:a")
    assert scoped["matches"], "workspace org:a content should be found"
    scopes = store.workspaces_of([m["node_id"] for m in scoped["matches"]])
    assert all(scopes.get(m["node_id"]) == "org:a" for m in scoped["matches"])


def test_context_for_query_default_and_hybrid_paths(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    default_ctx = store.context_for_query("hybrid retrieval vector")
    hybrid_ctx = store.context_for_query("hybrid retrieval vector", use_hybrid=True)
    assert default_ctx
    assert hybrid_ctx
    assert "Hybrid Retrieval Design" in hybrid_ctx


def test_context_for_query_hybrid_flag_survives_hybrid_failure(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    def _broken(*args, **kwargs):
        raise RuntimeError("fusion exploded")

    store.hybrid_search = _broken
    ctx = store.context_for_query("hybrid retrieval vector", use_hybrid=True)
    assert ctx  # silently fell back to the legacy lexical path
