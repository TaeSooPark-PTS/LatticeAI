"""Graph promotion review mode tests (review 2026-07-25 Wave 4).

Covers the review-before-promote governance for curator topic promotions:
default ``curate()`` still auto-promotes (regression guard), review mode parks
would-be promotions in ``graph_meta.pending_promotions`` without writing Topic
nodes, ``apply_pending_promotions`` lands exactly the nodes/edges a direct
curate would have written (single shared write path), reject drops entries,
the ``LATTICEAI_GRAPH_PROMOTION_REVIEW`` env opt-in flips the default, the
Enterprise capability seam resolves, and the ``/knowledge-graph/promotions*``
API routes round-trip. All seeds are deterministic (direct ``_upsert_node``
writes — no LLM extraction path is involved in ``curate()``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.knowledge_graph import create_knowledge_graph_router
from latticeai.core.enterprise import (
    CapabilityRegistry,
    EnterpriseCapability,
    capability_registry,
    is_capability_enabled,
)


def _store(tmp_path: Path, name: str = "kg") -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / f"{name}.sqlite", tmp_path / f"{name}-blobs")


def _seed_promotable(store: KnowledgeGraphStore) -> None:
    """Eight Documents sharing multi-source topic tokens (쿠버네티스, 배포, …)
    so the curator's gated pipeline promotes deterministically."""
    with store._connect() as conn:
        for index in range(8):
            store._upsert_node(
                conn,
                f"doc:k8s-{index}",
                "Document",
                f"쿠버네티스 배포 노트 {index}",
                summary="쿠버네티스 클러스터 오토스케일링 설정",
            )


def _topic_ids(store: KnowledgeGraphStore) -> set:
    with store._connect() as conn:
        return {
            row["id"]
            for row in conn.execute("SELECT id FROM nodes WHERE type='Topic'")
        }


def _mention_edges(store: KnowledgeGraphStore) -> set:
    with store._connect() as conn:
        return {
            (row["from_node"], row["to_node"])
            for row in conn.execute(
                "SELECT from_node, to_node FROM edges WHERE type='MENTIONS'"
            )
        }


# NOTE: promotion candidates with tied scores are ordered by set-iteration
# order (hash-randomized across runs), so every curate call here uses
# max_new_nodes=20 — enough for ALL above-threshold candidates. Assertions
# compare promotion *sets*, which are stable across runs.
_ALL = {"max_new_nodes": 20}


# ── (1) regression guard: default curate() still auto-promotes ───────────────

def test_default_curate_still_writes_topic_nodes(tmp_path, monkeypatch):
    monkeypatch.delenv("LATTICEAI_GRAPH_PROMOTION_REVIEW", raising=False)
    store = _store(tmp_path)
    _seed_promotable(store)
    result = store.curate(**_ALL)

    assert result["status"] == "ok"
    assert result["promoted"]
    topics = _topic_ids(store)
    assert "topic:쿠버네티스" in topics
    assert {item["node_id"] for item in result["promoted"]} == topics
    # No review queue was created on the default path.
    assert store.pending_promotions() == []


# ── (2) review mode parks promotions without writing ─────────────────────────

def test_review_mode_parks_promotions_without_writing(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    result = store.curate(review_mode=True, **_ALL)

    assert result["status"] == "pending_review"
    assert result["pending"]
    assert result["pending_total"] == len(result["pending"])
    assert result["documents_scanned"] == 8
    assert result["candidates_total"] >= len(result["pending"])
    assert _topic_ids(store) == set()
    assert _mention_edges(store) == set()

    pending = store.pending_promotions()
    assert {item["id"] for item in pending} == {
        item["id"] for item in result["pending"]
    }
    entry = pending[0]
    assert set(entry) >= {"id", "label", "importance", "sources", "proposed_at"}
    assert entry["id"].startswith("topic:")
    assert entry["sources"] and len(entry["sources"]) <= 10

    # Re-running review mode merges by id instead of duplicating the queue.
    again = store.curate(review_mode=True, **_ALL)
    assert again["pending_total"] == result["pending_total"]
    assert len(store.pending_promotions()) == result["pending_total"]


# ── (3) apply == the exact nodes/edges a direct curate would write ───────────

def test_apply_pending_matches_direct_curate_exactly(tmp_path):
    direct = _store(tmp_path, "direct")
    reviewed = _store(tmp_path, "reviewed")
    _seed_promotable(direct)
    _seed_promotable(reviewed)

    direct.curate(**_ALL)
    reviewed.curate(review_mode=True, **_ALL)
    outcome = reviewed.apply_pending_promotions()

    assert outcome["status"] == "ok"
    assert outcome["remaining"] == 0
    assert {item["node_id"] for item in outcome["applied"]} == _topic_ids(direct)
    assert _topic_ids(reviewed) == _topic_ids(direct)
    assert _mention_edges(reviewed) == _mention_edges(direct)
    assert reviewed.pending_promotions() == []


def test_apply_subset_by_ids_keeps_the_rest_pending(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    store.curate(review_mode=True, **_ALL)
    before = len(store.pending_promotions())

    outcome = store.apply_pending_promotions(ids=["topic:쿠버네티스"])
    assert [item["node_id"] for item in outcome["applied"]] == ["topic:쿠버네티스"]
    assert outcome["remaining"] == before - 1
    assert _topic_ids(store) == {"topic:쿠버네티스"}
    assert len(store.pending_promotions()) == before - 1


# ── (4) reject drops entries without writing ─────────────────────────────────

def test_reject_clears_pending_without_writing(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    store.curate(review_mode=True, **_ALL)
    assert store.pending_promotions()

    outcome = store.reject_pending_promotions()
    assert outcome["status"] == "ok"
    assert outcome["remaining"] == 0
    assert outcome["rejected"]
    assert store.pending_promotions() == []
    assert _topic_ids(store) == set()

    # Rejecting an already-empty queue is a harmless no-op.
    assert store.reject_pending_promotions()["rejected"] == []


def test_reject_subset_by_ids(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    store.curate(review_mode=True, **_ALL)
    before = len(store.pending_promotions())

    outcome = store.reject_pending_promotions(ids=["topic:쿠버네티스"])
    assert outcome["rejected"] == ["topic:쿠버네티스"]
    assert outcome["remaining"] == before - 1
    assert "topic:쿠버네티스" not in {
        item["id"] for item in store.pending_promotions()
    }


# ── (5) env flag flips the default; explicit argument always wins ────────────

def test_env_flag_flips_default_to_review(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_GRAPH_PROMOTION_REVIEW", "1")
    store = _store(tmp_path)
    _seed_promotable(store)

    result = store.curate(**_ALL)
    assert result["status"] == "pending_review"
    assert _topic_ids(store) == set()

    # Explicit review_mode=False overrides the env opt-in.
    forced = store.curate(review_mode=False, **_ALL)
    assert forced["status"] == "ok"
    assert _topic_ids(store)


def test_env_flag_off_values_keep_auto_promote(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_GRAPH_PROMOTION_REVIEW", "0")
    store = _store(tmp_path)
    _seed_promotable(store)
    assert store.curate(**_ALL)["status"] == "ok"


# ── (6) enterprise capability seam ───────────────────────────────────────────

def test_graph_promotion_review_capability_registered():
    assert (
        EnterpriseCapability.GRAPH_PROMOTION_REVIEW.value
        == "graph_promotion_review"
    )
    # Community default: declared but disabled — the registry resolves it.
    assert is_capability_enabled(EnterpriseCapability.GRAPH_PROMOTION_REVIEW) is False
    described = capability_registry.describe()
    assert "graph_promotion_review" in described["capabilities"]
    assert CapabilityRegistry().is_capability_enabled(
        EnterpriseCapability.GRAPH_PROMOTION_REVIEW
    ) is False


# ── noise-curate stamp (Wave 2.5 store side) ─────────────────────────────────

def test_noise_curate_apply_stamps_last_run(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    assert store.last_noise_curate_at() is None

    store.curate_noise(dry_run=True)
    assert store.last_noise_curate_at() is None  # dry-run never stamps

    store.curate_noise(dry_run=False)
    stamp = store.last_noise_curate_at()
    assert stamp  # applied run stamps even when nothing was removed


# ── API routes ───────────────────────────────────────────────────────────────

def _client(store: KnowledgeGraphStore, tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_knowledge_graph_router(
        get_graph=lambda: store,
        require_graph=lambda: None,
        require_user=lambda request: "admin@example.com",
        static_dir=tmp_path,
    ))
    return TestClient(app)


def test_promotions_api_round_trip(tmp_path):
    store = _store(tmp_path)
    _seed_promotable(store)
    store.curate(review_mode=True, **_ALL)
    client = _client(store, tmp_path)

    listed = client.get("/knowledge-graph/promotions")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == len(body["pending"]) > 0
    first_id = body["pending"][0]["id"]

    applied = client.post(
        "/knowledge-graph/promotions/apply", json={"ids": [first_id]}
    )
    assert applied.status_code == 200, applied.text
    assert [item["node_id"] for item in applied.json()["applied"]] == [first_id]
    assert first_id in _topic_ids(store)

    rejected = client.post("/knowledge-graph/promotions/reject", json={})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["remaining"] == 0
    assert client.get("/knowledge-graph/promotions").json()["total"] == 0
