from pathlib import Path

from knowledge_graph import KnowledgeGraphStore
from lattice_brain.quality import RetrievalBenchmarkRunner
from latticeai.services.search_service import SearchService
from lattice_brain.retrieval_benchmark_fixtures import DOCUMENTS, FIXTURE_NAME, QUERIES, TOP_K


def test_v750_corpus_scale_fixture_exercises_real_hybrid_search(tmp_path: Path):
    graph = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    id_map = {}
    for doc in DOCUMENTS:
        result = graph.ingest_event(
            doc["type"],
            f"{doc['title']} {doc['content']}",
            source="retrieval-benchmark-v740",
            conversation_id="retrieval-benchmark-v740",
            metadata={
                "fixture_id": doc["id"],
                "title": doc["title"],
                "content": doc["content"],
                "workspace_id": "personal",
            },
        )
        id_map[doc["id"]] = result["node_id"]

    service = SearchService(graph)
    judged = []
    for query in QUERIES:
        rel_spec = query.get("relevant", [])
        if isinstance(rel_spec, dict):
            rel_ids = list(rel_spec.keys())
        else:
            rel_ids = rel_spec
        mi_spec = query.get("must_include", [])
        if isinstance(mi_spec, dict):
            mi_ids = list(mi_spec.keys())
        else:
            mi_ids = mi_spec
        result = service.hybrid_search(query["query"], limit=TOP_K, keyword_limit=TOP_K, vector_limit=TOP_K, graph_limit=TOP_K)
        judged.append({
            "query": query["query"],
            "relevant": [id_map[item] for item in rel_ids],
            "must_include": [id_map[item] for item in mi_ids],
            "retrieved": [item.get("node_id") or item.get("id") for item in result["matches"]],
        })

    metrics = RetrievalBenchmarkRunner().run_fixture(FIXTURE_NAME, judged, top_k=TOP_K)

    assert len(DOCUMENTS) >= 250
    assert metrics["judged"] == len(QUERIES)
    assert metrics[f"recall@{TOP_K}"] >= 0.80
    assert metrics[f"precision@{TOP_K}"] >= 0.25
    assert metrics[f"ndcg@{TOP_K}"] >= 0.70
    assert metrics["must_include_hit_rate"] >= 0.90
