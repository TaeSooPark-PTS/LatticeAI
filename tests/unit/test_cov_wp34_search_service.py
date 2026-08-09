"""Coverage for SearchService (wp34).

Two graph doubles drive the service: a modern store that accepts the
workspace-scoping keywords, and a *legacy* store whose methods predate them.
The legacy store is the point — every ``except TypeError`` branch in the
service exists so a pre-opt-in graph is re-scoped in the service instead of
failing open, and those branches are what the tests assert on.
"""

from __future__ import annotations

import pytest

from latticeai.services.search_service import SearchService


class _LegacyGraph:
    """Pre-opt-in graph: no ``allowed_workspaces`` / ``include_legacy_global``."""

    def __init__(self, *, scopes=None, matches=None, relationships=None, nodes=None, edges=None):
        self.scopes = scopes or {}
        self.matches = matches or []
        self.relationships = relationships or []
        self.nodes = nodes or []
        self.edges = edges or []
        self.traverse_error = None

    def filter_scoped_nodes(self, matches, allowed):  # legacy 2-arg signature
        return list(matches)

    def workspaces_of(self, node_ids):
        return {nid: self.scopes[nid] for nid in node_ids if nid in self.scopes}

    def search(self, query, limit):
        return {"query": query, "matches": list(self.matches)}

    def relationship_search(self, *, query="", node_id="", relationship_type="", limit=30):
        return {"relationships": list(self.relationships)}

    def traverse(self, node_id, *, depth=1, limit=100):
        if self.traverse_error:
            raise self.traverse_error
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def get_node(self, node_id):
        return next((n for n in self.nodes if n.get("id") == node_id), {"id": node_id})

    def graph(self, *, limit=300):
        return {"nodes": list(self.nodes), "edges": list(self.edges), "limit": limit}


class _ModernGraph:
    def __init__(self, *, matches=None, relationships=None, nodes=None, edges=None, vector=None):
        self.matches = matches or []
        self.relationships = relationships or []
        self.nodes = nodes or []
        self.edges = edges or []
        self.vector = vector or []
        self.traverse_error = None

    def filter_scoped_nodes(self, matches, allowed, *, include_legacy_global=False):
        return list(matches)

    def search(self, query, limit, *, allowed_workspaces=None, include_legacy_global=False):
        return {"query": query, "matches": list(self.matches)}

    def relationship_search(
        self, *, query="", node_id="", relationship_type="", limit=30,
        allowed_workspaces=None, include_legacy_global=False,
    ):
        return {"relationships": list(self.relationships)}

    def traverse(self, node_id, *, depth=1, limit=100, allowed_workspaces=None, include_legacy_global=False):
        if self.traverse_error:
            raise self.traverse_error
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def vector_search(self, query, *, limit=30, min_score=0.0):
        return {"matches": list(self.vector), "embedding_model": "fake", "embedding_dim": 8}


def _node(node_id, **extra):
    payload = {"id": node_id, "type": "Concept", "title": node_id, "summary": f"about {node_id}"}
    payload.update(extra)
    return payload


# ── disabled graph ───────────────────────────────────────────────────────────


def test_every_read_refuses_when_the_graph_is_disabled():
    service = SearchService(graph_store=None)

    with pytest.raises(ValueError, match="knowledge graph is disabled"):
        service.keyword_search("릴리스")


# ── legacy scoping fallback ──────────────────────────────────────────────────


def test_legacy_keyword_search_is_rescoped_inside_the_service():
    graph = _LegacyGraph(
        scopes={"n1": "w1", "n2": None, "n3": "org:other"},
        matches=[_node("n1"), _node("n2"), _node("n3"), _node("n4"), _node("")],
    )
    service = SearchService(graph_store=graph)

    payload = service.keyword_search("릴리스", allowed_workspaces={"w1"})

    assert payload["mode"] == "keyword"
    assert [m["id"] for m in payload["matches"]] == ["n1"], (
        "legacy-global, other-workspace, unknown and id-less nodes must all be dropped"
    )


def test_legacy_keyword_search_can_opt_into_legacy_global_nodes():
    graph = _LegacyGraph(
        scopes={"n1": "w1", "n2": None},
        matches=[_node("n1"), _node("n2")],
    )
    service = SearchService(graph_store=graph)

    payload = service.keyword_search(
        "릴리스", allowed_workspaces={"w1"}, include_legacy_global=True
    )

    assert [m["id"] for m in payload["matches"]] == ["n1", "n2"]


def test_legacy_search_without_scoping_returns_the_raw_payload():
    graph = _LegacyGraph(matches=[_node("n1"), _node("n2")])
    service = SearchService(graph_store=graph)

    payload = service.keyword_search("릴리스")

    assert [m["id"] for m in payload["matches"]] == ["n1", "n2"]


def test_legacy_relationship_search_keeps_only_fully_visible_edges():
    graph = _LegacyGraph(
        scopes={"n1": "w1", "n2": "w1", "n3": "org:other"},
        relationships=[
            {"id": "r1", "type": "relates", "source": {"id": "n1"}, "target": {"id": "n2"}},
            {"id": "r2", "type": "relates", "source": {"id": "n1"}, "target": {"id": "n3"}},
        ],
    )
    service = SearchService(graph_store=graph)

    payload = service.relationships(query="릴리스", allowed_workspaces={"w1"})

    assert [rel["id"] for rel in payload["relationships"]] == ["r1"]


def test_legacy_relationship_search_without_scoping_is_untouched():
    graph = _LegacyGraph(relationships=[{"id": "r1", "source": {"id": "n1"}, "target": {"id": "n2"}}])
    service = SearchService(graph_store=graph)

    assert len(service.relationships(query="릴리스")["relationships"]) == 1


def test_legacy_traverse_drops_out_of_scope_nodes_and_dangling_edges():
    graph = _LegacyGraph(
        scopes={"n1": "w1", "n2": "w1", "n3": "org:other"},
        nodes=[_node("n1"), _node("n2"), _node("n3")],
        edges=[
            {"from": "n1", "to": "n2", "type": "relates"},
            {"from": "n1", "to": "n3", "type": "relates"},
        ],
    )
    service = SearchService(graph_store=graph)

    payload = service.node("n1", allowed_workspaces={"w1"})

    assert {n["id"] for n in payload["neighborhood"]["nodes"]} == {"n1", "n2"}
    assert payload["neighborhood"]["edges"] == [{"from": "n1", "to": "n2", "type": "relates"}]


def test_legacy_traverse_without_scoping_is_untouched():
    graph = _LegacyGraph(nodes=[_node("n1"), _node("n3")], edges=[{"from": "n1", "to": "n3"}])
    service = SearchService(graph_store=graph)

    payload = service.node("n1")

    assert len(payload["neighborhood"]["nodes"]) == 2
    assert payload["node"]["id"] == "n1"


def test_legacy_get_node_refuses_an_out_of_scope_node():
    graph = _LegacyGraph(scopes={"n3": "org:other"}, nodes=[_node("n3")])
    service = SearchService(graph_store=graph)

    with pytest.raises(ValueError, match="graph node not found: n3"):
        service.node("n3", include_neighbors=False, allowed_workspaces={"w1"})


def test_legacy_graph_snapshot_is_rescoped_inside_the_service():
    graph = _LegacyGraph(
        scopes={"n1": "w1", "n3": "org:other"},
        nodes=[_node("n1"), _node("n3")],
        edges=[{"from": "n1", "to": "n3"}, {"from": "n1", "to": "n1"}],
    )
    service = SearchService(graph_store=graph)

    scoped = service.graph(allowed_workspaces={"w1"})
    assert [n["id"] for n in scoped["nodes"]] == ["n1"]
    assert scoped["edges"] == [{"from": "n1", "to": "n1"}]

    unscoped = service.graph()
    assert len(unscoped["nodes"]) == 2


# ── graph_search expansion ───────────────────────────────────────────────────


def test_graph_search_skips_expansion_when_depth_clamps_to_zero():
    """``expand_depth=0`` falsily becomes 1; only a negative depth clamps to 0."""
    graph = _ModernGraph(
        matches=[_node("n1")],
        nodes=[_node("n1"), _node("n2")],
        edges=[{"from": "n1", "to": "n2"}],
    )
    service = SearchService(graph_store=graph)

    assert [m["id"] for m in service.graph_search("릴리스", expand_depth=0)["matches"]] == ["n1", "n2"]

    payload = service.graph_search("릴리스", expand_depth=-1)

    assert [m["id"] for m in payload["matches"]] == ["n1"]
    assert payload["expand_depth"] == 0


def test_graph_search_survives_a_failing_traversal():
    graph = _ModernGraph(matches=[_node("n1")])
    graph.traverse_error = RuntimeError("traversal index missing")
    service = SearchService(graph_store=graph)

    payload = service.graph_search("릴리스", expand_depth=2)

    assert [m["id"] for m in payload["matches"]] == ["n1"]


def test_graph_search_adds_relationship_endpoints_and_ignores_id_less_ones():
    graph = _ModernGraph(
        matches=[],
        relationships=[
            {
                "id": "r1",
                "type": "relates",
                "weight": 0.8,
                "source": _node("n1"),
                "target": _node("n2"),
            },
            {"id": "r2", "type": "relates", "source": {}, "target": {}},
        ],
    )
    service = SearchService(graph_store=graph)

    payload = service.graph_search("릴리스")

    assert [m["id"] for m in payload["matches"]] == ["n1", "n2"]
    context = payload["matches"][0]["graph_context"][0]
    assert context["reason"] == "relationship_match"
    assert context["relationship"]["from"] == "n1"


# ── hybrid fusion ────────────────────────────────────────────────────────────


def test_hybrid_fusion_drops_channel_rows_without_an_identity():
    graph = _ModernGraph(
        matches=[_node("n1")],
        vector=[{"score": 0.9, "title": "no identity"}],
    )
    service = SearchService(graph_store=graph)

    payload = service.hybrid_search("릴리스 절차", weights={"keyword": 1.0})

    assert [m["id"] for m in payload["matches"]] == ["n1"]


def test_recency_queries_decay_by_age_and_leave_undated_items_alone():
    graph = _ModernGraph(
        matches=[_node("n1", updated_at="2000-01-01T00:00:00"), _node("n2")],
    )
    service = SearchService(graph_store=graph)

    payload = service.hybrid_search("recent notes")

    assert payload["query_class"] == "recency"
    decay = {m["id"]: m["source_scores"]["age_decay"] for m in payload["matches"]}
    assert decay["n2"] == 1.0, "an unknown age is not evidence of staleness"
    assert decay["n1"] < 1.0


# ── embeddings status ────────────────────────────────────────────────────────


class _Embedder:
    def __init__(self, *, metadata_error=None, health_value=None, with_metadata=True):
        self.metadata_error = metadata_error
        self.health_value = health_value
        if not with_metadata:
            del self.__class__.metadata

    def metadata(self):
        if self.metadata_error:
            raise self.metadata_error
        return {"provider": "local", "model": "m", "model_id": "m", "dim": 8, "grade": "production"}

    def health(self):
        return self.health_value


class _LegacyEmbedder:
    model_id = "lattice-local-hash-v1"
    dim = 384


class _EmbeddingGraph(_ModernGraph):
    def __init__(self, embedder, *, index=None):
        super().__init__()
        self._embedding_model = embedder
        self._index = index or {
            "status": "ready",
            "source_items": 3,
            "indexed_items": 3,
            "ready_items": 3,
            "pending_items": 0,
            "stale_items": 0,
            "storage": {"embedding_model": "m", "embedding_dim": 8},
            "operations": [
                {"status": "running", "completed_at": None},
                {"status": "completed", "completed_at": "2026-01-02T03:04:05"},
            ],
        }

    def index_status(self):
        return self._index


def test_embedding_metadata_failure_degrades_to_an_empty_descriptor():
    service = SearchService(
        graph_store=_EmbeddingGraph(_Embedder(metadata_error=RuntimeError("provider gone")))
    )

    status = service.embeddings_status(resolved={"fell_back": True})

    assert status["provider"] is None
    assert status["state"] == "unavailable"
    assert status["last_indexed_at"] == "2026-01-02T03:04:05"


def test_legacy_hash_embedder_reports_the_fallback_grade():
    service = SearchService(graph_store=_EmbeddingGraph(_LegacyEmbedder()))

    status = service.embeddings_status()

    assert status["provider"] == "hash"
    assert status["dimensions"] == 384
    assert status["grade"] == "fallback"
    assert status["state"] == "fallback"


def test_refresh_asks_the_embedder_for_live_health():
    embedder = _Embedder(health_value={"status": "ok", "detail": "reachable"})
    service = SearchService(graph_store=_EmbeddingGraph(embedder))

    status = service.embeddings_status(resolved={"health": {"status": "unknown"}}, refresh=True)

    assert status["health"] == {"status": "ok", "detail": "reachable"}
    assert status["state"] == "production"
