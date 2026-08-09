"""wp31: the knowledge-graph router's pages, pipeline ribbon and ingest paths.

The router is well covered for search/context/scoping; the gaps were the two
legacy page redirects, the provenance-coverage read, the empty-node-id guard,
the ingestion-pipeline branch of ``/knowledge-graph/ingest``, and — the bulk of
it — the ``/knowledge-graph/pipeline/status`` fallbacks. That handler derives
three stage counts from whichever of documents / stats / index-status happens
to answer, so each fallback needs a store that answers differently. The fakes
below are named for the shape of store they imitate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.knowledge_graph import create_knowledge_graph_router

USER = "kg@example.com"


class FakeGraph:
    """A store whose every read can be configured (or made to fail)."""

    def __init__(
        self,
        *,
        documents: Optional[List[Dict[str, Any]]] = None,
        documents_error: bool = False,
        stats: Any = None,
        stats_error: bool = False,
        index: Any = None,
        has_index: bool = True,
    ) -> None:
        self._documents = documents if documents is not None else []
        self._documents_error = documents_error
        self._stats = stats if stats is not None else {"nodes": {}, "edges": {}}
        self._stats_error = stats_error
        self._index = index
        self.ingested: List[Dict[str, Any]] = []
        if not has_index:
            # A store that predates the vector index has no such attribute.
            self.index_status = None  # type: ignore[assignment]

    # ── reads the pipeline ribbon uses ───────────────────────────────────
    def list_documents(self, limit: int = 200) -> Dict[str, Any]:
        if self._documents_error:
            raise RuntimeError("documents table unavailable")
        return {"documents": list(self._documents), "total": len(self._documents)}

    def stats(self, **kwargs: Any) -> Any:
        if self._stats_error:
            raise RuntimeError("stats unavailable")
        return self._stats

    def index_status(self) -> Any:
        return self._index

    # ── scope contract ───────────────────────────────────────────────────
    def filter_scoped_nodes(self, items, allowed, include_legacy_global=False):
        allowed_ids = {str(x) for x in (allowed or [])}
        return [
            item
            for item in items
            if str(item.get("workspace_id") or "") in allowed_ids
        ]

    # ── other router surfaces ────────────────────────────────────────────
    def provenance_coverage(self) -> Dict[str, Any]:
        return {"documents": 4, "with_provenance": 3, "coverage": 0.75}

    def neighbors(self, node_id: str) -> Dict[str, Any]:
        return {"neighbors": [], "edges": []}

    def graph(self, limit: int = 300, **kwargs: Any) -> Dict[str, Any]:
        return {"nodes": [], "edges": [], "limit": limit}

    def ingest_message(self, role, content, **kwargs: Any) -> Dict[str, Any]:
        self.ingested.append({"role": role, "content": content, **kwargs})
        return {"node_id": "node-%d" % len(self.ingested), "role": role}


class FakePipeline:
    """Unified ingestion pipeline stand-in with a settable result status."""

    def __init__(self, status: str = "ok", detail: str = "") -> None:
        self.status = status
        self.detail = detail
        self.items: List[Any] = []

    def ingest(self, item, user_email=None):
        self.items.append((item, user_email))
        status = self.status
        detail = self.detail

        class _Result:
            def as_dict(self_inner):
                return {"status": status, "node_id": "ingested-1"}

        result = _Result()
        result.status = status
        result.detail = detail
        return result


def build(
    graph: FakeGraph,
    *,
    allowed=None,
    ingestion_pipeline: Any = None,
    require_admin=None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=lambda request: USER,
            static_dir=None,
            require_admin=require_admin,
            allowed_workspaces_for=(lambda user: allowed) if allowed is not None else None,
            ingestion_pipeline=ingestion_pipeline,
        )
    )
    return TestClient(app)


# ── pages ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/graph", "/knowledge-graph"])
def test_graph_pages_redirect_into_the_spa(path):
    client = build(FakeGraph())

    response = client.get(path + "?node=abc", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/app#/knowledge-graph?node=abc"


def test_provenance_coverage_is_reported_verbatim():
    client = build(FakeGraph())

    body = client.get("/knowledge-graph/provenance/coverage").json()

    assert body == {"documents": 4, "with_provenance": 3, "coverage": 0.75}


# ── pipeline ribbon: one configuration per fallback ──────────────────────────


def test_pipeline_status_filters_documents_to_the_callers_workspaces():
    graph = FakeGraph(
        documents=[
            {"id": "d1", "workspace_id": "ws-mine", "indexed": True},
            {"id": "d2", "workspace_id": "ws-mine", "chunks": 0},
            {"id": "d3", "workspace_id": "ws-theirs", "indexed": True},
        ],
        stats={"nodes": {"Document": 3}, "edges": {"RELATES_TO": 2}},
    )
    client = build(graph, allowed={"ws-mine"})

    body = client.get("/knowledge-graph/pipeline/status").json()

    assert body["received"] == 2
    assert body["extracted"] == 1
    assert body["connected"] == 2
    assert body["stages"]["received"]["status"] == "done"
    # received(2) - extracted(1) is the fallback backlog when no index answers.
    assert body["stages"]["extracted"] == {
        "count": 1,
        "pending": 1,
        "status": "working",
    }
    assert body["updated_at"]


def test_pipeline_status_falls_back_to_node_counts_when_documents_fail():
    graph = FakeGraph(
        documents_error=True,
        stats={"nodes": {"Decision": 3, "Chunk": 9}, "edges": 5},
        index={"ready_items": 2, "pending": 4},
    )
    client = build(graph)

    body = client.get("/knowledge-graph/pipeline/status").json()

    # edges came back as a plain integer, nodes as a dict (Chunk excluded).
    assert body["connected"] == 5
    assert body["received"] == 3
    assert body["extracted"] == 2
    # "pending" is honoured when the store has no "pending_items".
    assert body["stages"]["extracted"]["pending"] == 4


def test_pipeline_status_reads_v2_edges_and_a_scalar_node_count():
    graph = FakeGraph(
        documents_error=True,
        stats={"nodes": 4, "v2": {"edges": 12}},
        index=None,
    )
    client = build(graph)

    body = client.get("/knowledge-graph/pipeline/status").json()

    assert body["connected"] == 12
    assert body["received"] == 4
    assert "extracted" not in body
    assert set(body["stages"]) == {"received", "connected"}


def test_pipeline_status_uses_the_index_when_documents_and_stats_both_fail():
    graph = FakeGraph(
        documents_error=True,
        stats_error=True,
        index={"source_items": 7, "ready_items": 3, "pending_items": 1},
    )
    client = build(graph)

    body = client.get("/knowledge-graph/pipeline/status").json()

    assert body["received"] == 7
    assert body["extracted"] == 3
    assert "connected" not in body
    assert body["stages"]["extracted"] == {
        "count": 3,
        "pending": 1,
        "status": "working",
    }


def test_pipeline_status_reports_no_backlog_when_only_extraction_is_known():
    graph = FakeGraph(
        documents_error=True,
        stats={"edges": 0},
        index={"ready_items": 2},
    )
    client = build(graph)

    body = client.get("/knowledge-graph/pipeline/status").json()

    assert "received" not in body
    assert body["extracted"] == 2
    assert body["connected"] == 0
    # No received count and no index backlog → no invented pending number.
    assert body["stages"]["extracted"] == {"count": 2, "pending": 0, "status": "done"}


def test_pipeline_status_shows_connection_backlog_while_the_graph_is_empty():
    graph = FakeGraph(
        documents=[{"id": "d1", "indexed": True}],
        stats={"nodes": {"Document": 1}, "edges": {}},
        index={"pending_items": 3},
    )
    client = build(graph)

    body = client.get("/knowledge-graph/pipeline/status").json()

    assert body["connected"] == 0
    assert body["stages"]["connected"] == {
        "count": 0,
        "pending": 3,
        "status": "working",
    }


def test_pipeline_status_is_empty_when_nothing_can_be_computed():
    graph = FakeGraph(documents_error=True, stats_error=True, has_index=False)
    client = build(graph)

    assert client.get("/knowledge-graph/pipeline/status").json() == {}


# ── neighbors guard ──────────────────────────────────────────────────────────


def test_neighbors_requires_a_node_id():
    client = build(FakeGraph())

    response = client.get(
        "/knowledge-graph/neighbors/", headers={"Accept-Language": "en"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A node id is required."


# ── ingest through the unified pipeline ──────────────────────────────────────


def test_ingest_routes_through_the_unified_pipeline_when_one_is_wired():
    graph = FakeGraph()
    pipeline = FakePipeline()
    client = build(graph, ingestion_pipeline=pipeline)

    response = client.post(
        "/knowledge-graph/ingest",
        json={
            "type": "ai_response",
            "content": "the answer",
            "title": "Reply",
            "source": "chat",
            "conversation_id": "c-1",
            "metadata": {"tone": "brief"},
        },
        headers={"X-Workspace-Id": "ws-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "node_id": "ingested-1"}
    item, user_email = pipeline.items[0]
    assert user_email == USER
    assert item.source_type == "chat_message"
    assert item.workspace_id == "ws-1"
    assert item.metadata["role"] == "assistant"
    assert item.metadata["tone"] == "brief"
    assert item.metadata["raw"]["workspace_id"] == "ws-1"
    # The pipeline owns the write; the legacy store path is not also taken.
    assert graph.ingested == []


def test_ingest_surfaces_a_failed_pipeline_result_as_500():
    pipeline = FakePipeline(status="error", detail="extraction failed")
    client = build(FakeGraph(), ingestion_pipeline=pipeline)

    response = client.post(
        "/knowledge-graph/ingest", json={"type": "note", "content": "a note"}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "extraction failed"
    assert pipeline.items[0][0].source_type == "note"


def test_ingest_without_a_pipeline_still_writes_through_the_store():
    graph = FakeGraph()
    client = build(graph)

    response = client.post(
        "/knowledge-graph/ingest", json={"type": "message", "content": "hello"}
    )

    assert response.status_code == 200
    assert graph.ingested[0]["role"] == "user"
    assert graph.ingested[0]["content"] == "hello"
