"""wp32 coverage — the single-agent state machine's recovery and refusal paths.

Everything the loop touches arrives through ``AgentDeps``, so each test builds
a runtime over fakes (a canned-JSON model, a recording tool executor, recording
audit/rollback ports) and drives the real phase methods. The paths under test
are the ones that only appear when something goes wrong: an unparseable plan, a
model that never produces a tool call, a blocked tool, a snapshot that fails, a
critic that contradicts itself, and rollback with no usable recovery.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from latticeai.core import agent as agent_module
from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.agent_profiles import AgentProfile

_AUTO_POLICY = {
    "auto_approve": True, "risk": "write", "shell": False, "network": False,
    "destructive": False, "sandbox": "workspace", "rollback": "none",
}
_MANUAL_POLICY = {
    "auto_approve": False, "risk": "exec", "shell": True, "network": False,
    "destructive": False, "sandbox": "workspace", "rollback": "none",
}


class _Req:
    def __init__(self, message="파일 하나 만들어줘", **overrides):
        self.message = message
        self.conversation_id = None
        self.temperature = 0.2
        self.workspace_id = None
        self.source = "test"
        for key, value in overrides.items():
            setattr(self, key, value)


class _Harness:
    """A runtime plus the recorders its ports write into."""

    def __init__(self, runtime, *, tool_calls, audits, saved, replies):
        self.runtime = runtime
        self.tool_calls = tool_calls
        self.audits = audits
        self.saved = saved
        self.replies = replies

    def audit_events(self):
        return [event for event, _kwargs in self.audits]


def _harness(tmp_path, *, replies=(), policies=None, memory_reply=None, **overrides):
    queue = list(replies)
    tool_calls: list = []
    audits: list = []
    saved: list = []

    async def generate_as(model_id, message, context, max_tokens, temperature):
        if not queue:
            return '{"action": "final", "message": "done"}'
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def generate(**_kwargs):
        if isinstance(memory_reply, BaseException):
            raise memory_reply
        return memory_reply or '{"save_to_knowledge": false, "learnings": []}'

    def execute_tool(name, args):
        tool_calls.append((name, dict(args)))
        path = args.get("path")
        if path:
            target = Path(tmp_path) / str(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content") or ""), encoding="utf-8")
        return {"ok": True, "path": path or ""}

    table = dict(policies or {})

    deps_kwargs = dict(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: dict(table.get(name, _AUTO_POLICY)),
        risk_level=lambda policy: policy["risk"],
        check_role=lambda _name, _user: None,
        tool_governance={"write_file": dict(_AUTO_POLICY)},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **_kwargs: "",
        clear_history=lambda keep: {"ok": True, "kept": keep},
        knowledge_save=lambda text, **kwargs: saved.append((text, kwargs)),
        audit=lambda event, **kwargs: audits.append((event, kwargs)),
        planner_prompt="plan", executor_prompt="exec", critic_prompt="critic",
        memory_updater_prompt="memory", agent_root=Path(tmp_path),
    )
    deps_kwargs.update(overrides)
    return _Harness(
        SingleAgentRuntime(AgentDeps(**deps_kwargs)),
        tool_calls=tool_calls, audits=audits, saved=saved, replies=queue,
    )


def _ctx(state=AgentState.EXECUTING, **fields):
    ctx = AgentRunContext()
    ctx.state = state
    for key, value in fields.items():
        setattr(ctx, key, value)
    return ctx


# ── PLAN ────────────────────────────────────────────────────────────────────


def test_an_unparseable_plan_becomes_a_deterministic_empty_plan(tmp_path):
    harness = _harness(tmp_path, replies=["I think we should start by..."])
    ctx = _ctx(AgentState.PLANNING)
    req = _Req("지금 몇 시야?")

    asyncio.run(harness.runtime.plan(ctx, req, "ko", "u@t"))

    assert ctx.state is AgentState.WAITING_APPROVAL
    assert ctx.plan["goal"] == "지금 몇 시야?"
    assert ctx.plan["steps"] == []
    assert ctx.plan["rollback_strategy"] == "none"
    parse_errors = [e for e in ctx.trace.events if e.get("kind") == "parse_error"]
    assert parse_errors and parse_errors[0]["recovered"] is True


def test_plan_repairs_are_recorded_on_the_trace(tmp_path):
    harness = _harness(tmp_path, replies=[
        '{"action": "plan", "goal": "make notes", '
        '"steps": ["not a step", {"action": "write_file", "args": {"path": "notes.md"}}]}',
    ])
    ctx = _ctx(AgentState.PLANNING)

    asyncio.run(harness.runtime.plan(ctx, _Req("메모 파일 만들어줘"), "ko", "u@t"))

    assert [step["action"] for step in ctx.plan["steps"]] == ["write_file"]
    repairs = [e for e in ctx.trace.events if e.get("kind") == "repair"]
    assert any("steps_filtered" in (e.get("repairs") or []) for e in repairs)
    assert ctx.transcript[0]["plan_fixes"] == ["steps_filtered"]


# ── EXECUTE ─────────────────────────────────────────────────────────────────


def test_clear_history_runs_without_the_governance_gates(tmp_path):
    harness = _harness(tmp_path, replies=[
        '{"action": "clear_history", "thoughts": "tidy up", "args": {"keep_last": 5}}',
        '{"action": "final", "message": "done"}',
    ])
    ctx = _ctx()

    asyncio.run(harness.runtime.execute(ctx, _Req(), "ko", "u@t", max_steps=4))

    cleared = [s for s in ctx.transcript if s.get("action") == "clear_history"]
    assert cleared and cleared[0]["result"] == {"ok": True, "kept": 5}
    assert harness.tool_calls == []  # never routed through the tool dispatcher
    assert ctx.state is AgentState.VERIFYING


def test_a_tool_needing_approval_is_blocked_audited_and_skipped(tmp_path):
    harness = _harness(
        tmp_path,
        replies=[
            '{"action": "run_command", "thoughts": "list files", "args": {"command": "ls"}}',
            '{"action": "final", "message": "gave up"}',
        ],
        policies={"run_command": dict(_MANUAL_POLICY)},
    )
    ctx = _ctx()

    asyncio.run(harness.runtime.execute(ctx, _Req(), "ko", "u@t", max_steps=4))

    blocked = [s for s in ctx.transcript if s.get("action") == "run_command"]
    assert blocked and "requires explicit approval" in blocked[0]["error"]
    assert blocked[0]["permission_mode"] == "strict"
    assert harness.tool_calls == []
    assert "agent_exec" in harness.audit_events()
    tool_events = [e for e in ctx.trace.events if e.get("kind") == "tool"]
    assert tool_events[0]["outcome"] == "blocked_approval"


def test_a_failing_pre_write_snapshot_never_blocks_the_write(tmp_path, caplog):
    def snapshot_file(_path):
        raise OSError("snapshot volume is unreadable")

    harness = _harness(
        tmp_path,
        replies=[
            '{"action": "write_file", "args": {"path": "notes.md", "content": "hi"}}',
            '{"action": "final", "message": "done"}',
        ],
        snapshot_file=snapshot_file,
    )
    ctx = _ctx()

    with caplog.at_level("WARNING"):
        asyncio.run(harness.runtime.execute(ctx, _Req(), "ko", "u@t", max_steps=4))

    assert harness.tool_calls == [("write_file", {"path": "notes.md", "content": "hi"})]
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "hi"
    assert ctx.rollback_log == []
    assert "pre-write snapshot failed for notes.md" in caplog.text


# ── direct-path fallback ────────────────────────────────────────────────────

_COMPACT = AgentProfile(
    name="compact-test", transcript_window=4, parse_failure_budget=1,
    escalate_after=1, direct_path_fallback=True,
)

_PAGE = "<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n"


def test_the_direct_path_infers_a_target_when_the_plan_has_no_file_steps(tmp_path):
    harness = _harness(
        tmp_path,
        replies=["I am not going to emit JSON", _PAGE],
        agent_profile=_COMPACT,
    )
    ctx = _ctx(plan={
        "goal": "make a page",
        "steps": ["a bare string, not a step", {"action": "run_command", "args": {}}],
    })

    asyncio.run(harness.runtime.execute(
        ctx, _Req("html 파일 만들어줘"), "ko", "u@t", max_steps=3,
    ))

    assert [name for name, _args in harness.tool_calls] == ["write_file"]
    assert harness.tool_calls[0][1]["path"] == "generated_page.html"
    assert (tmp_path / "generated_page.html").read_text(encoding="utf-8").startswith("<!doctype")
    assert ctx.transcript[-1]["direct_path"] is True
    assert "직접 생성" in ctx.final_message
    assert ctx.state is AgentState.VERIFYING


def test_the_direct_path_gives_up_honestly_when_generation_raises(tmp_path, monkeypatch, caplog):
    async def exploding_generate_file_content(*_args, **_kwargs):
        raise RuntimeError("content pipeline is down")

    # ``_direct_file_path`` calls the generator through its own module globals,
    # which after the v11.3.0 split is ``agent.execution``: a name rebound on
    # the package ``__init__`` would leave that call untouched.
    monkeypatch.setattr(
        agent_module.execution, "generate_file_content", exploding_generate_file_content,
    )
    harness = _harness(
        tmp_path, replies=["still not JSON"], agent_profile=_COMPACT,
    )
    ctx = _ctx(plan={
        "goal": "make notes",
        "steps": [{"action": "write_file", "args": {"path": "notes.md"}}],
    })

    with caplog.at_level("WARNING"):
        asyncio.run(harness.runtime.execute(ctx, _Req(), "ko", "u@t", max_steps=3))

    assert harness.tool_calls == []  # no fabricated evidence
    assert ctx.final_message == ""
    assert "direct file path generation failed for notes.md" in caplog.text
    assert ctx.state is AgentState.VERIFYING


# ── VERIFY ──────────────────────────────────────────────────────────────────


def _evidence_ctx(**fields):
    return _ctx(
        AgentState.VERIFYING,
        transcript=[{
            "state": AgentState.EXECUTING.value, "action": "run_command",
            "result": {"ok": True},
        }],
        plan={"goal": "do the thing"},
        **fields,
    )


def test_the_critic_gets_one_strict_retry_before_the_run_fails_closed(tmp_path):
    harness = _harness(tmp_path, replies=[
        "PASS, looks good to me!",
        '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "all good"}',
    ])
    ctx = _evidence_ctx()

    asyncio.run(harness.runtime.verify(ctx, _Req("작업해줘"), "ko", "u@t"))

    assert ctx.state is AgentState.DONE
    assert ctx.final_message == "all good"
    verdict_steps = [s for s in ctx.transcript if s.get("verdict")]
    assert verdict_steps[-1]["verifier_available"] is True


def test_a_pass_that_left_requested_files_unwritten_needs_review(tmp_path):
    harness = _harness(tmp_path, replies=[
        '{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "done"}',
    ])
    ctx = _evidence_ctx()

    asyncio.run(harness.runtime.verify(ctx, _Req("react 앱 만들어줘"), "ko", "u@t"))

    assert ctx.state is AgentState.NEEDS_REVIEW
    assert "완료로 처리하지 않았습니다" in ctx.final_message
    coverage = ctx.transcript[-1]["requirement_coverage"]
    assert coverage["complete"] is False
    assert "index.html" in coverage["missing_files"]
    decisions = [e.get("decision") for e in ctx.trace.events]
    assert "needs_review_missing_files" in decisions


def test_a_fail_verdict_asking_for_rollback_enters_rollback(tmp_path):
    harness = _harness(tmp_path, replies=[
        '{"action": "verdict", "verdict": "FAIL", "next_state": "ROLLBACK", "reason": "broken"}',
    ])
    ctx = _evidence_ctx()

    asyncio.run(harness.runtime.verify(ctx, _Req("작업해줘"), "ko", "u@t"))

    assert ctx.state is AgentState.ROLLBACK


def test_a_done_without_a_pass_is_treated_as_inconsistent(tmp_path):
    harness = _harness(tmp_path, replies=[
        '{"action": "verdict", "verdict": "FAIL", "next_state": "DONE", "reason": "eh"}',
    ])
    ctx = _evidence_ctx()

    asyncio.run(harness.runtime.verify(ctx, _Req("작업해줘"), "ko", "u@t"))

    assert ctx.state is AgentState.NEEDS_REVIEW
    assert "일관되지 않아" in ctx.final_message
    decisions = [e.get("decision") for e in ctx.trace.events]
    assert "needs_review_inconsistent_verdict" in decisions


# ── ROLLBACK ────────────────────────────────────────────────────────────────


def _written_step(path, *, rollback="git", action="write_file"):
    return {
        "state": AgentState.EXECUTING.value, "action": action,
        "args": {"path": path}, "governance": {"rollback": rollback},
        "result": {"ok": True, "path": path},
    }


def test_a_git_rollback_that_raises_falls_through_to_no_recovery(tmp_path):
    def rollback_file(_path):
        raise RuntimeError("not a git workspace")

    harness = _harness(tmp_path, rollback_file=rollback_file)
    ctx = _ctx(AgentState.ROLLBACK, transcript=[_written_step("notes.md")])

    harness.runtime.rollback(ctx, "u@t")

    rolled = ctx.transcript[-1]["rolled_back"]
    assert rolled == [{
        "path": "notes.md", "ok": False, "mode": "none",
        "error": "no rollback available (git not applicable, no usable snapshot)",
    }]
    assert ctx.state is AgentState.FAILED
    assert "복구할 파일이 없거나" in ctx.final_message


def test_a_snapshot_restore_that_raises_is_reported_not_swallowed(tmp_path):
    def restore_snapshot(_path, _content):
        raise OSError("target is read-only")

    harness = _harness(
        tmp_path,
        rollback_file=lambda _path: {"ok": False},
        restore_snapshot=restore_snapshot,
    )
    ctx = _ctx(
        AgentState.ROLLBACK,
        transcript=[_written_step("notes.md")],
        rollback_log=[{"path": "notes.md", "existed": True, "content": "old"}],
    )

    harness.runtime.rollback(ctx, "u@t")

    rolled = ctx.transcript[-1]["rolled_back"]
    assert rolled[0]["ok"] is False
    assert rolled[0]["mode"] == "snapshot"
    assert rolled[0]["error"] == "target is read-only"
    assert rolled[0]["path"] == "notes.md"


def test_rollback_only_considers_recoverable_executing_steps(tmp_path):
    recovered: list = []

    def rollback_file(path):
        recovered.append(path)
        return {"path": path, "ok": True}

    harness = _harness(tmp_path, rollback_file=rollback_file)
    ctx = _ctx(AgentState.ROLLBACK, transcript=[
        {"state": AgentState.VERIFYING.value, "verdict": "FAIL"},        # not EXECUTING
        {"state": AgentState.EXECUTING.value, "action": "write_file",
         "error": "blocked"},                                            # no result dict
        {"state": AgentState.EXECUTING.value, "action": "run_command",
         "governance": {"rollback": "git"}, "result": {"ok": True}},     # no path
        _written_step("notes.md"),
        _written_step("notes.md"),                                       # duplicate path
        {"state": AgentState.EXECUTING.value, "action": "run_command",
         "governance": {"rollback": "none"},
         "result": {"ok": True, "path": "out.log"}},                     # not recoverable
    ])

    harness.runtime.rollback(ctx, "u@t")

    assert recovered == ["notes.md"]
    rolled = ctx.transcript[-1]["rolled_back"]
    assert [entry["path"] for entry in rolled] == ["notes.md"]
    assert rolled[0]["mode"] == "git"
    assert "notes.md (git)" in ctx.final_message
    assert "agent_rollback" in harness.audit_events()


# ── MEMORY ──────────────────────────────────────────────────────────────────


def test_learnings_go_to_the_vault_when_no_brain_memory_is_wired(tmp_path):
    harness = _harness(
        tmp_path,
        memory_reply=(
            '{"action": "memory", "save_to_knowledge": true, "learnings": '
            '["MLX 로더는 config.json 이 없으면 조용히 실패한다는 점을 확인했다"]}'
        ),
    )
    ctx = _ctx(AgentState.DONE, transcript=[{"state": "EXECUTING", "action": "write_file"}])

    asyncio.run(harness.runtime.memory_update(ctx, _Req("리포트 만들어줘"), "u@t"))

    assert len(harness.saved) == 1
    text, kwargs = harness.saved[0]
    assert "MLX 로더는" in text
    assert kwargs["folder"] == "30_Projects"
    assert kwargs["title"].startswith("Agent: 리포트")


def test_a_memory_update_failure_is_logged_and_never_raised(tmp_path, caplog):
    harness = _harness(tmp_path, memory_reply=RuntimeError("memory model offline"))
    ctx = _ctx(AgentState.FAILED)

    with caplog.at_level("WARNING"):
        asyncio.run(harness.runtime.memory_update(ctx, _Req(), "u@t"))

    assert "agent memory update failed: memory model offline" in caplog.text
    assert harness.saved == []


# ── DRIVE LOOP ──────────────────────────────────────────────────────────────


def test_the_loop_halts_itself_at_the_state_machine_ceiling(tmp_path):
    harness = _harness(tmp_path)
    ctx = _ctx(AgentState.EXECUTING, state_history=["EXECUTING"] * 200)

    asyncio.run(harness.runtime.run_to_completion(
        ctx, _Req(), "ko", "u@t", max_steps=1, max_retry=1,
    ))

    assert ctx.state is AgentState.FAILED
    assert "최대 반복(200)" in ctx.final_message
    assert harness.tool_calls == []


def test_the_loop_drives_rollback_to_a_failed_terminal_state(tmp_path):
    harness = _harness(tmp_path, rollback_file=lambda path: {"path": path, "ok": True})
    ctx = _ctx(AgentState.ROLLBACK, transcript=[_written_step("notes.md")])

    asyncio.run(harness.runtime.run_to_completion(
        ctx, _Req(), "ko", "u@t", max_steps=1, max_retry=1,
    ))

    assert ctx.state is AgentState.FAILED
    assert ctx.state_history[-1] == "FAILED"
    assert "notes.md (git)" in ctx.final_message


@pytest.mark.parametrize("state", [AgentState.IDLE, AgentState.PLANNING, AgentState.WAITING_APPROVAL])
def test_a_non_drivable_state_fails_instead_of_spinning(tmp_path, state):
    harness = _harness(tmp_path)
    ctx = _ctx(state)

    asyncio.run(harness.runtime.run_to_completion(
        ctx, _Req(), "ko", "u@t", max_steps=1, max_retry=1,
    ))

    assert ctx.state is AgentState.FAILED
    assert ctx.state_history == [state.value, "FAILED"]
