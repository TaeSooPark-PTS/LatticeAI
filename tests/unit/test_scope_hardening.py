from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.memory import create_memory_router
from latticeai.api.search import create_search_router
from latticeai.services.memory_service import MemoryService
from latticeai.services.search_service import SearchService


def _graph_store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with store._connect() as conn:
        store._upsert_node(
            conn,
            "node:alpha",
            "Document",
            "Alpha workspace document",
            "scoped alpha content",
            {"workspace_id": "ws-alpha"},
            workspace_id="ws-alpha",
        )
        store._upsert_node(
            conn,
            "node:beta",
            "Document",
            "Beta workspace document",
            "scoped beta content",
            {"workspace_id": "ws-beta"},
            workspace_id="ws-beta",
        )
        store._upsert_edge(conn, "node:alpha", "node:beta", "mentions", 1.0, {})
    return store


def test_graph_api_filters_graph_node_and_relationships_by_workspace(tmp_path):
    store = _graph_store(tmp_path)
    app = FastAPI()
    app.include_router(
        create_search_router(
            service=SearchService(store),
            require_user=lambda request: "alpha@example.com",
            allowed_workspaces_for=lambda user: {"ws-alpha"},
        )
    )
    client = TestClient(app)

    graph = client.get("/api/graph").json()
    assert {node["id"] for node in graph["nodes"]} == {"node:alpha"}
    assert graph["edges"] == []

    visible = client.get("/api/graph/node", params={"node_id": "node:alpha"}).json()
    assert visible["node"]["id"] == "node:alpha"
    assert {node["id"] for node in visible["neighborhood"]["nodes"]} == {"node:alpha"}
    assert visible["neighborhood"]["edges"] == []

    hidden = client.get("/api/graph/node", params={"node_id": "node:beta"})
    assert hidden.status_code == 404

    relationships = client.get("/api/graph/relationship", params={"q": "mentions"}).json()
    assert relationships["relationships"] == []


class _MemoryStore:
    def __init__(self):
        self.deleted = []
        self.memories = [
            {
                "id": "m-alpha",
                "kind": "preferences",
                "content": "alpha memory",
                "workspace_id": "ws-alpha",
                "user_email": "alpha@example.com",
            },
            {
                "id": "m-beta",
                "kind": "preferences",
                "content": "beta memory",
                "workspace_id": "ws-beta",
                "user_email": "alpha@example.com",
            },
        ]

    def list_memories(self, *, user_email=None, workspace_id=None):
        rows = [
            memory
            for memory in self.memories
            if (user_email is None or memory.get("user_email") == user_email)
            and (workspace_id is None or memory.get("workspace_id") == workspace_id)
        ]
        return {"memories": rows}

    def search_memories(self, query, *, user_email=None, limit=20, workspace_id=None):
        return self.list_memories(user_email=user_email, workspace_id=workspace_id)

    def list_memory_snapshots(self, *, workspace_id=None, limit=200):
        return {"snapshots": []}

    def delete_memory(self, memory_id):
        self.deleted.append(memory_id)
        self.memories = [memory for memory in self.memories if memory.get("id") != memory_id]


def test_memory_manager_mutations_are_workspace_scoped(tmp_path):
    backing = _MemoryStore()
    service = MemoryService(store=backing, data_dir=tmp_path, enable_graph=False)
    app = FastAPI()
    app.include_router(
        create_memory_router(
            service=service,
            require_user=lambda request: "alpha@example.com",
            get_current_user=lambda request: "alpha@example.com",
            gate_read=lambda request: "ws-alpha",
            gate_write=lambda request: "ws-alpha",
            append_audit_event=lambda *args, **kwargs: None,
        )
    )
    client = TestClient(app)

    prune = client.post("/api/memory/prune", json={"ids": ["m-alpha", "m-beta"]}).json()
    assert prune["removed"] == ["m-alpha"]
    assert prune["skipped"] == ["m-beta"]
    assert backing.deleted == ["m-alpha"]
    assert [memory["id"] for memory in backing.memories] == ["m-beta"]

    clear_graph = client.post(
        "/api/memory/clear",
        json={"scope": "graph", "confirm": True},
    )
    assert clear_graph.status_code == 400
    assert "not workspace-scoped" in clear_graph.json()["detail"]
