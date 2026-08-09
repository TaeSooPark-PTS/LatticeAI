"""wp21 coverage — the SQLite storage engine.

The default engine, and the only one that is fully local. Four seams live here:
the optional sqlite-vec probe (which must degrade to the *real* brute-force
cosine fallback rather than claim a capability it does not have), schema
initialisation, the honest capability report before/after the database can be
opened, and the backup/restore pair.

``sqlite_vec`` is not installed in CI, so the probe is driven through an
injected fake module and a stand-in connection: a real connection's
``enable_load_extension`` support depends on how the interpreter's sqlite3 was
compiled, which would make the branch platform-dependent.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import types
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.storage.sqlite import SQLiteEngine, _load_sqlite_vec


class _ProbeConnection:
    """Stand-in for sqlite3.Connection inside the extension probe."""

    def __init__(self, *, enable_raises: bool = False) -> None:
        self.enable_calls: list[bool] = []
        self._enable_raises = enable_raises

    def enable_load_extension(self, flag: bool) -> None:
        self.enable_calls.append(bool(flag))
        if self._enable_raises:
            raise sqlite3.OperationalError("enable_load_extension is not supported")


def _install_fake_sqlite_vec(monkeypatch, *, load_error: Exception | None = None):
    module = types.ModuleType("sqlite_vec")
    loaded: list = []

    def load(conn):
        if load_error is not None:
            raise load_error
        loaded.append(conn)

    module.load = load
    module.loaded = loaded
    monkeypatch.setitem(sys.modules, "sqlite_vec", module)
    return module


# ── sqlite-vec probe ─────────────────────────────────────────────────────────


def test_missing_sqlite_vec_package_is_reported_not_raised(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)

    loaded, reason = _load_sqlite_vec(_ProbeConnection())

    assert loaded is False
    assert reason is not None
    assert reason.startswith("sqlite-vec Python package not installed: ")


def test_sqlite_vec_loads_when_the_extension_is_available(monkeypatch):
    module = _install_fake_sqlite_vec(monkeypatch)
    conn = _ProbeConnection()

    loaded, reason = _load_sqlite_vec(conn)

    assert (loaded, reason) == (True, None)
    assert conn.enable_calls == [True]
    assert module.loaded == [conn]


def test_probe_survives_an_sqlite_build_without_extension_loading(monkeypatch, caplog):
    """A build that refuses ``enable_load_extension`` must not abort the probe."""
    module = _install_fake_sqlite_vec(monkeypatch)
    conn = _ProbeConnection(enable_raises=True)

    with caplog.at_level(logging.DEBUG, logger="lattice_brain.suppressed"):
        loaded, reason = _load_sqlite_vec(conn)

    assert (loaded, reason) == (True, None)
    assert module.loaded == [conn]
    assert "enable_load_extension is not supported" in caplog.text


def test_extension_load_failure_is_named_in_the_reason(monkeypatch):
    _install_fake_sqlite_vec(monkeypatch, load_error=RuntimeError("no vec0 entry point"))

    loaded, reason = _load_sqlite_vec(_ProbeConnection())

    assert loaded is False
    assert reason == "sqlite-vec extension failed to load: no vec0 entry point"


def test_connect_records_the_probe_result_for_the_capability_report(monkeypatch, tmp_path: Path):
    _install_fake_sqlite_vec(monkeypatch)
    engine = SQLiteEngine(tmp_path / "brain.sqlite")

    with closing(engine.connect()) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    assert engine._sqlite_vec_loaded is True
    caps = engine.capabilities()
    assert caps.vector_backend == "sqlite-vec"
    assert caps.reason is None
    assert caps.metadata["sqlite_vec_ann_available"] is True
    assert caps.metadata["degraded"] is False
    assert caps.metadata["honest_fallback"] is None


# ── initialize ───────────────────────────────────────────────────────────────


def test_initialize_stamps_the_engine_and_is_idempotent(tmp_path: Path):
    db = tmp_path / "nested" / "brain.sqlite"
    engine = SQLiteEngine(db, load_vec=False)

    first = engine.initialize()
    second = engine.initialize()

    assert first == {"engine": "sqlite", "db_path": str(db)}
    assert second == first
    with closing(sqlite3.connect(str(db))) as conn:
        assert conn.execute("SELECT value FROM storage_meta WHERE key='engine'").fetchall() == [
            ("sqlite",)
        ]


# ── capabilities ─────────────────────────────────────────────────────────────


def test_capabilities_before_the_data_directory_exists_admits_it_has_not_probed(tmp_path: Path):
    parent = tmp_path / "not-created-yet"
    engine = SQLiteEngine(parent / "brain.sqlite", load_vec=False)

    caps = engine.capabilities()

    assert caps.available is True
    assert caps.vector_backend == "bruteforce-cosine"
    assert caps.vector_available is True
    assert caps.metadata["sqlite_vec_loaded"] is False
    assert caps.metadata["vector_mode"] == "fallback"
    assert "has not been probed yet" in caps.metadata["honest_fallback"]
    assert not parent.exists(), "a capability report must not create the data directory"


def test_capabilities_fail_closed_when_the_database_cannot_be_opened(tmp_path: Path):
    blocked = tmp_path / "brain.sqlite"
    blocked.mkdir()  # a directory where the database file should be
    engine = SQLiteEngine(blocked, load_vec=False)

    caps = engine.capabilities()

    assert caps.available is False
    assert caps.vector_available is False
    assert "unable to open database file" in (caps.reason or "")
    assert caps.metadata == {"db_path": str(blocked)}


# ── backup / restore ─────────────────────────────────────────────────────────


def test_backup_writes_a_readable_copy_and_reports_its_size(tmp_path: Path):
    engine = SQLiteEngine(tmp_path / "brain.sqlite", load_vec=False)
    engine.initialize()
    with engine.session() as conn:
        conn.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO nodes(id, title) VALUES ('n1', 'Backed up')")

    dest = tmp_path / "backups" / "brain.bak"
    out = engine.backup(dest)

    assert out == {"engine": "sqlite", "path": str(dest), "bytes": dest.stat().st_size}
    assert out["bytes"] > 0
    with closing(sqlite3.connect(str(dest))) as conn:
        assert conn.execute("SELECT title FROM nodes WHERE id='n1'").fetchone()[0] == "Backed up"


def test_restore_replaces_the_database_and_clears_stale_wal_files(tmp_path: Path):
    source = tmp_path / "brain.bak"
    with closing(sqlite3.connect(str(source))) as conn, conn:
        conn.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO nodes(id, title) VALUES ('n1', 'From backup')")

    db = tmp_path / "live" / "brain.sqlite"
    engine = SQLiteEngine(db, load_vec=False)
    engine.initialize()
    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")
    wal.write_bytes(b"stale wal")
    shm.write_bytes(b"stale shm")

    out = engine.restore(source)

    assert out == {"engine": "sqlite", "restored": True, "path": str(db)}
    assert not wal.exists() and not shm.exists()
    with closing(sqlite3.connect(str(db))) as conn:
        assert conn.execute("SELECT title FROM nodes WHERE id='n1'").fetchone()[0] == "From backup"


def test_restore_refuses_a_backup_that_is_not_there(tmp_path: Path):
    engine = SQLiteEngine(tmp_path / "brain.sqlite", load_vec=False)
    missing = tmp_path / "absent.bak"

    with pytest.raises(FileNotFoundError, match="SQLite backup not found"):
        engine.restore(missing)

    assert not (tmp_path / "brain.sqlite").exists()
