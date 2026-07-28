from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from lattice_brain import BrainCore, KnowledgeGraphStore
from lattice_brain.storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteToPostgresMigrator,
    StorageUnavailable,
    storage_from_env,
)
from lattice_brain.storage.postgres import _quote_ident

pytestmark = pytest.mark.skipif(
    os.getenv("LTCAI_LIVE_POSTGRES_DOCKER_CONSENT") != "1",
    reason="live Docker/Postgres validation requires explicit consent",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _source_counts(sqlite_path: Path) -> dict[str, int]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row["name"]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


def _postgres_count(conn: Any, schema: str, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {_quote_ident(schema)}.{_quote_ident(table)}")
        return int(cur.fetchone()[0])


def _wait_for_postgres(engine: PostgresEngine, *, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    assert cur.fetchone()[0] == 1
            return
        except Exception as exc:  # pragma: no cover - exercised in live environment
            last = exc
            time.sleep(1)
    raise AssertionError(f"Postgres did not become ready: {last}")


def _seed_v42_sqlite_brain(data_dir: Path) -> Path:
    core = BrainCore.from_paths(data_dir)
    core.conversations.append(
        {
            "role": "user",
            "content": "live postgres migration validation",
            "timestamp": "2026-06-12T00:00:00Z",
            "conversation_id": "live-v42",
        }
    )

    doc = data_dir / "migration-source.txt"
    doc.write_text("pgvector sqlite postgres migration integrity", encoding="utf-8")
    store = KnowledgeGraphStore(data_dir / "knowledge_graph.sqlite", data_dir / "knowledge_graph_blobs")
    store.ingest_document(
        doc,
        original_filename="migration-source.txt",
        extracted={"content": doc.read_text(encoding="utf-8")},
    )
    store.rebuild_vector_index(full=True)

    with sqlite3.connect(data_dir / "knowledge_graph.sqlite") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS no_id_events(kind TEXT, payload TEXT)")
        conn.execute(
            "INSERT INTO no_id_events(kind, payload) VALUES (?, ?)",
            ("validation", "rowid conflict key must be preserved"),
        )
        conn.execute("CREATE TABLE IF NOT EXISTS binary_payloads(id TEXT PRIMARY KEY, payload BLOB)")
        conn.execute("INSERT OR REPLACE INTO binary_payloads(id, payload) VALUES (?, ?)", ("blob-1", b"abc"))

    return data_dir / "knowledge_graph.sqlite"


def test_live_sqlite_to_postgres_migration_integrity_pgvector_and_fail_closed(tmp_path: Path):
    port = int(os.getenv("LTCAI_LIVE_POSTGRES_PORT") or _free_port())
    docker_dir = tmp_path / "postgres"
    wizard = DockerPostgresWizard(docker_dir, port=port)
    start = wizard.start(consent=True)
    assert start["status"] == "started", start

    plan = wizard.write_compose()
    dsn = f"postgresql://lattice:lattice-local-only@127.0.0.1:{port}/lattice_brain"
    engine = PostgresEngine(dsn, schema="lattice_brain_live")
    try:
        _wait_for_postgres(engine)
        sqlite_path = _seed_v42_sqlite_brain(tmp_path / "brain")
        before_counts = _source_counts(sqlite_path)

        migrator = SQLiteToPostgresMigrator(sqlite_path, engine)
        first = migrator.migrate(dry_run=False)
        second = migrator.migrate(dry_run=False)

        assert first["status"] == "migrated"
        assert first["total_copied_rows"] == first["total_rows"] == sum(before_counts.values())
        assert second["total_copied_rows"] == first["total_copied_rows"]
        assert _source_counts(sqlite_path) == before_counts

        caps = engine.capabilities()
        assert caps.available is True
        assert caps.vector_backend == "pgvector"
        assert caps.vector_available is True

        schema = _quote_ident(engine.config.schema)
        with engine.connect() as conn:
            for table, expected in before_counts.items():
                assert _postgres_count(conn, engine.config.schema, table) == expected
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.brain_vectors
                      (item_id, item_type, source_node, text_hash, embedding,
                       embedding_dim, embedding_model, metadata_json)
                    VALUES
                      ('near', 'test', 'node-a', 'h1', CAST('[1,0,0]' AS vector), 3, 'test', '{{}}'::jsonb),
                      ('far', 'test', 'node-b', 'h2', CAST('[0,1,0]' AS vector), 3, 'test', '{{}}'::jsonb)
                    ON CONFLICT (item_id) DO UPDATE
                      SET embedding = EXCLUDED.embedding, indexed_at = now()
                    """
                )
                cur.execute(
                    f"""
                    SELECT item_id FROM {schema}.brain_vectors
                    ORDER BY embedding <-> CAST('[0.9,0.1,0]' AS vector)
                    LIMIT 1
                    """
                )
                assert cur.fetchone()[0] == "near"

        with pytest.raises(StorageUnavailable):
            storage_from_env({"LATTICEAI_STORAGE_ENGINE": "postgres"}, data_dir=tmp_path)
        with pytest.raises(StorageUnavailable):
            BrainCore.from_paths(tmp_path / "postgres-runtime", storage_engine=engine)
        with pytest.raises(StorageUnavailable):
            engine.backup(tmp_path / "postgres.backup")
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                plan.project_name,
                "-f",
                str(plan.compose_path),
                "down",
                "-v",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
