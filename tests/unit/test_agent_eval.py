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
    assert report["scenarios"] >= 12


def test_suite_covers_weak_model_and_safety_dimensions():
    names = {s.name for s in default_scenarios()}
    assert "weak-model-format-gauntlet" in names
    assert "destructive-action-blocked" in names
    assert "unrecoverable-garbage-still-terminates" in names


def test_suite_covers_file_generation_and_workflow_dimensions():
    names = {s.name for s in default_scenarios()}
    assert {
        "file-generation-happy-path",
        "file-generation-bad-args-recovers",
        "multi-step-workflow-chain",
        "governed-write-proposal-path",
    } <= names


def test_file_generation_recovery_counts_error_then_success():
    scenario = next(
        s for s in default_scenarios() if s.name == "file-generation-bad-args-recovers"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["summary"]["tool_outcomes"] == {"error": 1, "ok": 1}
    assert result["executed_tools"] == ["generate_file"]


def test_multi_step_chain_executes_tools_in_order():
    scenario = next(
        s for s in default_scenarios() if s.name == "multi-step-workflow-chain"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["executed_tools"] == ["read_file", "generate_file", "write_file"]


def test_governed_scenario_routes_mutation_to_proposal_not_write():
    scenario = next(
        s for s in default_scenarios() if s.name == "governed-write-proposal-path"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    # The mutation was staged (governor proposal), never executed directly;
    # the additive create ran without an approval block.
    assert result["proposals"] == 1
    assert result["summary"]["tool_outcomes"] == {"proposed": 1, "ok": 1}
    assert result["executed_tools"] == ["write_file"]
    assert result["final_state"] == "DONE"


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
