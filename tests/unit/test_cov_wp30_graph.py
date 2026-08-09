"""wp30 coverage — graph-layer proactive reads, ingest edges, provenance I/O.

The proactive layer is read-only over a graph *sample*, so every input it can
meet has to be tolerated: unparseable timestamps, nodes with no text, more
near-duplicate pairs than the caller asked for, and a store whose merge
primitive fails on one group without aborting the rest. The store-side cases
here are the write/read paths the sample depends on: conversation-linked
sources, filtered provenance listing, an import that carries knowledge
sources, a re-index that cannot reach its embedder, and repeated backups.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.ingest import _triple_edge_metadata
from lattice_brain.graph.proactive import (
    ProactiveBrain,
    _jaccard,
    _parse_ts,
    gate_ingest_candidate,
)
from lattice_brain.graph.store import KnowledgeGraphStore


class _EmptyUnion(set):
    """A set-like whose union is empty — the divide-by-zero guard's precondition."""

    def __or__(self, other):
        return set()


class _FakeGraphStore:
    """The public read surface ProactiveBrain documents it needs."""

    def __init__(self, nodes: List[Dict[str, Any]], edges=(), merge_nodes=None) -> None:
        self.nodes = nodes
        self.edges = list(edges)
        self.calls: List[Any] = []
        if merge_nodes is not None:
            self.merge_nodes = merge_nodes

    def graph(self, limit, *, allowed_workspaces=None):
        self.calls.append((limit, allowed_workspaces))
        return {"nodes": self.nodes, "edges": self.edges}


def _node(node_id, title, *, summary="", updated_at="2026-08-01T00:00:00+00:00"):
    return {"id": node_id, "type": "Document", "title": title, "summary": summary,
            "updated_at": updated_at}


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_parse_ts_returns_none_for_empty_and_unparseable_values():
    assert _parse_ts("") is None
    assert _parse_ts(None) is None
    assert _parse_ts("last tuesday") is None
    parsed = _parse_ts("2026-08-01T00:00:00Z")
    assert parsed is not None and parsed.tzinfo is not None
    # A naive stamp is read as UTC rather than rejected.
    assert _parse_ts("2026-08-01T00:00:00").tzinfo is not None


def test_jaccard_guards_both_emptiness_and_an_empty_union():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert _jaccard(_EmptyUnion({"a"}), _EmptyUnion({"b"})) == 0.0


def test_proactive_brain_requires_a_store():
    with pytest.raises(ValueError, match="requires a graph store instance"):
        ProactiveBrain(None)


# ── duplicates ───────────────────────────────────────────────────────────────

def test_duplicate_scan_ignores_textless_nodes_and_honours_the_pair_cap():
    nodes = [
        _node("n0", "ab"),  # under the 3-character floor: never a candidate
        _node("n1", "alpha beta gamma delta one"),
        _node("n2", "alpha beta gamma delta two"),
        _node("n3", "alpha beta gamma delta six"),
    ]
    brain = ProactiveBrain(_FakeGraphStore(nodes))

    capped = brain.find_duplicates(near_threshold=0.6, max_pairs=1)
    assert capped["nodes_scanned"] == 4
    assert capped["exact_groups"] == []
    assert len(capped["near_pairs"]) == 1

    everything = brain.find_duplicates(near_threshold=0.6, max_pairs=10)
    assert len(everything["near_pairs"]) == 3
    assert {"n0"} .isdisjoint(
        {pair["left"]["id"] for pair in everything["near_pairs"]}
        | {pair["right"]["id"] for pair in everything["near_pairs"]}
    )


def test_sample_normalizes_from_to_edges_and_forwards_the_workspace_scope():
    store = _FakeGraphStore(
        [_node("n1", "alpha beta gamma")],
        edges=[{"id": "e1", "from": "n1", "to": "n2", "type": "CONTRADICTS"}],
    )
    report = ProactiveBrain(store).detect_contradictions(workspace_id="w1", limit=25)
    assert store.calls == [(25, {"w1"})]
    assert report["contradiction_edges"] == [
        {"id": "e1", "source": "n1", "target": "n2", "type": "CONTRADICTS",
         "signal": "contradicts_edge"}
    ]


def test_contradiction_scan_skips_nodes_with_no_text():
    nodes = [
        _node("n1", "", summary=""),
        _node("n2", "team prefers dark theme colors"),
        _node("n3", "team does not like dark theme colors"),
    ]
    report = ProactiveBrain(_FakeGraphStore(nodes)).detect_contradictions()
    assert report["nodes_scanned"] == 2
    pairs = {(pair["left_id"], pair["right_id"]) for pair in report["node_pairs"]}
    assert ("n2", "n3") in pairs or ("n3", "n2") in pairs


def test_quality_report_only_ages_nodes_that_carry_a_timestamp():
    nodes = [
        _node("n1", "alpha beta gamma", updated_at="1999-01-01T00:00:00+00:00"),
        _node("n2", "delta epsilon zeta", updated_at=None),
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "type": "rel",
         "metadata": {"confidence": 0.0, "evidence": ["x"]}},
    ]
    report = ProactiveBrain(_FakeGraphStore(nodes, edges)).quality_report()

    assert report["stale_nodes"]["dated_nodes"] == 1
    assert report["stale_nodes"]["count"] == 1
    assert report["stale_nodes"]["samples"][0]["id"] == "n1"
    # confidence 0.0 is a real reading, not an absent one.
    assert report["edge_quality"]["metrics"]["avg_conf"] == 0.0


# ── consolidation ────────────────────────────────────────────────────────────

def test_consolidation_reports_per_group_merge_failures_and_keeps_going():
    nodes = [
        _node("keep", "shared duplicate body text", updated_at="2026-08-02T00:00:00+00:00"),
        _node("drop", "shared duplicate body text", updated_at="2026-08-01T00:00:00+00:00"),
    ]
    calls = []

    def _merge(keep_id, remove_ids):
        calls.append((keep_id, tuple(remove_ids)))
        raise RuntimeError("merge primitive is unsafe here")

    store = _FakeGraphStore(nodes, merge_nodes=_merge)
    plan = ProactiveBrain(store).consolidate_duplicates(dry_run=False)

    assert plan["mode"] == "applied"
    assert plan["apply_supported"] is True
    assert calls == [("keep", ("drop",))]
    assert plan["applied"] == [
        {"keep": "keep", "error": "merge primitive is unsafe here"}
    ]


def test_consolidation_without_a_merge_primitive_stays_a_plan():
    nodes = [
        _node("keep", "shared duplicate body text", updated_at="2026-08-02T00:00:00+00:00"),
        _node("drop", "shared duplicate body text", updated_at="2026-08-01T00:00:00+00:00"),
    ]
    plan = ProactiveBrain(_FakeGraphStore(nodes)).consolidate_duplicates(dry_run=False)
    assert plan["mode"] == "plan_only"
    assert plan["apply_supported"] is False
    assert plan["groups"][0]["remove"] == ["drop"]
    assert "no safe merge primitive" in plan["note"]


# ── ingestion gate ───────────────────────────────────────────────────────────

def test_gate_ignores_unusable_matches():
    matches = [
        "a bare string",
        {"id": "empty"},
        {"id": "other", "title": "completely unrelated material", "summary": ""},
    ]
    verdict = gate_ingest_candidate("brand new note about lattice graphs", lambda q: matches)
    assert verdict["action"] == "ingest"
    assert verdict["reason"] == "novel_content"

    exact = gate_ingest_candidate(
        "brand new note about lattice graphs",
        lambda q: {"matches": [{"id": "m1", "content": "brand new note about lattice graphs"}]},
    )
    assert exact["action"] == "skip_duplicate"
    assert exact["match_id"] == "m1"


# ── store write/read paths the sample depends on ─────────────────────────────

def test_triple_edge_metadata_drops_an_unusable_confidence():
    assert _triple_edge_metadata({"context": "c", "evidence": "verb", "confidence": 0.5}) == {
        "context": "c", "evidence": "verb", "confidence": 0.5,
    }
    assert _triple_edge_metadata({"confidence": "very high"}) == {"context": ""}


def test_ingest_source_links_the_conversation_that_captured_it(tmp_path):
    store = _store(tmp_path)
    result = store.ingest_source(
        source_type="web_url",
        title="Graph theory notes",
        text="Nodes and edges describe relationships between concepts.",
        source_uri="https://example.com/a",
        owner="u@x",
        conversation_id="conv-1",
    )
    graph = store.graph(200)
    ids = {node["id"] for node in graph["nodes"]}
    assert result["node_id"] in ids
    chat_ids = {node["id"] for node in graph["nodes"] if node["type"] == "Chat"}
    assert chat_ids
    linked = {
        (edge["from"], edge["to"])
        for edge in graph["edges"]
        if edge["from"] in chat_ids
    }
    assert (next(iter(chat_ids)), result["node_id"]) in linked


def test_list_provenance_filters_by_source_type(tmp_path):
    store = _store(tmp_path)
    store.record_provenance(node_id="n1", source_type="web_url", pipeline="p", title="A")
    store.record_provenance(node_id="n2", source_type="note", pipeline="p", title="B")

    filtered = store.list_provenance(source_type="note")
    assert filtered["count"] == 1
    assert filtered["items"][0]["node_id"] == "n2"
    assert store.list_provenance()["count"] == 2


def test_import_restores_knowledge_sources(tmp_path):
    store = _store(tmp_path)
    result = store.import_graph_data(
        {
            "nodes": [],
            "knowledge_sources": [
                {"id": "src-1", "root_path": "/data/notes", "os_type": "posix",
                 "label": "Notes"}
            ],
        }
    )
    assert result["knowledge_sources"] == 1
    with store._connect() as conn:
        rows = conn.execute("SELECT id, root_path, status FROM knowledge_sources").fetchall()
    assert [tuple(row) for row in rows] == [("src-1", "/data/notes", "active")]


def test_reindex_after_import_degrades_instead_of_raising(tmp_path):
    store = _store(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("embedding provider is down")

    store.rebuild_vector_index = _boom  # type: ignore[method-assign]
    outcome = store._reindex_after_import()

    assert outcome["status"] == "unavailable"
    assert outcome["degraded"] is True
    assert outcome["reindexed_items"] == 0
    assert "embedding provider is down" in outcome["detail"]


def test_backup_database_overwrites_a_previous_snapshot(tmp_path):
    store = _store(tmp_path)
    store.ingest_source(source_type="note", title="A", text="alpha body")
    dest = tmp_path / "snapshots" / "kg.sqlite"

    first = store.backup_database(dest)
    assert first.is_file()
    size_before = dest.stat().st_size

    store.ingest_source(source_type="note", title="B", text="beta body that is longer")
    store.backup_database(dest)
    assert dest.stat().st_size >= size_before
    assert not Path(str(dest) + "-wal").exists()
