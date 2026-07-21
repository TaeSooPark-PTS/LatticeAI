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


def test_suite_covers_brain_grounding_and_automation_dimensions():
    names = {s.name for s in default_scenarios()}
    assert {
        "ingestion-chain-confirms-save",
        "concept-extraction-reflected-in-answer",
        "rag-grounded-answer-cites-retrieval",
        "automation-suggestion-proposal-first",
    } <= names
    assert len(names) >= 16


def test_ingestion_scenario_confirms_with_node_id():
    scenario = next(
        s for s in default_scenarios() if s.name == "ingestion-chain-confirms-save"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["executed_tools"] == ["knowledge_graph_ingest"]


def test_concept_extraction_scenario_reflects_extracted_concepts():
    scenario = next(
        s for s in default_scenarios()
        if s.name == "concept-extraction-reflected-in-answer"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["executed_tools"] == ["knowledge_graph_ingest"]
    assert result["summary"]["tool_outcomes"] == {"ok": 1}


def test_rag_scenario_grounding_gate_actually_gates():
    scenario = next(
        s for s in default_scenarios() if s.name == "rag-grounded-answer-cites-retrieval"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["executed_tools"] == ["knowledge_graph_search"]
    # Same retrieval, but an ungrounded (hallucinated) final answer must fail
    # the grounding expectation — proving the assertion is load-bearing.
    ungrounded = Scenario(
        name="ungrounded-final-fails",
        replies=[
            scenario.replies[0],
            scenario.replies[1],
            '{"action": "final", "message": "I believe it ships sometime in December."}',
            '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"}',
        ],
        expect_final_contains=["node-42"],
    )
    bad = run_agent_eval([ungrounded])
    assert bad["passed"] == 0
    assert any("final_message missing" in f for f in bad["results"][0]["failures"])


def test_automation_scenario_respects_proposal_first_governance():
    scenario = next(
        s for s in default_scenarios() if s.name == "automation-suggestion-proposal-first"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    # The automation write was staged as a governor proposal, never executed;
    # only the evidence lookup actually ran.
    assert result["proposals"] == 1
    assert result["summary"]["tool_outcomes"] == {"ok": 1, "proposed": 1}
    assert "write_file" not in result["executed_tools"]
    assert result["final_state"] == "DONE"


def test_suite_covers_artifact_write_sanitize_dimensions():
    names = {s.name for s in default_scenarios()}
    assert {
        "filegen-dirty-write-sanitized-critic-pass",
        "filegen-truncated-write-repaired-critic-pass",
        "filegen-dirty-write-unverifiable-needs-review",
    } <= names


def test_dirty_write_scenario_sanitizes_before_tool_and_passes_critic():
    scenario = next(
        s for s in default_scenarios()
        if s.name == "filegen-dirty-write-sanitized-critic-pass"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["final_state"] == "DONE"
    assert result["summary"]["tool_outcomes"] == {"ok": 1}
    # The ArtifactWritePipeline fired exactly once, extraction-level (no
    # deterministic repair needed for a fence+prose wrapper).
    assert result["summary"]["repairs"].get("artifact_sanitize") == 1
    assert "artifact_repair" not in result["summary"]["repairs"]


def test_truncated_write_scenario_records_artifact_repair():
    scenario = next(
        s for s in default_scenarios()
        if s.name == "filegen-truncated-write-repaired-critic-pass"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    assert result["summary"]["repairs"].get("artifact_repair") == 1


def test_dirty_write_needs_review_scenario_fails_closed():
    scenario = next(
        s for s in default_scenarios()
        if s.name == "filegen-dirty-write-unverifiable-needs-review"
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]
    assert result["ok"], result["failures"]
    # File was sanitized and written, but verification was unavailable —
    # fail-closed means NEEDS_REVIEW, never a fabricated DONE.
    assert result["final_state"] == "NEEDS_REVIEW"
    assert result["summary"]["repairs"].get("artifact_sanitize") == 1


def test_write_content_expectations_actually_gate():
    # The content-level assertions must be load-bearing: a scenario whose
    # written payload violates expect_write_excludes has to fail.
    clean_write = Scenario(
        name="write-excludes-gates",
        replies=[
            '{"action": "plan", "goal": "x", "steps": [{"action": "write_file"}]}',
            '{"action": "write_file", "args": {"path": "note.txt", "content": "hi"}}',
            '{"action": "final", "message": "done"}',
            '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"}',
        ],
        expect_write_excludes=["hi"],
    )
    report = run_agent_eval([clean_write])
    assert report["passed"] == 0
    assert any("must not contain" in f for f in report["results"][0]["failures"])


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
