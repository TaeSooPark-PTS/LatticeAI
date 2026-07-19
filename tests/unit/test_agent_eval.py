"""Agent evaluation harness tests (v9.6.0).

The harness itself is a release gate; these tests pin its contract so a
regression in the loop (or in the harness) fails fast in CI.
"""

from latticeai.core.agent_eval import Scenario, default_scenarios, run_agent_eval


def test_default_suite_passes_completely():
    report = run_agent_eval()
    failed = [r["name"] for r in report["results"] if not r["ok"]]
    assert failed == []
    assert report["success_rate"] == 1.0
    assert report["scenarios"] >= 8


def test_suite_covers_weak_model_and_safety_dimensions():
    names = {s.name for s in default_scenarios()}
    assert "weak-model-format-gauntlet" in names
    assert "destructive-action-blocked" in names
    assert "unrecoverable-garbage-still-terminates" in names


def test_harness_detects_regressions():
    # A scenario whose expectation cannot hold must be reported as a failure,
    # proving the gate actually gates.
    impossible = Scenario(
        name="impossible",
        replies=['{"action": "plan", "goal": "x", "steps": []}'],
        expect_min={"parse_errors": 99},
    )
    report = run_agent_eval([impossible])
    assert report["passed"] == 0
    assert report["results"][0]["failures"]


def test_recovery_rate_reflects_unrecovered_garbage():
    report = run_agent_eval()
    # the garbage scenario intentionally leaves one unrecovered parse error
    assert report["parse_errors"] > report["parse_recovered"]
    assert 0 < report["recovery_rate"] < 1
