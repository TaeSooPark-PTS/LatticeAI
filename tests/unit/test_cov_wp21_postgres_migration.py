"""wp21 coverage — the opt-in Postgres engine and the SQLite→Postgres migrator.

Neither a Postgres server nor psycopg's network path is available on the
coverage leg, so ``psycopg`` is replaced with a recording fake injected into
``sys.modules``: every statement the engine and the migrator emit is captured
verbatim, which is a stronger assertion than "it did not raise" — the DDL, the
``ON CONFLICT`` targets and the parameter tuples are all checked.

The fake connections also record ``close()``, so these tests fail if the
storage session ever goes back to committing without closing (the 10.2.0
descriptor-leak class).
"""

from __future__ import annotations

import sqlite3
import sys
import types
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.storage import migration as migration_module
from lattice_brain.storage.base import StorageUnavailable
from lattice_brain.storage.migration import (
    SQLiteToPostgresMigrator,
    _adapt_value,
    _pg_type,
)
from lattice_brain.storage.postgres import PostgresEngine, _quote_ident

DSN = "postgresql://lattice:secret@127.0.0.1:5432/lattice_brain"


def _squash(sql: object) -> str:
    return " ".join(str(sql).split())


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection
        self.closed = False

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.closed = True
        return False

    def execute(self, sql, params=None):
        self._connection.statements.append(_squash(sql))
        return self

    def executemany(self, sql, rows):
        statement = _squash(sql)
        self._connection.statements.append(statement)
        self._connection.batches.append((statement, [tuple(row) for row in rows]))
        return self

    def fetchone(self):
        return self._connection.fetchone_result


class _FakeConnection:
    def __init__(self, dsn: str, fetchone_result=None) -> None:
        self.dsn = dsn
        self.fetchone_result = fetchone_result
        self.statements: list[str] = []
        self.batches: list[tuple[str, list[tuple]]] = []
        self.cursors: list[_FakeCursor] = []
        self.closed = False
        self.exits = 0

    def cursor(self) -> _FakeCursor:
        cur = _FakeCursor(self)
        self.cursors.append(cur)
        return cur

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.exits += 1
        return False

    def close(self) -> None:
        self.closed = True


def _install_fake_psycopg(monkeypatch, *, fetchone_result=None, connect_error=None):
    module = types.ModuleType("psycopg")
    connections: list[_FakeConnection] = []

    def connect(dsn):
        if connect_error is not None:
            raise connect_error
        conn = _FakeConnection(dsn, fetchone_result=fetchone_result)
        connections.append(conn)
        return conn

    module.connect = connect
    module.connections = connections
    monkeypatch.setitem(sys.modules, "psycopg", module)
    return module


def _statements(module) -> list[str]:
    return [statement for conn in module.connections for statement in conn.statements]


def _ddl_for(module, table: str) -> str:
    marker = '."' + table + '" ('
    return next(s for s in _statements(module) if s.startswith("CREATE TABLE") and marker in s)


def _insert_for(module, table: str) -> tuple[str, list[tuple]]:
    marker = '."' + table + '" ('
    batches = [
        (sql, rows)
        for conn in module.connections
        for sql, rows in conn.batches
        if marker in sql
    ]
    assert len(batches) == 1, f"expected exactly one insert batch for {table}, got {len(batches)}"
    return batches[0]


# ── postgres engine ──────────────────────────────────────────────────────────


def test_quote_ident_escapes_embedded_double_quotes():
    assert _quote_ident("lattice_brain") == '"lattice_brain"'
    assert _quote_ident('sch"ema') == '"sch""ema"'


def test_missing_psycopg_is_a_named_refusal_not_an_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    engine = PostgresEngine(DSN)

    with pytest.raises(StorageUnavailable, match="optional dependency 'psycopg'") as excinfo:
        engine.connect()

    assert isinstance(excinfo.value.__cause__, ImportError)
    assert "LATTICEAI_STORAGE_ENGINE=postgres" in str(excinfo.value)


def test_connect_without_a_dsn_refuses_instead_of_falling_back_to_sqlite(monkeypatch):
    module = _install_fake_psycopg(monkeypatch)

    with pytest.raises(StorageUnavailable, match="no SQLite fallback"):
        PostgresEngine("").connect()

    assert module.connections == []


def test_connect_hands_the_configured_dsn_to_psycopg(monkeypatch):
    module = _install_fake_psycopg(monkeypatch)
    engine = PostgresEngine(DSN, schema="brain_scale")

    conn = engine.connect()

    assert conn.dsn == DSN
    assert module.connections == [conn]
    conn.close()


def test_initialize_creates_schema_vector_extension_and_stamps_the_engine(monkeypatch):
    module = _install_fake_psycopg(monkeypatch)
    engine = PostgresEngine(DSN, schema="brain_scale")

    result = engine.initialize()

    assert result == {"engine": "postgres", "schema": "brain_scale"}
    statements = _statements(module)
    assert statements[0] == "CREATE EXTENSION IF NOT EXISTS vector"
    assert statements[1] == 'CREATE SCHEMA IF NOT EXISTS "brain_scale"'
    assert 'CREATE TABLE IF NOT EXISTS "brain_scale".storage_meta' in statements[2]
    vectors = statements[3]
    assert 'CREATE TABLE IF NOT EXISTS "brain_scale".brain_vectors' in vectors
    assert "embedding vector," in vectors
    assert "metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb" in vectors
    assert "INSERT INTO \"brain_scale\".storage_meta(key, value)" in statements[4]
    assert "ON CONFLICT (key) DO UPDATE" in statements[4]
    assert [conn.closed for conn in module.connections] == [True]


def test_capabilities_report_pgvector_when_the_extension_is_installed(monkeypatch):
    module = _install_fake_psycopg(monkeypatch, fetchone_result=("vector",))
    engine = PostgresEngine(DSN, schema="brain_scale")

    caps = engine.capabilities()

    assert _statements(module) == ["SELECT extname FROM pg_extension WHERE extname='vector'"]
    assert caps.available is True
    assert caps.reason is None
    assert caps.vector_backend == "pgvector"
    assert caps.vector_available is True
    assert caps.backup_restore is False
    assert caps.migrations is True
    assert caps.encrypted_archives is False
    assert caps.metadata == {"schema": "brain_scale"}
    assert [conn.closed for conn in module.connections] == [True]


def test_capabilities_say_so_when_pgvector_is_absent(monkeypatch):
    _install_fake_psycopg(monkeypatch, fetchone_result=None)

    caps = PostgresEngine(DSN).capabilities()

    assert caps.available is True
    assert caps.vector_available is False
    assert caps.reason == "pgvector extension is not installed"


def test_capabilities_fail_closed_when_the_server_is_unreachable(monkeypatch):
    _install_fake_psycopg(monkeypatch, connect_error=OSError("connection refused"))

    caps = PostgresEngine(DSN).capabilities()

    assert caps.available is False
    assert "connection refused" in (caps.reason or "")
    assert caps.vector_backend == "pgvector"
    assert caps.vector_available is False


def test_backup_and_restore_point_at_the_real_postgres_tools(tmp_path: Path):
    engine = PostgresEngine(DSN)

    with pytest.raises(StorageUnavailable, match="pg_dump"):
        engine.backup(tmp_path / "dump.sql")
    with pytest.raises(StorageUnavailable, match="pg_restore/psql"):
        engine.restore(tmp_path / "dump.sql")


# ── migration helpers ────────────────────────────────────────────────────────


def test_sqlite_affinities_map_to_postgres_types():
    assert _pg_type("INTEGER") == "bigint"
    assert _pg_type("bigint") == "bigint"
    assert _pg_type("REAL") == "double precision"
    assert _pg_type("FLOAT") == "double precision"
    assert _pg_type("DOUBLE PRECISION") == "double precision"
    assert _pg_type("BLOB") == "bytea"
    assert _pg_type("VARCHAR(32)") == "text"
    assert _pg_type("") == "text"
    assert _pg_type(None) == "text"


def test_adapt_value_materialises_memoryviews_and_passes_everything_else_through():
    adapted = _adapt_value(memoryview(b"\x00\x01binary"))

    assert adapted == b"\x00\x01binary"
    assert isinstance(adapted, bytes)
    assert _adapt_value("text") == "text"
    assert _adapt_value(None) is None
    assert _adapt_value(7) == 7


def test_plan_refuses_a_sqlite_brain_that_is_not_there(tmp_path: Path):
    migrator = SQLiteToPostgresMigrator(tmp_path / "absent.sqlite", PostgresEngine(DSN))

    with pytest.raises(FileNotFoundError, match="SQLite brain database not found"):
        migrator.plan()


def test_plan_refuses_a_table_with_no_safe_conflict_key(tmp_path: Path, monkeypatch):
    """No id, no primary key and no usable rowid: nothing identifies a row."""
    db = tmp_path / "brain.sqlite"
    with closing(sqlite3.connect(str(db))) as conn, conn:
        conn.execute("CREATE TABLE loose_notes(body TEXT, tag TEXT)")
        conn.execute("INSERT INTO loose_notes(body, tag) VALUES ('note', 'x')")
    monkeypatch.setattr(migration_module, "_rowid_available", lambda conn, table: False)

    migrator = SQLiteToPostgresMigrator(db, PostgresEngine(DSN))

    with pytest.raises(RuntimeError, match="rowid-less SQLite table without a primary key"):
        migrator.plan()


# ── migration ────────────────────────────────────────────────────────────────


def _seed_source_brain(db: Path) -> None:
    with closing(sqlite3.connect(str(db))) as conn, conn:
        conn.execute(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, title TEXT, weight REAL, "
            "payload BLOB, hits INTEGER)"
        )
        conn.execute(
            "INSERT INTO nodes(id, title, weight, payload, hits) VALUES (?, ?, ?, ?, ?)",
            ("n1", "Node", 0.5, b"\x01\x02", 3),
        )
        conn.execute("CREATE TABLE events(kind TEXT, payload TEXT)")
        conn.executemany(
            "INSERT INTO events(kind, payload) VALUES (?, ?)",
            [("ingest", "first"), ("recall", "second")],
        )
        conn.execute(
            "CREATE TABLE segment_index(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID"
        )
        conn.execute("INSERT INTO segment_index(segid, term, pgno) VALUES (1, 'brain', 7)")
        conn.execute(
            "CREATE TABLE tag_links(node_id TEXT, tag TEXT, PRIMARY KEY(node_id, tag)) WITHOUT ROWID"
        )
        conn.executemany(
            "INSERT INTO tag_links(node_id, tag) VALUES (?, ?)",
            [("n1", "brain"), ("n1", "storage")],
        )
        conn.execute("CREATE TABLE empty_audit(id TEXT PRIMARY KEY, note TEXT)")


def test_migrate_copies_every_table_shape_and_leaves_sqlite_untouched(monkeypatch, tmp_path: Path):
    db = tmp_path / "brain.sqlite"
    _seed_source_brain(db)
    module = _install_fake_psycopg(monkeypatch)
    engine = PostgresEngine(DSN, schema="brain_scale")

    result = SQLiteToPostgresMigrator(db, engine).migrate()

    assert result["status"] == "migrated"
    assert result["source"] == str(db)
    assert result["target_engine"] == "postgres"
    assert result["target_schema"] == "brain_scale"
    assert result["copied_rows"] == {
        "empty_audit": 0,
        "events": 2,
        "nodes": 1,
        "segment_index": 1,
        "tag_links": 2,
    }
    assert result["total_copied_rows"] == result["total_rows"] == 6

    assert _ddl_for(module, "nodes") == (
        'CREATE TABLE IF NOT EXISTS "brain_scale"."nodes" (__source_rowid bigint NOT NULL, '
        '"id" text, "title" text, "weight" double precision, "payload" bytea, "hits" bigint, '
        'PRIMARY KEY ("id"))'
    )
    segment_ddl = _ddl_for(module, "segment_index")
    assert "__source_rowid" not in segment_ddl, "a WITHOUT ROWID table has no rowid to preserve"
    assert segment_ddl.endswith('PRIMARY KEY ("segid", "term"))')
    assert _ddl_for(module, "empty_audit")  # created even though it has no rows

    nodes_sql, nodes_rows = _insert_for(module, "nodes")
    events_sql, events_rows = _insert_for(module, "events")
    segment_sql, _ = _insert_for(module, "segment_index")
    tag_sql, tag_rows = _insert_for(module, "tag_links")

    assert events_sql == (
        'INSERT INTO "brain_scale"."events" ("__source_rowid", "kind", "payload") '
        'VALUES (%s, %s, %s) ON CONFLICT ("__source_rowid") DO UPDATE SET '
        '"kind" = EXCLUDED."kind", "payload" = EXCLUDED."payload"'
    )
    assert 'ON CONFLICT ("id") DO UPDATE SET' in nodes_sql
    assert 'ON CONFLICT ("segid", "term") DO UPDATE SET "pgno" = EXCLUDED."pgno"' in segment_sql
    assert tag_sql.endswith('ON CONFLICT ("node_id", "tag") DO NOTHING'), (
        "every column is part of the key, so there is nothing to update"
    )

    assert nodes_rows == [(1, "n1", "Node", 0.5, b"\x01\x02", 3)]
    assert events_rows == [(1, "ingest", "first"), (2, "recall", "second")]
    assert tag_rows == [("n1", "brain"), ("n1", "storage")]
    assert [sql for sql, _ in module.connections[-1].batches if '."empty_audit" (' in sql] == []
    assert all(conn.closed for conn in module.connections), "migration leaked a connection"

    with closing(sqlite3.connect(str(db))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert conn.execute("SELECT title FROM nodes WHERE id='n1'").fetchone()[0] == "Node"


def test_migrate_is_idempotent_across_repeated_runs(monkeypatch, tmp_path: Path):
    db = tmp_path / "brain.sqlite"
    _seed_source_brain(db)
    _install_fake_psycopg(monkeypatch)
    migrator = SQLiteToPostgresMigrator(db, PostgresEngine(DSN, schema="brain_scale"))

    first = migrator.migrate()
    second = migrator.migrate()

    assert second["copied_rows"] == first["copied_rows"]
    assert second["total_copied_rows"] == first["total_copied_rows"] == 6


def test_dry_run_plans_without_opening_the_target(monkeypatch, tmp_path: Path):
    db = tmp_path / "brain.sqlite"
    _seed_source_brain(db)
    module = _install_fake_psycopg(monkeypatch)

    planned = SQLiteToPostgresMigrator(db, PostgresEngine(DSN)).migrate(dry_run=True)

    assert planned["status"] == "planned"
    assert "copied_rows" not in planned
    assert module.connections == [], "a dry run must not connect to Postgres"
