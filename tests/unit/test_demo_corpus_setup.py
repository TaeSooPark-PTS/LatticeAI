"""First Value Loop demo corpus API tests (backlog #3).

Drives the setup router's demo-corpus endpoints over a real IngestionPipeline
+ temp KnowledgeGraphStore: one-click install with demo:// provenance,
idempotent re-POST (duplicates, not copies), recall of the suggested
questions through hybrid search with the demo source cited, and full removal
via DELETE (nodes + chunks + orphaned Source cleanup).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from latticeai.api.setup import create_setup_router
from latticeai.setup.demo_corpus import DEMO_DOCUMENTS, DEMO_URI_PREFIX, SUGGESTED_QUESTIONS


def _client(tmp_path, *, enable_graph=True):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store, enable_graph=enable_graph)
    app = FastAPI()
    app.include_router(create_setup_router(
        model_router=None,
        require_user=lambda request: "demo@example.com",
        ingestion_pipeline=pipeline,
        knowledge_graph=store,
    ))
    return TestClient(app), store


def test_demo_corpus_install_ingests_three_documents(tmp_path):
    client, store = _client(tmp_path)
    r = client.post("/api/setup/demo-corpus", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["ingested"] == len(DEMO_DOCUMENTS) == 3
    assert body["duplicates"] == 0
    assert body["failed"] == 0
    for doc in body["documents"]:
        assert doc["status"] == "ok"
        assert doc["node_id"]
        assert doc["source_uri"].startswith(DEMO_URI_PREFIX)
        assert doc["chunk_count"] >= 1
        # Real provenance through the normal pipeline door.
        prov = store.get_provenance(doc["node_id"])
        assert prov["source_uri"] == doc["source_uri"]
    # Suggestion chips are pre-filled and answerable from the corpus.
    assert body["suggested_questions"] == SUGGESTED_QUESTIONS
    assert 2 <= len(body["suggested_questions"]) <= 3


def test_demo_corpus_install_is_idempotent(tmp_path):
    client, store = _client(tmp_path)
    first = client.post("/api/setup/demo-corpus", json={}).json()
    second = client.post("/api/setup/demo-corpus", json={}).json()
    assert second["status"] == "ok"
    assert second["ingested"] == 0
    assert second["duplicates"] == 3
    # Same nodes, no copies.
    first_ids = {doc["node_id"] for doc in first["documents"]}
    second_ids = {doc["node_id"] for doc in second["documents"]}
    assert first_ids == second_ids
    assert len(store.find_documents_by_uri_prefix(DEMO_URI_PREFIX)) == 3


def test_demo_corpus_status_reports_installed_state(tmp_path):
    client, _ = _client(tmp_path)
    before = client.get("/api/setup/demo-corpus").json()
    assert before["installed"] is False
    assert before["document_count"] == 0
    assert before["suggested_questions"] == SUGGESTED_QUESTIONS

    client.post("/api/setup/demo-corpus", json={})
    after = client.get("/api/setup/demo-corpus").json()
    assert after["installed"] is True
    assert after["document_count"] == 3


def test_demo_corpus_recall_answers_suggested_questions_with_sources(tmp_path):
    """The end-to-end promise: ask a chip question → the demo doc is cited."""
    client, store = _client(tmp_path)
    r = client.post("/api/setup/demo-corpus", json={})
    node_by_uri = {doc["source_uri"]: doc["node_id"] for doc in r.json()["documents"]}
    for chip in SUGGESTED_QUESTIONS:
        result = store.hybrid_search(chip["question"], top_k=3)
        retrieved = {m["node_id"] for m in result["matches"]}
        assert node_by_uri[chip["expected_source_uri"]] in retrieved, chip["question"]


def test_demo_corpus_delete_removes_documents_and_chunks(tmp_path):
    client, store = _client(tmp_path)
    installed = client.post("/api/setup/demo-corpus", json={}).json()
    node_ids = [doc["node_id"] for doc in installed["documents"]]

    r = client.delete("/api/setup/demo-corpus")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["removed_count"] == 3
    assert all(item["status"] == "ok" for item in body["removed"])

    assert store.find_documents_by_uri_prefix(DEMO_URI_PREFIX) == []
    with store._connect() as conn:
        for node_id in node_ids:
            assert conn.execute(
                "SELECT 1 FROM nodes WHERE id=?", (node_id,)
            ).fetchone() is None
            assert conn.execute(
                "SELECT 1 FROM chunks WHERE source_node=?", (node_id,)
            ).fetchone() is None
            assert conn.execute(
                "SELECT 1 FROM edges WHERE from_node=? OR to_node=?", (node_id, node_id)
            ).fetchone() is None
    # DELETE is idempotent too.
    again = client.delete("/api/setup/demo-corpus").json()
    assert again["removed_count"] == 0


def test_demo_corpus_requires_ingestion_pipeline(tmp_path):
    client, _ = _client(tmp_path, enable_graph=False)
    assert client.post("/api/setup/demo-corpus", json={}).status_code == 503
    assert client.get("/api/setup/demo-corpus").status_code == 503
    assert client.delete("/api/setup/demo-corpus").status_code == 503
