"""Graph curator noise-reduction job tests (backlog #10, review §7.2 D).

Covers: the pure decision helpers (relation-verb ko/en normalization map,
IDF/frequency-floor concept planning, user-created protection), the
store-level ``curate_noise`` job in dry-run vs apply mode over a real temp
store, edge-verb merge behavior on rename collisions, and the API endpoint's
dry-run default.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.curator import (
    normalize_relation_verb,
    plan_concept_noise_reduction,
    plan_relation_normalization,
)
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.knowledge_graph import create_knowledge_graph_router


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_normalize_relation_verb_ko_en_mapping():
    assert normalize_relation_verb("만들다") == "created"
    assert normalize_relation_verb("만든") == "created"
    assert normalize_relation_verb("creates") == "created"
    assert normalize_relation_verb("생성함") == "created"
    assert normalize_relation_verb("언급함") == "mentions"
    assert normalize_relation_verb("포함함") == "contains"
    assert normalize_relation_verb("수정함") == "fixed"
    # Unknown labels pass through unchanged (lossless rename map).
    assert normalize_relation_verb("indexed_from") == "indexed_from"
    assert normalize_relation_verb("") == ""


def test_plan_relation_normalization_only_lists_changes():
    plan = plan_relation_normalization(["만들다", "created", "mentions", "언급함", "indexed_from"])
    assert plan == {"만들다": "created", "언급함": "mentions"}


def test_plan_concept_noise_flags_low_idf_and_frequency_floor():
    concepts = [
        {"id": "c:ubiquitous", "label": "내용", "df": 10, "heuristic": True},
        {"id": "c:orphan", "label": "잔여", "df": 0, "heuristic": True},
        {"id": "c:signal", "label": "hybrid search", "df": 3, "heuristic": True},
        {"id": "c:user", "label": "내 프로젝트", "df": 0, "heuristic": False},
    ]
    plan = plan_concept_noise_reduction(concepts, total_docs=10, max_df_ratio=0.8)
    removed = {item["id"]: item["reason"] for item in plan["remove"]}
    kept = {item["id"]: item["reason"] for item in plan["keep"]}
    assert removed["c:ubiquitous"] == "low_idf_ubiquitous"
    assert removed["c:orphan"] == "below_frequency_floor"
    assert kept["c:signal"] == "signal"
    # User-created nodes are protected even when their stats look like noise.
    assert kept["c:user"] == "user_created_protected"


def test_plan_concept_noise_skips_idf_cut_on_tiny_corpus():
    concepts = [{"id": "c:common", "label": "회의", "df": 3, "heuristic": True}]
    plan = plan_concept_noise_reduction(concepts, total_docs=3, min_corpus_docs=5)
    assert plan["remove"] == []  # 3 docs is too small to judge ubiquity


# ── store job ────────────────────────────────────────────────────────────────

def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _raw_edge(conn, from_node: str, to_node: str, edge_type: str) -> None:
    """Insert a pre-v4 legacy edge row directly (the v4 write door would
    normalize the verb at write time; old databases carry the raw strings)."""
    conn.execute(
        "INSERT OR IGNORE INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, 1.0, '{}', '2025-01-01T00:00:00')",
        (f"edge:raw:{from_node}:{to_node}:{edge_type}", from_node, to_node, edge_type),
    )


def _seed_noise(store: KnowledgeGraphStore) -> dict:
    """Six docs all mentioning one ubiquitous concept + one orphan concept
    + one user-created concept + raw pre-v4 Korean verb edges."""
    doc_ids = []
    with store._connect() as conn:
        for index in range(6):
            doc_id = f"doc:noise-{index}"
            store._upsert_node(conn, doc_id, "Document", f"문서 {index}",
                               summary="노이즈 감소 테스트 문서")
            doc_ids.append(doc_id)
        # Ubiquitous heuristic concept: linked from every doc (raw verb rows).
        store._upsert_node(conn, "concept:ubiq", "Concept", "내용",
                           metadata={"auto_extracted": True})
        for doc_id in doc_ids:
            _raw_edge(conn, doc_id, "concept:ubiq", "포함함")
        # Orphan heuristic concept: no content edges at all.
        store._upsert_node(conn, "concept:orphan", "Concept", "잔여물",
                           metadata={"auto_extracted": True})
        # Healthy heuristic concept: present in 2/6 docs.
        store._upsert_node(conn, "concept:signal", "Concept", "하이브리드 검색",
                           metadata={"auto_extracted": True})
        _raw_edge(conn, doc_ids[0], "concept:signal", "언급함")
        _raw_edge(conn, doc_ids[1], "concept:signal", "언급함")
        # User-created concept with noise-like stats: must never be removed.
        store._upsert_node(conn, "concept:user", "Concept", "내 수동 노드",
                           metadata={"created_by": "user"})
        # Legacy Korean verb edge that should normalize 만들다 → created.
        _raw_edge(conn, doc_ids[0], "concept:signal", "만들다")
        # A v4-canonical enum edge that the verb plan must leave untouched.
        store._upsert_edge(conn, doc_ids[2], "concept:signal", "MENTIONS")
    return {"doc_ids": doc_ids}


def test_curate_noise_dry_run_reports_without_mutating(tmp_path):
    store = _store(tmp_path)
    _seed_noise(store)
    report = store.curate_noise(dry_run=True)

    assert report["dry_run"] is True
    removed_ids = {item["id"] for item in report["remove"]}
    assert "concept:ubiq" in removed_ids       # low IDF (6/6 docs)
    assert "concept:orphan" in removed_ids     # below frequency floor
    assert "concept:signal" not in removed_ids
    assert "concept:user" not in removed_ids
    assert report["protected_user_nodes"] >= 1
    assert report["verb_normalizations"].get("만들다") == "created"
    assert report["verb_normalizations"].get("포함함") == "contains"
    # v4-canonical enum labels are schema taxonomy, never in the rename plan.
    assert "MENTIONS" not in report["verb_normalizations"]
    assert report["applied"] == {"removed_nodes": 0, "renamed_edges": 0}

    # Dry run mutated nothing.
    with store._connect() as conn:
        assert conn.execute("SELECT 1 FROM nodes WHERE id='concept:ubiq'").fetchone()
        assert conn.execute("SELECT 1 FROM edges WHERE type='만들다'").fetchone()


def test_curate_noise_apply_removes_noise_and_normalizes_verbs(tmp_path):
    store = _store(tmp_path)
    _seed_noise(store)
    report = store.curate_noise(dry_run=False)

    assert report["applied"]["removed_nodes"] == 2
    assert report["applied"]["renamed_edges"] >= 1
    with store._connect() as conn:
        assert conn.execute("SELECT 1 FROM nodes WHERE id='concept:ubiq'").fetchone() is None
        assert conn.execute("SELECT 1 FROM nodes WHERE id='concept:orphan'").fetchone() is None
        # Never delete non-heuristic nodes.
        assert conn.execute("SELECT 1 FROM nodes WHERE id='concept:user'").fetchone()
        assert conn.execute("SELECT 1 FROM nodes WHERE id='concept:signal'").fetchone()
        # Verb normalization landed and left no stale labels behind.
        assert conn.execute("SELECT 1 FROM edges WHERE type='만들다'").fetchone() is None
        assert conn.execute("SELECT 1 FROM edges WHERE type='created'").fetchone()
        # Rename collisions merged instead of erroring: 언급함 → mentions.
        assert conn.execute("SELECT 1 FROM edges WHERE type='언급함'").fetchone() is None
        # The v4-canonical enum edge is untouched.
        assert conn.execute("SELECT 1 FROM edges WHERE type='MENTIONS'").fetchone()

    # Job is idempotent: a second apply finds nothing left to do.
    again = store.curate_noise(dry_run=False)
    assert again["applied"]["removed_nodes"] == 0
    assert again["verb_normalizations"] == {}


# ── API endpoint ─────────────────────────────────────────────────────────────

def test_curate_noise_endpoint_defaults_to_dry_run(tmp_path):
    store = _store(tmp_path)
    _seed_noise(store)
    app = FastAPI()
    app.include_router(create_knowledge_graph_router(
        get_graph=lambda: store,
        require_graph=lambda: None,
        require_user=lambda request: "admin@example.com",
        static_dir=tmp_path,
    ))
    client = TestClient(app)

    r = client.post("/knowledge-graph/curate/noise", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["applied"]["removed_nodes"] == 0
    assert {item["id"] for item in body["remove"]} == {"concept:ubiq", "concept:orphan"}

    applied = client.post("/knowledge-graph/curate/noise", json={"dry_run": False}).json()
    assert applied["applied"]["removed_nodes"] == 2
