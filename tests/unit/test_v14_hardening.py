"""Regression tests for v14.0.0 Python security & reliability hardening."""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path

import pytest

from lattice_brain import embeddings
from lattice_brain.graph import runtime as graph_runtime
from latticeai.core import security
from latticeai.tools import filesystem


def test_configure_trusted_proxies_warns_on_invalid_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="latticeai.core.security"):
        count = security.configure_trusted_proxies("127.0.0.1,not-a-cidr,10.0.0.0/8")
    assert count == 2
    assert any("not-a-cidr" in rec.getMessage() for rec in caplog.records)
    # Restore empty allowlist so other tests are unaffected.
    security.configure_trusted_proxies("")


def test_embeddings_reject_non_positive_dimension_and_validate_env(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        embeddings.LocalEmbeddingModel(dim=0)
    with pytest.raises(ValueError, match="positive"):
        embeddings._hash_to_index("tok:hello", 0)

    monkeypatch.setenv("LATTICEAI_VECTOR_DIM", "not-an-int")
    with caplog.at_level(logging.WARNING, logger="lattice_brain.embeddings"):
        importlib.reload(embeddings)
    assert embeddings.DEFAULT_EMBEDDING_DIM == 384

    monkeypatch.setenv("LATTICEAI_VECTOR_DIM", "-16")
    importlib.reload(embeddings)
    assert embeddings.DEFAULT_EMBEDDING_DIM == 384

    monkeypatch.delenv("LATTICEAI_VECTOR_DIM", raising=False)
    importlib.reload(embeddings)


def test_embeddings_embed_async_matches_sync() -> None:
    model = embeddings.LocalEmbeddingModel(dim=64)
    sync_vec = model.embed("Lattice AI local knowledge graph 한글 테스트")
    async_vec = asyncio.run(
        model.embed_async("Lattice AI local knowledge graph 한글 테스트")
    )
    assert async_vec == sync_vec


def test_graph_runtime_scoped_di_and_require_guard() -> None:
    prev = graph_runtime.get_llm_router()
    try:
        graph_runtime.set_llm_router(None)
        with pytest.raises(RuntimeError, match="not initialized"):
            graph_runtime.require_llm_router()
        sentinel = object()
        with graph_runtime.llm_router_scope(sentinel) as bound:
            assert bound is sentinel
            assert graph_runtime.get_llm_router() is sentinel
            assert graph_runtime.require_llm_router() is sentinel
        assert graph_runtime.get_llm_router() is None
    finally:
        graph_runtime.set_llm_router(prev)


def test_todo_read_accepts_injectable_root_without_creating_dirs(
    tmp_path: Path,
) -> None:
    isolated_root = tmp_path / "nonexistent_workspace"
    result = filesystem.todo_read(root=isolated_root)
    assert result == {"todos": [], "path": filesystem._TODO_REL_PATH}
    assert not isolated_root.exists(), (
        "todo_read must not create directories as a side effect"
    )
