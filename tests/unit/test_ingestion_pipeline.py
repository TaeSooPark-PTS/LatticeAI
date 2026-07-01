"""v3.6.0 unified ingestion pipeline + provenance tests.

Covers: text + file convergence through one entrypoint, content-hash
idempotency, SOURCE node + indexed_from provenance edge, the provenance trail,
embedding status, and the dispatch_tool hook lifecycle (fires + blocks) on
ingestion — the v3.5.0 coverage gap that v3.6.0 closes.
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_graph import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline
from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.services.upload_service import process_uploaded_document


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _pipeline(tmp_path: Path, hooks=None, enable_graph=True):
    return IngestionPipeline(_store(tmp_path), hooks=hooks, enable_graph=enable_graph)


def test_text_ingest_creates_content_source_chunks_and_provenance(tmp_path):
    pipe = _pipeline(tmp_path)
    item = IngestionItem(
        source_type="web_url",
        title="Lattice AI",
        text="Lattice AI is a Digital Brain Platform. The Knowledge Graph is the durable asset. "
             "TODO write the unified ingestion pipeline.",
        source_uri="https://example.com/lattice",
        owner="user@example.com",
    )
    res = pipe.ingest(item, user_email="user@example.com")

    assert res.status == "ok"
    assert res.node_id and res.node_id.startswith("webdoc:")
    assert res.content_hash and len(res.content_hash) == 64
    assert res.source_node_id and res.source_node_id.startswith("source:")
    assert res.chunk_count >= 1
    assert res.embedded is True
    assert res.indexing_status == "indexed"
    assert res.duplicate is False
    assert res.provenance_id

    prov = pipe._kg.get_provenance(res.node_id)
    assert prov is not None
    assert prov["source_type"] == "web_url"
    assert prov["source_uri"] == "https://example.com/lattice"
    assert prov["content_hash"] == res.content_hash
    assert prov["embedded"] is True
    assert prov["linked"] is True

    # SOURCE node is graph-visible and linked via indexed_from.
    stats = pipe._kg.stats()
    assert stats["nodes"].get("Source", 0) >= 1
    assert stats["nodes"].get("Document", 0) >= 1


def test_content_hash_idempotency_reports_duplicate(tmp_path):
    pipe = _pipeline(tmp_path)
    item = IngestionItem(source_type="note", title="note", text="same content body for dedup")
    first = pipe.ingest(item)
    second = pipe.ingest(item)
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.node_id == second.node_id  # same content -> same node (idempotent)


def test_ingestion_preserves_workspace_scope_for_duplicate_content(tmp_path):
    pipe = _pipeline(tmp_path)
    body = "same note body from two workspaces"

    first = pipe.ingest(
        IngestionItem(source_type="note", title="Shared", text=body, workspace_id="org:a"),
        user_email="alice@example.com",
    )
    second = pipe.ingest(
        IngestionItem(source_type="note", title="Shared", text=body, workspace_id="org:b"),
        user_email="bob@example.com",
    )

    assert first.status == "ok"
    assert second.status == "ok"
    assert first.content_hash == second.content_hash
    assert first.node_id != second.node_id
    assert pipe._kg.workspaces_of([first.node_id, second.node_id]) == {
        first.node_id: "org:a",
        second.node_id: "org:b",
    }
    assert pipe._kg.filter_scoped_nodes([{"id": first.node_id}], {"org:b"}) == []
    assert pipe._kg.filter_scoped_nodes([{"id": second.node_id}], {"org:a"}) == []


def test_file_ingest_converges_through_same_pipeline(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("# Plan\nLattice AI Knowledge Graph First. We must decide the schema.", encoding="utf-8")
    pipe = _pipeline(tmp_path)
    res = pipe.ingest(IngestionItem(source_type="file", path=str(src), owner="u@x.com", workspace_id="org:acme"))

    assert res.status == "ok"
    assert res.node_id and res.node_id.startswith("file:")
    assert res.source_node_id and res.source_node_id.startswith("source:")
    assert res.content_hash and len(res.content_hash) == 64
    assert pipe._kg.workspaces_of([res.node_id]).get(res.node_id) == "org:acme"
    assert pipe._kg.workspaces_of([res.source_node_id]).get(res.source_node_id) == "org:acme"
    assert pipe._kg.filter_scoped_nodes([{"id": res.node_id}], {"org:other"}) == []
    prov = pipe._kg.get_provenance(res.node_id)
    assert prov["source_type"] == "file"


def test_upload_result_enters_unified_ingestion_pipeline(tmp_path):
    class _Request:
        headers = {"X-Workspace-Id": "org:upload"}
        query_params = {"conversation_id": "upload-thread"}

    audits = []
    graph = _store(tmp_path)
    pipe = IngestionPipeline(graph)
    upload = UploadFile(
        filename="brief.txt",
        file=io.BytesIO(b"Lattice AI upload enters the unified ingestion pipeline."),
    )

    result = asyncio.run(
        process_uploaded_document(
            request=_Request(),
            file=upload,
            current_user="uploader@example.com",
            enable_graph=True,
            knowledge_graph=graph,
            ingestion_pipeline=pipe,
            bytes_match_extension=lambda _contents, _suffix: True,
            classify_sensitive_message=lambda _item, _index: {
                "preview": "Lattice AI upload",
                "sensitivity": "low",
                "labels": [],
            },
            append_audit_event=lambda *args, **kwargs: audits.append((args, kwargs)),
            enforce_rate_limit=lambda _user, _action: None,
        )
    )

    kg = result["knowledge_graph"]
    assert kg["node_id"].startswith("file:")
    assert kg["provenance_id"]
    assert graph.get_provenance(kg["node_id"])["source_type"] == "upload"
    assert graph.workspaces_of([kg["node_id"]])[kg["node_id"]] == "org:upload"
    assert audits


def test_dispatch_tool_hooks_fire_on_ingestion(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    fired = []
    reg.register_hook("builtin:tool-permission-gate", lambda ctx: fired.append(ctx.event))
    pipe = _pipeline(tmp_path, hooks=reg)
    res = pipe.ingest(IngestionItem(source_type="text", title="t", text="hook lifecycle body"))
    assert res.status == "ok"
    assert any(e.startswith("tool.kg_ingest.") for e in fired), fired


def test_pre_tool_block_makes_ingestion_blocked(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    reg.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("ingest denied"))
    pipe = _pipeline(tmp_path, hooks=reg)
    res = pipe.ingest(IngestionItem(source_type="text", title="t", text="blocked body"))
    assert res.status == "blocked"
    assert "denied" in (res.detail or "")


def test_oversized_text_is_rejected_gracefully(tmp_path):
    pipe = IngestionPipeline(_store(tmp_path), max_text_bytes=64)
    res = pipe.ingest(IngestionItem(source_type="text", title="big", text="x" * 500))
    assert res.status == "failed"
    assert "limit" in (res.detail or "").lower()


def test_unavailable_when_graph_disabled(tmp_path):
    pipe = _pipeline(tmp_path, enable_graph=False)
    res = pipe.ingest(IngestionItem(source_type="text", title="t", text="body"))
    assert res.status == "unavailable"
    assert res.indexing_status == "skipped"


def test_provenance_stats_aggregate(tmp_path):
    pipe = _pipeline(tmp_path)
    pipe.ingest(IngestionItem(source_type="web_url", title="a", text="alpha body", source_uri="u1"))
    pipe.ingest(IngestionItem(source_type="note", title="b", text="beta body"))
    stats = pipe._kg.provenance_stats()
    assert stats["total"] >= 2
    assert stats["by_source_type"].get("web_url", 0) >= 1
    assert stats["by_source_type"].get("note", 0) >= 1
    assert stats["embedded"] >= 1
