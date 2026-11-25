from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph._kg_common import (
    _chunks,
    _classify_node_type,
    _extract_concepts_rules,
)


def test_chunks_with_a_negative_overlap_skips_past_the_end_and_stops() -> None:
    assert _chunks("abcdefgh", size=3, overlap=-10) == ["abc"]


def test_chunks_of_blank_text_is_empty() -> None:
    assert _chunks("   ") == []


def test_extract_concepts_rules_drops_numeric_and_short_hyphen_terms() -> None:
    text = "The `12` marker and a-b tokens sit next to `Graph RAG` here."

    concepts = _extract_concepts_rules(text)

    lowered = {c.lower() for c in concepts}
    assert "graph rag" in lowered
    assert "12" not in lowered
    assert "a-b" not in lowered


def test_extract_concepts_rules_keeps_a_long_enough_hyphen_identifier() -> None:
    concepts = _extract_concepts_rules("we ship gpt-4o and a-b together")

    assert "gpt-4o" in {c.lower() for c in concepts}


def test_two_technical_words_are_not_classified_as_a_person() -> None:
    assert _classify_node_type("Test Mode", "flip the Test Mode switch") == "Concept"


def test_person_shape_with_non_technical_words_is_still_a_person() -> None:
    assert _classify_node_type("Grace Hopper", "Grace Hopper wrote it") == "Person"


def test_concept_absent_from_the_text_skips_the_context_window() -> None:
    assert _classify_node_type("Widget", "nothing matching here") == "Concept"
