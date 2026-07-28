"""Database connections must be closed, not merely committed.

``with sqlite3.connect(...) as conn`` commits or rolls back and leaves the
connection **open**. Every store helper that returned a raw connection for a
``with`` block therefore leaked a file descriptor until garbage collection
reclaimed it. CPython's refcounting normally does that immediately, which hid
the bug — until something held the frame alive. A coverage tracer does exactly
that, and running ``pytest --cov`` exhausted the descriptor limit and failed
~400 tests before 10.2.0.

These tests fail if any of those helpers goes back to yielding a connection it
does not close.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.conversations import ConversationStore
from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.storage.sqlite import SQLiteEngine
from latticeai.core.workspace_os import WorkspaceOSStore


def _is_closed(conn: sqlite3.Connection) -> bool:
    """A closed sqlite3 connection raises ProgrammingError on any use."""
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_graph_store_connection_is_closed_after_the_block(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with store._connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert _is_closed(conn), "_connect() leaked an open connection"


def test_storage_engine_session_is_closed_after_the_block(tmp_path):
    engine = SQLiteEngine(tmp_path / "engine.sqlite", load_vec=False)
    with engine.session() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t (a INTEGER)")
    assert _is_closed(conn), "StorageEngine.session() leaked an open connection"


def test_conversation_store_connection_is_closed_after_the_block(tmp_path):
    store = ConversationStore(tmp_path / "conv.sqlite")
    with store._connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert _is_closed(conn), "ConversationStore._connect() leaked an open connection"


def test_workspace_state_connection_is_closed_after_the_block(tmp_path):
    store = WorkspaceOSStore(tmp_path)
    with store._connect_state_db() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert _is_closed(conn), "_connect_state_db() leaked an open connection"


def test_session_still_commits_on_success(tmp_path):
    """Closing must not cost us the transaction semantics callers rely on."""
    engine = SQLiteEngine(tmp_path / "commit.sqlite", load_vec=False)
    with engine.session() as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
    with engine.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_session_still_rolls_back_on_error(tmp_path):
    engine = SQLiteEngine(tmp_path / "rollback.sqlite", load_vec=False)
    with engine.session() as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")

    with pytest.raises(RuntimeError):
        with engine.session() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")

    with engine.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_many_sequential_blocks_do_not_accumulate_connections(tmp_path):
    """The failure mode that broke coverage: references outliving the block.

    Holding every connection object alive is what a tracer effectively does.
    If the helper closes properly, holding the object is harmless.
    """
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    held = []
    for _ in range(100):
        with store._connect() as conn:
            conn.execute("SELECT 1")
            held.append(conn)
    assert all(_is_closed(c) for c in held), "connections stayed open while referenced"


def test_no_silently_swallowed_exceptions_remain():
    """`except Exception: pass` erases evidence; `quiet()` keeps behaviour and logs.

    112 handlers discarded their exception with no trace before 10.2.0, so a
    real bug in an optional path was indistinguishable from the optional thing
    being absent. Ruff enforces this (S110/S112); this test states the intent
    where a reader will find it.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S110,S112",
         "--output-format=concise", "latticeai", "lattice_brain"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "silent exception handlers reappeared:\n" + result.stdout
    )


def test_quiet_records_the_live_exception(caplog):
    import logging

    from latticeai.core.quiet import quiet, quiet_summary

    with caplog.at_level(logging.DEBUG, logger="latticeai.suppressed"):
        try:
            raise ValueError("optional probe failed")
        except ValueError:
            quiet("probing an optional thing")
            summary = quiet_summary()

    assert "optional probe failed" in caplog.text
    assert "probing an optional thing" in caplog.text
    assert summary == "optional probe failed"


def test_quiet_outside_an_except_block_is_a_no_op():
    """A refactor that moves the call must not itself raise."""
    from latticeai.core.quiet import quiet

    assert quiet() is None
