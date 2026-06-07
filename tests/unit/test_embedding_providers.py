"""Provider-backed embedding architecture tests.

Covers the interface contract, the offline hash fallback, graceful degradation
when a real provider is unavailable, the knowledge-graph integration (injected
embedder), and the /api/embeddings/status surface.
"""

import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_graph import KnowledgeGraphStore
from latticeai.api.search import create_search_router
from latticeai.core.embedding_providers import (
    HashEmbeddingProvider,
    PROVIDER_TYPES,
    build_embedding_provider,
    embedding_provider_profiles,
    resolve_embedding_profile,
    resolve_embedder,
)
from latticeai.services.search_service import SearchService


def test_hash_provider_is_normalized_and_round_trips():
    p = HashEmbeddingProvider()
    vec = p.embed("Lattice AI hybrid retrieval over the knowledge graph")
    assert len(vec) == p.dim == 384
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-6)
    # codec round-trip preserves the vector
    restored = p.decode(p.encode(vec), p.dim)
    assert all(math.isclose(a, b, rel_tol=1e-6) for a, b in zip(vec, restored))
    # identical vectors have similarity 1.0
    assert math.isclose(p.similarity(vec, restored), 1.0, rel_tol=1e-6)
    assert p.grade == "fallback"


def test_batch_embed_matches_single():
    p = HashEmbeddingProvider()
    texts = ["alpha vector", "beta graph", "gamma fusion"]
    batch = p.embed_batch(texts)
    assert len(batch) == 3
    for text, vec in zip(texts, batch):
        assert vec == p.embed(text)


def test_factory_knows_every_provider_type():
    assert set(PROVIDER_TYPES) == {"hash", "mlx", "ollama", "openai", "custom"}
    # constructing a remote provider must not perform any network I/O
    prov = build_embedding_provider("ollama", model="nomic-embed-text", base_url="http://127.0.0.1:1")
    assert prov.provider == "ollama"
    assert prov.model_id.startswith("ollama:")


def test_production_embedding_profiles_cover_v310_matrix():
    profiles = {p["id"]: p for p in embedding_provider_profiles()}
    required = {
        "local:bge-m3",
        "local:nomic-embed-text",
        "local:e5-large",
        "local:gte-large",
        "ollama:nomic-embed-text",
        "ollama:mxbai-embed-large",
        "ollama:bge-m3",
        "mlx:bge-m3",
        "openai:text-embedding-3-small",
        "openai:text-embedding-3-large",
    }
    assert required <= set(profiles)
    assert all(profiles[item]["grade"] == "production" for item in required)
    assert resolve_embedding_profile("openai:text-embedding-3-large")["dimensions"] == 3072


def test_unavailable_provider_degrades_to_hash_without_crashing():
    # an unreachable Ollama endpoint must fall back to the offline hash model
    resolved = resolve_embedder("ollama", model="nomic-embed-text", base_url="http://127.0.0.1:1", timeout=1)
    assert resolved.fell_back is True
    assert resolved.active == "hash"
    assert resolved.requested == "ollama"
    assert resolved.health["status"] == "unavailable"
    # the returned embedder still works
    assert len(resolved.provider.embed("ping")) == resolved.provider.dim


def test_hash_resolution_is_not_a_fallback():
    resolved = resolve_embedder("hash")
    assert resolved.fell_back is False
    assert resolved.active == "hash"
    info = resolved.as_dict()
    assert info["grade"] == "fallback"
    assert info["dim"] == 384


def _store(tmp_path: Path, embedder) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs", embedder=embedder)


def test_knowledge_graph_uses_injected_embedder(tmp_path):
    embedder = resolve_embedder("hash").provider
    store = _store(tmp_path, embedder)
    assert store._embedding_model is embedder
    store.ingest_message(
        "user",
        "Lattice AI hybrid search blends vector similarity and graph traversal.",
        user_email="user@example.com",
        conversation_id="emb",
        source="test",
    )
    rebuild = store.rebuild_vector_index(full=True)
    assert rebuild["status"] == "completed"
    assert rebuild["embedding_model"] == embedder.model_id
    hits = store.vector_search("vector graph retrieval", limit=5)
    assert hits["embedding_model"] == embedder.model_id
    assert isinstance(hits["matches"], list)


def test_embeddings_status_reports_provider_and_state(tmp_path):
    embedder = resolve_embedder("hash")
    store = _store(tmp_path, embedder.provider)
    store.ingest_message(
        "user", "seed content for the vector index",
        user_email="u@example.com", conversation_id="c", source="test",
    )
    store.rebuild_vector_index(full=True)
    svc = SearchService(graph_store=store)
    status = svc.embeddings_status(resolved=embedder.as_dict())
    assert status["provider"] == "hash"
    assert status["state"] == "fallback"
    assert status["dimensions"] == 384
    assert status["last_indexed_at"] is not None


def test_search_router_embeddings_endpoints(tmp_path):
    embedder = resolve_embedder("hash")
    store = _store(tmp_path, embedder.provider)
    svc = SearchService(graph_store=store)
    app = FastAPI()
    app.include_router(create_search_router(
        service=svc,
        require_user=lambda request: "tester",
        embedding_info=lambda: {**embedder.as_dict(), "available_providers": list(PROVIDER_TYPES)},
    ))
    client = TestClient(app)

    r = client.get("/api/embeddings/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "hash"
    assert body["state"] in {"fallback", "production", "unavailable"}
    assert body["dimensions"] == 384

    r2 = client.get("/api/embeddings/providers")
    assert r2.status_code == 200
    ids = {p["id"] for p in r2.json()["providers"]}
    assert ids == {"hash", "mlx", "ollama", "openai", "custom"}
    profile_ids = {p["id"] for p in r2.json()["profiles"]}
    assert {"local:bge-m3", "ollama:mxbai-embed-large", "openai:text-embedding-3-small"} <= profile_ids
