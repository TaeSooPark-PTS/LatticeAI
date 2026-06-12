"""Opt-in Postgres/pgvector storage engine for Brain Core scale mode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .base import StorageCapabilities, StorageEngine, StorageUnavailable


def _quote_ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


@dataclass(frozen=True)
class PostgresConfig:
    dsn: str
    schema: str = "lattice_brain"


class PostgresEngine(StorageEngine):
    name = "postgres"

    def __init__(self, dsn: str, *, schema: str = "lattice_brain") -> None:
        self.config = PostgresConfig(dsn=dsn, schema=schema)

    def _psycopg(self):
        try:
            import psycopg  # type: ignore
        except Exception as exc:
            raise StorageUnavailable(
                "Postgres storage requires optional dependency 'psycopg'. "
                "Install the postgres extra before selecting LATTICEAI_STORAGE_ENGINE=postgres."
            ) from exc
        return psycopg

    def connect(self) -> Any:
        if not self.config.dsn:
            raise StorageUnavailable(
                "Postgres storage requires LATTICEAI_POSTGRES_DSN; no SQLite fallback is attempted."
            )
        psycopg = self._psycopg()
        return psycopg.connect(self.config.dsn)

    def initialize(self) -> Dict[str, Any]:
        schema = _quote_ident(self.config.schema)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema}.storage_meta (
                      key text PRIMARY KEY,
                      value text NOT NULL,
                      updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema}.brain_vectors (
                      item_id text PRIMARY KEY,
                      item_type text NOT NULL,
                      source_node text NOT NULL,
                      text_hash text NOT NULL,
                      embedding vector,
                      embedding_dim integer NOT NULL,
                      embedding_model text NOT NULL,
                      metadata_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                      indexed_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    f"""
                    INSERT INTO {schema}.storage_meta(key, value)
                    VALUES ('engine', 'postgres')
                    ON CONFLICT (key) DO UPDATE
                      SET value = EXCLUDED.value, updated_at = now()
                    """
                )
        return {"engine": self.name, "schema": self.config.schema}

    def capabilities(self) -> StorageCapabilities:
        try:
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
                    pgvector = cur.fetchone() is not None
        except Exception as exc:
            return StorageCapabilities(
                engine=self.name,
                available=False,
                reason=str(exc),
                vector_backend="pgvector",
                metadata={"schema": self.config.schema},
            )
        return StorageCapabilities(
            engine=self.name,
            available=True,
            reason=None if pgvector else "pgvector extension is not installed",
            vector_backend="pgvector",
            vector_available=pgvector,
            backup_restore=False,
            migrations=True,
            encrypted_archives=False,
            metadata={"schema": self.config.schema},
        )

    def backup(self, destination: Path) -> Dict[str, Any]:
        raise StorageUnavailable(
            "Postgres logical backup is not implemented inside the app; use pg_dump for this engine."
        )

    def restore(self, source: Path) -> Dict[str, Any]:
        raise StorageUnavailable(
            "Postgres restore is not implemented inside the app; use pg_restore/psql for this engine."
        )


__all__ = ["PostgresConfig", "PostgresEngine", "_quote_ident"]
