"""Agent profiles — matching the loop to the model driving it (v9.9.7).

Review follow-up: "로컬 약모델 전용 에이전트 프로파일 — 더 공격적인 plan
normalize, 더 짧은 tool schema, 더 강한 repair, 도구 JSON 실패 시 direct path
폴백". House rules verified here: selection is deterministic and conservative
(an unknown model keeps today's behaviour), the compact loop escalates sooner,
and the direct-path fallback writes a real file rather than fabricating
evidence — a staged proposal or a failed write is reported as *not* written.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.agent_profiles import (
    COMPACT,
    COMPACT_MAX_PARAMS_B,
    STANDARD,
    model_size_b,
    profile_for_model,
)

# ── selection ────────────────────────────────────────────────────────────────


def test_model_size_is_parsed_without_confusing_quantization():
    assert model_size_b("mlx-community/gemma-3-4b-it-4bit") == 4.0
    assert model_size_b("qwen2.5-1.5b-instruct") == 1.5
    assert model_size_b("Llama-3.2-3B-Instruct") == 3.0
    # MoE ids name total then active params ("30B-A3B"); the active suffix is
    # glued to a letter, so only the real total is read — a 30B model must not
    # be mistaken for a 3B one.
    assert model_size_b("Qwen3-30B-A3B") == 30.0
    # "4bit"/"8bit" is a quantization, not a parameter count.
    assert model_size_b("some-model-8bit") is None
    assert model_size_b("gpt-4o") is None
    assert model_size_b("") is None


def test_small_local_models_get_the_compact_loop():
    for model in ("gemma-3-4b-it-4bit", "qwen2.5-1.5b", "llama-3.2-3B"):
        assert profile_for_model(model, env={}) is COMPACT


def test_large_and_unknown_models_keep_todays_behaviour():
    for model in ("qwen3-32b", "claude-opus-5", "gpt-4o", "", None):
        assert profile_for_model(model, env={}) is STANDARD
    assert COMPACT_MAX_PARAMS_B == 4.0


def test_explicit_override_wins_and_a_bad_name_falls_through():
    assert profile_for_model("qwen3-32b", env={"LATTICEAI_AGENT_PROFILE": "compact"}) is COMPACT
    assert profile_for_model("gemma-3-4b", env={"LATTICEAI_AGENT_PROFILE": "standard"}) is STANDARD
    # An unrecognized override never fails the run.
    assert profile_for_model("gemma-3-4b", env={"LATTICEAI_AGENT_PROFILE": "nonsense"}) is COMPACT


def test_compact_works_harder_than_standard():
    assert COMPACT.escalate_after < STANDARD.escalate_after
    assert COMPACT.transcript_window < STANDARD.transcript_window
    assert COMPACT.parse_failure_budget >= STANDARD.parse_failure_budget
    assert COMPACT.direct_path_fallback and not STANDARD.direct_path_fallback


# ── loop wiring ──────────────────────────────────────────────────────────────


class Req:
    def __init__(self, message="todo 앱을 html로 만들어줘"):
        self.message = message
        self.conversation_id = None
        self.temperature = 0.1
        self.workspace_id = None
        self.source = "test"


def _runtime(*, profile, executor_replies, written, write_result=None):
    state = {"i": 0}

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        if message == "Execute the next step.":
            reply = executor_replies[min(state["i"], len(executor_replies) - 1)]
            state["i"] += 1
            return reply
        if message == "Write the file content.":
            return "<!doctype html>\n<html><body><h1>Todo</h1></body></html>"
        return json.dumps({"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok"})

    async def generate(*, message, context, max_tokens, temperature):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    def execute_tool(name, args):
        written.append(dict(args))
        if write_result is not None:
            return write_result
        return {"success": True, "path": args.get("path")}

    deps = AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: {
            "risk": "write", "destructive": False, "shell": False, "network": False,
            "auto_approve": True, "sandbox": "workspace", "rollback": "none",
        },
        risk_level=lambda policy: "low",
        check_role=lambda name, user: None,
        tool_governance={"write_file": {"auto_approve": True}},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=Path("/tmp"),
        agent_profile=profile,
    )
    return SingleAgentRuntime(deps)


def _ctx_with_plan(path="index.html"):
    ctx = AgentRunContext()
    ctx.plan = {
        "goal": "todo 앱 만들기",
        "steps": [{"action": "write_file", "args": {"path": path}, "description": "make it"}],
    }
    ctx.state = AgentState.EXECUTING
    return ctx


GARBAGE = "I think I should write the file now. Here you go!"


def test_compact_profile_falls_back_to_writing_the_planned_file():
    written: list = []
    runtime = _runtime(profile=COMPACT, executor_replies=[GARBAGE], written=written)
    ctx = _ctx_with_plan()
    asyncio.run(runtime.execute(ctx, Req(), "Korean", "u@x.com", max_steps=8, model_id="gemma-3-4b"))

    assert [item["path"] for item in written] == ["index.html"]
    assert "<html" in written[0]["content"].lower()
    assert ctx.state == AgentState.VERIFYING
    step = [s for s in ctx.transcript if s.get("direct_path")]
    assert step, "the fallback write must be marked as such in the transcript"
    assert "직접 생성" in ctx.final_message


def test_standard_profile_never_takes_the_fallback():
    written: list = []
    runtime = _runtime(profile=STANDARD, executor_replies=[GARBAGE], written=written)
    ctx = _ctx_with_plan()
    asyncio.run(runtime.execute(ctx, Req(), "Korean", "u@x.com", max_steps=8, model_id="qwen3-32b"))

    assert written == [], "the standard loop must keep today's behaviour"
    parse_errors = [s for s in ctx.transcript if s.get("action") == "parse_error"]
    assert parse_errors


def test_fallback_reports_no_write_when_the_tool_fails():
    from latticeai.tools import ToolError

    written: list = []

    def failing(name, args):
        written.append(dict(args))
        raise ToolError("disk is full")

    runtime = _runtime(profile=COMPACT, executor_replies=[GARBAGE], written=[])
    runtime.deps.execute_tool = failing
    ctx = _ctx_with_plan()
    asyncio.run(runtime.execute(ctx, Req(), "Korean", "u@x.com", max_steps=8, model_id="gemma-3-4b"))

    # A failed write is not evidence: no direct_path step, no success message.
    assert not [s for s in ctx.transcript if s.get("direct_path")]
    assert "직접 생성" not in (ctx.final_message or "")


def test_fallback_does_nothing_without_a_planned_file():
    written: list = []
    runtime = _runtime(profile=COMPACT, executor_replies=[GARBAGE], written=written)
    ctx = AgentRunContext()
    ctx.plan = {"goal": "이 코드 설명해줘", "steps": []}
    ctx.state = AgentState.EXECUTING
    asyncio.run(runtime.execute(ctx, Req("이 코드 설명해줘"), "Korean", "u@x.com", max_steps=8, model_id="gemma-3-4b"))
    assert written == []


def test_a_recovering_model_never_reaches_the_fallback():
    written: list = []
    good = json.dumps({
        "thoughts": "write", "action": "write_file",
        "args": {"path": "index.html", "content": "<!doctype html><html></html>"},
    })
    final = json.dumps({"thoughts": "done", "action": "final", "message": "완료"})
    runtime = _runtime(profile=COMPACT, executor_replies=[GARBAGE, good, final], written=written)
    ctx = _ctx_with_plan()
    asyncio.run(runtime.execute(ctx, Req(), "Korean", "u@x.com", max_steps=8, model_id="gemma-3-4b"))
    assert len(written) == 1
    assert not [s for s in ctx.transcript if s.get("direct_path")]


def test_profile_selection_is_visible_on_the_runtime():
    runtime = _runtime(profile=None, executor_replies=[GARBAGE], written=[])
    assert runtime.profile_for("gemma-3-4b-it-4bit") is COMPACT
    assert runtime.profile_for("qwen3-32b") is STANDARD
