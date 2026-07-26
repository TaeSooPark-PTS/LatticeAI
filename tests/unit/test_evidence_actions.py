"""Evidence → action bridge (v9.9.6).

Review 2026-07-27 P0 #2: "근거 → 행동 원클릭 — supported 출처에서 바로
요약/파일/액션 만들기". House rules verified here: composition is
deterministic and model-free, unresolvable citations are reported rather than
dropped, an evidence-free request offers no actions at all, and every composed
prompt carries the "use only this evidence" guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.evidence_actions import create_evidence_actions_router
from latticeai.services.evidence_actions import EvidenceActionService, slugify

NODES = {
    "node-a": {
        "id": "node-a",
        "type": "document",
        "title": "2026 예산 계획",
        "summary": "1분기 예산은 1,200만원이며 마케팅에 40%를 배정한다.",
        "metadata": {"relative_path": "docs/budget.md"},
    },
    "node-b": {
        "id": "node-b",
        "type": "note",
        "title": "회의 메모",
        "summary": "예산 승인은 2월 첫째 주 이사회에서 결정.",
        "metadata": {},
    },
}


def _reader(node_id, allowed_workspaces=None):
    if node_id not in NODES:
        raise ValueError(f"graph node not found: {node_id}")
    return NODES[node_id]


def _service():
    return EvidenceActionService(node_reader=_reader)


def test_actions_are_composed_from_resolved_evidence():
    result = _service().actions_for(
        question="예산 어떻게 되지?", source_ids=["node-a", "node-b"]
    )
    assert [s["id"] for s in result["sources"]] == ["node-a", "node-b"]
    assert result["missing"] == []
    ids = [action["id"] for action in result["actions"]]
    assert ids == ["summary", "checklist", "document", "page"]
    prompt = result["actions"][0]["prompt"]
    # Evidence text, the guard, and the original question all reach the model.
    assert "2026 예산 계획" in prompt and "docs/budget.md" in prompt
    assert "지어내지 말고" in prompt
    assert "예산 어떻게 되지?" in prompt


def test_file_actions_suggest_a_deterministic_path():
    result = _service().actions_for(question="Q3 budget review", source_ids=["node-a"])
    by_id = {action["id"]: action for action in result["actions"]}
    assert by_id["document"]["suggested_path"] == "q3-budget-review.md"
    assert by_id["page"]["suggested_path"] == "q3-budget-review.html"
    assert by_id["document"]["suggested_path"] in by_id["document"]["prompt"]
    assert by_id["summary"].get("suggested_path") is None


def test_korean_question_falls_back_to_a_safe_stem():
    result = _service().actions_for(question="예산 정리해줘", source_ids=["node-a"])
    by_id = {action["id"]: action for action in result["actions"]}
    assert by_id["document"]["suggested_path"] == "evidence-note.md"
    assert slugify("") == "evidence-note"


def test_unresolvable_citations_are_reported_not_dropped():
    result = _service().actions_for(question="q", source_ids=["node-a", "ghost"])
    assert [s["id"] for s in result["sources"]] == ["node-a"]
    assert result["missing"] == ["ghost"]
    assert result["actions"]


def test_no_evidence_offers_no_actions_and_says_why():
    result = _service().actions_for(question="q", source_ids=["ghost"])
    assert result["sources"] == []
    assert result["actions"] == []
    assert result["reason"]


def test_service_without_a_graph_degrades_honestly():
    result = EvidenceActionService(node_reader=None).actions_for(
        question="q", source_ids=["node-a"]
    )
    assert result["actions"] == []
    assert result["missing"] == ["node-a"]


def test_english_language_switches_every_composed_string():
    result = _service().actions_for(
        question="budget", source_ids=["node-a"], language="en"
    )
    prompt = result["actions"][0]["prompt"]
    assert "[EVIDENCE]" in prompt
    assert "Use only the evidence quoted above." in prompt
    assert "Original question: budget" in prompt


def test_duplicate_ids_collapse_and_composition_is_deterministic():
    service = _service()
    once = service.actions_for(question="q", source_ids=["node-a", "node-a"])
    twice = service.actions_for(question="q", source_ids=["node-a"])
    assert once == twice


def test_long_summaries_are_excerpted_and_marked_truncated():
    long_node = {"id": "long", "title": "T", "summary": "가" * 900, "metadata": {}}
    service = EvidenceActionService(node_reader=lambda nid, **kw: long_node)
    result = service.actions_for(question="q", source_ids=["long"])
    source = result["sources"][0]
    assert len(source["excerpt"]) == 600
    assert source["truncated"] is True
    assert "…" in result["actions"][0]["prompt"]


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(
        create_evidence_actions_router(
            service=_service(),
            require_user=lambda request: "local@lattice",
            allowed_workspaces_for=lambda user: None,
        )
    )
    return TestClient(app)


def test_router_returns_actions_for_cited_sources(client):
    response = client.post(
        "/api/evidence/actions",
        json={"question": "예산", "source_ids": ["node-a"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"][0]["title"] == "2026 예산 계획"
    assert len(payload["actions"]) == 4


def test_router_requires_no_sources_to_answer(client):
    response = client.post("/api/evidence/actions", json={})
    assert response.status_code == 200
    assert response.json()["actions"] == []
