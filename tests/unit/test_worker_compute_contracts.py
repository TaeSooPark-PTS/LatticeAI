"""EmbeddingIndexContract and sysinfo tests split from test_worker_compute.py."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agent_worker_seam import SEAM_ENV_VAR
from latticeai.api.worker_compute import (
    create_worker_compute_router,
    pointer_tools_available,
    sysinfo_payload_extras,
)
from latticeai.core.embedding_providers import (
    HashEmbeddingProvider,
    build_embedding_provider,
)

USER = "worker@local"


@pytest.fixture(autouse=True)
def _seam_open(monkeypatch):
    monkeypatch.setenv(SEAM_ENV_VAR, "1")


def _client(embedder: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_worker_compute_router(
            embedder=embedder,
            transcriber=None,
            require_user=lambda _req: USER,
            enforce_rate_limit=lambda _u, _b: None,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_embed_endpoint_emits_full_contract_and_marks_hash_fallback_explicit():
    gemma = build_embedding_provider(
        "huggingface", model="google/embeddinggemma-2", dim=8
    )
    gemma.embed_batch_for = lambda texts, kind="passage": [[0.25] * 8 for _ in texts]
    resolved_gemma = type("Resolved", (), {"provider": gemma, "active": "huggingface"})()
    semantic_body = _client(embedder=resolved_gemma).post(
        "/worker/embed", json={"texts": ["lattice contract"], "kind": "query"}
    ).json()

    assert semantic_body["semantic"] is True
    assert semantic_body["grade"] == "production"
    assert semantic_body["passage_mode"] == "embeddinggemma_passage"
    assert semantic_body["query_mode"] == "embeddinggemma_query"
    assert semantic_body["contract"]["semantic"] is True
    assert semantic_body["contract"]["index_version"] == 1

    hash_prov = HashEmbeddingProvider(dim=16)
    resolved_hash = type("Resolved", (), {"provider": hash_prov, "active": "hash"})()
    hash_body = _client(embedder=resolved_hash).post(
        "/worker/embed", json={"texts": ["offline fallback"]}
    ).json()

    assert hash_body["semantic"] is False
    assert hash_body["grade"] == "fallback"
    assert hash_body["contract"]["provider"] == "hash"
    assert hash_body["contract"]["semantic"] is False


def test_sysinfo_extras_report_pointer_tools_and_python_version():
    extras = sysinfo_payload_extras()
    assert extras["capabilities"]["pointer_tools"] is pointer_tools_available()
    assert extras["python_version"].count(".") == 2
    parts = extras["python_version"].split(".")
    assert all(part.isdigit() for part in parts)


def test_pointer_tools_follows_whether_pyautogui_is_findable(monkeypatch):
    import importlib.util

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if name == "pyautogui" else None
    )
    assert pointer_tools_available() is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert pointer_tools_available() is False
