"""Timeline/audit filtering, graph answer traces, and the relationship explorer."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from latticeai.core.workspace_os import WorkspaceOSStore

AUDIT_EVENTS: List[Dict[str, Any]] = [
    {"event_type": "model_call", "user_email": "alice@example.com", "timestamp": "2026-01-01T00:00:00", "model": "llama-3"},
    {"event_type": "file_read", "user_email": "bob@example.com", "timestamp": "2026-01-02T00:00:00"},
    {"event_type": "folder_approved", "user": "bob@example.com", "timestamp": "2026-01-03T00:00:00"},
    {"event_type": "secret_scan", "user_email": "alice@example.com", "timestamp": "2026-01-04T00:00:00"},
    {"event_type": "admin_invite", "user_email": "alice@example.com", "timestamp": "2026-01-05T00:00:00"},
    {"event_type": "auth_failure", "user_email": "bob@example.com", "timestamp": "2026-01-06T00:00:00"},
    {"event_type": "workspace_touched", "user_email": "alice@example.com", "timestamp": "2026-01-07T00:00:00"},
]


def _store(tmp_path: Path, name: str = "data", **kwargs: Any) -> WorkspaceOSStore:
    target = tmp_path / name
    target.mkdir()
    return WorkspaceOSStore(target, **kwargs)


def _categories(result: Dict[str, Any]) -> List[str]:
    return [event["category"] for event in result["events"]]


def test_audit_events_are_classified_into_product_categories(tmp_path: Path):
    store = _store(tmp_path)

    result = store.filter_audit_timeline(AUDIT_EVENTS)

    assert result["total"] == len(AUDIT_EVENTS)
    # Newest first, so the categories come back in reverse insertion order.
    assert _categories(result) == [
        "workspace_event",
        "security_event",
        "admin_action",
        "sensitive_data",
        "folder_approval",
        "file_access",
        "model_usage",
    ]


def test_audit_filters_narrow_by_user_type_model_and_time_window(tmp_path: Path):
    store = _store(tmp_path)

    by_user = store.filter_audit_timeline(AUDIT_EVENTS, user="alice")
    by_type = store.filter_audit_timeline(AUDIT_EVENTS, event_type="model_call")
    by_model = store.filter_audit_timeline(AUDIT_EVENTS, model="llama-3")
    since = store.filter_audit_timeline(AUDIT_EVENTS, since="2026-01-06T00:00:00")
    until = store.filter_audit_timeline(AUDIT_EVENTS, until="2026-01-02T00:00:00")
    capped = store.filter_audit_timeline(AUDIT_EVENTS, limit=2)

    assert by_user["total"] == 4
    assert [event["event_type"] for event in by_type["events"]] == ["model_call"]
    assert [event["event_type"] for event in by_model["events"]] == ["model_call"]
    assert [event["event_type"] for event in since["events"]] == ["workspace_touched", "auth_failure"]
    assert [event["event_type"] for event in until["events"]] == ["file_read", "model_call"]
    assert len(capped["events"]) == 2
    assert capped["total"] == len(AUDIT_EVENTS)


def test_the_timeline_is_trimmed_once_it_passes_its_ceiling(tmp_path: Path):
    store = _store(tmp_path)
    state = store.load_state()
    state["timeline"] = [
        {"area": "test", "event_type": "seed", "timestamp": "2026-01-01T00:00:00", "payload": {"i": i}}
        for i in range(10001)
    ]
    store.save_state(state)

    store.record_timeline_event("workspace", "trim_check", {})

    trimmed = store.load_state()["timeline"]
    assert len(trimmed) == 8000
    assert trimmed[-1]["event_type"] == "trim_check"


def test_a_failing_event_sink_never_breaks_the_write(tmp_path: Path):
    seen: List[Dict[str, Any]] = []

    def sink(event: Dict[str, Any]) -> None:
        seen.append(event)
        raise RuntimeError("realtime bus is down")

    store = _store(tmp_path, event_sink=sink)

    entry = store.record_timeline_event("workspace", "sink_check", {"ok": True})

    assert entry["event_type"] == "sink_check"
    assert seen[-1]["type"] == "timeline"
    assert store.load_state()["timeline"][-1]["event_type"] == "sink_check"


def test_the_timeline_merges_runs_workflows_and_audit_events(tmp_path: Path):
    store = _store(tmp_path)
    store.record_agent_run(
        agent_id="agent:planner",
        status="ok",
        input_text="plan",
        output_text="done",
        user_email=None,
    )
    store.create_workflow(name="Digest", steps=[], user_email=None)
    store.create_organization_workspace(name="Acme", owner_user_id="user-owner")

    merged = store.timeline(audit_events=AUDIT_EVENTS, limit=500)

    areas = {event["area"] for event in merged["events"]}
    assert {"agent", "workflow", "audit", "workspace"} <= areas
    assert any(event["event_type"] == "workspace_touched" for event in merged["events"])


class TraceGraph:
    """Search/neighbour surface with switches for each failure the store handles."""

    def __init__(self, *, matches: Any = None, search_error: bool = False,
                 neighbor_error: bool = False, edge_count: int = 1) -> None:
        self._matches = [{"id": "node:a", "title": "A", "type": "Concept", "metadata": {"filename": "a.md"}}] \
            if matches is None else matches
        self._search_error = search_error
        self._neighbor_error = neighbor_error
        self._edge_count = edge_count

    def search(self, question: str, limit: int = 8, **_kwargs: Any) -> Dict[str, Any]:
        if self._search_error:
            raise RuntimeError("index is rebuilding")
        return {"matches": list(self._matches)[:limit]}

    def neighbors(self, node_id: str, **_kwargs: Any) -> Dict[str, Any]:
        if self._neighbor_error:
            raise RuntimeError("node vanished")
        return {
            "edges": [
                {"from": node_id, "to": "node:" + str(i), "type": "relates_to"}
                for i in range(self._edge_count)
            ]
        }


def test_graph_trace_without_a_graph_reports_that_it_was_disabled(tmp_path: Path):
    trace = _store(tmp_path).build_graph_trace("what changed?", None, "some context")

    assert trace["confidence"] == 0.0
    assert trace["graph_nodes"] == []
    assert trace["retrieval_metadata"]["graph_enabled"] is False
    assert trace["retrieval_metadata"]["context_chars"] == len("some context")


def test_graph_trace_survives_a_search_failure(tmp_path: Path):
    trace = _store(tmp_path).build_graph_trace("what changed?", TraceGraph(search_error=True), "ctx")

    assert trace["retrieval_metadata"]["search_error"] == "index is rebuilding"
    assert trace["graph_nodes"] == []
    assert trace["confidence"] == 0.05


def test_graph_trace_skips_idless_matches_and_neighbour_failures(tmp_path: Path):
    graph = TraceGraph(
        matches=[{"title": "no id"}, {"id": "node:a", "metadata": {"source": "a.md"}}],
        neighbor_error=True,
    )

    trace = _store(tmp_path).build_graph_trace("what changed?", graph)

    assert trace["graph_edges"] == []
    assert trace["retrieval_metadata"]["matched_nodes"] == 2
    assert [item["source"] for item in trace["source_files"]] == ["a.md"]


def test_graph_trace_caps_the_edges_it_collects(tmp_path: Path):
    graph = TraceGraph(edge_count=40)

    trace = _store(tmp_path).build_graph_trace("what changed?", graph)

    assert len(trace["graph_edges"]) == 24
    assert trace["retrieval_metadata"]["matched_edges"] == 24


def test_traces_can_be_listed_per_conversation(tmp_path: Path):
    store = _store(tmp_path)
    trace = store.build_graph_trace("what changed?", TraceGraph())
    store.record_trace(
        question="what changed?", response="a lot", conversation_id="conv-1",
        user_email=None, trace=trace,
    )
    store.record_trace(
        question="and now?", response="less", conversation_id="conv-2",
        user_email=None, trace=trace,
    )

    assert len(store.list_traces()["traces"]) == 2
    assert [item["conversation_id"] for item in store.list_traces(conversation_id="conv-1")["traces"]] == ["conv-1"]
    assert store.list_traces(conversation_id="conv-missing")["traces"] == []


class ExplorerGraph:
    """A capped ``graph()`` window plus a neighbour lookup outside of it."""

    def __init__(self, *, window: Dict[str, Any], neighbor_error: bool = False) -> None:
        self._window = window
        self._neighbor_error = neighbor_error

    def graph(self, limit: int = 500) -> Dict[str, Any]:
        return {
            "nodes": [dict(node) for node in self._window.get("nodes", [])],
            "edges": [dict(edge) for edge in self._window.get("edges", [])],
        }

    def neighbors(self, node_id: str, **_kwargs: Any) -> Dict[str, Any]:
        if self._neighbor_error:
            raise RuntimeError("node vanished")
        return {
            "neighbors": [{"id": "node:b", "title": "B"}],
            "edges": [{"from": node_id, "to": "node:b", "type": "relates_to"}],
        }


def test_relationship_explorer_without_a_graph_returns_an_empty_shape(tmp_path: Path):
    explored = _store(tmp_path).relationship_explorer(None, "node:a")

    assert explored == {
        "node_id": "node:a",
        "inbound": [],
        "outbound": [],
        "related_entities": [],
        "shortest_path": [],
    }


def test_relationship_explorer_asks_for_neighbours_outside_the_window(tmp_path: Path):
    graph = ExplorerGraph(window={"nodes": [{"id": "node:z"}], "edges": []})

    explored = _store(tmp_path).relationship_explorer(graph, "node:a")

    assert [edge["to"] for edge in explored["outbound"]] == ["node:b"]
    assert [node["id"] for node in explored["related_entities"]] == ["node:b"]


def test_relationship_explorer_tolerates_a_failing_neighbour_lookup(tmp_path: Path):
    graph = ExplorerGraph(window={"nodes": [{"id": "node:z"}], "edges": []}, neighbor_error=True)

    explored = _store(tmp_path).relationship_explorer(graph, "node:a", target_id="node:z")

    assert explored["inbound"] == []
    assert explored["outbound"] == []
    assert explored["shortest_path"] == []
    assert explored["node"] == {"id": "node:a"}


def test_shortest_path_needs_both_ends_and_a_route():
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "x", "to": "y"}]

    assert WorkspaceOSStore._shortest_path(edges, "a", "c") == ["a", "b", "c"]
    assert WorkspaceOSStore._shortest_path(edges, "a", None) == []
    assert WorkspaceOSStore._shortest_path(edges, "", "c") == []
    assert WorkspaceOSStore._shortest_path(edges, "a", "y") == []
