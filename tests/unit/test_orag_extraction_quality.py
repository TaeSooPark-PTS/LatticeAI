"""Entity normalization, typed relations and section provenance (v12.0.0).

Every rule gets a Korean sample and an English one, because the two languages
break in opposite directions: English glues grammar in *front* of a name ("The
Vector Index") and Korean glues it *behind* one ("지식그래프에서"), and a rule
that only ever saw one of them ships the other's duplicates into the graph.
"""

from __future__ import annotations

import pytest

from lattice_brain.graph._kg_common.extraction import (
    _extract_concepts_rules,
    _extract_triples_rules,
    _semantic_items,
)
from lattice_brain.graph._kg_common.normalize import (
    entity_key,
    merge_entity_aliases,
    normalize_entity,
    occurrence_count,
    strip_particle,
)
from lattice_brain.graph._kg_common.patterns import concept_positions, typed_relation
from lattice_brain.graph._kg_common.sections import (
    heading_at,
    heading_spans,
    leading_offset,
    with_section,
)

# ── surface normalization ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  “Lattice   AI”  ", "Lattice AI"),
        ("(Graph RAG)", "Graph RAG"),
        ("『지식그래프』", "지식그래프"),
        ("Anthropic's", "Anthropic"),
        ("GPT-4o.", "GPT-4o"),
        ("ＡＩ 모델", "AI 모델"),
        ("", ""),
        ("   ", ""),
        ("...", ""),
    ],
)
def test_normalize_entity_trims_without_inventing(raw: str, expected: str) -> None:
    assert normalize_entity(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("지식그래프에서", "지식그래프"),
        ("플랫폼을", "플랫폼"),
        ("데이터베이스로부터", "데이터베이스"),
        ("모델까지", "모델"),
        ("에이전트처럼", "에이전트"),
    ],
)
def test_unconditional_particles_are_stripped(raw: str, expected: str) -> None:
    assert strip_particle(raw) == expected


@pytest.mark.parametrize("word", ["고양이", "전문가", "정확도", "결과", "회의", "경로"])
def test_words_that_merely_end_in_a_particle_survive(word: str) -> None:
    """The failure mode this whole tier system exists to prevent."""
    assert strip_particle(word) == word
    assert strip_particle(word, f"{word}가 있다") == word


def test_single_syllable_particles_need_the_text_to_agree() -> None:
    # With no corroboration nothing is stripped…
    assert strip_particle("플랫폼이") == "플랫폼이"
    # …and with the bare stem elsewhere in the passage, it is.
    assert strip_particle("플랫폼이", "플랫폼이 있고 플랫폼도 크다") == "플랫폼"
    # A stem that only ever appears with this ending is part of the word.
    assert strip_particle("정확도", "정확도가 높고 정확도를 잰다") == "정확도"


def test_entity_key_folds_case_and_separator_style() -> None:
    assert entity_key("Graph RAG") == entity_key("graph-rag") == entity_key("Graph_RAG")
    assert entity_key("Lattice AI") != entity_key("Lattice AGI")
    assert entity_key("  ") == ""


def test_merge_picks_the_spelling_the_document_actually_uses() -> None:
    text = "Lattice AI ships. Lattice AI grows. lattice ai once."
    assert merge_entity_aliases(["lattice ai", "Lattice AI", "LATTICE AI"], text) == [
        "Lattice AI"
    ]
    # Korean surfaces merge through their particles.
    korean = "지식그래프는 노드를 담는다. 지식그래프에서 찾는다."
    assert merge_entity_aliases(["지식그래프에서", "지식그래프"], korean) == ["지식그래프"]
    # Nothing to merge is not an error; empties simply drop out.
    assert merge_entity_aliases(["", "   ", "..."]) == []


def test_occurrence_count_is_case_insensitive_and_safe() -> None:
    assert occurrence_count("rag", "RAG and rag and Rag") == 3
    assert occurrence_count("", "anything") == 0
    assert occurrence_count("x", "") == 0


# ── concept extraction: one entity, one node ─────────────────────────────────


def test_a_name_inside_a_longer_name_is_not_a_second_concept() -> None:
    text = (
        "Graph RAG is the retrieval layer. Graph RAG reads the Vector Index, "
        "and the Vector Index is rebuilt nightly."
    )
    concepts = _extract_concepts_rules(text, limit=12)
    lowered = {concept.lower() for concept in concepts}
    assert "graph rag" in lowered
    assert "vector index" in lowered
    # "RAG", "Vector" and "Index" never appear outside those names.
    assert "rag" not in lowered
    assert "vector" not in lowered
    assert "index" not in lowered


def test_a_korean_particle_does_not_hide_the_english_name_it_follows() -> None:
    """`Lattice AI는` used to leave both `Lattice AI` *and* `Lattice`."""
    concepts = _extract_concepts_rules(
        "Lattice AI는 로컬에서 동작한다. Lattice AI는 그래프를 쓴다.", limit=12
    )
    lowered = {concept.lower() for concept in concepts}
    assert "lattice ai" in lowered
    assert "lattice" not in lowered


def test_a_sentence_opener_is_not_part_of_the_name() -> None:
    concepts = _extract_concepts_rules(
        "The Vector Index is warm. Unlike Keyword Search, it is fast.", limit=12
    )
    lowered = {concept.lower() for concept in concepts}
    assert "vector index" in lowered
    assert "the vector index" not in lowered
    assert "unlike keyword search" not in lowered


def test_a_heading_does_not_glue_itself_to_the_next_line() -> None:
    concepts = _extract_concepts_rules("# Retrieval\n\nLattice AI ships today.", 12)
    assert "Retrieval Lattice AI" not in concepts
    assert "Lattice AI" in concepts


def test_korean_terms_before_the_wider_particle_set_are_found() -> None:
    concepts = _extract_concepts_rules("임베딩모델은 벡터스토어에 의존한다.", limit=12)
    assert "벡터스토어" in concepts
    assert "임베딩모델" in concepts


# ── typed, directed relations ────────────────────────────────────────────────


def _relations(text: str) -> set:
    concepts = _extract_concepts_rules(text, limit=14)
    return {
        (triple["subject"], triple["relation"], triple["object"], triple["evidence"])
        for triple in _extract_triples_rules(text, concepts)
    }


def test_english_definition_becomes_a_definition_edge() -> None:
    assert ("Lattice AI", "설명함", "Digital Brain", "definition") in _relations(
        "Lattice AI is a Digital Brain that runs locally."
    )


def test_korean_definition_becomes_a_definition_edge() -> None:
    found = _relations("지식그래프란 노드와 엣지로 이루어진 자료구조이다.")
    assert any(relation == "설명함" and evidence == "definition" for _, relation, _, evidence in found)


def test_english_part_of_points_from_the_part_to_the_whole() -> None:
    assert ("Vector Index", "구성요소", "Graph RAG", "structure") in _relations(
        "The Vector Index is part of Graph RAG."
    )


def test_a_whole_first_sentence_still_produces_a_part_to_whole_edge() -> None:
    assert ("Vector Index", "구성요소", "Graph RAG", "structure") in _relations(
        "Graph RAG consists of Vector Index today."
    )


def test_korean_part_of_reads_the_particle_glued_to_the_second_noun() -> None:
    assert ("GraphWriter", "구성요소", "SQLite", "structure") in _relations(
        "GraphWriter는 SQLite의 일부이다."
    )


def test_contrast_becomes_a_contradiction_edge_in_both_languages() -> None:
    assert ("Keyword Search", "상충함", "Vector Search", "contrast") in _relations(
        "We ship Keyword Search instead of Vector Search."
    )
    assert ("키워드검색", "상충함", "벡터검색", "contrast") in _relations(
        "하이브리드검색은 키워드검색이 아니라 벡터검색을 쓴다."
    )


def test_a_korean_subject_reaches_its_object_across_a_clause() -> None:
    """SOV: the particles decide the direction, the tail verb names the edge."""
    assert ("하이브리드검색", "사용함", "벡터검색", "verb") in _relations(
        "하이브리드검색은 키워드검색이 아니라 벡터검색을 쓴다."
    )


def test_a_pattern_silences_the_coarse_pairing_around_it() -> None:
    """The rejected alternative must not also become "A uses B"."""
    found = _relations("하이브리드검색은 키워드검색이 아니라 벡터검색을 쓴다.")
    assert ("하이브리드검색", "사용함", "키워드검색", "verb") not in found


def test_direction_survives_a_reversed_english_sentence() -> None:
    triples = _extract_triples_rules(
        "Graph RAG depends on Vector Index.", ["Graph RAG", "Vector Index"]
    )
    assert triples[0]["subject"] == "Graph RAG"
    assert triples[0]["relation"] == "의존함"


def test_a_name_nested_in_another_is_never_its_own_counterparty() -> None:
    triples = _extract_triples_rules(
        "Graph RAG uses SQLite here.", ["Graph RAG", "RAG", "SQLite"]
    )
    assert all("RAG" != triple["subject"] for triple in triples)
    assert {triple["object"] for triple in triples} == {"SQLite"}


def test_typed_relation_declines_when_nothing_matches() -> None:
    sentence = "Alpha Bravo sit here."
    positions = concept_positions(sentence, ["Alpha", "Bravo"])
    assert typed_relation(sentence, positions[0], positions[1]) is None
    # A non-adjacent pair is only ever the Korean subject→object rule.
    assert typed_relation(sentence, positions[0], positions[1], adjacent=False) is None


# ── section provenance ───────────────────────────────────────────────────────

_SECTIONED = (
    "# 아키텍처\n\n"
    "머리말 문장이다.\n\n"
    "## 저장소\n\n"
    "Lattice AI는 GraphWriter를 사용한다.\n"
    "TODO: 임베딩 자동 배선을 구현한다.\n"
)


def test_heading_spans_build_the_path_the_chunker_builds() -> None:
    spans = heading_spans(_SECTIONED)
    assert [path for _, _, path in spans] == ["아키텍처", "아키텍처 > 저장소"]
    assert heading_at(spans, 0) == "아키텍처"
    assert heading_at(spans, len(_SECTIONED) - 1) == "아키텍처 > 저장소"
    # Text before the first heading belongs to no section.
    assert heading_at(heading_spans("no headings here"), 0) == ""


def test_a_triple_context_names_the_section_it_came_from() -> None:
    concepts = _extract_concepts_rules(_SECTIONED, limit=12)
    triples = _extract_triples_rules(_SECTIONED, concepts)
    assert triples
    assert all(
        triple["context"].startswith("[아키텍처 > 저장소] ") for triple in triples
    )


def test_a_semantic_item_carries_its_section() -> None:
    items = _semantic_items(_SECTIONED)
    tasks = [item for item in items if item["type"] == "Task"]
    assert tasks and tasks[0]["section"] == "아키텍처 > 저장소"
    # A document with no headings claims no section rather than an empty one.
    plain = _semantic_items("TODO: implement the extract seam now.")
    assert plain and "section" not in plain[0]


def test_with_section_leaves_an_unsectioned_context_alone() -> None:
    assert with_section("a fact", "") == "a fact"
    assert with_section("a fact", "  ") == "a fact"
    assert with_section("a fact", "A > B") == "[A > B] a fact"


def test_leading_offset_reports_what_strip_removed() -> None:
    assert leading_offset("  hi ", "hi") == 2
    assert leading_offset("   ", "") is None
