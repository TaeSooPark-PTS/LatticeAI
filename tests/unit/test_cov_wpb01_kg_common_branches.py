"""wpb01 branch coverage — ``lattice_brain.graph._kg_common`` windowing + concepts.

Covers the loop/guard direction ``_chunks`` only takes with an out-of-range
overlap, plus the two rejection paths of the rule-based concept extractor and
the "classification found nothing" exits of ``_classify_node_type``.

The typed chunker these branches used to be reached through was removed in
11.8.0 — chunking is ``lattice-ingest``'s, pinned by
``rust/lattice-ingest/tests/chunking_parity.rs``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph._kg_common import (  # noqa: E402
    _chunks,
    _classify_node_type,
    _extract_concepts_rules,
)

# ── the chunk walk that ends by exhausting the string, not by the break ──────


def test_chunks_with_a_negative_overlap_skips_past_the_end_and_stops() -> None:
    """A negative overlap advances the cursor beyond the text: the walk ends.

    Nothing clamps ``_chunks``'s own overlap, so this is the only way its
    ``while`` condition — rather than the ``end >= len`` break — terminates
    the walk.
    """
    assert _chunks("abcdefgh", size=3, overlap=-10) == ["abc"]


def test_chunks_of_blank_text_is_empty() -> None:
    assert _chunks("   ") == []


# ── rule-based concept extraction rejections ────────────────────────────────


def test_extract_concepts_rules_drops_numeric_and_short_hyphen_terms() -> None:
    """A bare number and a 3-char hyphen token are both refused by ``_add``."""
    text = "The `12` marker and a-b tokens sit next to `Graph RAG` here."

    concepts = _extract_concepts_rules(text)

    lowered = {c.lower() for c in concepts}
    assert "graph rag" in lowered
    assert "12" not in lowered
    assert "a-b" not in lowered


def test_extract_concepts_rules_keeps_a_long_enough_hyphen_identifier() -> None:
    """The same loop's other direction — 4+ chars is kept — still holds."""
    concepts = _extract_concepts_rules("we ship gpt-4o and a-b together")

    assert "gpt-4o" in {c.lower() for c in concepts}


# ── node-type classification fallbacks ──────────────────────────────────────


def test_two_technical_words_are_not_classified_as_a_person() -> None:
    """"Test Mode" matches the First-Last shape but both words are technical."""
    assert _classify_node_type("Test Mode", "flip the Test Mode switch") == "Concept"


def test_person_shape_with_non_technical_words_is_still_a_person() -> None:
    assert _classify_node_type("Grace Hopper", "Grace Hopper wrote it") == "Person"


def test_concept_absent_from_the_text_skips_the_context_window() -> None:
    """No occurrence means no ±60-char window to classify from."""
    assert _classify_node_type("Widget", "nothing matching here") == "Concept"
