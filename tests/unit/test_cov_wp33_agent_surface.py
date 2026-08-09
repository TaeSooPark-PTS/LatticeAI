"""Coverage for the agent-loop support surface: the eval harness scoreboard,
permission-mode gates, plan/transcript helpers, the agent registry, run
explanations and model identity resolution.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from latticeai.core import agent_eval, agent_helpers, agent_permission, run_explain
from latticeai.core.agent import AgentState
from latticeai.core.agent_eval import Scenario, classify_result, run_agent_eval
from latticeai.core.agent_permission import (
    approval_requirements_for,
    block_reason_for_tool,
    call_mode_source,
    non_auto_plan_steps,
)
from latticeai.core.agent_registry import AgentRegistry
from latticeai.core.model_resolution import (
    ModelResolution,
    PrepareState,
    transition_log,
)
from latticeai.core.permission_mode import (
    DEFAULT_MODE,
    PermissionMode,
    effective_auto_approve,
    is_circuit_breaker,
)

# ── agent_eval: result classification ──────────────────────────────────────


def test_classify_result_puts_a_safety_violation_above_everything():
    assert classify_result("DONE", [], {}, ["write_file", "delete_everything"]) == "failed"


def test_classify_result_buckets_completion_guard_stops_and_review():
    assert classify_result("DONE", [], {}, ["write_file"]) == "correct_completion"

    assert classify_result(
        "FAILED", [], {"tool_outcomes": {"blocked_destructive": 1}}, []
    ) == "safe_termination"
    assert classify_result(
        "FAILED", [], {"tool_outcomes": {"blocked_approval": 2}}, []
    ) == "safe_termination"
    assert classify_result(
        "FAILED",
        [{"kind": "parse_error", "phase": "execute", "recovered": False}],
        {"tool_outcomes": {}},
        [],
    ) == "safe_termination"

    # A recovered parse error is not a guard stop.
    assert classify_result(
        "FAILED",
        [{"kind": "parse_error", "phase": "execute", "recovered": True}],
        {"tool_outcomes": {}},
        [],
    ) == "failed"
    assert classify_result("NEEDS_REVIEW", [], {}, []) == "needs_review"


def test_eval_deps_expose_an_inert_plain_generate_port():
    deps = agent_eval._build_deps([], [])

    assert json.loads(asyncio.run(deps.generate(prompt="anything"))) == {"action": "noop"}


def test_every_scenario_expectation_is_load_bearing():
    scenario = Scenario(
        name="wp33-every-expectation-wrong",
        replies=[agent_eval._PLAN, agent_eval._WRITE, agent_eval._FINAL, agent_eval._PASS],
        expect_state="FAILED",
        expect_exact={"parse_errors": 7},
        expect_tool_outcomes={"ok": 5},
        expect_tool_calls=["read_file"],
        expect_repairs={"artifact_repair": 3},
        expect_write_contains=["never-written"],
    )

    report = run_agent_eval([scenario])
    failures = report["results"][0]["failures"]

    assert report["passed"] == 0
    assert any(item.startswith("state=DONE expected=FAILED") for item in failures)
    assert any(item.startswith("parse_errors=0 != 7") for item in failures)
    assert any(item.startswith("tool_outcomes[ok]=1 != 5") for item in failures)
    assert any("tool_calls=['write_file']" in item for item in failures)
    assert any(item.startswith("repairs[artifact_repair]=0 != 3") for item in failures)
    assert any("written content missing 'never-written'" in item for item in failures)


def test_governor_proposal_count_must_match_the_traced_outcomes(monkeypatch):
    class _PhantomGovernor(agent_eval._EvalChangeGovernor):
        """A governor that reports a proposal the loop never traced."""

        def __init__(self):
            super().__init__()
            self.proposals.append({"id": "eval-proposal-phantom", "path": "existing/ghost.html"})

    monkeypatch.setattr(agent_eval, "_EvalChangeGovernor", _PhantomGovernor)

    scenario = Scenario(
        name="wp33-governor-accounting",
        use_governor=True,
        replies=[agent_eval._PLAN, agent_eval._WRITE, agent_eval._FINAL, agent_eval._PASS],
    )
    report = run_agent_eval([scenario])
    result = report["results"][0]

    assert result["ok"] is False
    assert result["proposals"] == 1
    assert any("governor proposals=1 != " in item for item in result["failures"])


# ── agent_permission ───────────────────────────────────────────────────────


def test_call_mode_source_falls_back_to_the_default_when_every_call_fails():
    def _broken(*_args, **kwargs):
        if kwargs:
            raise TypeError("resolver takes no scope kwargs")
        raise RuntimeError("legacy resolver is broken")

    assert call_mode_source(_broken, user_email="owner@example.com") is DEFAULT_MODE
    assert call_mode_source("trusted") == "trusted"


def test_non_auto_plan_steps_ignores_steps_without_an_action():
    governance = {"write_file": {"auto_approve": False, "risk": "write"}}

    steps = [{"description": "think about it"}, {"action": "write_file"}]

    assert non_auto_plan_steps(PermissionMode.STRICT, steps, governance) == ["write_file"]


def test_approval_requirements_summarize_the_plan_and_the_gated_steps():
    governance = {
        "read_file": {"auto_approve": True, "risk": "read"},
        "write_file": {"auto_approve": False, "risk": "write"},
    }
    plan = {
        "goal": "빌드하고 배포",
        "steps": [
            {"action": "read_file", "description": "read the config"},
            {"action": "write_file"},
        ],
    }

    requirements = approval_requirements_for(PermissionMode.STRICT, plan, governance)

    assert requirements["non_auto_steps"] == ["write_file"]
    assert requirements["requires_approval"] is True
    assert requirements["permission_mode"] == "strict"
    assert requirements["plan_summary"].splitlines() == [
        "빌드하고 배포",
        "1. read the config",
        "2. write_file",
    ]

    empty = approval_requirements_for(PermissionMode.STRICT, {}, governance)
    assert empty["plan_summary"] == ""
    assert empty["non_auto_steps"] == []


def test_block_reason_reports_circuit_breakers_before_anything_else():
    write_policy = {"risk": "write", "destructive": False, "auto_approve": False}

    reason = block_reason_for_tool(
        PermissionMode.BYPASS, "write_file", write_policy, {"path": "/"}
    )
    assert reason == "BLOCKED: circuit breaker: refusing path '/'"

    destructive = block_reason_for_tool(
        PermissionMode.BYPASS, "wipe_disk", {"risk": "destructive"}, {}
    )
    assert destructive == "BLOCKED: destructive action is always blocked"

    gated = block_reason_for_tool(
        PermissionMode.STRICT, "write_file", write_policy, {"path": "notes.md"}
    )
    assert gated == "BLOCKED: action 'write_file' requires explicit approval (mode=strict)."


def test_block_reason_re_denies_destructive_policies_independently(monkeypatch):
    """Defence in depth: ``block_reason_for_tool`` re-checks the destructive
    flags itself, so a circuit-breaker table that stopped flagging them would
    still not let the call through. Unreachable while the breaker agrees, so
    the breaker is pinned permissive here.
    """
    monkeypatch.setattr(agent_permission, "is_circuit_breaker", lambda *_a, **_kw: None)

    assert block_reason_for_tool(PermissionMode.BYPASS, "wipe_disk", {"risk": "destructive"}, {}) == (
        "BLOCKED: destructive action 'wipe_disk' not permitted in agent mode."
    )
    assert block_reason_for_tool(PermissionMode.BYPASS, "erase_all", {"destructive": True}, {}) == (
        "BLOCKED: destructive action 'erase_all' not permitted in agent mode."
    )


def test_block_reason_honours_a_policy_flagged_auto_approve_as_a_last_resort(monkeypatch):
    """The final ``policy['auto_approve']`` arm is a redundant safety net.

    ``effective_auto_approve`` already returns True for such a policy, so the
    branch is only reachable when the mode table disagrees with the policy —
    modelled here by pinning the mode table to "no".
    """
    monkeypatch.setattr(agent_permission, "effective_auto_approve", lambda *_a, **_kw: False)

    reason = block_reason_for_tool(
        PermissionMode.STRICT, "read_file", {"auto_approve": True, "risk": "read"}, {}
    )

    assert reason is None


# ── permission_mode ────────────────────────────────────────────────────────


def test_circuit_breaker_refuses_root_and_home_paths():
    policy = {"risk": "write", "destructive": False}

    assert is_circuit_breaker("write_file", policy, {"path": "/"}) == "circuit breaker: refusing path '/'"
    assert is_circuit_breaker("write_file", policy, {"filename": "/Users"}) == (
        "circuit breaker: refusing path '/Users'"
    )
    assert is_circuit_breaker("write_file", policy, {"path": "~/notes.md"}) is None


def test_bypass_still_refuses_destructive_and_system_sandbox_work():
    assert effective_auto_approve(
        PermissionMode.BYPASS, "wipe_disk", {"risk": "destructive"}
    ) is False
    assert effective_auto_approve(
        PermissionMode.BYPASS, "erase_all", {"risk": "write", "destructive": True}
    ) is False
    assert effective_auto_approve(
        PermissionMode.BYPASS, "system_install", {"risk": "exec", "sandbox": "system"}
    ) is False
    assert effective_auto_approve(
        PermissionMode.BYPASS, "system_write", {"risk": "write", "sandbox": "system"}
    ) is False
    # Desktop control is exempt from the system-sandbox clamp under bypass.
    assert effective_auto_approve(
        PermissionMode.BYPASS, "computer_click", {"risk": "write", "sandbox": "system"}
    ) is True


# ── agent_helpers ──────────────────────────────────────────────────────────


def test_extract_action_rejects_a_non_dict_python_literal():
    with pytest.raises(ValueError, match="did not return valid JSON"):
        agent_helpers.extract_action_details("'just a string'")


def test_normalize_plan_replaces_a_non_object_plan():
    plan, fixes = agent_helpers.normalize_plan(["not", "a", "plan"], "make me a page")

    assert "plan_not_object" in fixes
    assert plan["goal"] == "make me a page"


def test_compact_transcript_summarizes_older_failures_in_one_line():
    older = [{"state": "EXECUTING", "action": "write_file", "error": "boom " * 100}]
    recent = [{"state": "EXECUTING", "action": "read_file", "result": {"path": f"f{i}.md"}} for i in range(3)]

    compacted = agent_helpers.compact_transcript(older + recent, window=3)

    assert compacted[0] == {
        "summarized_older_steps": 1,
        "note": "older steps compacted — full detail retained in the run record",
    }
    assert compacted[1]["action"] == "write_file"
    assert len(compacted[1]["error"]) == 160
    assert "ok" not in compacted[1]
    assert len(compacted) == 5


def test_artifact_checklist_skips_pathless_write_results():
    transcript = [
        {"state": AgentState.EXECUTING.value, "action": "write_file", "result": {"ok": True}},
        {
            "state": AgentState.EXECUTING.value,
            "action": "write_file",
            "result": {"path": "page.html"},
            "content_sanitize": {"sanitized": True, "repaired": False},
        },
    ]

    assert agent_helpers.artifact_checklist(transcript, frozenset({"write_file"})) == [
        {"path": "page.html", "sanitized": True, "repaired": False}
    ]


def test_format_requirement_coverage_marks_missing_files_and_lists_requirements():
    text = agent_helpers.format_requirement_coverage({
        "files": {"declared": ["report.html", "notes/todo.md"], "written": ["out/report.html"]},
        "requirements": ["다크모드", "검색 기능"],
    })

    assert "- report.html: written" in text
    assert "- notes/todo.md: MISSING" in text
    assert "- 다크모드" in text
    assert "- 검색 기능" in text

    assert agent_helpers.format_requirement_coverage(
        {"files": {"declared": [], "written": []}, "requirements": []}
    ) == ""


# ── agent_registry ─────────────────────────────────────────────────────────


def test_registry_reads_a_corrupt_state_file_as_empty(tmp_path):
    path = tmp_path / "agent_registry.json"
    path.write_text("{not json", encoding="utf-8")

    registry = AgentRegistry(path)

    assert registry._state == {"custom": [], "config_overrides": {}}
    assert registry.list()["total"] == len(registry.all())


def test_registry_cleans_up_its_temp_file_when_the_atomic_replace_fails(tmp_path, monkeypatch):
    registry = AgentRegistry(tmp_path / "agent_registry.json")

    def _no_replace(_src, _dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "replace", _no_replace)

    with pytest.raises(OSError, match="cross-device link"):
        registry.register(name="Scout")

    assert list(tmp_path.glob("*.tmp")) == []


def test_registry_filters_by_type_and_validates_registration(tmp_path):
    registry = AgentRegistry(tmp_path / "agent_registry.json")

    planners = registry.list("planner")
    assert planners["agents"] and all(a["type"] == "planner" for a in planners["agents"])
    assert planners["total"] < len(registry.all())

    with pytest.raises(ValueError, match="name is required"):
        registry.register(name="   ")
    with pytest.raises(ValueError, match="type must be one of"):
        registry.register(name="Scout", agent_type="wizard")

    first = registry.register(name="Scout", capabilities=["recon"])
    second = registry.register(name="Scout")
    assert first["id"] == "agent:custom:scout"
    assert second["id"] == "agent:custom:scout-2"
    assert registry.discover("RECON") == [registry.get("agent:custom:scout")]


def test_registry_config_overrides_apply_to_builtin_and_custom_agents(tmp_path):
    registry = AgentRegistry(tmp_path / "agent_registry.json")

    with pytest.raises(KeyError):
        registry.update_config("agent:custom:ghost", {})

    builtin_id = registry.list("planner")["agents"][0]["id"]
    updated = registry.update_config(builtin_id, {"temperature": 0.1}, enabled=False)
    assert updated["config"] == {"temperature": 0.1}
    assert updated["enabled"] is False
    assert registry._state["config_overrides"][builtin_id]["enabled"] is False

    # Overrides survive a reload from disk.
    assert AgentRegistry(tmp_path / "agent_registry.json").get(builtin_id)["enabled"] is False

    with pytest.raises(ValueError, match="Built-in role agents cannot be removed"):
        registry.remove(builtin_id)
    with pytest.raises(KeyError):
        registry.remove("agent:custom:ghost")


# ── run_explain ────────────────────────────────────────────────────────────


def test_explain_run_counts_unknown_repairs_separately_and_grades_light_strain():
    explanation = run_explain.explain_run(
        state="DONE",
        loop={"repairs": {"some_new_repair": 2}, "corrections": 1},
    )

    assert explanation["model_strain"]["repairs"]["other"] == 2
    assert explanation["model_strain"]["repairs"]["format"] == 0
    assert explanation["model_strain"]["level"] == "light"
    assert explanation["model_strain"]["score"] == 1


def test_needs_review_without_a_verdict_or_with_a_plain_fail_stays_needs_review():
    assert run_explain.explain_run(state="NEEDS_REVIEW", transcript=[])["code"] == "needs_review"

    verdict_fail = run_explain.explain_run(
        state="NEEDS_REVIEW",
        transcript=[{"state": "VERIFYING", "verdict": "FAIL", "next_state": "NEEDS_REVIEW"}],
    )
    assert verdict_fail["code"] == "needs_review"


# ── model_resolution ───────────────────────────────────────────────────────


def test_alias_resolver_can_rewrite_both_the_engine_and_the_model():
    engine_switch = ModelResolution.from_request(
        "gemma-4-12b",
        alias_resolver=lambda model, engine: "ollama:hf.co/gemma-4-12b",
    )
    assert engine_switch.provider == "ollama"
    assert engine_switch.resolved_model == "hf.co/gemma-4-12b"
    assert engine_switch.load_id == "ollama:hf.co/gemma-4-12b"

    model_only = ModelResolution.from_request(
        "gemma-4-12b",
        alias_resolver=lambda model, engine: "mlx-community/gemma-4-12b-4bit",
    )
    assert model_only.provider == "local_mlx"
    assert model_only.resolved_model == "mlx-community/gemma-4-12b-4bit"
    assert model_only.load_id == "mlx-community/gemma-4-12b-4bit"


def test_a_broken_alias_resolver_leaves_the_resolution_untouched():
    def _boom(_model, _engine):
        raise RuntimeError("alias table unavailable")

    resolution = ModelResolution.from_request("mlx-community/test", alias_resolver=_boom)

    assert resolution.resolved_model == "mlx-community/test"
    assert resolution.provider == "local_mlx"


def test_update_after_load_ignores_an_empty_actual_current():
    resolution = ModelResolution.from_request("lmstudio:llama-3", user_email="owner@example.com")
    before = resolution.to_dict()

    resolution.update_after_load(actual_current=None)

    assert resolution.to_dict() == before


def test_transition_log_carries_optional_extra_context():
    assert transition_log(PrepareState.READY, "done") == {"state": "READY", "message": "done"}
    assert transition_log(PrepareState.FAILED, "nope", {"reason": "oom"}) == {
        "state": "FAILED",
        "message": "nope",
        "extra": {"reason": "oom"},
    }
