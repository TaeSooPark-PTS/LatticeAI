"""wp31: the v3 search router's GET variants and its disabled-graph behaviour.

Existing suites drive the POST hybrid / GET node / index endpoints over a live
store. What never ran: the GET twins of hybrid/keyword/vector, both POST
``/api/graph/*`` handlers, and — for every route — the ``ValueError`` branch
that turns "the knowledge graph is disabled" into a 404 instead of a 500.

``SearchService(graph_store=None)`` is the real disabled service: every
graph-backed method raises ``ValueError("knowledge graph is disabled")``, which
is exactly the condition these branches exist for. Only ``embeddings_status``
needs a stand-in, because the real one swallows its own failures.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.search import create_search_router
from latticeai.services.search_service import SearchService

USER = "searcher@example.com"


def _seeded_store(tmp_path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    store.ingest_message(
        "user",
        "Lattice AI hybrid search blends keyword retrieval, vector similarity "
        "and knowledge graph traversal for workspace recall.",
        user_email=USER,
        conversation_id="wp31-search",
        source="test",
    )
    store.ingest_message(
        "assistant",
        "The local vector index lives in SQLite and expands context through "
        "graph relationships between documents.",
        user_email=USER,
        conversation_id="wp31-search",
        source="test",
    )
    store.rebuild_vector_index(full=True)
    return store


def _client(service: Any, **kwargs: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_search_router(
            service=service,
            require_user=lambda request: USER,
            **kwargs,
        )
    )
    return TestClient(app)


@pytest.fixture()
def live(tmp_path):
    return _client(SearchService(_seeded_store(tmp_path)))


@pytest.fixture()
def disabled():
    """A router over a service whose knowledge graph is switched off."""
    return _client(SearchService(None))


# ── GET twins over a live index ──────────────────────────────────────────────


def test_hybrid_get_returns_the_same_shape_as_the_post_form(live):
    body = live.get(
        "/api/search/hybrid", params={"q": "hybrid vector graph", "limit": 5}
    ).json()

    assert body["mode"] == "hybrid"
    assert body["matches"]
    assert body["matches"][0]["score"] > 0


def test_keyword_search_is_reachable_by_get_and_post(live):
    posted = live.post(
        "/api/search/keyword", json={"query": "vector similarity", "limit": 5}
    )
    fetched = live.get(
        "/api/search/keyword", params={"q": "vector similarity", "limit": 5}
    )

    assert posted.status_code == 200
    assert fetched.status_code == 200
    assert posted.json()["matches"]
    assert [m["node_id"] for m in fetched.json()["matches"]] == [
        m["node_id"] for m in posted.json()["matches"]
    ]


def test_vector_search_is_reachable_by_get_and_post_and_honours_min_score(live):
    posted = live.post(
        "/api/search/vector",
        json={"query": "graph relationships", "limit": 5, "min_score": 0.0},
    )
    fetched = live.get(
        "/api/search/vector",
        params={"q": "graph relationships", "limit": 5, "min_score": 0.99},
    )

    assert posted.status_code == 200
    assert posted.json()["matches"]
    assert fetched.status_code == 200
    assert all(
        match["score"] >= 0.99 for match in fetched.json()["matches"]
    )


def test_graph_search_returns_matches_and_the_relationships_that_reached_them(live):
    """The third channel, reachable at last.

    ``graph_search`` was in the router's scope allow-list from the day the
    chokepoint was written, but no handler called it: the service method was
    only ever exercised from inside ``hybrid_search``. 11.5.2 gives it the
    ``POST /api/search/graph`` route the allow-list already assumed.
    """
    response = live.post(
        "/api/search/graph", json={"query": "graph relationships", "limit": 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matches"], "a seeded store must answer the graph channel"
    assert all(match.get("node_id") for match in body["matches"])
    assert "graph" in body["matches"][0]["sources"]


def test_graph_search_expand_depth_zero_keeps_only_the_direct_matches(live):
    # depth is the whole difference between "what matched" and "what the
    # matches are connected to"; a route that ignored it would look correct
    # against a small fixture and quietly return the same page every time.
    direct = live.post(
        "/api/search/graph",
        json={"query": "graph relationships", "limit": 5, "expand_depth": 0},
    ).json()
    expanded = live.post(
        "/api/search/graph",
        json={"query": "graph relationships", "limit": 5, "expand_depth": 3},
    ).json()

    assert direct["matches"]
    assert len(direct["matches"]) <= len(expanded["matches"])


def test_graph_search_is_workspace_scoped_like_its_siblings(tmp_path):
    seen: List[Dict[str, Any]] = []

    class RecordingService:
        def graph_search(self, query, **kwargs):
            seen.append({"query": query, **kwargs})
            return {"matches": [], "query": query}

    client = _client(
        RecordingService(), allowed_workspaces_for=lambda user: {"ws-a"},
    )
    client.post("/api/search/graph", json={"query": "scoped", "limit": 4})

    assert seen == [
        {"query": "scoped", "limit": 4, "expand_depth": 1, "allowed_workspaces": {"ws-a"}}
    ]


def test_graph_node_post_matches_the_get_form(live):
    node_id = live.get(
        "/api/search/hybrid", params={"q": "hybrid vector graph", "limit": 3}
    ).json()["matches"][0]["node_id"]

    fetched = live.get("/api/graph/node", params={"node_id": node_id})
    posted = live.post(
        "/api/graph/node",
        json={"node_id": node_id, "include_neighbors": True, "depth": 1, "limit": 10},
    )

    assert posted.status_code == 200
    assert posted.json()["node"]["id"] == node_id
    assert posted.json()["node"] == fetched.json()["node"]


def test_graph_relationship_post_matches_the_get_form(live):
    fetched = live.get("/api/graph/relationship", params={"q": "vector", "limit": 5})
    posted = live.post(
        "/api/graph/relationship", json={"query": "vector", "limit": 5}
    )

    assert fetched.status_code == 200
    assert posted.status_code == 200
    assert posted.json()["relationships"] == fetched.json()["relationships"]


def test_graph_node_post_404s_an_unknown_node(live):
    response = live.post("/api/graph/node", json={"node_id": "no-such-node"})

    assert response.status_code == 404


def test_workspace_scope_is_injected_at_the_single_chokepoint(tmp_path):
    """Every scoped call gets the caller's allowed set without per-handler code."""
    seen: List[Dict[str, Any]] = []

    class RecordingService:
        def keyword_search(self, query, **kwargs):
            seen.append({"query": query, **kwargs})
            return {"matches": [], "query": query}

        def index_status(self):
            seen.append({"index_status": True})
            return {"status": "ready"}

    client = _client(
        RecordingService(),
        allowed_workspaces_for=lambda user: {"ws-a", "ws-b"},
    )

    client.get("/api/search/keyword", params={"q": "scoped"})
    client.get("/api/index/status")

    assert seen[0]["allowed_workspaces"] == {"ws-a", "ws-b"}
    # Unscoped methods are passed straight through, untouched.
    assert seen[1] == {"index_status": True}


# ── the disabled-graph branch on every route ─────────────────────────────────

_DISABLED_CALLS = [
    ("POST", "/api/search/hybrid", {"query": "q"}, None),
    ("GET", "/api/search/hybrid", None, {"q": "q"}),
    ("POST", "/api/search/keyword", {"query": "q"}, None),
    ("GET", "/api/search/keyword", None, {"q": "q"}),
    ("POST", "/api/search/vector", {"query": "q"}, None),
    ("GET", "/api/search/vector", None, {"q": "q"}),
    ("POST", "/api/search/graph", {"query": "q"}, None),
    ("GET", "/api/graph", None, None),
    ("GET", "/api/graph/node", None, {"node_id": "n1"}),
    ("POST", "/api/graph/node", {"node_id": "n1"}, None),
    ("GET", "/api/graph/relationship", None, {"q": "x"}),
    ("POST", "/api/graph/relationship", {"query": "x"}, None),
    ("GET", "/api/index/status", None, None),
    ("POST", "/api/index/rebuild", {"full": True}, None),
]


@pytest.mark.parametrize(("method", "path", "payload", "params"), _DISABLED_CALLS)
def test_a_disabled_graph_is_a_404_not_a_500(disabled, method, path, payload, params):
    response = disabled.request(method, path, json=payload, params=params)

    assert response.status_code == 404
    assert response.json()["detail"] == "knowledge graph is disabled"


def test_embeddings_status_translates_a_service_value_error(tmp_path):
    class FailingEmbeddings:
        def embeddings_status(self, *, resolved=None, refresh=False):
            raise ValueError("embedding provider is not configured")

    client = _client(FailingEmbeddings(), embedding_info=lambda: {"provider": "hash"})

    response = client.get("/api/embeddings/status", params={"refresh": True})

    assert response.status_code == 404
    assert response.json()["detail"] == "embedding provider is not configured"
