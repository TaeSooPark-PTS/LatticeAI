"""Graph-layer Proactive Brain tests (lattice_brain/graph/proactive.py).

Covers: duplicate discovery (exact + near), contradiction detection over node
contents, the combined quality report, consolidation planning (dry-run first,
plan-only when the store has no safe merge primitive), the ingestion quality
gate seam, service wiring, and the /api/brain/* router additions.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.graph.proactive import ProactiveBrain, gate_ingest_candidate
from lattice_brain.quality import content_signature, dedupe_key
from latticeai.services.brain_intelligence import BrainIntelligenceService


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class FakeStore:
    """Minimal store exposing the public read API ProactiveBrain relies on."""

    def __init__(self, nodes=None, edges=None):
        self.nodes = nodes or []
        self.edges = edges or []
        self.graph_calls = []

    def graph(self, limit, **kwargs):
        self.graph_calls.append((limit, kwargs))
        return {"nodes": self.nodes, "edges": self.edges}


class MergingStore(FakeStore):
    """Store variant that DOES expose a safe merge primitive."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.merged = []

    def merge_nodes(self, keep_id, remove_ids):
        self.merged.append((keep_id, list(remove_ids)))
        return {"status": "ok", "keep": keep_id, "removed": list(remove_ids)}


def _dup_nodes():
    return [
        {"id": "doc-old", "type": "Document", "title": "Quarterly plan",
         "summary": "Ship the proactive quality layer in Q3", "updated_at": _iso(10)},
        {"id": "doc-new", "type": "Document", "title": "Quarterly plan",
         "summary": "Ship the proactive quality layer in Q3", "updated_at": _iso(1)},
        {"id": "doc-other", "type": "Document", "title": "Roadmap review",
         "summary": "Frontend rebuild milestones", "updated_at": _iso(2)},
    ]


# ── find_duplicates ──────────────────────────────────────────────────────

def test_find_duplicates_groups_exact_content():
    brain = ProactiveBrain(FakeStore(_dup_nodes()))
    result = brain.find_duplicates()
    assert result["nodes_scanned"] == 3
    assert result["exact_duplicate_nodes"] == 1
    groups = result["exact_groups"]
    assert len(groups) == 1
    assert set(groups[0]["node_ids"]) == {"doc-old", "doc-new"}


def test_find_duplicates_reports_near_pairs_not_exact():
    nodes = [
        {"id": "a", "type": "Document", "title": "Release checklist",
         "summary": "release checklist deployment pipeline steps", "updated_at": _iso(1)},
        {"id": "b", "type": "Document", "title": "Release checklist",
         "summary": "release checklist deployment pipeline steps verification", "updated_at": _iso(2)},
    ]
    result = ProactiveBrain(FakeStore(nodes)).find_duplicates(near_threshold=0.7)
    assert result["exact_groups"] == []
    assert len(result["near_pairs"]) == 1
    pair = result["near_pairs"][0]
    assert {pair["left"]["id"], pair["right"]["id"]} == {"a", "b"}
    assert 0.7 <= pair["similarity"] <= 1.0


def test_find_duplicates_scopes_to_workspace():
    store = FakeStore()
    ProactiveBrain(store).find_duplicates(workspace_id="team-1")
    assert store.graph_calls[0][1] == {"allowed_workspaces": {"team-1"}}


def test_find_duplicates_unscoped_passes_no_workspace_kwargs():
    store = FakeStore()
    ProactiveBrain(store).find_duplicates()
    assert store.graph_calls[0][1] == {}


# ── detect_contradictions ────────────────────────────────────────────────

def test_detect_contradictions_finds_node_content_negation_pairs():
    nodes = [
        {"id": "n1", "type": "Decision", "title": "user prefers dark mode themes",
         "summary": "", "updated_at": _iso(5)},
        {"id": "n2", "type": "Decision", "title": "user does not like dark mode themes",
         "summary": "", "updated_at": _iso(1)},
    ]
    result = ProactiveBrain(FakeStore(nodes)).detect_contradictions()
    pairs = result["node_pairs"]
    assert len(pairs) == 1
    assert {pairs[0]["left_id"], pairs[0]["right_id"]} == {"n1", "n2"}
    assert pairs[0]["signal"] == "preference_negation"


def test_detect_contradictions_normalizes_from_to_edge_keys():
    edges = [{"id": "e1", "from": "a", "to": "b", "type": "CONTRADICTS"}]
    result = ProactiveBrain(FakeStore(edges=edges)).detect_contradictions()
    assert len(result["contradiction_edges"]) == 1
    edge = result["contradiction_edges"][0]
    assert edge["source"] == "a"
    assert edge["target"] == "b"


# ── quality_report ───────────────────────────────────────────────────────

def test_quality_report_combines_all_sections():
    nodes = _dup_nodes() + [
        {"id": "stale-1", "type": "Document", "title": "Ancient notes",
         "summary": "old content nobody touched", "updated_at": _iso(200)},
    ]
    edges = [
        {"id": "e1", "from": "doc-old", "to": "doc-other", "type": "MENTIONS",
         "metadata": {"confidence": 0.0}},
    ]
    report = ProactiveBrain(FakeStore(nodes, edges)).quality_report()
    assert report["summary"]["exact_duplicate_nodes"] == 1
    assert report["stale_nodes"]["count"] == 1
    assert report["stale_nodes"]["threshold_days"] == 90
    # confidence 0.0 must survive (score-0-falsy pitfall): explicit reads only.
    assert report["edge_quality"]["metrics"]["avg_conf"] == 0.0
    assert report["generated_at"]
    # JSON-safe
    import json
    json.dumps(report)


def test_quality_report_counts_duplicate_edges():
    edges = [
        {"id": "e1", "from": "a", "to": "b", "type": "MENTIONS"},
        {"id": "e2", "from": "a", "to": "b", "type": "MENTIONS"},
    ]
    report = ProactiveBrain(FakeStore(edges=edges)).quality_report()
    assert report["edge_quality"]["duplicate_edge_count"] == 1
    assert report["edge_quality"]["duplicate_edge_ids"] == ["e2"]


# ── consolidate_duplicates ───────────────────────────────────────────────

def test_consolidate_dry_run_keeps_most_recent_node():
    edges = [{"id": "e1", "from": "doc-old", "to": "doc-other", "type": "MENTIONS"}]
    plan = ProactiveBrain(FakeStore(_dup_nodes(), edges)).consolidate_duplicates()
    assert plan["mode"] == "dry_run"
    assert plan["group_count"] == 1
    group = plan["groups"][0]
    assert group["keep"] == "doc-new"
    assert group["remove"] == ["doc-old"]
    assert group["edges_to_redirect"] == 1
    assert plan["applied"] == []


def test_consolidate_apply_degrades_to_plan_only_without_merge_primitive():
    plan = ProactiveBrain(FakeStore(_dup_nodes())).consolidate_duplicates(dry_run=False)
    assert plan["mode"] == "plan_only"
    assert plan["apply_supported"] is False
    assert plan["note"]
    assert plan["applied"] == []
    assert plan["groups"]  # actionable plan still returned


def test_consolidate_apply_uses_store_merge_primitive_when_present():
    store = MergingStore(_dup_nodes())
    plan = ProactiveBrain(store).consolidate_duplicates(dry_run=False)
    assert plan["mode"] == "applied"
    assert store.merged == [("doc-new", ["doc-old"])]
    assert plan["applied"][0]["result"]["status"] == "ok"


# ── real KnowledgeGraphStore integration ────────────────────────────────

def test_proactive_brain_over_real_store_finds_ingested_duplicates(tmp_path):
    from lattice_brain.graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    text = "Ship the proactive brain quality layer during the third quarter"
    r1 = store.ingest_source(source_type="note", title="Quarterly plan",
                             text=text, source_uri="note://one")
    r2 = store.ingest_source(source_type="note", title="Quarterly plan",
                             text=text, source_uri="note://two")
    assert r1["node_id"] != r2["node_id"]

    result = ProactiveBrain(store).find_duplicates()
    doc_ids = {r1["node_id"], r2["node_id"]}
    assert any(doc_ids <= set(g["node_ids"]) for g in result["exact_groups"])

    report = ProactiveBrain(store).quality_report()
    assert report["summary"]["exact_duplicate_nodes"] >= 1
    plan = ProactiveBrain(store).consolidate_duplicates(dry_run=False)
    # Real store exposes no merge primitive -> plan only, no mutation.
    assert plan["mode"] == "plan_only"
    assert store.graph(50)["nodes"]  # graph unchanged and readable


# ── gate_ingest_candidate ───────────────────────────────────────────────

def test_gate_skips_exact_duplicate():
    existing = [{"id": "doc-1", "title": "Team decision",
                 "summary": "we will keep sqlite as the primary database"}]
    gate = gate_ingest_candidate(
        "Team decision we will keep sqlite as the primary database",
        lambda q: existing,
    )
    assert gate["action"] == "skip_duplicate"
    assert gate["match_id"] == "doc-1"
    assert gate["similarity"] == 1.0


def test_gate_flags_near_duplicate_for_review():
    existing = {"matches": [{"id": "doc-1", "title": "",
                             "summary": "release checklist deployment pipeline verification"}]}
    gate = gate_ingest_candidate(
        "release checklist deployment pipeline verification postmortem",
        lambda q: existing,
        near_threshold=0.6,
    )
    assert gate["action"] == "review"
    assert gate["reason"] == "near_duplicate"
    assert gate["match_id"] == "doc-1"
    assert gate["similarity"] >= 0.6


def test_gate_ingests_novel_content():
    existing = [{"id": "doc-1", "title": "Frontend rebuild", "summary": "react vite migration"}]
    gate = gate_ingest_candidate(
        "database sharding strategy for postgres replicas",
        lambda q: existing,
    )
    assert gate["action"] == "ingest"
    assert gate["reason"] == "novel_content"


def test_gate_reviews_on_empty_text_and_search_failure():
    assert gate_ingest_candidate("", lambda q: [])["action"] == "review"

    def boom(q):
        raise RuntimeError("search down")

    gate = gate_ingest_candidate("some new content worth checking", boom)
    assert gate["action"] == "review"
    assert "search_failed" in gate["reason"]


def test_quality_helpers_are_shared_definitions():
    # dedupe_key/content_signature are the seam both layers rely on.
    assert dedupe_key("Hello  World") == dedupe_key("hello world")
    assert content_signature("user prefers dark themes") == {"dark", "themes"}


# ── service wiring ──────────────────────────────────────────────────────

def _service(store=None, enable_graph=True):
    return BrainIntelligenceService(
        knowledge_graph=store, memory_service=None, enable_graph=enable_graph
    )


def test_service_graph_duplicates_reports_groups():
    result = _service(FakeStore(_dup_nodes())).graph_duplicates()
    assert result["available"] is True
    assert result["exact_duplicate_nodes"] == 1


def test_service_graph_endpoints_degrade_without_graph():
    service = _service(None, enable_graph=False)
    assert service.graph_duplicates()["available"] is False
    assert service.quality_report()["available"] is False


def test_service_contradictions_include_graph_node_pairs():
    nodes = [
        {"id": "n1", "type": "Decision", "title": "user prefers dark mode themes",
         "summary": "", "updated_at": _iso(5)},
        {"id": "n2", "type": "Decision", "title": "user does not like dark mode themes",
         "summary": "", "updated_at": _iso(1)},
    ]
    result = _service(FakeStore(nodes)).contradictions()
    kinds = {i["kind"] for i in result["items"]}
    assert "graph_node_pair" in kinds
    assert result["sources"]["graph_node_pairs"] == 1


def test_service_consolidate_includes_graph_plan_dry_run_only():
    result = _service(FakeStore(_dup_nodes())).consolidate()
    plan = result["graph_consolidation"]
    assert plan is not None
    assert plan["mode"] == "dry_run"
    assert plan["groups"][0]["keep"] == "doc-new"


# ── router ──────────────────────────────────────────────────────────────

def _client(store=None):
    from latticeai.api.brain_intelligence import create_brain_intelligence_router

    audits = []
    app = FastAPI()
    app.include_router(create_brain_intelligence_router(
        service=_service(store if store is not None else FakeStore(_dup_nodes())),
        require_user=lambda request: "owner@example.com",
        gate_read=lambda request: None,
        gate_write=lambda request: None,
        append_audit_event=lambda *a, **k: audits.append((a, k)),
    ))
    return TestClient(app), audits


def test_router_duplicates_endpoint():
    client, _ = _client()
    response = client.get("/api/brain/duplicates")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["exact_duplicate_nodes"] == 1


def test_router_quality_report_endpoint():
    client, _ = _client()
    response = client.get("/api/brain/quality-report")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["summary"]["exact_duplicate_nodes"] == 1


def test_router_consolidate_defaults_to_dry_run_and_accepts_dry_run_alias():
    client, audits = _client()
    body = client.post("/api/brain/consolidate", json={}).json()
    assert body["mode"] == "dry_run"
    assert body["graph_consolidation"]["mode"] == "dry_run"

    # dry_run=true alias keeps a dry run even with apply=true (explicit wins).
    body = client.post("/api/brain/consolidate", json={"apply": True, "dry_run": True}).json()
    assert body["mode"] == "dry_run"

    # dry_run=false requests an apply (memory-side only; graph stays dry-run).
    body = client.post("/api/brain/consolidate", json={"dry_run": False}).json()
    assert body["mode"] == "applied"
    assert body["graph_consolidation"]["mode"] == "dry_run"
    assert audits  # applies are audited
