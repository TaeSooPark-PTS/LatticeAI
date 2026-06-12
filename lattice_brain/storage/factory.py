"""StorageEngine construction from environment/config values."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .base import StorageEngine, StorageUnavailable
from .postgres import PostgresEngine
from .sqlite import SQLiteEngine


def storage_from_env(env: Mapping[str, str], *, data_dir: Path) -> StorageEngine:
    engine = (env.get("LATTICEAI_STORAGE_ENGINE") or "sqlite").strip().lower()
    if engine in {"", "sqlite"}:
        return SQLiteEngine(Path(data_dir) / "knowledge_graph.sqlite")
    if engine in {"postgres", "pg", "pgvector"}:
        dsn = env.get("LATTICEAI_POSTGRES_DSN") or ""
        if not dsn:
            raise StorageUnavailable(
                "LATTICEAI_STORAGE_ENGINE=postgres requires LATTICEAI_POSTGRES_DSN; "
                "SQLite fallback is disabled for explicit Postgres selection."
            )
        return PostgresEngine(
            dsn,
            schema=env.get("LATTICEAI_POSTGRES_SCHEMA") or "lattice_brain",
        )
    raise StorageUnavailable(f"Unknown Brain Core storage engine: {engine}")


__all__ = ["storage_from_env"]
