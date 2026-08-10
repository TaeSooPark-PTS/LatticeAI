"""Self-Model ownership surface on the memory router (Track 4).

The user must be able to see, correct and delete what the Brain believes about
them — and the extraction path must never reach the graph without an approval.
Both are asserted here against real collaborators: a real ``MemoryService``
over a real ``WorkspaceOSStore``, a real ``KnowledgeGraphStore``, and the real
``ReviewQueueService`` the service derives from the store.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain import self_model as sm
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.memory import create_memory_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.memory_service import MemoryService
from latticeai.services.self_model_service import SelfModelService

USER = "owner@example.com"


def _client(tmp_path, *, graph: bool = True, self_model_service: Any = None):
    store = WorkspaceOSStore(tmp_path / "data")
    kg = (
        KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
        if graph
        else None
    )
    service = MemoryService(
        store=store,
        data_dir=tmp_path / "data",
        knowledge_graph=kg,
        enable_graph=graph,
    )
    audits: List[Tuple[str, Dict[str, Any]]] = []
    app = FastAPI()
    app.include_router(
        create_memory_router(
            service=service,
            require_user=lambda request: USER,
            get_current_user=lambda request: USER,
            gate_read=lambda request: None,
            gate_write=lambda request: None,
            append_audit_event=lambda event, **payload: audits.append((event, payload)),
            self_model=self_model_service,
        )
    )
    return TestClient(app), kg, store, audits


def test_the_user_sees_edits_and_forgets_their_own_profile(tmp_path):
    client, kg, _store, audits = _client(tmp_path)

    empty = client.get("/api/memory/self-model").json()
    assert empty["available"] is True
    assert empty["facts"] == []
    assert empty["summary"] == ""
    assert empty["kind_options"] == list(sm.KIND_ORDER)

    created = client.post(
        "/api/memory/self-model", json={"kind": "preference", "text": "로컬 모델"}
    ).json()
    assert created["kind"] == "preference"
    assert created["origin"] == "user"

    listed = client.get("/api/memory/self-model").json()
    assert [fact["text"] for fact in listed["facts"]] == ["로컬 모델"]
    assert "로컬 모델" in listed["summary"]

    removed = client.delete(f"/api/memory/self-model/{created['id']}")
    assert removed.status_code == 200
    assert client.get("/api/memory/self-model").json()["facts"] == []
    assert [event for event, _ in audits] == [
        "self_model_upsert",
        "self_model_delete",
    ]
    assert sm.list_self_model(kg)["count"] == 0


def test_a_bad_edit_answers_in_the_users_language(tmp_path):
    client, _kg, _store, _audits = _client(tmp_path)

    bad_kind = client.post(
        "/api/memory/self-model", json={"kind": "mood", "text": "좋음"}
    )
    empty_text = client.post(
        "/api/memory/self-model", json={"kind": "habit", "text": "  "}
    )
    foreign = client.delete("/api/memory/self-model/conversation:42")
    missing = client.delete("/api/memory/self-model/self:habit:deadbeef")

    assert bad_kind.status_code == 400
    assert "선호" in bad_kind.json()["detail"]
    assert empty_text.status_code == 400
    assert foreign.status_code == 400
    assert missing.status_code == 404
    english = client.post(
        "/api/memory/self-model",
        json={"kind": "mood", "text": "x"},
        headers={"x-lattice-language": "en"},
    )
    assert "preference" in english.json()["detail"]


def test_extraction_proposes_and_only_approval_writes(tmp_path):
    client, kg, store, audits = _client(tmp_path)

    proposed = client.post(
        "/api/memory/self-model/propose",
        json={"text": "저는 로컬 모델을 선호합니다.", "source": "chat:9"},
    ).json()

    assert proposed["proposed_count"] == 1
    assert sm.list_self_model(kg)["count"] == 0  # nothing written yet
    item_id = proposed["proposed"][0]["id"]
    assert store.get_review_item(item_id)["kind"] == sm.SELF_MODEL_KIND

    applied = client.post(
        "/api/memory/self-model/apply", json={"item_id": item_id}
    ).json()

    assert applied["status"] == "approved"
    assert [fact["text"] for fact in sm.list_self_model(kg)["facts"]] == ["로컬 모델"]
    assert store.get_review_item(item_id)["status"] == "approved"
    assert [event for event, _ in audits] == [
        "self_model_proposed",
        "self_model_applied",
    ]


def test_applying_the_wrong_item_is_refused(tmp_path):
    client, _kg, store, _audits = _client(tmp_path)
    other = store.create_review_item(
        title="not mine", source="kg_change_digest", kind="contradiction", payload={}
    )

    wrong = client.post("/api/memory/self-model/apply", json={"item_id": other["id"]})
    missing = client.post("/api/memory/self-model/apply", json={"item_id": "nope"})

    assert wrong.status_code == 400
    assert missing.status_code == 404


def test_a_brain_without_a_graph_says_so_instead_of_pretending(tmp_path):
    client, _kg, _store, _audits = _client(tmp_path, graph=False)

    profile = client.get("/api/memory/self-model").json()
    write = client.post(
        "/api/memory/self-model", json={"kind": "habit", "text": "회고"}
    )
    proposed = client.post(
        "/api/memory/self-model/propose", json={"text": "저는 커피를 좋아합니다."}
    ).json()

    assert profile["available"] is False
    assert profile["facts"] == []
    assert write.status_code == 400
    assert proposed["available"] is False


def test_without_a_review_queue_nothing_is_proposed_or_applied(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    service = SelfModelService(knowledge_graph=kg)  # no memory service → no queue
    client, _kg, _store, _audits = _client(tmp_path, self_model_service=service)

    proposed = client.post(
        "/api/memory/self-model/propose", json={"text": "저는 커피를 좋아합니다."}
    ).json()
    applied = client.post("/api/memory/self-model/apply", json={"item_id": "any"})

    assert proposed["available"] is False
    assert applied.status_code == 400


# ── service seams ────────────────────────────────────────────────────────────


def test_the_service_finds_its_collaborators_or_reports_their_absence(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    store = WorkspaceOSStore(tmp_path / "data")
    memory = MemoryService(store=store, data_dir=tmp_path / "data", knowledge_graph=kg)

    derived = SelfModelService(memory_service=memory)
    assert derived._kg() is kg
    assert derived._review_queue() is derived._review_queue()  # built once, reused

    disabled = SelfModelService(memory_service=memory, enable_graph=False)
    assert disabled._kg() is None
    assert disabled.summary() == ""

    injected = SelfModelService(knowledge_graph=kg, review_queue=object())
    assert injected._review_queue() is injected._queue
    assert SelfModelService()._review_queue() is None


def test_the_service_summary_is_the_injected_text(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    service = SelfModelService(knowledge_graph=kg)
    sm.upsert_self_model_fact(kg, kind="preference", text="로컬 모델")

    assert "로컬 모델" in service.summary()


def test_a_graphless_service_refuses_writes_with_a_reason():
    service = SelfModelService(enable_graph=False)

    with pytest.raises(sm.SelfModelError) as upsert:
        service.upsert(kind="habit", text="회고")
    with pytest.raises(sm.SelfModelError) as delete:
        service.delete("self:habit:1")

    assert upsert.value.code == "graph_unavailable"
    assert delete.value.code == "graph_unavailable"
