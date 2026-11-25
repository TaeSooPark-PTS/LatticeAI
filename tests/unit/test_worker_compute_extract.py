"""Tests for the `/worker/extract` pure-compute seam.

Split from ``tests/unit/test_worker_compute.py`` to keep each test module
cohesive and well below the 1,000-line ceiling.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agent_worker_seam import SEAM_ENV_VAR
from latticeai.api.worker_compute import (
    EXTRACT_KINDS,
    EXTRACT_LIMITS,
    build_extract_reply,
    create_worker_compute_router,
)
from latticeai.core.embedding_providers import resolve_embedder
from latticeai.core.messages import MESSAGES

USER = "worker@local"


@pytest.fixture(autouse=True)
def _open_seam(monkeypatch):
    monkeypatch.setenv(SEAM_ENV_VAR, "1")


@pytest.fixture
def _rules_only(monkeypatch):
    """Pin extraction to the rule fallback so two calls in one test agree."""
    monkeypatch.setattr(
        "lattice_brain.graph._kg_common.extraction.ENABLE_LLM_EXTRACTION",
        False,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(
        create_worker_compute_router(
            embedder=resolve_embedder(""),
            transcriber=None,
            require_user=lambda request: USER,
            enforce_rate_limit=lambda user, bucket: None,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def _python_extract(text: str, kind: str) -> Dict[str, Any]:
    """The literal composition ingest_* makes after calling the extractors."""
    from lattice_brain.graph._kg_common import (
        _classify_node_type,
        _extract_concepts,
        _extract_triples,
        _semantic_items,
    )

    limit = EXTRACT_LIMITS[kind]
    raw = _extract_concepts(text, limit=limit)
    return {
        "concepts": [
            {"text": concept, "node_type": _classify_node_type(concept, text)}
            for concept in raw
        ],
        "triples": [
            {
                "subject": triple["subject"],
                "object": triple["object"],
                "relation": triple["relation"],
                "weight": float(triple.get("weight") or 1.0),
                "context": str(triple.get("context") or ""),
                "evidence": str(triple.get("evidence") or ""),
                "confidence": triple.get("confidence"),
            }
            for triple in _extract_triples(text, raw)
        ],
        "semantic": [
            {
                "item_type": item["type"],
                "title": item["title"],
                "summary": item["summary"],
                "raw": dict(item),
            }
            for item in _semantic_items(text)
        ],
    }


def test_extract_returns_the_structures_ingestion_consumes(_rules_only):
    """Parity with the call sites ingest_message / ingest_document use."""
    client = _client()
    text = (
        "Lattice AI uses Graph RAG.\n"
        "We decided to keep the write path native.\n"
        "TODO: implement the extract seam."
    )
    body = client.post("/worker/extract", json={"text": text, "kind": "message"}).json()
    expected = _python_extract(text, "message")
    assert body == expected
    assert set(body) == {"concepts", "triples", "semantic"}
    assert any(item["raw"]["type"] == item["item_type"] for item in body["semantic"])


def test_extract_document_kind_uses_the_ingest_document_limit(monkeypatch):
    seen: List[int] = []

    def _fake_concepts(text: str, limit: int = 12) -> List[str]:
        seen.append(limit)
        return ["Lattice AI"]

    import lattice_brain.graph._kg_common as kg

    monkeypatch.setattr(kg, "_extract_concepts", _fake_concepts)
    client = _client()
    body = client.post(
        "/worker/extract", json={"text": "Lattice AI", "kind": "document"}
    ).json()
    assert seen == [EXTRACT_LIMITS["document"]]
    assert body["concepts"] == [{"text": "Lattice AI", "node_type": "Concept"}]


def test_extract_default_kind_is_the_chat_turn_door(_rules_only):
    client = _client()
    text = "FastAPI and Pydantic share a request body."
    omitted = client.post("/worker/extract", json={"text": text}).json()
    named = client.post(
        "/worker/extract", json={"text": text, "kind": "message"}
    ).json()
    assert omitted == named == _python_extract(text, "message")


def test_extract_empty_text_is_empty_structures_not_an_error():
    client = _client()
    body = client.post("/worker/extract", json={"text": ""}).json()
    assert body == {"concepts": [], "triples": [], "semantic": []}
    assert build_extract_reply("", "message") == body


def test_extract_rejects_an_unknown_kind():
    client = _client()
    response = client.post("/worker/extract", json={"text": "x", "kind": "query"})
    assert response.status_code == 422
    assert response.json()["detail"] == MESSAGES["worker_compute.extract_kind_invalid"][
        "ko"
    ].format(kind="query", allowed=", ".join(EXTRACT_KINDS))


def test_extract_kind_is_case_and_space_insensitive(_rules_only):
    client = _client()
    text = "Claude Sonnet writes the note."
    body = client.post(
        "/worker/extract", json={"text": text, "kind": " MESSAGE "}
    ).json()
    assert body == _python_extract(text, "message")


def test_extract_maps_a_thin_triple_the_way_the_write_path_does(monkeypatch):
    """``weight or 1.0`` and empty context/evidence are the ingest mapping."""
    import lattice_brain.graph._kg_common as kg

    monkeypatch.setattr(kg, "_extract_concepts", lambda text, limit=12: ["A", "B"])
    monkeypatch.setattr(
        kg,
        "_extract_triples",
        lambda text, concepts, limit=20: [
            {"subject": "A", "object": "B", "relation": "관련됨"}
        ],
    )
    monkeypatch.setattr(kg, "_semantic_items", lambda text: [])
    monkeypatch.setattr(kg, "_classify_node_type", lambda concept, text: "Concept")
    reply = build_extract_reply("A and B", "message")
    assert reply["triples"] == [
        {
            "subject": "A",
            "object": "B",
            "relation": "관련됨",
            "weight": 1.0,
            "context": "",
            "evidence": "",
            "confidence": None,
        }
    ]


def test_extract_kinds_are_the_two_ingest_doors():
    assert EXTRACT_KINDS == ("message", "document")
    assert EXTRACT_LIMITS == {"message": 12, "document": 15}
