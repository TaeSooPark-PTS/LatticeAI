from pathlib import Path

import pytest

from latticeai.core.workspace_os import WorkspaceOSStore


class FakeGraph:
    def __init__(self):
        self.nodes = [
            {"id": "node:a", "type": "Decision", "title": "Ship 1.0", "summary": "release", "metadata": {}},
            {"id": "node:b", "type": "Feature", "title": "Workspace OS", "summary": "foundation", "metadata": {"filename": "README.md"}},
        ]
        self.edges = [
            {"from": "node:a", "to": "node:b", "type": "depends_on", "weight": 1.0},
        ]
        self.events = []

    def graph(self, limit=2000):
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def stats(self):
        return {"nodes": {"Decision": 1, "Feature": 1}, "edges": {"depends_on": 1}, "local_sources": 1}

    def local_sources(self):
        return {
            "sources": [{
                "id": "source:one",
                "label": "Repo",
                "root_path": "/tmp/repo",
                "status": "indexed",
                "watch_enabled": True,
                "last_scanned_at": "2026-05-31T00:00:00",
                "file_status": {"indexed": 2, "failed": 1},
            }]
        }

    def search(self, query, limit=8):
        return {"matches": list(self.nodes)[:limit]}

    def neighbors(self, node_id):
        return {"node_id": node_id, "neighbors": list(self.nodes), "edges": list(self.edges)}

    def set_local_source_watch(self, source_id, enabled):
        return {"source_id": source_id, "watch_enabled": enabled}

    def ingest_event(self, event_type, title, **kwargs):
        node_id = f"event:{len(self.events)}"
        self.events.append({"node_id": node_id, "event_type": event_type, "title": title, **kwargs})
        return {"node_id": node_id}


def test_onboarding_state_is_reentrant(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)

    first = store.update_onboarding_step("hardware", status="complete", data={"cpu": "apple"})
    second = WorkspaceOSStore(tmp_path).onboarding_status()

    assert first["steps"][2]["status"] == "complete"
    assert second["steps"][2]["data"]["cpu"] == "apple"
    assert second["current_step"] == "model_recommendation"


def test_graph_trace_includes_sources_edges_and_confidence(tmp_path: Path):
    trace = WorkspaceOSStore(tmp_path).build_graph_trace("Workspace OS", FakeGraph(), "context")

    assert trace["confidence"] > 0.5
    assert trace["source_files"][0]["source"] == "README.md"
    assert trace["graph_edges"][0]["type"] == "depends_on"
    assert trace["retrieval_metadata"]["matched_nodes"] == 2


def test_snapshot_compare_reports_knowledge_diff(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    graph = FakeGraph()
    first = store.create_snapshot(name="before", graph=graph, history=[], settings={}, models={})["snapshot"]["id"]
    graph.nodes.append({"id": "node:c", "type": "Decision", "title": "Add Time Machine", "summary": "done", "metadata": {}})
    graph.edges.append({"from": "node:c", "to": "node:b", "type": "relates_to", "weight": 0.7})
    second = store.create_snapshot(name="after", graph=graph, history=[], settings={}, models={})["snapshot"]["id"]

    diff = store.compare_snapshots(first, second)

    assert diff["summary"]["nodes_added"] == 1
    assert diff["summary"]["edges_added"] == 1
    assert diff["summary"]["decisions_changed"] == 1


def test_memory_requires_known_kind_and_links_graph(tmp_path: Path):
    graph = FakeGraph()
    store = WorkspaceOSStore(tmp_path)

    memory = store.upsert_memory(
        kind="preferences",
        content="Prefer autonomous execution",
        user_email="user@example.com",
        graph=graph,
    )

    assert memory["graph_node_id"].startswith("event:")
    assert store.search_memories("autonomous")["memories"][0]["id"] == memory["id"]
    with pytest.raises(ValueError):
        store.upsert_memory(kind="unknown", content="x", user_email=None)


def test_computer_memory_is_off_until_explicit_approval(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)

    assert store.record_computer_activity({"summary": "change"})["status"] == "ignored"
    with pytest.raises(PermissionError):
        store.configure_computer_memory(enabled=True, approved_by="user@example.com", consent={})

    config = store.configure_computer_memory(
        enabled=True,
        approved_by="user@example.com",
        consent={"approved": True},
    )
    assert config["enabled"] is True
    assert store.record_computer_activity({"summary": "change"})["status"] == "ok"


def test_relationship_explorer_finds_shortest_path(tmp_path: Path):
    result = WorkspaceOSStore(tmp_path).relationship_explorer(FakeGraph(), "node:a", target_id="node:b")

    assert result["outbound"][0]["to"] == "node:b"
    assert result["shortest_path"] == ["node:a", "node:b"]
