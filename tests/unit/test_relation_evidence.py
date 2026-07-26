"""Meaning edges vs adjacency edges (v9.9.6).

Review 2026-07-27 P1 #6: "그래프가 '의미 그래프'보다 '동시발생'에 가까운 구간이
남아 있음". A verb-less sentence used to produce a "관련됨" edge that looked
exactly like a real relation. The evidence class now travels with the edge, a
list-like sentence no longer manufactures a chain of relations, and the curator
can separate the two without guessing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph._kg_common import (
    COOCCURRENCE_CONCEPT_LIMIT,
    COOCCURRENCE_EDGE_WEIGHT,
    VERB_EDGE_WEIGHT,
    _extract_triples_rules,
    _infer_edge,
    infer_edge_relation,
)
from lattice_brain.graph.curator import plan_relation_noise_reduction
from lattice_brain.graph.ingest import _triple_edge_metadata


def test_verb_sentences_are_classified_as_semantic_evidence():
    relation = infer_edge_relation("FastAPI는 Pydantic을 사용한다")
    assert relation["relation"] == "사용함"
    assert relation["evidence"] == "verb"
    assert relation["weight"] == VERB_EDGE_WEIGHT


def test_verbless_sentences_are_classified_as_cooccurrence():
    relation = infer_edge_relation("FastAPI, Pydantic")
    assert relation["relation"] == "관련됨"
    assert relation["evidence"] == "cooccurrence"
    assert relation["weight"] == COOCCURRENCE_EDGE_WEIGHT
    # Weaker than any verb-backed edge — the graph can rank meaning first.
    assert relation["weight"] < VERB_EDGE_WEIGHT


def test_legacy_infer_edge_still_returns_the_label_only():
    assert _infer_edge("FastAPI는 Pydantic을 사용한다") == "사용함"
    assert _infer_edge("FastAPI, Pydantic") == "관련됨"


def test_verb_triples_carry_their_evidence_class():
    triples = _extract_triples_rules(
        "FastAPI는 Pydantic을 사용한다.", ["FastAPI", "Pydantic"]
    )
    assert len(triples) == 1
    assert triples[0]["evidence"] == "verb"
    assert triples[0]["weight"] == VERB_EDGE_WEIGHT


def test_enumeration_sentences_no_longer_manufacture_relation_chains():
    concepts = ["FastAPI", "Pydantic", "SQLite", "Uvicorn", "Ruff", "Pytest"]
    listed = ", ".join(concepts)
    assert len(concepts) > COOCCURRENCE_CONCEPT_LIMIT
    assert _extract_triples_rules(listed, concepts) == []
    # The same crowded sentence *with* a verb keeps its relations: the verb is
    # the evidence, not the count.
    with_verb = _extract_triples_rules(f"{listed}를 사용한다.", concepts)
    assert with_verb
    assert all(triple["evidence"] == "verb" for triple in with_verb)


def test_small_cooccurrence_sentences_are_still_recorded_but_weak():
    triples = _extract_triples_rules("FastAPI, Pydantic 정리", ["FastAPI", "Pydantic"])
    assert len(triples) == 1
    assert triples[0]["evidence"] == "cooccurrence"
    assert triples[0]["weight"] == COOCCURRENCE_EDGE_WEIGHT


def test_edge_metadata_keeps_evidence_and_confidence():
    metadata = _triple_edge_metadata({
        "context": "FastAPI는 Pydantic을 사용한다",
        "evidence": "verb",
        "confidence": 0.912345,
    })
    assert metadata["evidence"] == "verb"
    assert metadata["confidence"] == 0.9123
    assert metadata["context"].startswith("FastAPI")
    # Legacy triples without an evidence class do not gain a fabricated one.
    assert "evidence" not in _triple_edge_metadata({"context": "x"})


def _edge(**kw):
    base = {"id": "e", "from": "a", "to": "b", "type": "관련됨", "weight": 0.35, "degree": 1}
    base.update(kw)
    return base


def test_curator_never_demotes_a_verb_backed_edge():
    plan = plan_relation_noise_reduction([
        _edge(evidence="verb", weight=0.05, degree=99),
    ])
    assert plan["demote"] == []
    assert plan["keep"][0]["reason"] == "verb_evidence"


def test_curator_leaves_legacy_edges_alone_and_says_so():
    plan = plan_relation_noise_reduction([_edge(evidence="")])
    assert plan["demote"] == []
    assert plan["keep"][0]["reason"] == "unknown_evidence"


def test_curator_demotes_weak_and_hub_cooccurrence_edges():
    plan = plan_relation_noise_reduction([
        _edge(id="weak", evidence="cooccurrence", weight=0.1),
        _edge(id="hub", evidence="cooccurrence", weight=0.4, degree=40),
        _edge(id="ok", evidence="cooccurrence", weight=0.4, degree=2),
    ])
    reasons = {item["id"]: item["reason"] for item in plan["demote"]}
    assert reasons == {"weak": "weak_cooccurrence", "hub": "cooccurrence_hub"}
    assert [item["id"] for item in plan["keep"]] == ["ok"]


def test_curator_plan_is_pure_and_tolerates_garbage_numbers():
    plan = plan_relation_noise_reduction([
        _edge(evidence="cooccurrence", weight="n/a", degree="lots"),
    ])
    assert plan["demote"][0]["reason"] == "weak_cooccurrence"
    assert plan["demote"][0]["weight"] == 0.0
