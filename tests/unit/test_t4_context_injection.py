"""Self-Model injection into the document-generation context (Track 4).

The seam is opt-in and additive. Three things must hold, or it is not safe to
have it on by default:

* an empty Self-Model changes nothing — same markdown, same trace, byte for
  byte as before the feature existed;
* the profile never spends more than half the context budget, and the
  assembled context still fits the budget the caller asked for;
* the existing contract (keys, ``context_quality`` shape, stats method) is
  untouched.
"""

from __future__ import annotations

from lattice_brain import self_model as sm
from lattice_brain.context import approx_tokens
from latticeai.core import context_builder as cb
from tests.unit.test_t2_support import make_store

CONTRACT_KEYS = {"query", "context_markdown", "sources", "stats", "context_quality", "trace"}


def _brain(tmp_path):
    store = make_store(tmp_path)
    with store._connect() as conn:
        store._upsert_node(
            conn, "doc:1", "Document", "예산 보고서", "2026년 예산 계획 요약",
            metadata={"filename": "budget.md"},
        )
    return store


def test_an_empty_self_model_injects_nothing(tmp_path):
    store = _brain(tmp_path)

    result = cb.retrieve_context_for_generation(store, "예산")

    assert set(result) == CONTRACT_KEYS
    assert result["stats"]["method"] == "hybrid"
    assert result["context_markdown"].startswith("### 📄")
    assert [section["source"] for section in result["trace"]["sections"]] == ["knowledge"]


def test_a_populated_self_model_rides_along_with_the_knowledge(tmp_path):
    store = _brain(tmp_path)
    sm.upsert_self_model_fact(store, kind="preference", text="표보다 그래프")

    result = cb.retrieve_context_for_generation(store, "예산")

    assert set(result) == CONTRACT_KEYS  # contract unchanged
    assert result["stats"]["method"] == "hybrid"
    assert "사용자 프로필" in result["context_markdown"]
    assert "표보다 그래프" in result["context_markdown"]
    assert result["context_markdown"].index("사용자 프로필") < result[
        "context_markdown"
    ].index("관련 문서/파일")
    sections = result["trace"]["sections"]
    assert sections[0]["source"] == cb.SELF_MODEL_TRACE_SOURCE
    assert sections[0]["approx_tokens"] > 0
    assert result["trace"]["used_approx_tokens"] == approx_tokens(
        result["context_markdown"]
    )


def test_the_profile_can_be_turned_off(tmp_path):
    store = _brain(tmp_path)
    sm.upsert_self_model_fact(store, kind="preference", text="표보다 그래프")

    off = cb.retrieve_context_for_generation(store, "예산", include_self_model=False)

    assert "사용자 프로필" not in off["context_markdown"]
    assert [section["source"] for section in off["trace"]["sections"]] == ["knowledge"]


def test_the_lexical_fallback_carries_the_profile_too(tmp_path):
    store = _brain(tmp_path)
    sm.upsert_self_model_fact(store, kind="preference", text="표보다 그래프")

    result = cb.retrieve_context_for_generation(store, "존재하지않는질의zzz")

    assert result["stats"]["method"] == "fallback"
    assert "표보다 그래프" in result["context_markdown"]
    assert result["trace"]["sections"][0]["source"] == cb.SELF_MODEL_TRACE_SOURCE
    assert result["trace"]["sections"][1]["name"] == "Knowledge (fallback)"


def test_the_profile_never_outgrows_its_share_of_the_budget(tmp_path):
    store = _brain(tmp_path)
    for index in range(12):
        sm.upsert_self_model_fact(
            store, kind="preference", text=f"아주 긴 선호 문장 번호 {index} " * 3
        )

    tight = cb.retrieve_context_for_generation(store, "예산", budget=40)

    assert approx_tokens(tight["context_markdown"]) <= 40
    profile_tokens = tight["trace"]["sections"][0]["approx_tokens"]
    assert profile_tokens <= 20  # half of 40, never more


def test_half_the_budget_is_the_ceiling_and_zero_means_no_profile():
    assert cb._self_model_budget(0, 200) == 200  # unbounded caller keeps the cap
    assert cb._self_model_budget(-5, 200) == 200
    assert cb._self_model_budget(1000, 200) == 200
    assert cb._self_model_budget(40, 200) == 20
    assert cb._self_model_budget(1, 200) == 0


def test_a_zero_allowance_injects_nothing(tmp_path):
    store = _brain(tmp_path)
    sm.upsert_self_model_fact(store, kind="preference", text="표보다 그래프")

    assert cb._self_model_block(
        store, enabled=True, budget=1, limit_tokens=200, allowed_workspaces=None
    ) == ""
    assert cb._self_model_block(
        store, enabled=False, budget=2000, limit_tokens=200, allowed_workspaces=None
    ) == ""


def test_another_workspaces_profile_is_not_injected(tmp_path):
    store = _brain(tmp_path)
    sm.upsert_self_model_fact(
        store, kind="preference", text="표보다 그래프", workspace_id="team-a"
    )

    mine = cb.retrieve_context_for_generation(
        store, "예산", allowed_workspaces={"team-a"}
    )
    theirs = cb.retrieve_context_for_generation(
        store, "예산", allowed_workspaces={"team-b"}
    )

    assert "표보다 그래프" in mine["context_markdown"]
    assert "표보다 그래프" not in theirs["context_markdown"]


def test_a_brain_with_nothing_to_search_still_answers_the_old_contract(tmp_path):
    empty = cb.retrieve_context_for_generation(None, "질문")

    assert set(empty) == CONTRACT_KEYS
    assert empty["context_markdown"] == ""
