"""Agent loop hardening — review 2026-07-25 Wave 0.3 / 0.4 / 1.1 / §4.2 L6.

Covers:
- executor transcript sliding window + tool-result truncation (bounded prompts)
- TranscriptBudget env config
- per-run on_step observer events (live step timeline feed)
- agent_live_stream SSE framing (named agent_step frames before the payload)
- normalize_plan manifest rewrite (agent path joins the deterministic
  multi-file project flow)
- react / python package manifest expansion
- terminal-state learning policy (FAILED runs record failure experiences)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException

from latticeai.api.chat_stream import agent_live_stream
from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
    TranscriptBudget,
    _truncate_strings,
    compact_transcript,
    normalize_plan,
)
from latticeai.core.file_generation import infer_project_manifest


def _deps(**overrides):
    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        raise AssertionError("not used")

    async def generate(*, message, context, max_tokens, temperature):
        raise AssertionError("not used")

    base = dict(
        generate_as=generate_as,
        generate=generate,
        execute_tool=lambda name, args: {"success": True},
        policy_for=lambda name, args: {
            "risk": "low", "destructive": False, "shell": False,
            "network": False, "auto_approve": True, "sandbox": "workspace",
            "rollback": "none",
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
        agent_root=None,
    )
    base.update(overrides)
    return AgentDeps(**base)


# ── transcript shaping ──────────────────────────────────────────────────

def test_truncate_strings_caps_deeply_nested_values():
    value = {"result": {"output": "x" * 500, "meta": [{"body": "y" * 300}]}, "n": 7}
    capped = _truncate_strings(value, 100)
    assert capped["n"] == 7
    assert capped["result"]["output"].startswith("x" * 100)
    assert "+400 chars" in capped["result"]["output"]
    assert "+200 chars" in capped["result"]["meta"][0]["body"]
    # original untouched (deep copy semantics)
    assert len(value["result"]["output"]) == 500


def test_compact_transcript_keeps_recent_window_and_summarizes_older():
    steps = []
    for i in range(20):
        steps.append({
            "state": "EXECUTING", "action": "write_file",
            "args": {"path": f"f{i}.txt"},
            "result": {"path": f"f{i}.txt", "output": "z" * 2000},
        })
    view = compact_transcript(steps, window=4, result_chars=100)
    assert view[0]["summarized_older_steps"] == 16
    # older entries are one-line summaries without the fat result body
    assert view[1] == {"state": "EXECUTING", "action": "write_file", "ok": True, "path": "f0.txt"}
    # recent entries keep structure but truncate strings
    assert "+1900 chars" in view[-1]["result"]["output"]
    assert len(view) == 1 + 16 + 4


def test_compact_transcript_short_run_passthrough_with_truncation_only():
    steps = [{"state": "PLANNING", "goal": "g" * 50}]
    view = compact_transcript(steps, window=8, result_chars=700)
    assert view == steps  # under the window → same shape, strings under cap


def test_executor_context_is_bounded_on_long_runs():
    runtime = SingleAgentRuntime(_deps(
        transcript_budget=TranscriptBudget(window=6, result_chars=200, verify_chars=200),
    ))
    ctx = AgentRunContext()
    ctx.plan = {"goal": "long task", "steps": []}
    for i in range(40):
        ctx.transcript.append({
            "state": "EXECUTING", "action": "run_command",
            "args": {"command": f"step {i}"},
            "result": {"output": "o" * 5000},
        })
    ctx.corrections = [f"hint {i}" for i in range(10)]

    class Req:
        message = "do the long thing"
        conversation_id = None

    context = runtime._executor_context(ctx, Req(), "Korean", "user@example.com", None)
    naive = len(json.dumps(ctx.transcript, ensure_ascii=False, indent=2))
    assert len(context) < naive * 0.25  # bounded, not merely trimmed
    assert "summarized_older_steps" in context
    # only the last 3 corrections steer the next attempt
    assert "hint 9" in context and "hint 0" not in context


def test_transcript_budget_from_env_clamps_and_defaults():
    budget = TranscriptBudget.from_env({
        "LATTICEAI_AGENT_TRANSCRIPT_WINDOW": "1",
        "LATTICEAI_AGENT_TRANSCRIPT_CHARS": "50",
        "LATTICEAI_AGENT_VERIFY_CHARS": "junk",
    })
    assert budget.window == 2        # floored — a window of 1 starves context
    assert budget.result_chars == 120
    assert budget.verify_chars == 1200


# ── on_step observer (live timeline feed) ───────────────────────────────

def _scripted_runtime(events):
    plan_json = json.dumps({
        "action": "plan", "goal": "make a file",
        "steps": [{"action": "write_file", "args": {"path": "notes.txt"},
                   "description": "write it"}],
        "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
    })
    exec_replies = [
        json.dumps({"thoughts": "write", "action": "write_file",
                    "args": {"path": "notes.txt", "content": "hello"}}),
        json.dumps({"thoughts": "done", "action": "final", "message": "done"}),
    ]
    state = {"i": 0}

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        if "execution plan" in message:
            return plan_json
        if message == "Execute the next step.":
            reply = exec_replies[min(state["i"], 1)]
            state["i"] += 1
            return reply
        return json.dumps({"action": "verdict", "verdict": "PASS",
                           "next_state": "DONE", "reason": "ok", "corrections": []})

    async def generate(**kwargs):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    return SingleAgentRuntime(_deps(generate_as=generate_as, generate=generate))


def test_on_step_observer_sees_the_whole_loop():
    events = []
    runtime = _scripted_runtime(events)
    ctx = AgentRunContext()
    ctx.on_step = events.append
    ctx.state = AgentState.PLANNING

    class Req:
        message = "make a file"
        conversation_id = None
        temperature = 0.1
        workspace_id = None
        source = "test"

    async def run():
        await runtime.plan(ctx, Req(), "Korean", "u@example.com")
        runtime.approve(ctx, "u@example.com")
        await runtime.run_to_completion(ctx, Req(), "Korean", "u@example.com", 5, 3)

    asyncio.run(run())
    kinds = [(e["phase"], e["event"]) for e in events]
    assert ("plan", "planned") in kinds
    assert ("approval", "decision") in kinds
    assert ("execute", "tool") in kinds
    assert ("execute", "final") in kinds
    assert ("verify", "verdict") in kinds
    assert kinds[-1] == ("terminal", "state")
    tool_event = next(e for e in events if e["event"] == "tool")
    assert tool_event["action"] == "write_file"
    assert tool_event["ok"] is True
    assert tool_event["step"] == 1
    assert tool_event["path"] == "notes.txt"
    assert events[-1]["state"] == "DONE"


def test_broken_observer_never_breaks_the_run():
    runtime = _scripted_runtime([])
    ctx = AgentRunContext()

    def explode(event):
        raise RuntimeError("observer bug")

    ctx.on_step = explode
    ctx.state = AgentState.PLANNING

    class Req:
        message = "make a file"
        conversation_id = None
        temperature = 0.1
        workspace_id = None
        source = "test"

    async def run():
        await runtime.plan(ctx, Req(), "Korean", "u@example.com")
        runtime.approve(ctx, "u@example.com")
        await runtime.run_to_completion(ctx, Req(), "Korean", "u@example.com", 5, 3)

    asyncio.run(run())
    assert ctx.state == AgentState.DONE


# ── live SSE framing ────────────────────────────────────────────────────

def _collect_frames(stream):
    async def gather():
        return [frame async for frame in stream]

    return asyncio.run(gather())


def test_agent_live_stream_emits_step_frames_then_payload():
    async def start(observer):
        observer({"phase": "plan", "event": "planned", "steps": 1})
        observer({"phase": "execute", "event": "tool", "action": "write_file", "ok": True})
        return {"response": "made it", "status": "ok"}

    class Router:
        current_model_id = "local-model"

    frames = _collect_frames(agent_live_stream(start, router=Router()))
    step_frames = [f for f in frames if f.startswith("event: agent_step\n")]
    assert len(step_frames) == 2
    first = json.loads(step_frames[0].split("data: ", 1)[1])
    assert first == {"phase": "plan", "event": "planned", "steps": 1}
    # named frames come before the classic payload frames; shape is unchanged
    assert frames[-1] == "data: [DONE]\n\n"
    payload_frame = json.loads(frames[-3].split("data: ", 1)[1])
    assert payload_frame["chunk"] == "made it"
    assert payload_frame["agent"]["status"] == "ok"


def test_agent_live_stream_reports_start_errors_honestly():
    async def start(observer):
        raise HTTPException(status_code=400, detail="No model loaded.")

    class Router:
        current_model_id = "local-model"

    frames = _collect_frames(agent_live_stream(start, router=Router()))
    error_frame = json.loads(frames[0].split("data: ", 1)[1])
    assert error_frame["error"] == "No model loaded."
    assert frames[-1] == "data: [DONE]\n\n"


def test_agent_live_stream_finalize_failure_keeps_the_payload():
    async def start(observer):
        return {"response": "answer", "status": "ok"}

    def finalize(result):
        raise RuntimeError("history write failed")

    class Router:
        current_model_id = "m"

    frames = _collect_frames(agent_live_stream(start, router=Router(), finalize=finalize))
    payload_frame = json.loads(frames[0].split("data: ", 1)[1])
    assert payload_frame["chunk"] == "answer"
    assert frames[-1] == "data: [DONE]\n\n"


# ── manifest-aware planning (Wave 0.4) ──────────────────────────────────

def test_normalize_plan_rewrites_partial_file_plan_to_manifest():
    plan = {"goal": "메모 앱", "steps": [
        {"action": "write_file", "args": {"path": "memo.html"}, "description": "page"},
    ]}
    normalized, fixes = normalize_plan(plan, "메모 앱 html css js로 만들어줘")
    assert "manifest_rewrite" in fixes
    paths = [s["args"]["path"] for s in normalized["steps"]]
    assert paths == ["index.html", "style.css", "app.js"]
    # briefs ride along as step descriptions for the executor
    assert "stylesheet" in normalized["steps"][0]["description"]


def test_normalize_plan_fills_empty_plan_from_manifest():
    normalized, fixes = normalize_plan({}, "todo 앱 html css js로 만들어줘")
    assert "manifest_steps" in fixes
    assert len(normalized["steps"]) == 3
    assert normalized["estimated_steps"] == 3


def test_normalize_plan_leaves_covering_or_mixed_plans_alone():
    covering = {"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "a.html"}},
        {"action": "write_file", "args": {"path": "b.css"}},
        {"action": "write_file", "args": {"path": "c.js"}},
    ]}
    normalized, fixes = normalize_plan(covering, "메모 앱 html css js로 만들어줘")
    assert "manifest_rewrite" not in fixes
    assert [s["args"]["path"] for s in normalized["steps"]] == ["a.html", "b.css", "c.js"]

    mixed = {"goal": "g", "steps": [
        {"action": "read_file", "args": {"path": "spec.md"}},
        {"action": "write_file", "args": {"path": "memo.html"}},
    ]}
    normalized, fixes = normalize_plan(mixed, "메모 앱 html css js로 만들어줘")
    assert "manifest_rewrite" not in fixes
    assert len(normalized["steps"]) == 2


# ── manifest expansion (Wave 4) ─────────────────────────────────────────

def test_react_manifest_inference():
    manifest = infer_project_manifest("react로 계산기 앱 만들어줘")
    assert manifest is not None and manifest["kind"] == "react"
    paths = [f["path"] for f in manifest["files"]]
    assert paths == ["package.json", "index.html", "src/main.jsx", "src/App.jsx", "src/App.css"]


def test_python_package_manifest_inference():
    manifest = infer_project_manifest("날짜 계산 python 패키지 만들어줘")
    assert manifest is not None and manifest["kind"] == "python"
    paths = [f["path"] for f in manifest["files"]]
    assert f"{manifest['name']}/__init__.py" in paths
    assert f"{manifest['name']}/cli.py" in paths
    assert "README.md" in paths


def test_single_file_requests_still_bypass_manifests():
    assert infer_project_manifest("csv 파일 만들어줘") is None
    assert infer_project_manifest("index.html 만들어줘") is None  # explicit filename
    assert infer_project_manifest("react가 뭐야?") is None  # no creation verb


# ── terminal-state learning policy (§4.2 L6) ────────────────────────────

def test_memory_update_records_failure_status_and_context():
    recorded = {}
    prompts = {}

    class BrainMemory:
        def record_experience(self, title, learnings, *, run, user_email):
            recorded.update({"title": title, "learnings": learnings, "run": run})

    async def generate(*, message, context, max_tokens, temperature):
        prompts["context"] = context
        return json.dumps({
            "action": "memory",
            "learnings": ["도구 승인 게이트에 막히면 계획 단계에서 승인 요구를 명시해야 한다"],
            "save_to_knowledge": True,
        })

    runtime = SingleAgentRuntime(_deps(generate=generate, brain_memory=BrainMemory()))
    ctx = AgentRunContext()
    ctx.state = AgentState.FAILED
    ctx.transcript = [{"state": "EXECUTING", "action": "run_command", "error": "blocked"}]

    class Req:
        message = "위험한 작업을 해줘"

    asyncio.run(runtime.memory_update(ctx, Req(), "u@example.com"))
    assert recorded["run"]["status"] == "failed"
    assert "FAILED" in prompts["context"]
    assert "went wrong" in prompts["context"]
