"""Proactive Brain Intelligence service tests (v9.3.0).

Covers the four capabilities over fake stores: health diagnosis, insights
digest, contradiction surfacing, and consent-first consolidation — plus the
hybrid (lexical+vector) recall upgrade in MemoryService.
"""

from datetime import datetime, timedelta, timezone

from latticeai.services.brain_intelligence import BrainIntelligenceService


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class FakeGraph:
    def __init__(self, nodes=None, edges=None, index_status=None):
        self.nodes = nodes or []
        self.edges = edges or []
        self._index_status = index_status or {
            "status": "ready",
            "scale": {"coverage_ratio": 1.0, "ready_items": 4, "pending_items": 0},
        }
        self.graph_calls = []

    def graph(self, limit, **kwargs):
        self.graph_calls.append(kwargs)
        return {"nodes": self.nodes, "edges": self.edges}

    def index_status(self):
        return self._index_status


class FakeMemoryService:
    def __init__(self, memories=None):
        self.memories = memories or []
        self.pruned = []

    def inspect(self, source, *, user_email=None, workspace_id=None, limit=50):
        assert source == "workspace"
        return {"source": source, "items": self.memories[:limit], "count": len(self.memories)}

    def prune(self, *, ids, user_email=None, workspace_id=None):
        self.pruned.extend(ids)
        return {"count": len(ids)}


def _service(graph=None, memory=None, enable_graph=True):
    return BrainIntelligenceService(
        knowledge_graph=graph,
        memory_service=memory,
        enable_graph=enable_graph,
    )


# ── health report ───────────────────────────────────────────────────────

def test_health_report_scores_fresh_connected_graph():
    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _iso(1)},
        {"id": "b", "type": "Decision", "title": "B", "updated_at": _iso(2)},
    ]
    edges = [{"id": "e1", "from": "a", "to": "b", "type": "MENTIONS", "confidence": 0.9, "evidence": []}]
    report = _service(FakeGraph(nodes, edges)).health_report()
    assert report["grade"] in {"excellent", "good"}
    assert report["dimensions"]["freshness"]["score"] == 100
    assert report["dimensions"]["connectivity"]["orphan_nodes"] == 0
    assert report["dimensions"]["embedding_coverage"]["score"] == 100
    assert report["graph_available"] is True


def test_health_report_flags_stale_orphans_and_reindex():
    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _iso(200)},
        {"id": "b", "type": "Document", "title": "B", "updated_at": _iso(300)},
        {"id": "c", "type": "Document", "title": "C", "updated_at": _iso(1)},
    ]
    graph = FakeGraph(
        nodes,
        edges=[],
        index_status={
            "status": "needs_reindex",
            "scale": {"coverage_ratio": 0.5, "ready_items": 1, "pending_items": 1},
        },
    )
    report = _service(graph).health_report()
    action_ids = {a["id"] for a in report["recommended_actions"]}
    assert "rebuild_vector_index" in action_ids
    assert "review_orphans" in action_ids
    assert "refresh_stale_knowledge" in action_ids
    assert report["dimensions"]["freshness"]["stale_nodes"] == 2


def test_health_report_degrades_without_graph():
    report = _service(None, enable_graph=False).health_report()
    assert report["overall_score"] is None
    assert report["graph_available"] is False
    for dim in report["dimensions"].values():
        assert dim["status"] == "unavailable"


def test_health_report_scopes_graph_reads_to_workspace():
    graph = FakeGraph()
    _service(graph).health_report(workspace_id="team-1")
    assert graph.graph_calls[0] == {"allowed_workspaces": {"team-1"}}


# ── insights ────────────────────────────────────────────────────────────

def test_insights_reports_activity_stale_and_orphans():
    nodes = [
        {"id": "new1", "type": "Document", "title": "프로젝트 킥오프 회의록", "updated_at": _iso(1)},
        {"id": "new2", "type": "Decision", "title": "DB는 SQLite 유지", "updated_at": _iso(2)},
        {"id": "old1", "type": "Document", "title": "낡은 문서", "updated_at": _iso(120)},
    ]
    edges = [{"id": "e1", "from": "new1", "to": "new2", "type": "MENTIONS"}]
    insights = _service(FakeGraph(nodes, edges)).insights()
    assert insights["activity"]["recent_nodes"] == 2
    assert insights["attention"]["stale_nodes"] == 1
    assert insights["attention"]["orphan_nodes"] == 1  # old1 has no edges
    assert insights["suggested_questions"]
    assert any("킥오프" in q for q in insights["suggested_questions"])


# ── contradictions ──────────────────────────────────────────────────────

def test_contradictions_finds_preference_negation_pairs():
    memory = FakeMemoryService([
        {"id": "m1", "content": "user prefers dark mode themes", "created_at": _iso(10)},
        {"id": "m2", "content": "user does not like dark mode themes", "created_at": _iso(1)},
    ])
    result = _service(FakeGraph(), memory).contradictions()
    pair = [i for i in result["items"] if i["kind"] == "memory_pair"]
    assert len(pair) == 1
    assert {pair[0]["left_id"], pair[0]["right_id"]} == {"m1", "m2"}
    assert result["sources"]["memory_pairs"] == 1


def test_contradictions_includes_graph_contradicts_edges():
    edges = [{"id": "e9", "from": "a", "to": "b", "type": "CONTRADICTS"}]
    result = _service(FakeGraph(edges=edges), FakeMemoryService()).contradictions()
    kinds = {i["kind"] for i in result["items"]}
    assert "graph_edge" in kinds


# ── consolidation ───────────────────────────────────────────────────────

def test_consolidate_dry_run_reports_without_pruning():
    memory = FakeMemoryService([
        {"id": "m1", "content": "The team uses FastAPI for the backend"},
        {"id": "m2", "content": "The team uses FastAPI for the backend"},
        {"id": "m3", "content": "Frontend is React with Vite"},
    ])
    result = _service(FakeGraph(), memory).consolidate()
    assert result["mode"] == "dry_run"
    assert result["duplicate_memory_count"] == 1
    assert result["pruned"] == 0
    assert memory.pruned == []


def test_consolidate_apply_prunes_duplicates_only():
    memory = FakeMemoryService([
        {"id": "m1", "content": "The team uses FastAPI for the backend"},
        {"id": "m2", "content": "The team uses FastAPI for the backend"},
    ])
    result = _service(FakeGraph(), memory).consolidate(apply=True)
    assert result["mode"] == "applied"
    assert result["pruned"] == 1
    assert memory.pruned == ["m2"]


def test_consolidate_reports_duplicate_edges_without_mutation():
    edges = [
        {"id": "e1", "from": "a", "to": "b", "type": "MENTIONS"},
        {"id": "e2", "from": "a", "to": "b", "type": "MENTIONS"},
    ]
    result = _service(FakeGraph(edges=edges), FakeMemoryService()).consolidate()
    assert result["duplicate_edge_count"] == 1
    assert result["duplicate_edges"] == ["e2"]


# ── hybrid recall (MemoryService) ───────────────────────────────────────

class _RecallGraph:
    """Graph store stub exposing lexical search + vector search."""

    def __init__(self, *, vector_matches=None, fail_vector=False):
        self.vector_matches = vector_matches or []
        self.fail_vector = fail_vector
        self.scope_calls = []

    def search(self, q, limit, **kwargs):
        return {"matches": [
            {"id": "n-lex", "title": "release checklist", "summary": "release checklist for 9.3", "type": "Document"},
        ]}

    def vector_search(self, q, *, limit=30):
        if self.fail_vector:
            raise RuntimeError("vector backend down")
        return {"matches": self.vector_matches}

    def filter_scoped_nodes(self, items, allowed, *, id_key="id", include_legacy_global=False):
        self.scope_calls.append((set(allowed), id_key))
        return items


class _RecallStore:
    def search_memories(self, q, **kwargs):
        return {"memories": []}


def _memory_service(graph):
    from latticeai.services.memory_service import MemoryService
    return MemoryService(store=_RecallStore(), data_dir=".", knowledge_graph=graph, enable_graph=True)


def test_recall_blends_vector_evidence_and_reports_hybrid_gate():
    graph = _RecallGraph(vector_matches=[
        {"node_id": "n-sem", "title": "배포 절차 정리", "summary": "출시 전 확인 사항", "type": "Document", "score": 0.82},
    ])
    result = _memory_service(graph).recall("release checklist")
    assert result["quality_gate"]["gate"] == "hybrid-evidence/v2"
    by_id = {r["id"]: r for r in result["results"]}
    # Semantically similar Korean node surfaces despite zero lexical overlap.
    assert "n-sem" in by_id
    assert by_id["n-sem"]["vector_score"] == 0.82
    assert "semantic" in by_id["n-sem"]["evidence_kinds"]
    assert "lexical" in by_id["n-lex"]["evidence_kinds"]


def test_recall_merges_vector_score_into_existing_lexical_hit():
    graph = _RecallGraph(vector_matches=[
        {"node_id": "n-lex", "title": "release checklist", "summary": "", "type": "Document", "score": 0.9},
    ])
    result = _memory_service(graph).recall("release checklist")
    rows = [r for r in result["results"] if r["id"] == "n-lex"]
    assert len(rows) == 1
    assert rows[0]["vector_score"] == 0.9
    assert rows[0]["score"] >= 0.9


def test_recall_scopes_vector_hits_to_workspace():
    graph = _RecallGraph(vector_matches=[
        {"node_id": "n-sem", "title": "x", "summary": "", "type": "Document", "score": 0.7},
    ])
    _memory_service(graph).recall("release checklist", workspace_id="team-1")
    assert graph.scope_calls == [({"team-1"}, "node_id")]


def test_recall_degrades_to_lexical_when_vector_fails():
    graph = _RecallGraph(fail_vector=True)
    result = _memory_service(graph).recall("release checklist")
    assert result["quality_gate"]["gate"] == "lexical-evidence/v1"
    assert result["status"] == "degraded"
    assert any(e["source"] == "vector" for e in result["errors"])
    assert any(r["id"] == "n-lex" for r in result["results"])
