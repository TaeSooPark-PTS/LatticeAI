"""Lattice AI v3 backend search architecture tests."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_graph import KnowledgeGraphStore
from latticeai.api.search import create_search_router
from latticeai.services.search_service import SearchService


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _seed_hybrid_graph(store: KnowledgeGraphStore) -> None:
    store.ingest_message(
        "user",
        (
            "Lattice AI hybrid search combines keyword retrieval, local vector "
            "similarity, and graph traversal for workspace knowledge."
        ),
        user_email="user@example.com",
        conversation_id="hybrid-search",
        source="test",
    )
    store.ingest_message(
        "assistant",
        (
            "The vector index stores local embeddings in SQLite and expands "
            "context through knowledge graph relationships."
        ),
        user_email="user@example.com",
        conversation_id="hybrid-search",
        source="test",
    )


def test_vector_index_is_incremental_and_rebuildable(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)

    ready = store.index_status()
    assert ready["status"] == "ready"
    assert ready["indexed_items"] > 0

    with store._connect() as conn:
        conn.execute("DELETE FROM vector_embeddings")

    missing = store.index_status()
    assert missing["status"] == "needs_reindex"
    assert missing["missing_items"] > 0
    assert missing["scale"]["coverage_ratio"] == 0
    assert missing["scale"]["incremental_reindex_recommended"] is True
    assert missing["scale"]["backlog_reasons"]["missing_vector"] == missing["missing_items"]
    assert missing["scale"]["backlog_samples"][0]["reason"] == "missing_vector"

    rebuilt = store.rebuild_vector_index(full=True)
    assert rebuilt["status"] == "completed"
    assert rebuilt["items_indexed"] > 0
    rebuilt_status = store.index_status()
    assert rebuilt_status["status"] == "ready"
    assert rebuilt_status["scale"]["coverage_percent"] == 100.0
    assert rebuilt_status["scale"]["latency_budget"]["last_items_per_second"] > 0


def test_vector_index_status_explains_stale_backlog(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)
    with store._connect() as conn:
        node_id = conn.execute(
            "SELECT item_id FROM vector_embeddings WHERE item_type='node' LIMIT 1"
        ).fetchone()["item_id"]
        conn.execute(
            "UPDATE nodes SET summary=? WHERE id=?",
            ("changed summary that should force a vector refresh", node_id),
        )

    status = store.index_status()
    assert status["status"] == "needs_reindex"
    assert status["stale_items"] == 1
    assert status["scale"]["backlog_reasons"]["text_changed"] == 1
    assert status["scale"]["backlog_by_item_type"]["node"] == 1
    assert status["scale"]["backlog_samples"][0]["item_id"] == node_id


def test_vector_search_returns_local_similarity_results(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)

    result = store.vector_search("local vector embeddings sqlite", limit=5)

    assert result["embedding_model"].startswith("lattice-local-hash-v1")
    assert result["matches"]
    assert any("vector" in match["summary"].lower() for match in result["matches"])


def test_graph_relationship_search_and_traversal(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)

    relationships = store.relationship_search(query="vector", limit=10)
    assert relationships["relationships"]

    node_id = relationships["relationships"][0]["source"]["id"]
    traversal = store.traverse(node_id, depth=2, limit=20)

    assert traversal["root"] == node_id
    assert traversal["nodes"]
    assert traversal["edges"]


def test_hybrid_search_fuses_keyword_vector_and_graph_sources(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)
    service = SearchService(store)

    result = service.hybrid_search("hybrid vector graph traversal", limit=5)

    assert result["mode"] == "hybrid"
    assert result["matches"]
    assert {"keyword", "vector", "graph"} & set(result["matches"][0]["sources"])
    assert result["matches"][0]["score"] > 0


def test_v3_search_api_contracts(tmp_path):
    store = _store(tmp_path)
    _seed_hybrid_graph(store)
    app = FastAPI()
    app.include_router(
        create_search_router(
            service=SearchService(store),
            require_user=lambda request: "user@example.com",
        )
    )
    client = TestClient(app)

    status = client.get("/api/index/status")
    assert status.status_code == 200
    assert status.json()["storage"]["backend"] == "sqlite"

    hybrid = client.post(
        "/api/search/hybrid",
        json={"query": "hybrid vector graph", "limit": 5},
    )
    assert hybrid.status_code == 200
    assert hybrid.json()["matches"]

    node_id = hybrid.json()["matches"][0]["node_id"]
    node = client.get("/api/graph/node", params={"node_id": node_id})
    assert node.status_code == 200
    assert node.json()["node"]["id"] == node_id

    rel = client.get("/api/graph/relationship", params={"q": "vector", "limit": 5})
    assert rel.status_code == 200
    assert "relationships" in rel.json()

    rebuild = client.post("/api/index/rebuild", json={"full": True})
    assert rebuild.status_code == 200
    assert rebuild.json()["status"] == "completed"
