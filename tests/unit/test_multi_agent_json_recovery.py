"""Recovering a plan from a weak local model's JSON.

The runtime's honesty contract says an unparseable plan fails the run loudly
rather than falling back to a fabricated one — that is right, and these tests
do not weaken it. What they cover is the step before: a 1-4B model that *did*
write a correct object should not have it thrown away because it also wrote a
closing sentence, or a trailing comma, or a reasoning scratchpad full of
braces. Recovery only, never invention.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.runtime.multi_agent import _extract_json_object  # noqa: E402

PLAN = {"steps": ["read", "write"], "summary": "do the thing"}


def test_plain_object():
    assert _extract_json_object('{"steps": ["read", "write"], "summary": "do the thing"}') == PLAN


def test_fenced_object():
    raw = 'Here is the plan:\n```json\n{"steps": ["read", "write"], "summary": "do the thing"}\n```'
    assert _extract_json_object(raw) == PLAN


def test_object_followed_by_a_closing_remark():
    """`find("{")`..`rfind("}")` swallowed the remark and parsed as neither."""
    raw = '{"steps": ["read", "write"], "summary": "do the thing"}\n\nLet me know if you want changes!'
    assert _extract_json_object(raw) == PLAN


def test_object_between_two_sentences_containing_braces():
    raw = (
        "I considered using { } notation here.\n"
        '{"steps": ["read", "write"], "summary": "do the thing"}\n'
        "That should cover it }"
    )
    assert _extract_json_object(raw) == PLAN


def test_trailing_comma_is_repaired():
    """The single most common reason a correct plan was discarded."""
    raw = '{"steps": ["read", "write",], "summary": "do the thing",}'
    assert _extract_json_object(raw) == PLAN


def test_reasoning_scratchpad_is_dropped_before_scanning():
    raw = (
        "<think>Maybe {\"steps\": [\"guess\"]} would work? No, let me reconsider.</think>\n"
        '{"steps": ["read", "write"], "summary": "do the thing"}'
    )
    assert _extract_json_object(raw) == PLAN


def test_unterminated_reasoning_block_does_not_eat_the_answer():
    # A model that hit its token cap mid-thought leaves the tag open; the
    # scratchpad is then everything to the end and must not be parsed.
    raw = '{"steps": ["read", "write"], "summary": "do the thing"}\n<think>hmm { unbalanced'
    assert _extract_json_object(raw) == PLAN


def test_braces_inside_string_values_do_not_split_the_object():
    raw = '{"steps": ["write } and { chars"], "summary": "do the thing"}'
    assert _extract_json_object(raw)["steps"] == ["write } and { chars"]


def test_escaped_quote_inside_a_string_value():
    raw = '{"summary": "he said \\"hi\\" }", "steps": []}'
    assert _extract_json_object(raw)["summary"] == 'he said "hi" }'


def test_first_complete_object_wins_when_the_model_wrote_two():
    raw = '{"steps": ["read", "write"], "summary": "do the thing"}\n{"steps": ["ignored"]}'
    assert _extract_json_object(raw) == PLAN


# ── the honesty contract is unchanged ───────────────────────────────────


@pytest.mark.parametrize("raw", ["", "   ", "I cannot help with that.", "no json here"])
def test_nothing_parseable_still_raises(raw):
    with pytest.raises(ValueError):
        _extract_json_object(raw)


def test_a_json_array_is_not_an_object():
    with pytest.raises(ValueError):
        _extract_json_object('["steps", "are", "not", "an", "object"]')


def test_irreparably_broken_json_still_raises():
    # Recovery must not become guessing: a truncated object has no plan in it.
    with pytest.raises(ValueError):
        _extract_json_object('{"steps": ["read"')


def test_an_empty_object_is_only_the_answer_when_nothing_else_is():
    # It is still a legal reply on its own...
    assert _extract_json_object("{}") == {}
    # ...but it must not beat a real plan that appears later in the reply.
    raw = 'Options are {} or the plan below.\n{"steps": ["read", "write"], "summary": "do the thing"}'
    assert _extract_json_object(raw) == PLAN
