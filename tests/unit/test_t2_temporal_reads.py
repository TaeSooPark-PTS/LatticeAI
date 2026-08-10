"""``as_of`` read slicing and access bookkeeping (v11.1.0 Track 2).

The temporal read is additive by construction: every existing signature keeps
its behaviour, ``as_of`` defaults to ``None``, and the slice is computed from
the authoritative ``nodes_v2``/``edges_v2`` projection so it answers the same
way whichever read mode the store is in.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from lattice_brain.graph import retrieval_reads as reads_mod
from lattice_brain.graph.retrieval_reads import _as_of_stamp, _record_access
from lattice_brain.graph.schema import KGStoreV2
from tests.unit.test_t2_support import link, make_store, seed

ROWS = [
    ("n1", "Concept", "Alpha", "the first fact"),
    ("n2", "Concept", "Beta", "the second fact"),
    ("n3", "Concept", "Gamma", "the third fact"),
]


def _store(tmp_path):
    store = make_store(tmp_path)
    seed(store, ROWS)
    link(store, "n1", "n2")
    link(store, "n2", "n3")
    return store


# ── stamp normalization ──────────────────────────────────────────────────────


def test_as_of_stamp_normalizes_every_accepted_form():
    naive = datetime(2026, 6, 1, 12, 30, 15)
    assert _as_of_stamp(naive) == "2026-06-01T12:30:15"
    aware = datetime(2026, 6, 1, 12, 30, 15, tzinfo=timezone.utc)
    # Converted into the store's own naive-local format rather than compared
    # with an offset suffix no stored row carries.
    assert _as_of_stamp(aware) == aware.astimezone().replace(tzinfo=None).isoformat(
        timespec="seconds"
    )
    assert _as_of_stamp("  2026-06-01T00:00:00 ") == "2026-06-01T00:00:00"


def test_as_of_requires_a_timestamp():
    with pytest.raises(ValueError, match="as_of requires a timestamp"):
        _as_of_stamp("")
    with pytest.raises(ValueError):
        _as_of_stamp(None)


# ── the slice ────────────────────────────────────────────────────────────────


def test_as_of_before_anything_existed_is_empty(tmp_path):
    store = _store(tmp_path)
    early = store.as_of("2000-01-01T00:00:00")
    assert early["nodes"] == [] and early["edges"] == []
    assert early["node_count"] == 0 and early["edge_count"] == 0
    assert early["as_of"] == "2000-01-01T00:00:00"


def test_as_of_now_returns_everything_even_without_a_backfill(tmp_path):
    store = _store(tmp_path)
    slice_now = store.as_of(datetime.now() + timedelta(minutes=1))
    assert {node["id"] for node in slice_now["nodes"]} == {"n1", "n2", "n3"}
    assert slice_now["edge_count"] == 2
    # No backfill ran: valid_from is still NULL and created_at carries the day.
    assert all(node["valid_from"] is None for node in slice_now["nodes"])
    assert all(node["valid_to"] is None for node in slice_now["nodes"])


def test_as_of_excludes_a_superseded_memory_and_its_edges(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().replace(microsecond=0)
    cutoff = (now + timedelta(minutes=1)).isoformat(timespec="seconds")
    KGStoreV2(store.db_path).stamp_node_validity(
        "n2", valid_to=cutoff, superseded_by="n3"
    )

    after = store.as_of((now + timedelta(minutes=2)).isoformat(timespec="seconds"))
    assert {node["id"] for node in after["nodes"]} == {"n1", "n3"}
    # Both edges ran through n2, so neither survives the slice.
    assert after["edges"] == []

    before = store.as_of(now.isoformat(timespec="seconds"))
    assert {node["id"] for node in before["nodes"]} == {"n1", "n2", "n3"}
    assert before["edge_count"] == 2
    n2 = next(node for node in before["nodes"] if node["id"] == "n2")
    assert n2["valid_to"] == cutoff and n2["superseded_by"] == "n3"


def test_as_of_window_is_half_open_at_the_moment_of_supersession(tmp_path):
    """At exactly ``valid_to`` the successor owns the instant, not the old fact."""
    store = _store(tmp_path)
    cutoff = (datetime.now() + timedelta(minutes=1)).replace(microsecond=0).isoformat(
        timespec="seconds"
    )
    KGStoreV2(store.db_path).stamp_node_validity("n2", valid_to=cutoff)
    assert {node["id"] for node in store.as_of(cutoff)["nodes"]} == {"n1", "n3"}


def test_as_of_respects_workspace_scope(tmp_path):
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "own", "Concept", "Mine", "kept", workspace_id="ws-a")
        store._upsert_node(conn, "other", "Concept", "Theirs", "hidden", workspace_id="ws-b")
    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")

    scoped = store.as_of(later, allowed_workspaces={"ws-a"})
    assert {node["id"] for node in scoped["nodes"]} == {"own"}
    assert {node["id"] for node in store.as_of(later)["nodes"]} == {"own", "other"}


def test_as_of_edge_validity_is_read_independently_of_its_endpoints(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
    with store._connect() as conn:
        conn.execute("UPDATE edges_v2 SET valid_to = ? WHERE source='n1'", (now,))
    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")

    after = store.as_of(later)
    assert {node["id"] for node in after["nodes"]} == {"n1", "n2", "n3"}
    assert [edge["from"] for edge in after["edges"]] == ["n2"]


def test_as_of_limit_is_clamped(tmp_path):
    store = _store(tmp_path)
    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
    # An explicit 0 means "none", not "the default page" — it clamps to 1.
    assert store.as_of(later, limit=0)["node_count"] == 1
    assert store.as_of(later, limit=9999)["node_count"] == 3
    assert store.as_of(later, limit=None)["node_count"] == 3


# ── neighbors(as_of=…) ───────────────────────────────────────────────────────


def test_neighbors_signature_is_unchanged_without_as_of(tmp_path):
    store = _store(tmp_path)
    assert {n["id"] for n in store.neighbors("n2")["neighbors"]} == {"n1", "n3"}


def test_neighbors_drops_memories_that_were_not_valid_yet(tmp_path):
    store = _store(tmp_path)
    early = store.neighbors("n2", as_of="2000-01-01T00:00:00")
    assert early["neighbors"] == [] and early["edges"] == []

    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
    assert len(store.neighbors("n2", as_of=later)["neighbors"]) == 2


def test_neighbors_as_of_keeps_scoping_behaviour(tmp_path):
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(conn, "root", "Concept", "Root", "r", workspace_id="ws-a")
        store._upsert_node(conn, "mine", "Concept", "Mine", "m", workspace_id="ws-a")
        store._upsert_node(conn, "theirs", "Concept", "Theirs", "t", workspace_id="ws-b")
        store._upsert_edge(conn, "root", "mine", "RELATED_TO")
        store._upsert_edge(conn, "root", "theirs", "RELATED_TO")
    later = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
    result = store.neighbors("root", allowed_workspaces={"ws-a"}, as_of=later)
    assert {node["id"] for node in result["neighbors"]} == {"mine"}


# ── access bookkeeping ───────────────────────────────────────────────────────


def test_opening_a_node_records_one_access(tmp_path):
    store = _store(tmp_path)
    store.get_node("n1")
    store.get_node("n1")
    stats = store.access_stats(["n1", "n2"])
    assert stats["n1"]["accesses"] == 2.0
    assert stats["n1"]["last_used"] is not None
    assert stats["n2"]["accesses"] == 0.0


def test_access_stats_filters_and_short_circuits(tmp_path):
    store = _store(tmp_path)
    assert set(store.access_stats()) == {"n1", "n2", "n3"}
    assert store.access_stats([]) == {}
    assert store.access_stats([None, ""]) == {}
    assert set(store.access_stats(["n3"])) == {"n3"}


def test_recording_an_access_never_breaks_the_read(tmp_path, monkeypatch):
    store = _store(tmp_path)

    class _Broken:
        def __init__(self, _db_path):
            pass

        def touch_node(self, _node_id):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(reads_mod, "KGStoreV2", _Broken)
    assert store.get_node("n1")["title"] == "Alpha"

    # A build without the v2 schema simply records nothing.
    monkeypatch.setattr(reads_mod, "KGStoreV2", None)
    assert store.get_node("n1")["title"] == "Alpha"
    _record_access(store.db_path, "n1")
