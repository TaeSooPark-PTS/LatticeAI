"""Provenance rows are identified by origin, never by when they happened.

The Obsidian bridge's idempotency (and every folder re-scan's) rests on this
store-level contract, so it is asserted here directly rather than only through
a bridge that happens to exercise it.

Through 11.0.x the row id hashed a second-resolution timestamp alongside the
node and content hash. Two consequences, both invisible until a slow machine
found them: re-ingesting unchanged content inside one second silently collapsed
onto a single row, and the same work one second later appended a duplicate.
These tests advance a fake clock so neither outcome can be produced by timing.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.utils as brain_utils
from lattice_brain.graph.store import KnowledgeGraphStore

BASE = datetime(2026, 8, 10, 9, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    """The seam every graph timestamp resolves through (``_now`` → ``now_iso``)."""
    state = {"offset": timedelta(0)}
    monkeypatch.setattr(brain_utils, "local_now", lambda: BASE + state["offset"])
    return state


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _rows(store: KnowledgeGraphStore):
    return store.export_graph_data()["provenance"]


def _record(store: KnowledgeGraphStore, **overrides):
    fields = {
        "node_id": "webdoc:abc",
        "source_type": "obsidian",
        "source_uri": "/vault/notes/alpha.md",
        "content_hash": "hash-1",
        "pipeline": "unified-ingestion",
        "title": "Alpha",
    }
    fields.update(overrides)
    return store.record_provenance(**fields)


def test_re_recording_the_same_origin_updates_one_row_however_long_the_gap(tmp_path, clock):
    store = _store(tmp_path)
    first = _record(store)
    assert len(_rows(store)) == 1

    clock["offset"] = timedelta(seconds=1)
    same_second_later = _record(store)
    clock["offset"] = timedelta(hours=9)
    much_later = _record(store, duplicate=True)

    rows = _rows(store)
    assert len(rows) == 1
    assert first["id"] == same_second_later["id"] == much_later["id"]
    # The timestamp is data on the row: "last seen" moves, identity does not.
    assert rows[0]["created_at"] == much_later["created_at"]
    assert rows[0]["created_at"] != first["created_at"]
    assert rows[0]["duplicate"] == 1


def test_new_content_or_a_new_origin_still_appends_its_own_row(tmp_path, clock):
    store = _store(tmp_path)
    _record(store)

    _record(store, content_hash="hash-2")               # the file changed
    _record(store, source_uri="/other/vault/alpha.md")  # a different origin
    _record(store, source_type="file")                  # a different door
    _record(store, pipeline="folder-watch")             # a different pipeline
    _record(store, node_id="webdoc:def")                # a different node

    assert len(_rows(store)) == 6
    assert len({row["id"] for row in _rows(store)}) == 6


def test_the_row_id_does_not_depend_on_the_clock(tmp_path, clock):
    """Two brains recording the same origin agree on the id at any time."""
    first = _record(_store(tmp_path / "one"))
    clock["offset"] = timedelta(days=400)
    second = _record(_store(tmp_path / "two"))

    assert first["id"] == second["id"]
    assert first["created_at"] != second["created_at"]


def test_a_missing_content_hash_still_keys_on_the_origin(tmp_path, clock):
    """Sources that report no hash (chat, memory) must not multiply either."""
    store = _store(tmp_path)
    _record(store, content_hash=None)
    clock["offset"] = timedelta(minutes=5)
    _record(store, content_hash=None)

    assert len(_rows(store)) == 1
    assert _rows(store)[0]["content_hash"] is None
