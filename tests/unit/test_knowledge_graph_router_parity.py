"""Endpoint-parity guard for the relocated knowledge graph router (T2).

``knowledge_graph_api.py`` (root) moved to ``latticeai.api.knowledge_graph``;
the root module is now a deprecation shim. These tests freeze the route paths,
methods, and response keys so the relocation cannot change the API the v3 SPA
consumes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.knowledge_graph import create_knowledge_graph_router

# Frozen from the pre-move root knowledge_graph_api.py.
BASELINE_ROUTES = {
    ("/graph", "GET"),
    ("/knowledge-graph", "GET"),
    ("/knowledge-graph/stats", "GET"),
    # v4 T4.1 addition: the provenance coverage honesty metric.
    ("/knowledge-graph/provenance/coverage", "GET"),
    ("/knowledge-graph/schema", "GET"),
    ("/knowledge-graph/graph", "GET"),
    ("/knowledge-graph/documents", "GET"),
    ("/knowledge-graph/search", "GET"),
    ("/knowledge-graph/context", "GET"),
    ("/knowledge-graph/neighbors/{node_id:path}", "GET"),
    ("/knowledge-graph/ingest", "POST"),
}


class _StubGraph:
    def stats(self):
        return {"schema_version": 3, "v2_schema_available": True, "v2": {"nodes": 1}}

    def graph(self, limit):
        return {"nodes": [], "edges": [], "limit": limit}

    def list_documents(self, limit):
        return {"documents": [], "limit": limit}

    def search(self, q, limit):
        return {"query": q, "matches": []}

    def context_for_query(self, q, limit):
        return ""

    def neighbors(self, node_id):
        return {"node": node_id, "neighbors": []}

    def ingest_message(self, role, content, **kwargs):
        return {"status": "ok", "role": role, "ingested": bool(content)}


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    graph = _StubGraph()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=lambda _request: "user@example.com",
            static_dir=tmp_path,
        )
    )
    return TestClient(app)


def test_route_paths_and_methods_unchanged(tmp_path: Path):
    router = create_knowledge_graph_router(
        get_graph=lambda: _StubGraph(),
        require_graph=lambda: None,
        require_user=lambda _request: "user@example.com",
        static_dir=tmp_path,
    )
    current = {
        (route.path, method)
        for route in router.routes
        for method in (getattr(route, "methods", set()) or set())
        if method not in {"HEAD", "OPTIONS"}
    }
    assert current == BASELINE_ROUTES


def test_root_shim_reexports_the_same_router_factory():
    import knowledge_graph_api

    assert knowledge_graph_api.create_knowledge_graph_router is create_knowledge_graph_router
    assert hasattr(knowledge_graph_api, "KnowledgeGraphIngestRequest")


def test_response_keys_unchanged(tmp_path: Path):
    client = _client(tmp_path)

    stats = client.get("/knowledge-graph/stats")
    assert stats.status_code == 200
    assert set(stats.json()) == {"schema_version", "v2_schema_available", "v2"}

    schema = client.get("/knowledge-graph/schema")
    assert schema.status_code == 200
    assert set(schema.json()) == {"legacy_schema_version", "v2_schema_available", "v2"}

    search_empty = client.get("/knowledge-graph/search", params={"q": "  "})
    assert search_empty.status_code == 200
    assert set(search_empty.json()) == {"query", "matches"}

    search = client.get("/knowledge-graph/search", params={"q": "프로젝트"})
    assert search.status_code == 200
    assert set(search.json()) == {"query", "matches"}

    context = client.get("/knowledge-graph/context", params={"q": "hello"})
    assert context.status_code == 200
    assert set(context.json()) == {"query", "context"}

    graph_data = client.get("/knowledge-graph/graph", params={"limit": 5})
    assert graph_data.status_code == 200
    assert graph_data.json()["limit"] == 5

    documents = client.get("/knowledge-graph/documents")
    assert documents.status_code == 200
    assert "documents" in documents.json()

    neighbors = client.get("/knowledge-graph/neighbors/node-1")
    assert neighbors.status_code == 200
    assert set(neighbors.json()) == {"node", "neighbors"}


def test_ingest_validates_type_and_delegates(tmp_path: Path):
    client = _client(tmp_path)

    bad = client.post("/knowledge-graph/ingest", json={"type": "bogus", "content": "x"})
    assert bad.status_code == 400

    ok = client.post("/knowledge-graph/ingest", json={"type": "note", "content": "hello"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
