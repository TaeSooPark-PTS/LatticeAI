"""Query-class retrieval fusion + benchmark threshold gate (backlog #5).

Covers: query-class detection heuristics, the per-class fusion weight table
(defaults + env override), class-aware wiring in both hybrid paths
(SearchService three-channel fusion and the graph-layer two-channel fusion),
and a CI benchmark gate over ``tests/fixtures/retrieval_benchmark_fixtures``
that fails when precision/recall/must-include regress below THRESHOLDS.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.fusion import (
    DEFAULT_FUSION_WEIGHTS,
    classify_query,
    fusion_profile,
    fusion_weight_table,
)
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.quality import RetrievalBenchmarkRunner
from latticeai.services.search_service import DEFAULT_HYBRID_WEIGHTS, SearchService
from tests.fixtures.retrieval_benchmark_fixtures import (
    DOCUMENTS,
    FIXTURE_NAME,
    QUERIES,
    THRESHOLDS,
    TOP_K,
)


# ── query-class detection ────────────────────────────────────────────────────

def test_classify_query_detects_code_signals():
    assert classify_query("왜 ingest_folder가 실패해?") == "code"
    assert classify_query("fix the `parse()` helper") == "code"
    assert classify_query("```python\nprint(1)\n```") == "code"
    assert classify_query("traceback 에러 원인 알려줘") == "code"


def test_classify_query_detects_person_signals():
    assert classify_query("백엔드 담당자 누구야") == "person"
    assert classify_query("who is responsible for onboarding") == "person"
    assert classify_query("김민준 님 연락처") == "person"


def test_classify_query_detects_recency_signals():
    assert classify_query("어제 회의 요약해줘") == "recency"
    assert classify_query("what changed last week") == "recency"
    assert classify_query("최근 배포 내역") == "recency"


def test_classify_query_falls_back_to_fact():
    assert classify_query("출시 결정 내용") == "fact"
    assert classify_query("") == "fact"
    assert classify_query(None) == "fact"


# ── weight table + overrides ─────────────────────────────────────────────────

def test_fact_weights_match_legacy_defaults():
    """The fallback class must be byte-compatible with pre-fusion behavior."""
    fact = DEFAULT_FUSION_WEIGHTS["fact"]
    assert {
        "keyword": fact["keyword"],
        "vector": fact["vector"],
        "graph": fact["graph"],
    } == DEFAULT_HYBRID_WEIGHTS
    assert fact["alpha"] == 0.6


def test_fusion_weight_table_env_override(monkeypatch):
    monkeypatch.setenv("LATTICEAI_FUSION_WEIGHTS", '{"code": {"alpha": 0.2}}')
    table = fusion_weight_table()
    assert table["code"]["alpha"] == 0.2
    # Untouched keys/classes keep their defaults.
    assert table["code"]["keyword"] == DEFAULT_FUSION_WEIGHTS["code"]["keyword"]
    assert table["fact"] == DEFAULT_FUSION_WEIGHTS["fact"]


def test_fusion_weight_table_ignores_malformed_env(monkeypatch):
    monkeypatch.setenv("LATTICEAI_FUSION_WEIGHTS", "not json at all")
    assert fusion_weight_table() == DEFAULT_FUSION_WEIGHTS


def test_fusion_profile_resolves_class_weights():
    profile = fusion_profile("hybrid_search 코드 버그")
    assert profile["query_class"] == "code"
    assert profile["alpha"] == DEFAULT_FUSION_WEIGHTS["code"]["alpha"]
    assert set(profile["weights"]) == {"keyword", "vector", "graph"}


# ── wiring: both hybrid paths report the class-aware fusion ──────────────────

def _seeded_store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    for doc in DOCUMENTS:
        store.ingest_source(
            source_type="note",
            title=doc["title"],
            text=doc["text"],
            source_uri=doc["id"],
        )
    return store


def test_graph_layer_hybrid_search_uses_query_class_alpha(tmp_path):
    store = _seeded_store(tmp_path)
    result = store.hybrid_search("ingest_folder recursive 버그", top_k=5)
    assert result["query_class"] == "code"
    assert result["alpha"] == DEFAULT_FUSION_WEIGHTS["code"]["alpha"]
    # Explicit alpha pins the value and disables class resolution.
    pinned = store.hybrid_search("ingest_folder recursive 버그", top_k=5, alpha=0.9)
    assert pinned["alpha"] == 0.9
    assert pinned["query_class"] is None


def test_service_hybrid_search_reports_query_class(tmp_path):
    service = SearchService(_seeded_store(tmp_path))
    payload = service.hybrid_search("백엔드 담당자 누구야", limit=5)
    assert payload["query_class"] == "person"
    assert payload["weights"] == fusion_profile("백엔드 담당자 누구야")["weights"]
    for match in payload["matches"]:
        assert match["fusion"]["query_class"] == "person"
    # Caller-pinned weights still win and suppress classification.
    pinned = service.hybrid_search("백엔드 담당자 누구야", limit=5, weights={"keyword": 1.0})
    assert pinned["query_class"] is None
    assert pinned["weights"]["keyword"] == 1.0


# ── single retrieval policy across both hybrid layers (review Wave 0.2) ─────

def test_both_hybrid_layers_resolve_the_same_policy(tmp_path):
    """RetrievalPolicy 단일화: SearchService and the graph-layer hybrid must
    report the same query_class for the same query and echo the policy's
    rewritten search_query additively (response "query" stays the original)."""
    store = _seeded_store(tmp_path)
    service = SearchService(store)
    queries = {
        "fact": "출시 결정 내용이 뭐였지",
        "code": "hybrid_search alpha 융합 코드",
        "person": "백엔드 담당자 누구야",
        "recency": "어제 회의에서 합의한 것",
    }
    for expected_class, query in queries.items():
        graph_result = store.hybrid_search(query, top_k=5)
        service_result = service.hybrid_search(query, limit=5)
        assert graph_result["query_class"] == expected_class, query
        assert service_result["query_class"] == expected_class, query
        assert graph_result["query"] == query
        assert service_result["query"] == query
        for payload in (graph_result, service_result):
            assert payload["policy"]["search_query"]
            assert isinstance(payload["policy"]["rewrite_rules"], list)
        assert (
            graph_result["policy"]["search_query"]
            == service_result["policy"]["search_query"]
        )
        assert (
            graph_result["policy"]["rewrite_rules"]
            == service_result["policy"]["rewrite_rules"]
        )
    # The fact query above ends in the "뭐였지" filler — both layers must have
    # searched with the same rewritten form, not two different strings.
    rewritten = store.hybrid_search("출시 결정 내용이 뭐였지", top_k=5)["policy"]
    assert rewritten["search_query"] == "출시 결정 내용이"
    assert "strip_filler_ko" in rewritten["rewrite_rules"]


# ── CI benchmark threshold gate ──────────────────────────────────────────────

def test_retrieval_fusion_benchmark_gate(tmp_path):
    """Precision/recall/must-include over the fixture corpus must hold."""
    store = _seeded_store(tmp_path)
    # Map fixture ids → actual node ids through the same source_uri we ingested.
    id_map = {}
    for doc in DOCUMENTS:
        result = store.ingest_source(
            source_type="note",
            title=doc["title"],
            text=doc["text"],
            source_uri=doc["id"],
        )
        assert result["duplicate"] is True  # re-ingest is idempotent
        id_map[doc["id"]] = result["node_id"]

    service = SearchService(store)
    judged = []
    class_hits = 0
    for query in QUERIES:
        assert classify_query(query["query"]) == query["query_class"], query["query"]
        class_hits += 1
        payload = service.hybrid_search(
            query["query"],
            limit=TOP_K,
            keyword_limit=TOP_K,
            vector_limit=TOP_K,
            graph_limit=TOP_K,
        )
        retrieved = [item.get("node_id") or item.get("id") for item in payload["matches"]]
        judged.append({
            "query": query["query"],
            "relevant": {id_map[k]: grade for k, grade in query["relevant"].items()},
            "must_include": [id_map[k] for k in query.get("must_include", [])],
            "retrieved": retrieved,
        })

    metrics = RetrievalBenchmarkRunner().run_fixture(FIXTURE_NAME, judged, top_k=TOP_K)
    accuracy = class_hits / len(QUERIES)
    assert accuracy >= THRESHOLDS["query_class_accuracy"], accuracy
    assert metrics[f"precision@{TOP_K}"] >= THRESHOLDS["precision@k"], metrics
    assert metrics[f"recall@{TOP_K}"] >= THRESHOLDS["recall@k"], metrics
    assert metrics["must_include_hit_rate"] >= THRESHOLDS["must_include_hit_rate"], metrics
