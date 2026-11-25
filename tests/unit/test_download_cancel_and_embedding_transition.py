"""Unit tests for cooperative model download cancellation and post-download semantic embedding transition."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from latticeai.services.embedding_recommendation import (
    activate_downloaded_embedding_model,
    is_embedding_model_id,
    recommend_embedding_for_hardware,
)
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime.download import (
    check_download_cancelled,
    clear_download_cancel,
    is_download_cancelled,
    request_download_cancel,
)


def test_cooperative_download_cancel_lifecycle() -> None:
    clear_download_cancel()
    assert not is_download_cancelled("google/embeddinggemma-2")
    request_download_cancel("local_mlx:google/embeddinggemma-2")
    assert is_download_cancelled("google/embeddinggemma-2")
    assert is_download_cancelled("local_mlx:google/embeddinggemma-2")
    with pytest.raises(ModelRuntimeError) as exc_info:
        check_download_cancelled("google/embeddinggemma-2")
    assert exc_info.value.status_code == 499
    clear_download_cancel("google/embeddinggemma-2")
    assert not is_download_cancelled("google/embeddinggemma-2")


def test_hardware_recommendation_includes_batch_and_auto_drain() -> None:
    high_ram = recommend_embedding_for_hardware(ram_gb=32.0, apple_silicon=True)
    assert high_ram["recommended_batch_size"] == 64
    assert high_ram["auto_drain_on_ready"] is True
    assert high_ram["transition_from_fallback"] is True

    low_ram = recommend_embedding_for_hardware(ram_gb=6.0, apple_silicon=False)
    assert low_ram["recommended_batch_size"] == 16
    assert low_ram["auto_drain_on_ready"] is True


def test_activate_downloaded_embedding_model_enqueues_fallback_nodes(
    tmp_path: Path,
) -> None:
    assert is_embedding_model_id("mlx-community/embeddinggemma-2-4bit")
    assert not is_embedding_model_id("mlx-community/gemma-4-e4b-it-4bit")

    db_path = tmp_path / "knowledge_graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE vector_embeddings(
            item_id TEXT PRIMARY KEY,
            source_node TEXT,
            embedding_model TEXT
        );
        CREATE TABLE vector_jobs(
            node_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO vector_embeddings VALUES ('doc:1', 'doc:1', 'lattice-local-hash-v1');
        INSERT INTO vector_embeddings VALUES ('doc:2', 'doc:2', 'mlx:mlx-community/embeddinggemma-2-4bit:768');
        INSERT INTO vector_jobs VALUES ('doc:1', 'done', 1, NULL, '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    res = activate_downloaded_embedding_model(
        "local_mlx:mlx-community/embeddinggemma-2-4bit",
        provider="local_mlx",
        dim=768,
        data_dir=tmp_path,
    )
    assert res["activated"] is True
    assert res["enqueued_upgrade_jobs"] == 1

    conn = sqlite3.connect(str(db_path))
    raw_contract = conn.execute(
        "SELECT value FROM graph_meta WHERE key='embedding_index_contract'"
    ).fetchone()[0]
    contract = json.loads(raw_contract)
    assert contract["model_id"] == "mlx:mlx-community/embeddinggemma-2-4bit:768"
    assert contract["is_fallback"] is False
    job_status, job_detail = conn.execute(
        "SELECT status, detail FROM vector_jobs WHERE node_id='doc:1'"
    ).fetchone()
    conn.close()
    assert job_status == "pending"
    assert job_detail == "semantic_upgrade"
