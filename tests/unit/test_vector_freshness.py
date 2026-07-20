"""Vector freshness summary (v9.8.0) tests.

Covers the store-level ``vector_freshness()`` reduction of ``index_status``
(ready / pending / unavailable, never raises), the service-level
normalization in ``BrainIntelligenceService.vector_freshness`` (graph
disabled, legacy index_status-only stores, broken stores), and the fixed
``GET /api/brain/vector-freshness`` router contract the frontend consumes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.brain_intelligence import create_brain_intelligence_router
from latticeai.services.brain_intelligence import BrainIntelligenceService

CONTRACT_KEYS = {"status", "pending_items", "total_items", "detail"}


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    store.ingest_source(
        source_type="note",
        title="Freshness Note",
        text="Vector freshness summarizes pending embedding backlog for the API.",
        source_uri="note:freshness",
    )
    return store


def _assert_contract(payload):
    assert set(payload) == CONTRACT_KEYS
    assert payload["status"] in {"ready", "pending", "unavailable"}
    assert isinstance(payload["pending_items"], int)
    assert isinstance(payload["total_items"], int)
    assert isinstance(payload["detail"], str)


# ── store layer ──────────────────────────────────────────────────────────


def test_store_vector_freshness_ready_after_ingest(tmp_path):
    store = _store(tmp_path)
    result = store.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "ready"
    assert result["pending_items"] == 0
    assert result["total_items"] >= 1


def test_store_vector_freshness_reports_pending_backlog(tmp_path):
    store = _store(tmp_path)
    with store._connect() as conn:
        conn.execute("DELETE FROM vector_embeddings")
    result = store.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "pending"
    assert result["pending_items"] >= 1
    assert result["pending_items"] <= result["total_items"]
    assert str(result["pending_items"]) in result["detail"]


def test_store_vector_freshness_never_raises(tmp_path, monkeypatch):
    store = _store(tmp_path)

    def boom():
        raise RuntimeError("embedding provider offline")

    monkeypatch.setattr(store, "index_status", boom)
    result = store.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "unavailable"
    assert "embedding provider offline" in result["detail"]


# ── service layer ────────────────────────────────────────────────────────


class _FreshnessKG:
    def vector_freshness(self):
        return {
            "status": "pending",
            "pending_items": 3,
            "total_items": 10,
            "detail": "3 of 10 items are missing or stale in the vector index",
        }


class _StatusOnlyKG:
    """Legacy shape: index_status exists but vector_freshness does not."""

    def index_status(self):
        return {"status": "needs_reindex", "pending_items": 2, "source_items": 5}


class _BrokenKG:
    def vector_freshness(self):
        raise RuntimeError("no embedder configured")


class _NoVectorKG:
    pass


def test_service_disabled_graph_is_unavailable_not_error():
    service = BrainIntelligenceService(knowledge_graph=None, enable_graph=False)
    result = service.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "unavailable"
    assert result["detail"]


def test_service_passes_through_store_freshness():
    service = BrainIntelligenceService(knowledge_graph=_FreshnessKG())
    result = service.vector_freshness()
    _assert_contract(result)
    assert result == {
        "status": "pending",
        "pending_items": 3,
        "total_items": 10,
        "detail": "3 of 10 items are missing or stale in the vector index",
    }


def test_service_summarizes_legacy_index_status_stores():
    service = BrainIntelligenceService(knowledge_graph=_StatusOnlyKG())
    result = service.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "pending"
    assert result["pending_items"] == 2
    assert result["total_items"] == 5


def test_service_degrades_broken_store_to_unavailable():
    service = BrainIntelligenceService(knowledge_graph=_BrokenKG())
    result = service.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "unavailable"
    assert "no embedder configured" in result["detail"]


def test_service_reports_unavailable_without_vector_support():
    service = BrainIntelligenceService(knowledge_graph=_NoVectorKG())
    result = service.vector_freshness()
    _assert_contract(result)
    assert result["status"] == "unavailable"


# ── router layer ─────────────────────────────────────────────────────────


def _client(kg) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_brain_intelligence_router(
            service=BrainIntelligenceService(knowledge_graph=kg),
            require_user=lambda request: "owner@example.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda *a, **k: None,
        )
    )
    return TestClient(app)


def test_router_vector_freshness_contract():
    response = _client(_FreshnessKG()).get("/api/brain/vector-freshness")
    assert response.status_code == 200
    body = response.json()
    _assert_contract(body)
    assert body["status"] == "pending"
    assert body["pending_items"] == 3


def test_router_vector_freshness_unavailable_is_still_200():
    response = _client(_BrokenKG()).get("/api/brain/vector-freshness")
    assert response.status_code == 200
    body = response.json()
    _assert_contract(body)
    assert body["status"] == "unavailable"
