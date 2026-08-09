"""wp23 coverage — ``lattice_brain.graph.curator`` decision helpers.

Pure, dependency-free functions: secret masking, tokenization, topic-candidate
extraction and its diversity penalty, alias indexing, the promotion gate's four
refusal reasons, derived thread stories, and the relation-verb normalizer's
josa-stripped lookup.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import curator


def _candidate(label: str, score: float, sources) -> curator.TopicCandidate:
    return curator.TopicCandidate(label=label, score=score, sources=list(sources))


# ── secrets / tokenization ───────────────────────────────────────────────────


def test_secret_helpers_short_circuit_on_empty_text() -> None:
    assert curator.contains_secret("") is False
    assert curator.mask_secrets("") == ""


def test_secret_helpers_still_detect_and_mask_real_material() -> None:
    text = "password: hunter2 stays out of the graph"

    assert curator.contains_secret(text) is True
    assert "hunter2" not in curator.mask_secrets(text)


def test_tokenize_of_empty_text_is_empty() -> None:
    assert curator._tokenize("") == []


# ── topic candidates ─────────────────────────────────────────────────────────


def test_topic_candidate_to_dict_sorts_aliases() -> None:
    candidate = curator.TopicCandidate(
        label="graph", score=1.25, sources=["d1"], aliases={"b", "a"}
    )

    assert candidate.to_dict() == {
        "label": "graph",
        "score": 1.25,
        "sources": ["d1"],
        "aliases": ["a", "b"],
    }


def test_extract_candidates_boosts_tasks_and_skips_tokenless_documents() -> None:
    documents = [
        {"id": "d1", "text": "retrieval pipeline retrieval pipeline", "kind": "task"},
        {"id": "d2", "text": "retrieval pipeline tuning", "kind": "task"},
        {"id": "d3", "text": "!!! ??? ...", "kind": "chat"},
    ]

    candidates = curator.extract_topic_candidates(documents)

    labels = {c.label for c in candidates}
    assert "retrieval" in labels
    # the punctuation-only document contributed no sources at all
    for candidate in candidates:
        assert "d3" not in candidate.sources


def test_single_source_repetition_is_penalised_against_multi_source_terms() -> None:
    documents = [
        {"id": "same", "text": "alpha alpha beta"},
        {"id": "same", "text": "alpha alpha gamma"},
        {"id": "one", "text": "delta epsilon"},
        {"id": "two", "text": "delta epsilon"},
    ]

    scores = {c.label: c.score for c in curator.extract_topic_candidates(documents)}

    # "alpha" and "delta" both appear in two documents, but "alpha" only ever
    # in one *distinct* source, so the diversity penalty ranks it lower.
    assert scores["alpha"] < scores["delta"]


def test_build_alias_index_skips_empty_groups() -> None:
    index = curator.build_alias_index([[], ["Canon Label", "canon-alias"]])

    assert index == {"canon label": "canon label", "canon-alias": "canon label"}


# ── promotion gate ───────────────────────────────────────────────────────────


def test_promotion_refuses_a_secret_bearing_label() -> None:
    decision = curator.should_promote(
        _candidate("api_key: sk-abcdefghijklmnopqrstuvwxyz", 9.0, ["d1", "d2"])
    )

    assert decision.promote is False
    assert decision.reason == "contains secret"
    assert decision.importance == 0.0


def test_promotion_refuses_a_single_source_candidate() -> None:
    decision = curator.should_promote(_candidate("graph", 9.0, ["d1", "d1"]))

    assert decision.promote is False
    assert decision.reason == "too few sources"


def test_promotion_refuses_a_one_character_label() -> None:
    decision = curator.should_promote(_candidate("a", 9.0, ["d1", "d2"]))

    assert decision.promote is False
    assert decision.reason == "label too short"


def test_promotion_refuses_a_candidate_below_the_importance_floor() -> None:
    decision = curator.should_promote(_candidate("graph", 0.5, ["d1", "d2"]))

    assert decision.promote is False
    assert decision.reason == "importance below threshold"
    assert decision.importance == 0.5


def test_promotion_accepts_a_multi_source_significant_candidate() -> None:
    decision = curator.should_promote(_candidate("graph", 2.5, ["d1", "d2"]))

    assert decision.promote is True
    assert decision.reason == "promoted"


# ── thread stories ───────────────────────────────────────────────────────────


def test_thread_edge_to_dict_carries_its_evidence() -> None:
    edge = curator.ThreadEdge(
        source="a", target="b", story="s", evidence=["e1"], created_at=12.0
    )

    assert edge.to_dict() == {
        "source": "a",
        "target": "b",
        "story": "s",
        "evidence": ["e1"],
        "created_at": 12.0,
    }


def test_derive_thread_story_falls_back_when_no_snippet_fits() -> None:
    story = curator.derive_thread_story(
        "출발", "도착", snippets=["", "hi", "x" * 400]
    )

    assert story == "출발에서 도착로 이어지는 흐름이 발견되었습니다."


def test_derive_thread_story_joins_the_first_two_usable_sentences() -> None:
    story = curator.derive_thread_story(
        "출발",
        "도착",
        snippets=[
            "First usable sentence. trailing noise",
            "Second usable sentence.",
            "Third usable sentence.",
        ],
    )

    assert story == "First usable sentence. Second usable sentence"
    assert "Third" not in story


# ── relation verbs ───────────────────────────────────────────────────────────


def test_normalize_relation_verb_strips_a_trailing_josa_before_lookup() -> None:
    assert curator.normalize_relation_verb("생성함을") == "created"
    # a label the dictionary does not know passes through unchanged
    assert curator.normalize_relation_verb("완전히-모르는-동사") == "완전히-모르는-동사"


# ── end-to-end overlay ───────────────────────────────────────────────────────


def test_overlay_reports_skipped_candidates_with_their_reason() -> None:
    documents = [
        {"id": "only", "text": "orchestration lattice orchestration lattice"},
        {"id": "only", "text": "orchestration lattice tuning"},
    ]

    overlay = curator.auto_build_graph_overlay(documents)

    assert overlay["promotions"] == []
    assert overlay["candidates_total"] > 0
    reasons = {entry["reason"] for entry in overlay["skipped"]}
    assert reasons == {"too few sources"}


def test_overlay_stops_promoting_once_max_new_nodes_is_reached() -> None:
    documents = [
        {"id": "d1", "text": "orchestration lattice retrieval curation"},
        {"id": "d2", "text": "orchestration lattice retrieval curation"},
    ]

    overlay = curator.auto_build_graph_overlay(documents, max_new_nodes=1)

    assert len(overlay["promotions"]) == 1
    assert "max_new_nodes reached" in {e["reason"] for e in overlay["skipped"]}
