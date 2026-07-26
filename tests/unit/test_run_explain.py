"""Plain-language run explanation (v9.9.6).

Review 2026-07-27 P0 #3: "약모델 실패 설명 — repairs / 보정 횟수를 사용자
언어로 한 줄 더 친절하게". House rules verified here: the explanation is
deterministic, never upgrades a non-success into a success, and degrades to a
usable payload instead of raising when the run data is partial.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.agent import AgentState
from latticeai.core.run_explain import explain_run


def _loop(**kw):
    base = {
        "parse_errors": 0,
        "parse_recovered": 0,
        "corrections": 0,
        "retries": 0,
        "repairs": {},
    }
    base.update(kw)
    return base


def test_done_run_is_the_only_ok_outcome():
    result = explain_run(
        state=AgentState.DONE,
        loop=_loop(),
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
    )
    assert result["code"] == "done"
    assert result["ok"] is True
    assert result["headline"]["ko"] and result["headline"]["en"]


def test_pass_without_evidence_explains_why_it_is_not_complete():
    result = explain_run(
        state=AgentState.NEEDS_REVIEW,
        loop=_loop(),
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": False}],
    )
    assert result["code"] == "no_evidence"
    assert result["ok"] is False
    # Honest: the sentence must not read as a success.
    assert "완료" in result["headline"]["ko"]
    assert "not marked complete" in result["headline"]["en"]


def test_unreadable_verifier_is_distinguished_from_a_bad_verdict():
    unavailable = explain_run(
        state=AgentState.NEEDS_REVIEW,
        loop=_loop(),
        transcript=[{"state": "VERIFYING", "verdict": "UNAVAILABLE"}],
    )
    inconsistent = explain_run(
        state=AgentState.NEEDS_REVIEW,
        loop=_loop(),
        transcript=[{"state": "VERIFYING", "verdict": "FAIL", "next_state": "DONE"}],
    )
    assert unavailable["code"] == "verifier_unavailable"
    assert inconsistent["code"] == "inconsistent_verdict"


def test_failure_codes_separate_rollback_approval_and_retry_budget():
    rolled = explain_run(
        state=AgentState.FAILED,
        loop=_loop(),
        transcript=[{"state": "ROLLBACK", "rolled_back": []}],
    )
    approval = explain_run(
        state=AgentState.FAILED,
        loop=_loop(),
        transcript=[{
            "state": "WAITING_APPROVAL",
            "decision": "blocked_pending_approval",
        }],
    )
    retried = explain_run(
        state=AgentState.FAILED,
        loop=_loop(retries=3),
        transcript=[{"state": "VERIFYING", "verdict": "FAIL", "next_state": "EXECUTING"}],
        max_retry=3,
    )
    assert rolled["code"] == "rolled_back"
    assert approval["code"] == "approval_required"
    assert retried["code"] == "retry_budget"
    assert any("3" in d["ko"] for d in retried["details"])


def test_weak_model_strain_is_counted_and_named():
    result = explain_run(
        state=AgentState.DONE,
        loop=_loop(
            parse_errors=3,
            parse_recovered=2,
            corrections=2,
            retries=1,
            repairs={"fence": 4, "slice": 1, "artifact_repair": 1, "goal_defaulted": 1},
        ),
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
    )
    strain = result["model_strain"]
    assert strain["level"] == "heavy"
    assert strain["parse_errors"] == 3
    assert strain["repairs"]["format"] == 5
    assert strain["repairs"]["plan"] == 1
    assert strain["repairs"]["artifact"] == 1
    joined = " ".join(d["ko"] for d in result["details"])
    assert "3번" in joined and "2번은 자동으로 복구" in joined
    assert "더 큰 모델" in joined


def test_clean_run_reports_no_strain_and_no_noise():
    result = explain_run(
        state=AgentState.DONE,
        loop=_loop(),
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
    )
    assert result["model_strain"]["level"] == "none"
    assert result["details"] == []


def test_repaired_artifacts_and_proposals_are_named():
    result = explain_run(
        state=AgentState.DONE,
        loop=_loop(repairs={"artifact_repair": 1}),
        transcript=[
            {
                "state": "EXECUTING",
                "action": "write_file",
                "args": {"path": "app.html"},
                "result": {"path": "app.html"},
                "content_sanitize": {"sanitized": True, "repaired": True},
            },
            {
                "state": "EXECUTING",
                "action": "write_file",
                "args": {"path": "README.md"},
                "result": {"proposed": True, "proposal_id": "p1"},
            },
            {"state": "VERIFYING", "verdict": "PASS", "evidence": True},
        ],
    )
    joined = " ".join(d["ko"] for d in result["details"])
    assert "app.html" in joined
    assert "변경 제안" in joined


def test_blocked_tools_are_reported():
    result = explain_run(
        state=AgentState.FAILED,
        loop=_loop(),
        transcript=[{
            "state": "EXECUTING",
            "action": "run_command",
            "error": "BLOCKED: destructive action 'run_command' not permitted in agent mode.",
        }],
    )
    joined = " ".join(d["ko"] for d in result["details"])
    assert "run_command" in joined


def test_partial_or_garbage_input_never_raises():
    for bad in (None, "", 17, {"nope": True}):
        result = explain_run(state=bad, loop=bad, transcript=bad)
        assert result["ok"] is False
        assert result["headline"]["ko"]
    assert explain_run(state=AgentState.DONE)["code"] == "done"


def test_explanation_is_deterministic():
    args = dict(
        state=AgentState.NEEDS_REVIEW,
        loop=_loop(parse_errors=1, repairs={"fence": 1}),
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": False}],
    )
    assert explain_run(**args) == explain_run(**args)
