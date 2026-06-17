"""Workspace-scoping / ownership guarantees for the Digital Brain read paths.

Priority-1 isolation hardening (v6.4.0). Three boundaries are covered:

1. The knowledge-graph router scopes every node/content read to the caller's
   allowed workspaces (legacy-global rows stay visible), and is a no-op when no
   scope resolver is wired (single-user / no-auth mode).
2. ``SearchService.hybrid_search`` passes the caller's scope down into each
   fusion channel, not just the fused result.
3. ``MemoryService.prune`` refuses to delete a memory the caller does not own,
   even when its id is supplied explicitly.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.knowledge_graph import create_knowledge_graph_router
from latticeai.services.memory_service import MemoryService
from latticeai.services.search_service import SearchService


# ── shared fake knowledge graph ───────────────────────────────────────────────
class _FakeGraph:
    """Three documents: Alice's (ws-alice), Bob's (ws-bob), and a legacy-global
    one (no workspace). Implements only the surface the router touches, and
    mirrors the real ``filter_scoped_nodes`` contract."""

    def __init__(self):
        self._scopes = {"n-alice": "ws-alice", "n-bob": "ws-bob", "n-global": None}
        self._nodes = {
            "n-alice": {"id": "n-alice", "type": "Document", "title": "Alice plan",
                        "summary": "alice secret roadmap", "metadata": {}},
            "n-bob": {"id": "n-bob", "type": "Document", "title": "Bob plan",
                      "summary": "bob secret roadmap", "metadata": {}},
            "n-global": {"id": "n-global", "type": "Document", "title": "Shared note",
                         "summary": "shared content", "metadata": {}},
        }

    # read surface used by the router
    def search(self, q, limit=30):
        return {"query": q, "matches": [dict(n) for n in self._nodes.values()]}

    def graph(self, limit=300, *, allowed_workspaces=None):
        nodes = [dict(n) for n in self._nodes.values()]
        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(nodes, allowed_workspaces)
        return {"nodes": nodes, "edges": []}

    def list_documents(self, limit=200):
        docs = [{"id": nid, "filename": n["title"]} for nid, n in self._nodes.items()]
        return {"documents": docs, "total": len(docs), "generated_at": "now"}

    def neighbors(self, node_id):
        nbrs = [dict(n) for nid, n in self._nodes.items() if nid != node_id]
        edges = [{"from": node_id, "to": n["id"], "type": "rel", "weight": 1.0} for n in nbrs]
        return {"node_id": node_id, "neighbors": nbrs, "edges": edges}

    def context_for_query(self, q, limit=6):
        return "\n".join(f"- {n['title']}: {n['summary']}" for n in self._nodes.values())

    # scoping primitives (copied contract from KnowledgeGraphRetrievalMixin)
    def workspaces_of(self, node_ids):
        return {str(nid): self._scopes.get(str(nid)) for nid in node_ids}

    def filter_scoped_nodes(self, items, allowed_workspaces, *, id_key="id"):
        if allowed_workspaces is None:
            return list(items)
        allowed = set(allowed_workspaces)
        scopes = self.workspaces_of([it.get(id_key) for it in items])
        return [
            it for it in items
            if scopes.get(it.get(id_key)) is None or scopes.get(it.get(id_key)) in allowed
        ]


def _kg_client(*, scoped: bool) -> TestClient:
    app = FastAPI()
    graph = _FakeGraph()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=lambda _r: "alice@test.local",
            static_dir=Path("/tmp"),
            allowed_workspaces_for=(lambda _u: {"ws-alice"}) if scoped else None,
        )
    )
    return TestClient(app)


# ── 1. knowledge-graph router scoping ─────────────────────────────────────────
def test_kg_search_hides_other_workspaces():
    client = _kg_client(scoped=True)
    ids = {m["id"] for m in client.get("/knowledge-graph/search?q=secret").json()["matches"]}
    assert ids == {"n-alice", "n-global"}  # Bob's row never reaches Alice


def test_kg_graph_documents_and_neighbors_are_scoped():
    client = _kg_client(scoped=True)
    graph_ids = {n["id"] for n in client.get("/knowledge-graph/graph").json()["nodes"]}
    doc_ids = {d["id"] for d in client.get("/knowledge-graph/documents").json()["documents"]}
    nbr = client.get("/knowledge-graph/neighbors/n-alice").json()
    hidden = client.get("/knowledge-graph/neighbors/n-bob")
    nbr_ids = {n["id"] for n in nbr["neighbors"]}
    edge_targets = {e["to"] for e in nbr["edges"]}

    assert "n-bob" not in graph_ids
    assert "n-bob" not in doc_ids
    assert hidden.status_code == 404
    assert nbr_ids == {"n-global"}  # n-bob filtered, n-global (legacy) kept
    assert "n-bob" not in edge_targets  # edges to dropped nodes are pruned


def test_kg_context_excludes_other_workspaces():
    context = _kg_client(scoped=True).get("/knowledge-graph/context?q=secret").json()["context"]
    assert "alice secret" in context
    assert "bob secret" not in context  # RAG context cannot leak Bob's content


def test_kg_no_scope_resolver_is_transparent():
    # Single-user / no-auth mode: nothing is filtered.
    ids = {m["id"] for m in _kg_client(scoped=False).get("/knowledge-graph/search?q=x").json()["matches"]}
    assert ids == {"n-alice", "n-bob", "n-global"}


# ── 2. SearchService.hybrid_search pushes scope into every channel ─────────────
def test_hybrid_search_passes_scope_to_each_channel():
    class _ScopeRecorder(SearchService):
        def __init__(self, graph_store):
            super().__init__(graph_store)
            self.seen = {}

        def keyword_search(self, query, **kw):
            self.seen["keyword"] = kw.get("allowed_workspaces")
            return {"matches": []}

        def vector_search(self, query, **kw):
            self.seen["vector"] = kw.get("allowed_workspaces")
            return {"matches": []}

        def graph_search(self, query, **kw):
            self.seen["graph"] = kw.get("allowed_workspaces")
            return {"matches": []}

    svc = _ScopeRecorder(_FakeGraph())
    svc.hybrid_search("q", allowed_workspaces={"ws-alice"})
    assert svc.seen == {"keyword": {"ws-alice"}, "vector": {"ws-alice"}, "graph": {"ws-alice"}}


# ── 3. MemoryService.prune ownership guard ────────────────────────────────────
class _FakeMemoryStore:
    def __init__(self):
        self.memories = {
            "m-alice": {"id": "m-alice", "user_email": "alice@test.local", "kind": "long_term", "content": "a"},
            "m-bob": {"id": "m-bob", "user_email": "bob@test.local", "kind": "long_term", "content": "b"},
        }

    def list_memories(self, user_email=None, workspace_id=None):
        mems = list(self.memories.values())
        if user_email:
            mems = [m for m in mems if m["user_email"] == user_email]
        return {"memories": mems}

    def delete_memory(self, mid):
        if mid not in self.memories:
            raise KeyError(mid)
        del self.memories[mid]


def _memory_service(store, tmp_path):
    return MemoryService(store=store, data_dir=tmp_path, knowledge_graph=None, enable_graph=False)


def test_prune_refuses_to_delete_another_users_memory(tmp_path):
    store = _FakeMemoryStore()
    result = _memory_service(store, tmp_path).prune(ids=["m-bob"], user_email="alice@test.local")
    assert result["count"] == 0
    assert result.get("skipped") == ["m-bob"]
    assert "m-bob" in store.memories  # Bob's memory survives Alice's forged prune


def test_prune_deletes_callers_own_memory(tmp_path):
    store = _FakeMemoryStore()
    result = _memory_service(store, tmp_path).prune(ids=["m-alice"], user_email="alice@test.local")
    assert result["removed"] == ["m-alice"]
    assert "m-alice" not in store.memories
    assert "m-bob" in store.memories
