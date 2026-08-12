"""The Python half of the Python↔Rust **agent loop** parity contract.

``rust/lattice-agent``'s orchestrator is pinned to the golden files under
``rust/fixtures/agent_loop/golden/``. Those goldens were produced by the real
Python loop, so a change to a Python gate can silently invalidate them: the Rust
tests keep passing (they compare against the same stale file), and nobody learns
that the port stopped being a port until an agent reaches a state it should not
have.

This module closes that loop from the other side. It re-runs the **real**
``SingleAgentRuntime`` over the same scripts, the real helpers over the same
grids, and the real ``verify()`` over the same verdict matrix, and asserts the
goldens still describe what Python does. Two consequences worth stating:

* it is a contract test, not a regression test — a deliberate loop change is
  *supposed* to fail it, and the fix is to regenerate with
  ``.venv/bin/python scripts/generate_agent_loop_fixtures.py`` and re-run
  ``cargo test -p lattice-agent`` so both halves move together;
* it imports nothing from the Rust side. The shared artefacts are JSON files;
  no toolchain is required to run this file.

Comparison is over the canonical JSON encoding rather than ``==`` so that a
``bool`` quietly becoming an ``int`` (``True == 1`` in Python) still fails.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_agent_loop_fixtures.py"


def _load_generator():
    """Import the fixture generator by path (``scripts`` is not a package)."""
    spec = importlib.util.spec_from_file_location("agent_loop_fixtures", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = _load_generator()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _golden(name: str) -> dict:
    path = fixtures.GOLDEN_DIR / name
    assert path.is_file(), f"{path} is missing — run {GENERATOR.name}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory) -> dict:
    """Everything the generator would write, rebuilt from the real loop."""
    return fixtures.build(tmp_path_factory.mktemp("agent-loop-parity"))


# ── structure ────────────────────────────────────────────────────────────────
def test_the_golden_directory_holds_exactly_what_the_generator_writes(rebuilt: dict):
    """A file added to the grid without regenerating shows up here, not as a
    confusing "missing fixture" panic from the cargo suite."""
    assert {path.name for path in fixtures.GOLDEN_DIR.glob("*.json")} == set(rebuilt)


def test_the_manifest_still_describes_the_current_grid(rebuilt: dict):
    manifest = _golden("manifest.json")
    assert _canonical(manifest) == _canonical(rebuilt["manifest.json"])
    assert manifest["scenarios"] == sorted(fixtures.SCENARIOS)
    assert len(manifest["normalization"]) == 4, "both sides apply four rules"
    assert manifest["constants"]["max_state_history"] == 200
    assert manifest["constants"]["max_retry"] == 3
    assert manifest["constants"]["compact_max_params_b"] == 4.0


def test_the_grid_is_wide_enough_to_be_worth_running():
    """Coverage here is the product of several axes; a shrunken axis is a silent
    loss of proof, so the floor is asserted rather than assumed."""
    assert len(fixtures.RAW_ACTIONS) >= 30
    assert len(fixtures.PLAN_CASES) >= 20
    assert len(fixtures.INFERENCE_MESSAGES) >= 20
    assert len(fixtures.SCENARIOS) >= 7
    assert len(fixtures.DOCUMENT_TARGET_CASES) >= 15
    assert len(fixtures.PROFILE_MODEL_IDS) >= 15
    assert len(fixtures.PROFILE_OVERRIDES) >= 5
    assert len(_golden("verification.json")["cases"]) >= 88
    assert len(_golden("helpers.json")["extract_action_details"]) == len(fixtures.RAW_ACTIONS)


def test_the_policy_table_is_the_real_registry(rebuilt: dict):
    """The trajectories are only worth anything if the policies in them are real."""
    from latticeai.core.tool_registry import (
        LOCAL_WRITE_BLOCKED_PREFIXES,
        TOOL_GOVERNANCE,
    )

    recorded = _golden("policies.json")
    assert _canonical(recorded) == _canonical(rebuilt["policies.json"])
    assert set(recorded["tools"]) == set(TOOL_GOVERNANCE)
    assert recorded["tools"]["write_file"]["risk"] == "write"
    assert recorded["tools"]["read_file"]["auto_approve"] is True
    assert recorded["tools"]["run_command"]["risk"] == "exec"
    assert recorded["blocked_write_prefixes"] == list(LOCAL_WRITE_BLOCKED_PREFIXES)


# ── deterministic helpers ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "section",
    [
        "extract_action_details",
        "normalize_plan",
        "inference",
        "transcript_helpers",
        "truncate_strings",
        "filter_learnings",
        "document_targets",
        "agent_profiles",
        "budgets",
    ],
)
def test_every_helper_still_answers_what_its_golden_records(rebuilt: dict, section: str):
    assert _canonical(_golden("helpers.json")[section]) == _canonical(
        rebuilt["helpers.json"][section]
    )


def test_the_tolerance_chain_is_exercised_end_to_end():
    """Every rung of `extract_action_details` must appear in the grid, or the
    grid is proving less than it looks like it proves."""
    cases = {case["key"]: case for case in _golden("helpers.json")["extract_action_details"]}
    reached = {repair for case in cases.values() for repair in case.get("repairs", [])}
    assert reached == {"think_strip", "fence", "slice", "trailing_comma", "python_literal"}
    assert cases["broken_prose"]["ok"] is False
    assert cases["broken_prose"]["error"] == "Agent did not return valid JSON: <decoder-detail>", (
        "the decoder detail is normalised; the prefix is the contract"
    )
    assert cases["no_action_key"]["error"] == "Agent JSON must include an action field."
    assert cases["not_an_object"]["error"] == "Agent JSON must include an action field."


def test_plan_normalization_names_every_repair_it_applied():
    fixes = {fix for case in _golden("helpers.json")["normalize_plan"] for fix in case["fixes"]}
    assert fixes == {
        "plan_not_object",
        "goal_defaulted",
        "steps_filtered",
        "manifest_steps",
        "manifest_rewrite",
        "heuristic_file_step",
        "estimated_steps_invalid",
    }


# ── verification mapping ─────────────────────────────────────────────────────
def test_the_verdict_mapping_still_maps_the_way_it_is_recorded(rebuilt: dict):
    assert _canonical(_golden("verification.json")["cases"]) == _canonical(
        rebuilt["verification.json"]["cases"]
    )


def test_done_requires_a_pass_and_evidence_and_coverage():
    """The three facts that outrank the critic, read off the recorded matrix."""
    for case in _golden("verification.json")["cases"]:
        if case["final_state"] != "DONE" or case["verdict"] not in ("PASS", "FAIL", ""):
            continue
        assert case["verdict"] == "PASS", case
        assert case["evidence"] is True, "a PASS with no evidence is never DONE"
        assert case["message"] == "make a note", (
            "the manifest request leaves style.css unwritten, so it cannot be DONE"
        )


def test_an_unparseable_critic_never_fabricates_a_pass():
    cases = {case["verdict"]: case for case in _golden("verification.json")["cases"]}
    never = cases["never_parses"]
    assert never["final_state"] == "NEEDS_REVIEW"
    assert never["llm_calls"] == 2, "exactly one strict retry"
    assert never["temperatures"] == [0.1, 0.0], "the retry is asked at zero"
    assert never["transcript"][-1]["verdict"] == "UNAVAILABLE"
    assert never["transcript"][-1]["verifier_available"] is False
    recovered = cases["strict_retry_recovers"]
    assert recovered["final_state"] == "DONE"
    assert recovered["llm_calls"] == 2


def test_the_retry_ceiling_is_the_max_retry_it_was_given():
    rows = {
        (case["next_state"], case["retry_count"]): case
        for case in _golden("verification.json")["cases"]
        if case["verdict"] == "FAIL" and case["evidence"] and case["message"] == "make a note"
    }
    assert rows[("EXECUTING", 0)]["final_state"] == "EXECUTING"
    assert rows[("EXECUTING", 0)]["retry_count_after"] == 1
    assert rows[("EXECUTING", 3)]["final_state"] == "FAILED"
    assert rows[("EXECUTING", 3)]["retry_count_after"] == 3, "the ceiling does not increment"
    assert rows[("RETRY", 0)]["final_state"] == "EXECUTING", "the legacy alias"
    assert rows[("ROLLBACK", 0)]["final_state"] == "ROLLBACK"
    assert rows[("DONE", 0)]["final_state"] == "NEEDS_REVIEW", "DONE without PASS is inconsistent"
    assert rows[("SOMETHING_ELSE", 0)]["final_state"] == "FAILED"


# ── run store ────────────────────────────────────────────────────────────────
def test_the_run_store_contract_is_unchanged(rebuilt: dict):
    assert _canonical(_golden("run_store.json")["cases"]) == _canonical(
        rebuilt["run_store.json"]["cases"]
    )


def test_a_context_round_trips_and_an_unknown_state_fails_safe():
    cases = {case["key"]: case for case in _golden("run_store.json")["cases"]}
    assert _canonical(cases["full"]["serialized"]) == _canonical(cases["full"]["round_trip"])
    assert set(cases["full"]["serialized"]) == {
        "state", "plan", "transcript", "retry_count", "state_history", "corrections",
        "final_message", "rollback_log", "executing_model", "reviewing_model",
        "approved_by_human", "permission_mode", "trace",
    }
    for key in ("restore_unknown_state", "restore_no_state", "restore_null_state"):
        assert cases[key]["restored"]["state"] == "WAITING_APPROVAL", key
    assert cases["restore_blank_mode"]["restored"]["permission_mode"] is None
    assert cases["restore_kept_mode"]["restored"]["permission_mode"] == "bypass"


# ── trajectories ─────────────────────────────────────────────────────────────
def test_every_trajectory_still_runs_the_way_it_is_recorded(rebuilt: dict):
    assert _canonical(_golden("trajectories.json")["cases"]) == _canonical(
        rebuilt["trajectories.json"]["cases"]
    )


def test_the_trajectories_reach_every_terminal_state_by_every_route():
    cases = {case["key"]: case for case in _golden("trajectories.json")["cases"]}
    assert cases["clean_done_trusted"]["final_state"] == "DONE"
    assert cases["strict_proposal_pause"]["final_state"] == "DONE"
    assert cases["parse_budget_exhaustion"]["final_state"] == "NEEDS_REVIEW"
    assert cases["repeated_create_guard"]["final_state"] == "DONE"
    assert cases["verify_retry_then_failed"]["final_state"] == "FAILED"
    assert cases["verify_pass_no_evidence"]["final_state"] == "NEEDS_REVIEW"
    assert cases["rollback_path"]["final_state"] == "FAILED"
    assert cases["blocked_fail_closed_strict"]["final_state"] == "NEEDS_REVIEW"
    assert cases["blocked_breaker_bypass"]["final_state"] == "FAILED"
    assert cases["approval_pause_strict"]["final_state"] == "WAITING_APPROVAL"
    assert {case["final_state"] for case in cases.values()} >= {
        "DONE", "FAILED", "NEEDS_REVIEW", "WAITING_APPROVAL"
    }
    # Every scripted completion is consumed: a trajectory that stopped early
    # would otherwise pass while proving less than the script describes.
    for key, case in cases.items():
        assert case["unused_script"] == 0, key
        assert case["llm_calls"] == len(case["scripted_llm"]), key


def test_a_staged_proposal_applies_nothing_and_leaves_the_decision_on_the_record():
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "strict_proposal_pause"
    )
    staged = next(step for step in case["transcript"] if step.get("action") == "write_file")
    assert staged["result"]["proposed"] is True
    assert staged["result"]["proposal_id"] == "prop-1"
    assert "content" not in staged["args"], "the payload is stripped from the record"
    assert case["tool_calls"] == [], "nothing was dispatched"
    assert [event["event"] for event in case["audit"]] == [
        "agent_approval",
        "agent_change_proposed",
    ]


def test_trusted_applies_a_governed_mutation_with_an_audit_instead_of_a_proposal():
    case = next(
        row for row in _golden("trajectories.json")["cases"] if row["key"] == "rollback_path"
    )
    assert "agent_change_auto_applied" in [event["event"] for event in case["audit"]]
    assert case["rollback_log"][0] == {
        "path": "note.md", "existed": True, "content": "original\n", "too_large": False
    }
    rolled = case["transcript"][-1]["rolled_back"]
    assert rolled == [{"path": "note.md", "ok": True, "action": "restored", "mode": "snapshot"}]
    assert case["final_message"] == (
        "실행 실패로 롤백했습니다. 복구 파일: ['note.md (snapshot)']"
    ), "the message interpolates a Python list repr"


def test_an_unstageable_change_fails_closed_before_it_reaches_the_registry():
    """`delete_file` is not in the registry, so it carries the default policy —
    and the governor still classifies it as a deletion, which cannot be staged
    as a reviewable proposal. Fail-closed is the only safe answer."""
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "blocked_fail_closed_strict"
    )
    blocked = next(step for step in case["transcript"] if step.get("action") == "delete_file")
    assert blocked["error"].startswith("NEEDS_REVIEW: ")
    assert blocked["change_class"] == "destructive"
    assert blocked["permission_mode"] == "strict"
    assert case["tool_calls"] == [], "the registry was never asked"
    assert "agent_blocked" in [event["event"] for event in case["audit"]]


def test_the_circuit_breaker_is_mode_invariant_in_the_recorded_trajectory():
    """`bypass` skips approval prompts; it never unlocks a breaker."""
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "blocked_breaker_bypass"
    )
    blocked = next(step for step in case["transcript"] if step.get("action") == "write_file")
    assert blocked["permission_mode"] == "bypass"
    assert blocked["error"].startswith("BLOCKED: ")
    assert blocked["governance"]["destructive"] is True, (
        "the registry rewrote the policy for a blocked system prefix"
    )
    assert case["tool_calls"] == [], "nothing was written to /etc"
    assert case["final_state"] == "FAILED"


def test_the_parse_budget_stops_at_the_profiles_ceiling():
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "parse_budget_exhaustion"
    )
    slips = [step for step in case["transcript"] if step.get("action") == "parse_error"]
    assert len(slips) == 3, "the standard profile tolerates three format slips"
    assert case["loop"]["parse_errors"] == 3
    assert case["loop"]["parse_recovered"] == 2, "the last slip is not recovered"
    assert case["loop"]["corrections"] == 2, "the plain hint, then the escalated one"


def test_the_loop_guard_halts_an_identical_reissue():
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "repeated_create_guard"
    )
    assert len(case["tool_calls"]) == 1, "the second identical write never ran"
    assert any(
        step.get("error", "").startswith("LOOP_DETECTED:") for step in case["transcript"]
    )


def test_the_approval_pause_names_what_needs_a_human():
    case = next(
        row for row in _golden("trajectories.json")["cases"]
        if row["key"] == "approval_pause_strict"
    )
    assert case["paused"] is True
    assert case["approval_requirements"]["requires_approval"] is True
    assert case["approval_requirements"]["non_auto_steps"] == ["run_command"]
    assert case["approval_requirements"]["permission_mode"] == "strict"
    assert case["approval_requirements"]["plan_summary"] == "run the tests\n1. list"
    assert case["tool_calls"] == [], "a paused plan executes nothing"


# ── normalisation ────────────────────────────────────────────────────────────
def test_no_golden_carries_a_machine_specific_value():
    """The four rules exist to make this assertion true; here it is asserted."""
    for path in fixtures.GOLDEN_DIR.glob("*.json"):
        body = path.read_text(encoding="utf-8")
        assert str(REPO_ROOT) not in body, path.name
        assert "/private/var/folders" not in body, path.name
        assert "/tmp/" not in body, path.name
        payload = json.loads(body)
        assert '"at":' not in body, path.name
        _assert_no_key(payload, "at", path.name)
        _assert_no_key(payload, "stderr", path.name)


def _assert_no_key(value, key: str, where: str) -> None:
    if isinstance(value, dict):
        assert key not in value, f"{where} still carries a {key!r}"
        for item in value.values():
            _assert_no_key(item, key, where)
    elif isinstance(value, list):
        for item in value:
            _assert_no_key(item, key, where)
