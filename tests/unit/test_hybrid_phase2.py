"""Phase 2 hybrid unit tests: token guard, extraction, mode resolution."""

from __future__ import annotations

from latticeai.core.network_boundary import NetworkBoundaryMode, normalize_network_mode
from latticeai.services.cloud_extraction import (
    extract_candidates,
    plan_kg_expansion_rich,
)
from latticeai.services.cloud_streaming import CloudTurnResult
from latticeai.services.cloud_token_guard import TokenBudget, budget_for, reset_budget


def test_token_budget_blocks_oversized_turn():
    b = TokenBudget(max_tokens_per_turn=100, max_tokens_per_session=1000)
    assert b.check_turn(50) is None
    assert b.check_turn(250) is not None


def test_token_budget_session_cap():
    b = TokenBudget(max_tokens_per_turn=500, max_tokens_per_session=600)
    b.record(400)
    assert b.check_turn(300) is not None
    assert b.check_turn(100) is None


def test_budget_for_is_scoped():
    reset_budget("u1|ws")
    reset_budget("u2|ws")
    a = budget_for("u1|ws")
    a.record(10)
    b = budget_for("u2|ws")
    assert b.session_used == 0
    assert a.session_used == 10


def test_extract_decision_and_task():
    answer = (
        "Decision: Ship hybrid mode first.\n"
        "- [ ] Write Phase 2 tests\n"
        "**NetworkBoundaryMode** is the dial.\n"
    )
    cands = extract_candidates(answer)
    types = {c["type"] for c in cands}
    assert "Decision" in types
    assert "Task" in types or "Concept" in types


def test_rich_plan_stages_candidates():
    result = CloudTurnResult(
        user_message="next steps?",
        answer_text="Decision: enable cloud preview.\n1. Add UI toggle",
        sent_node_ids=["n1"],
        provider="test",
        model="m",
    )
    plan = plan_kg_expansion_rich(result)
    assert plan.auto_commit is False
    assert len(plan.new_nodes) >= 2  # turn + at least one candidate
    assert any(e["type"] == "grounded_on" for e in plan.new_edges)


def test_normalize_request_override():
    assert normalize_network_mode("cloud_allowed") == NetworkBoundaryMode.CLOUD_ALLOWED
