"""Single retrieval policy: deterministic query rewrite + class resolution.

Review 2026-07-25 Wave 0.2 ("RetrievalPolicy 단일화" / "query rewrite" /
"recency age decay"): ``lattice_brain.graph.retrieval_policy`` is the one
module both hybrid fusion layers consult. Covers the conservative ko/en
filler-strip rules (code queries untouched, >= 4-char remainder guard, the
``LATTICEAI_QUERY_REWRITE=0`` kill-switch, never raises) and
``resolve_policy`` composition (fact class byte-identical to the default
fusion weights with no half-life; recency class carries the 14-day
half-life that gates age decay).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.fusion import DEFAULT_FUSION_WEIGHTS
from lattice_brain.graph.retrieval_policy import (
    QUERY_REWRITE_ENV,
    RECENCY_HALF_LIFE_DAYS,
    resolve_policy,
    rewrite_query,
)


# ── rewrite_query ────────────────────────────────────────────────────────────


def test_rewrite_strips_trailing_korean_filler():
    result = rewrite_query("어제 회의 내용 좀 알려줘")
    assert result["original"] == "어제 회의 내용 좀 알려줘"
    assert result["rewritten"] == "어제 회의 내용"
    assert "strip_filler_ko" in result["rules"]


def test_rewrite_strips_leading_english_filler():
    result = rewrite_query("what is the release decision")
    assert result["rewritten"] == "the release decision"
    assert "strip_filler_en_leading" in result["rules"]

    trailing = rewrite_query("summarize the deploy log, please")
    assert trailing["rewritten"] == "summarize the deploy log"
    assert "strip_filler_en_trailing" in trailing["rules"]


def test_rewrite_collapses_repeated_whitespace():
    result = rewrite_query("출시   결정 \n 내용")
    assert result["rewritten"] == "출시 결정 내용"
    assert result["rules"] == ["collapse_whitespace"]


def test_rewrite_keeps_code_queries_untouched():
    """Exact identifiers are the retrieval signal — no filler stripping."""
    result = rewrite_query("ingest_folder recursive 버그 알려줘")
    assert result["rewritten"] == "ingest_folder recursive 버그 알려줘"
    assert result["rules"] == []

    spaced = rewrite_query("hybrid_search   alpha 융합")
    assert spaced["rewritten"] == "hybrid_search alpha 융합"
    assert spaced["rules"] == ["collapse_whitespace"]


def test_rewrite_guards_short_remainders():
    """Stripping that would leave < 4 chars is never applied."""
    result = rewrite_query("이게 뭐야")
    assert result["rewritten"] == "이게 뭐야"
    assert result["rules"] == []


def test_rewrite_kill_switch_disables_rewrite(monkeypatch):
    monkeypatch.setenv(QUERY_REWRITE_ENV, "0")
    result = rewrite_query("어제 회의 내용 좀 알려줘")
    assert result["rewritten"] == result["original"] == "어제 회의 내용 좀 알려줘"
    assert result["rules"] == []


def test_rewrite_never_raises_and_handles_empty():
    assert rewrite_query("") == {"original": "", "rewritten": "", "rules": []}
    assert rewrite_query("   ") == {"original": "", "rewritten": "", "rules": []}
    assert rewrite_query(None) == {"original": "", "rewritten": "", "rules": []}


# ── resolve_policy ───────────────────────────────────────────────────────────


def test_resolve_policy_fact_class_matches_default_weights_without_half_life():
    """Fact class must stay byte-compatible: default weights, no decay."""
    policy = resolve_policy("출시 결정 내용")
    fact = DEFAULT_FUSION_WEIGHTS["fact"]
    assert policy["query_class"] == "fact"
    assert policy["weights"] == {
        "keyword": fact["keyword"],
        "vector": fact["vector"],
        "graph": fact["graph"],
    }
    assert policy["alpha"] == fact["alpha"]
    assert policy["recency_half_life_days"] is None
    assert policy["search_query"] == policy["original_query"] == "출시 결정 내용"
    assert policy["rewrite_rules"] == []


def test_resolve_policy_recency_class_carries_half_life_and_rewrite():
    policy = resolve_policy("어제 회의 내용 좀 알려줘")
    assert policy["query_class"] == "recency"
    assert policy["recency_half_life_days"] == RECENCY_HALF_LIFE_DAYS == 14.0
    assert policy["original_query"] == "어제 회의 내용 좀 알려줘"
    assert policy["search_query"] == "어제 회의 내용"
    assert "strip_filler_ko" in policy["rewrite_rules"]


def test_resolve_policy_kill_switch_keeps_search_query_original(monkeypatch):
    monkeypatch.setenv(QUERY_REWRITE_ENV, "0")
    policy = resolve_policy("어제 회의 내용 좀 알려줘")
    assert policy["search_query"] == policy["original_query"]
    assert policy["rewrite_rules"] == []
    # Class resolution is untouched by the rewrite kill-switch.
    assert policy["query_class"] == "recency"
