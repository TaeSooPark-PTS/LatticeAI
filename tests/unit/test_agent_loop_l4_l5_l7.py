"""Agent loop L4/L5/L7 hardening shipped in 9.9.5.

L4 — critic sees a deterministic artifact checklist (sanitize/repair flags)
L5 — executor sees files this run already wrote (mid-run workspace awareness)
L7 — rollback is none|git|snapshot, not git-only
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
    artifact_checklist,
    files_written,
)


FILE_CREATE = frozenset({"write_file"})


def _deps(*, scripted=None, tool_calls=None, snapshot_file=None, restore_snapshot=None, rollback_file=None):
    scripted = list(scripted or [])
    tool_calls = tool_calls if tool_calls is not None else []

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        if scripted:
            return scripted.pop(0)
        return json.dumps({"action": "final", "message": "done"})

    async def generate(*, message, context, max_tokens, temperature):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    def execute_tool(name, args):
        tool_calls.append({"action": name, "args": dict(args)})
        return {"success": True, "path": args.get("path"), "bytes": 3}

    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: {
            "risk": "write", "destructive": False, "shell": False,
            "network": False, "auto_approve": True, "sandbox": "workspace",
            "rollback": "none",
        },
        risk_level=lambda policy: "medium",
        check_role=lambda name, user: None,
        tool_governance={"write_file": {"auto_approve": True}},
        file_create_actions=FILE_CREATE,
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=None,
        rollback_file=rollback_file,
        snapshot_file=snapshot_file,
        restore_snapshot=restore_snapshot,
    )


# ── L4 artifact checklist ──────────────────────────────────────────────

def test_artifact_checklist_surfaces_repair_flags():
    transcript = [
        {
            "state": AgentState.EXECUTING.value,
            "action": "write_file",
            "result": {"path": "a.html"},
            "content_sanitize": {"sanitized": True, "repaired": True},
        },
        {
            "state": AgentState.EXECUTING.value,
            "action": "write_file",
            "result": {"path": "b.css"},
            "content_sanitize": {"sanitized": True, "repaired": False},
        },
        {
            "state": AgentState.EXECUTING.value,
            "action": "run_command",
            "result": {"output": "ok"},
        },
    ]
    checklist = artifact_checklist(transcript, FILE_CREATE)
    assert checklist == [
        {"path": "a.html", "sanitized": True, "repaired": True},
        {"path": "b.css", "sanitized": True, "repaired": False},
    ]


def test_critic_prompt_includes_artifact_checklist():
    captured = []

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        captured.append(context)
        return json.dumps({
            "action": "verdict", "verdict": "PASS",
            "next_state": "DONE", "reason": "ok", "corrections": [],
        })

    deps = _deps()
    deps.generate_as = generate_as
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "write_file",
        "result": {"path": "index.html"},
        "content_sanitize": {"sanitized": True, "repaired": True},
    })
    ctx.state = AgentState.VERIFYING
    req = SimpleNamespace(message="make a page", planning_model=None,
                          executing_model=None, reviewing_model=None)
    asyncio.run(rt.verify(ctx, req, "ko", "tester"))
    assert any("Artifact checklist" in c and "index.html" in c and "auto-REPAIRED" in c
               for c in captured)


# ── L5 mid-run workspace awareness ─────────────────────────────────────

def test_files_written_lists_unique_paths_in_order():
    transcript = [
        {"state": AgentState.EXECUTING.value, "action": "write_file",
         "result": {"path": "a.py"}},
        {"state": AgentState.EXECUTING.value, "action": "write_file",
         "result": {"path": "b.py"}},
        {"state": AgentState.EXECUTING.value, "action": "write_file",
         "result": {"path": "a.py"}},  # rewrite — still one entry
        {"state": AgentState.EXECUTING.value, "action": "run_command",
         "result": {"output": "x"}},
    ]
    assert files_written(transcript, FILE_CREATE) == ["a.py", "b.py"]


def test_executor_prompt_lists_files_already_written():
    deps = _deps()
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.plan = {"goal": "write then explain", "steps": []}
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "write_file",
        "result": {"path": "hello.py"},
    })
    ctx.state = AgentState.EXECUTING
    req = SimpleNamespace(
        message="write hello.py then explain it",
        conversation_id=None,
        planning_model=None, executing_model=None, reviewing_model=None,
    )
    prompt = rt._executor_context(ctx, req, "ko", "tester", None)
    assert "Files written by this run so far" in prompt
    assert "hello.py" in prompt


# ── L7 rollback: none|git|snapshot ─────────────────────────────────────

def test_rollback_prefers_git_when_governed_and_available():
    rolled = []

    def rollback_file(path):
        rolled.append(path)
        return {"path": path, "ok": True}

    deps = _deps(rollback_file=rollback_file)
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "edit_file",
        "args": {"path": "tracked.py"},
        "governance": {"rollback": "git"},
        "result": {"path": "tracked.py"},
    })
    rt.rollback(ctx, "tester")
    entry = ctx.transcript[-1]["rolled_back"][0]
    assert entry["mode"] == "git"
    assert entry["ok"] is True
    assert rolled == ["tracked.py"]


def test_rollback_uses_snapshot_when_git_unavailable():
    restored = []

    def restore_snapshot(path, content):
        restored.append((path, content))
        return {"path": path, "ok": True, "action": "deleted" if content is None else "restored"}

    deps = _deps(
        snapshot_file=lambda path: {"existed": False, "content": None, "too_large": False},
        restore_snapshot=restore_snapshot,
        rollback_file=lambda path: {"path": path, "ok": False, "error": "not a git repo"},
    )
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    # Simulate a pre-write snapshot already captured for a newly created file.
    ctx.rollback_log.append({"path": "new.py", "existed": False, "content": None, "too_large": False})
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "write_file",
        "args": {"path": "new.py"},
        "governance": {"rollback": "none"},
        "result": {"path": "new.py"},
    })
    rt.rollback(ctx, "tester")
    entry = ctx.transcript[-1]["rolled_back"][0]
    assert entry["mode"] == "snapshot"
    assert entry["ok"] is True
    assert restored == [("new.py", None)]


def test_rollback_reports_mode_none_when_no_recovery_path():
    deps = _deps()
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.transcript.append({
        "state": AgentState.EXECUTING.value,
        "action": "write_file",
        "args": {"path": "orphan.py"},
        "governance": {"rollback": "none"},
        "result": {"path": "orphan.py"},
    })
    rt.rollback(ctx, "tester")
    entry = ctx.transcript[-1]["rolled_back"][0]
    assert entry["mode"] == "none"
    assert entry["ok"] is False


def test_pre_write_snapshot_captured_once_per_path():
    snaps = []

    def snapshot_file(path):
        snaps.append(path)
        return {"existed": True, "content": "old", "too_large": False}

    scripted = [
        json.dumps({"thoughts": "w1", "action": "write_file", "args": {"path": "x.py", "content": "a"}}),
        json.dumps({"thoughts": "w2", "action": "write_file", "args": {"path": "x.py", "content": "b"}}),
        json.dumps({"thoughts": "done", "action": "final", "message": "ok"}),
        json.dumps({"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                    "reason": "ok", "corrections": []}),
    ]
    deps = _deps(scripted=scripted, snapshot_file=snapshot_file)
    rt = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.plan = {"goal": "write x", "steps": [{"action": "write_file"}], "requires_approval": False}
    ctx.state = AgentState.EXECUTING
    req = SimpleNamespace(
        message="write x.py",
        conversation_id=None,
        workspace_id=None,
        temperature=0.2,
        planning_model=None, executing_model=None, reviewing_model=None,
    )
    asyncio.run(rt.run_to_completion(ctx, req, "ko", "tester", max_steps=10, max_retry=1))
    # First capture only — second write to the same path must not overwrite the
    # true pre-run snapshot.
    assert snaps == ["x.py"]
    assert ctx.rollback_log[0]["content"] == "old"
