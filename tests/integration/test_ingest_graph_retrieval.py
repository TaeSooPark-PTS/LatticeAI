"""Hermetic ingest -> provenance -> retrieval integration coverage."""

from __future__ import annotations

from pathlib import Path

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline


def test_text_ingest_provenance_retrieval_and_workspace_scope(tmp_path: Path):
    graph = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipe = IngestionPipeline(graph)

    result = pipe.ingest(
        IngestionItem(
            source_type="note",
            title="Launch decision",
            text="Lattice AI 8.3.0 launch decision: keep graph ingestion workspace scoped.",
            source_uri="note://launch-decision",
            owner="alice@example.com",
            workspace_id="org:release",
        ),
        user_email="alice@example.com",
    )

    assert result.status == "ok"
    provenance = graph.get_provenance(result.node_id)
    assert provenance["source_type"] == "note"
    assert provenance["workspace_id"] == "org:release"

    matches = graph.search("workspace scoped launch decision", limit=10)["matches"]
    assert any(match["id"] == result.node_id for match in matches)
    scoped = graph.filter_scoped_nodes(matches, {"org:other"})
    assert all(match["id"] != result.node_id for match in scoped)
