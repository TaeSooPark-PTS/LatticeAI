"""Fail-closed overwrite guard inside the agent loop (mode-invariant).

A tool call that rewrites content which already exists but that the
ChangeProposalService cannot stage as a reviewable proposal — the binary
document creators (``create_docx`` and friends) and the home-sandbox
``local_write`` — has no safe apply path in *any* permission mode. The HTTP
surface has always refused it with 409 (``ToolDispatchService.enforce_policy``);
the agent loop did not, so ``trusted``/``bypass`` runs silently overwrote files
the API would have rejected.

Two invariants are pinned here:

1. ``SingleAgentRuntime._blocked_by_gates`` blocks the call in every mode, with
   a ``NEEDS_REVIEW:`` transcript error, an ``overwrite_fail_closed`` audit
   record, and no tool execution.
2. Both surfaces resolve the *real* write target through
   ``document_output_target`` first. ``create_docx`` sanitizes ``filename``
   into ``generated_documents/``, so an existence check against the raw
   argument inspects a path nothing ever writes — and the guard never fires.
   This is the bug the ``_governed_path_exists(tool_name, path)`` signature
   change fixes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import latticeai.core.agent as agent_module
import latticeai.services.tool_dispatch as tool_dispatch_module
import latticeai.tools as tools
from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.permission_mode import PermissionMode
from latticeai.services.tool_dispatch import ToolDispatchService
from latticeai.tools import document_output_target

# Every mode, named explicitly: the guard is mode-invariant, so the trusted /
# bypass dials that skip the approval *prompt* must not skip this check either.
MODES = [
    PermissionMode.STRICT.value,   # the default dial
    PermissionMode.TRUSTED.value,
    PermissionMode.BYPASS.value,
]

# What a model hands to create_docx: a directory it does not control plus
# characters the creator refuses to keep.
RAW_FILENAME = "reports/월간(Q1) 보고서.docx"
# Where create_docx *actually* writes it: DOCUMENT_OUTPUT_DIR + sanitized name.
# Hardcoded on purpose — deriving it from document_output_target would only
# prove the guard agrees with itself. `test_create_docx_really_writes_to_the_
# sanitized_target` anchors this constant against the real creator.
SANITIZED_TARGET = "generated_documents/월간_Q1_ 보고서.docx"

EXISTING_BYTES = b"PK\x03\x04 last quarter's report"

# A plausible-but-unused payload key. The blocked transcript entry must drop it
# (blocked decisions are worth replaying, model-authored payloads are not).
PAYLOAD = "x" * 256

_UNSET = object()


class _Req:
    message = "지난 분기 보고서를 다시 만들어줘"
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "test"


# The real registry policy for create_docx (`_w()`): workspace write, not
# auto-approved. Kept faithful so the test cannot pass by weakening the gates.
DOCX_POLICY = {
    "risk": "write", "destructive": False, "shell": False, "network": False,
    "auto_approve": False, "sandbox": "workspace", "rollback": "none",
}
# local_write: same, but the home sandbox.
LOCAL_WRITE_POLICY = {**DOCX_POLICY, "sandbox": "home"}

FINAL_REPLY = json.dumps({"thoughts": "끝", "action": "final", "message": "완료"})


def _docx_call(filename: str) -> str:
    return json.dumps({
        "thoughts": "보고서를 만든다",
        "action": "create_docx",
        "args": {
            "title": "월간 보고서",
            "body": "본문",
            "filename": filename,
            "content": PAYLOAD,
        },
    })


def _local_write_call(path: str) -> str:
    return json.dumps({
        "thoughts": "홈 폴더에 저장",
        "action": "local_write",
        "args": {"path": path, "content": PAYLOAD},
    })


def _build_runtime(
    tmp_path: Path,
    *,
    mode: str = PermissionMode.STRICT.value,
    replies=(),
    policy=None,
    agent_root=_UNSET,
):
    """A real SingleAgentRuntime over fake ports — the gates live in agent.py.

    ``change_governor`` stays None on purpose: the proposal service governs only
    write_file/edit_file, so for create_docx/local_write the overwrite guard is
    the *only* thing standing between the model and the disk.
    """
    executed: list = []
    audits: list = []
    queue = list(replies)

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        return queue.pop(0) if queue else FINAL_REPLY

    async def generate(**kwargs):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    def execute_tool(name, args):
        executed.append((name, dict(args)))
        return {"success": True, "path": args.get("filename") or args.get("path", "")}

    def audit(event, **fields):
        audits.append((event, fields))

    tool_policy = dict(policy or DOCX_POLICY)
    deps = AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: dict(tool_policy),
        risk_level=lambda p: "medium",
        check_role=lambda name, user: None,
        tool_governance={"create_docx": dict(tool_policy)},
        file_create_actions=frozenset({"create_docx", "write_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **kw: None,
        audit=audit,
        planner_prompt="p", executor_prompt="e", critic_prompt="c",
        memory_updater_prompt="m",
        agent_root=tmp_path if agent_root is _UNSET else agent_root,
        permission_mode=mode,
        change_governor=None,
    )
    return SingleAgentRuntime(deps), executed, audits


def _run_execute(runtime: SingleAgentRuntime, *, max_steps: int = 3) -> AgentRunContext:
    """Drive one EXECUTING pass with the plan already approved by a human.

    ``approved_by_human`` is the most permissive honest setup: it satisfies the
    classic approval gate in strict mode, so if the overwrite guard were removed
    the call would run under *every* mode in MODES — which is exactly what makes
    the mode-invariance assertions below load-bearing.
    """
    ctx = AgentRunContext()
    ctx.state = AgentState.EXECUTING
    ctx.approved_by_human = True
    ctx.plan = {"goal": "보고서 재생성", "steps": [{"action": "create_docx"}]}
    asyncio.run(runtime.execute(ctx, _Req(), "Korean", "owner@example.com", max_steps))
    return ctx


def _errors(ctx: AgentRunContext) -> list:
    return [step for step in ctx.transcript if step.get("error")]


def _seed_existing_target(tmp_path: Path, relative: str = SANITIZED_TARGET) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(EXISTING_BYTES)
    return target


# ── 1. mode invariance: the headline ────────────────────────────────────

@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_create_docx_overwrite_is_blocked_in_every_permission_mode(tmp_path, mode):
    """trusted/bypass skip the approval prompt; they never skip this check."""
    target = _seed_existing_target(tmp_path)
    runtime, executed, _ = _build_runtime(
        tmp_path, mode=mode, replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    assert executed == [], f"{mode}: the overwrite reached the tool executor"
    blocked = _errors(ctx)
    assert len(blocked) == 1
    entry = blocked[0]
    assert entry["error"].startswith("NEEDS_REVIEW:"), entry["error"]
    assert entry["action"] == "create_docx"
    assert entry["permission_mode"] == mode
    assert entry["change_class"] == "mutation"
    # the file on disk is byte-identical
    assert target.read_bytes() == EXISTING_BYTES
    assert ctx.trace.summary()["tool_outcomes"] == {"blocked_overwrite": 1}
    # a blocked step is not evidence — a critic PASS must not turn it into DONE
    assert runtime._has_execution_evidence(ctx) is False


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_create_docx_for_a_new_name_still_runs_in_every_mode(tmp_path, mode):
    """Not a blanket ban on the tool — only on rewriting an existing target."""
    _seed_existing_target(tmp_path)  # an unrelated document already exists
    runtime, executed, _ = _build_runtime(
        tmp_path, mode=mode, replies=[_docx_call("2026 신규 보고서.docx"), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    assert [name for name, _ in executed] == ["create_docx"]
    assert executed[0][1]["filename"] == "2026 신규 보고서.docx"
    assert _errors(ctx) == []
    assert ctx.trace.summary()["tool_outcomes"] == {"ok": 1}


def test_local_write_over_an_existing_file_is_blocked_under_bypass(tmp_path):
    """The other fail-closed family: home-sandbox writes cannot be staged."""
    home_file = tmp_path / "home" / "notes.md"
    home_file.parent.mkdir(parents=True, exist_ok=True)
    home_file.write_text("소중한 원본", encoding="utf-8")
    runtime, executed, _ = _build_runtime(
        tmp_path,
        mode=PermissionMode.BYPASS.value,
        policy=LOCAL_WRITE_POLICY,
        replies=[_local_write_call(str(home_file)), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    assert executed == []
    assert _errors(ctx)[0]["error"].startswith("NEEDS_REVIEW:")
    assert home_file.read_text(encoding="utf-8") == "소중한 원본"


def test_local_write_to_a_new_absolute_path_runs(tmp_path):
    fresh = tmp_path / "home" / "fresh.md"
    runtime, executed, _ = _build_runtime(
        tmp_path,
        mode=PermissionMode.BYPASS.value,
        policy=LOCAL_WRITE_POLICY,
        replies=[_local_write_call(str(fresh)), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    assert [name for name, _ in executed] == ["local_write"]
    assert _errors(ctx) == []


# ── 2. the resolution bug the change fixes ──────────────────────────────

def test_create_docx_really_writes_to_the_sanitized_target(tmp_path, monkeypatch):
    """Ground truth for SANITIZED_TARGET: run the real creator, look on disk.

    Without this anchor the guard's notion of "the target" could drift away
    from where the tool writes and every other test here would still pass.
    """
    pytest.importorskip("docx")
    monkeypatch.setattr(tools, "AGENT_ROOT", tmp_path)

    tools.create_docx("월간 보고서", "본문", RAW_FILENAME)

    assert (tmp_path / SANITIZED_TARGET).exists()
    assert not (tmp_path / RAW_FILENAME).exists(), "the raw filename is never written"


def test_governed_path_exists_looks_where_create_docx_actually_writes(tmp_path):
    runtime, _, _ = _build_runtime(tmp_path)
    assert document_output_target("create_docx", RAW_FILENAME) == SANITIZED_TARGET

    # A file parked at the raw `filename` argument path is NOT the target, so
    # it must not be what the check inspects. (The pre-change implementation
    # inspected exactly this path — and therefore never fired.)
    decoy = tmp_path / RAW_FILENAME
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("decoy", encoding="utf-8")
    assert runtime._governed_path_exists("create_docx", RAW_FILENAME) is False

    _seed_existing_target(tmp_path)
    assert runtime._governed_path_exists("create_docx", RAW_FILENAME) is True


def test_a_decoy_at_the_raw_filename_path_does_not_block_the_loop(tmp_path):
    """End-to-end mirror of the above: the wrong path must not fail closed."""
    decoy = tmp_path / RAW_FILENAME
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("decoy", encoding="utf-8")
    runtime, executed, _ = _build_runtime(
        tmp_path, mode=PermissionMode.TRUSTED.value,
        replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    assert [name for name, _ in executed] == ["create_docx"]
    assert _errors(ctx) == []


def test_governed_path_exists_resolves_relative_targets_under_agent_root(tmp_path):
    """Workspace-relative resolution, and no cross-workspace false positives."""
    runtime, _, _ = _build_runtime(tmp_path)
    other_root = tmp_path.parent / "another_workspace"
    (other_root / "generated_documents").mkdir(parents=True, exist_ok=True)
    (other_root / SANITIZED_TARGET).write_bytes(EXISTING_BYTES)

    assert runtime._governed_path_exists("create_docx", RAW_FILENAME) is False

    _seed_existing_target(tmp_path)
    assert runtime._governed_path_exists("create_docx", RAW_FILENAME) is True


# ── 3. the check can never crash the loop ───────────────────────────────

@pytest.mark.parametrize(
    "name,path",
    [
        ("create_docx", "\x00bad"),
        ("write_file", "\x00bad"),
        ("local_write", "/tmp/\x00bad"),
        ("create_docx", "a" * 5000),
        ("write_file", "b" * 5000),
        ("local_write", "/" + "c" * 5000),
        ("create_docx", "." * 400 + "/../" * 200),
    ],
    ids=[
        "docx-null-byte", "write-null-byte", "local-null-byte",
        "docx-too-long", "write-too-long", "local-too-long", "docx-traversal",
    ],
)
def test_governed_path_exists_never_raises_on_pathological_input(tmp_path, name, path):
    runtime, _, _ = _build_runtime(tmp_path)
    assert runtime._governed_path_exists(name, path) is False


def test_governed_path_exists_survives_an_unusable_agent_root(tmp_path):
    """``agent_root=None`` is a real shape in this tree's fakes — and the one
    input that genuinely reaches the ``except`` arm on this platform, since
    ``Path(None)`` raises TypeError. Governance degrades to "new file"; it never
    takes the run down with it."""
    runtime, _, _ = _build_runtime(tmp_path, agent_root=None)

    assert runtime._governed_path_exists("create_docx", RAW_FILENAME) is False
    assert runtime._governed_path_exists("write_file", "notes.md") is False


def test_a_broken_target_resolver_degrades_open_instead_of_crashing(tmp_path, monkeypatch):
    """Documented degradation, pinned so it stays a decision rather than a bug.

    The target would exist, so the guard would normally block. With the
    resolver raising, ``_governed_path_exists`` returns False and the call is
    classified additive — the run survives (no traceback out of the loop) but
    the overwrite hole re-opens. Anyone widening what runs inside that ``try``
    is widening this.
    """
    _seed_existing_target(tmp_path)
    runtime, executed, _ = _build_runtime(
        tmp_path, mode=PermissionMode.TRUSTED.value,
        replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )
    monkeypatch.setattr(
        agent_module, "document_output_target",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("classifier bug")),
    )

    ctx = _run_execute(runtime)

    assert ctx.state == AgentState.VERIFYING
    assert [name for name, _ in executed] == ["create_docx"]
    assert _errors(ctx) == []


# ── 4. audit trail and payload hygiene ──────────────────────────────────

def test_blocked_overwrite_is_audited_with_its_reason_and_change_class(tmp_path):
    _seed_existing_target(tmp_path)
    runtime, _, audits = _build_runtime(
        tmp_path, mode=PermissionMode.TRUSTED.value,
        replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )

    _run_execute(runtime)

    blocked = [fields for event, fields in audits if event == "agent_blocked"]
    assert len(blocked) == 1
    record = blocked[0]
    assert record["reason"] == "overwrite_fail_closed"
    assert record["change_class"] == "mutation"
    assert record["action"] == "create_docx"
    assert record["path"] == RAW_FILENAME
    assert record["permission_mode"] == PermissionMode.TRUSTED.value
    assert record["source"] == "test"
    assert record["user_email"] == "owner@example.com"
    # the model's payload never lands in the audit log
    assert PAYLOAD not in json.dumps(record, ensure_ascii=False, default=str)


def test_blocked_transcript_entry_strips_the_content_argument(tmp_path):
    _seed_existing_target(tmp_path)
    runtime, _, _ = _build_runtime(
        tmp_path, mode=PermissionMode.BYPASS.value,
        replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )

    ctx = _run_execute(runtime)

    args = _errors(ctx)[0]["args"]
    assert "content" not in args, "the payload must not be replayed into the transcript"
    # everything that explains the decision survives
    assert args["filename"] == RAW_FILENAME
    assert args["title"] == "월간 보고서"
    assert PAYLOAD not in json.dumps(ctx.transcript, ensure_ascii=False, default=str)


# ── 5. HTTP-surface parity ──────────────────────────────────────────────

def _dispatch(monkeypatch, tmp_path) -> ToolDispatchService:
    monkeypatch.setattr(tool_dispatch_module, "AGENT_ROOT", tmp_path)
    return ToolDispatchService()


def _enforce(service: ToolDispatchService, tool_name: str, args: dict):
    # require_auto_approval=False isolates the fail-closed 409 from the
    # unrelated 403 the approval gate would raise for a non-admin caller.
    return service.enforce_policy(
        tool_name, args,
        current_user="owner@example.com", source="api",
        require_auto_approval=False,
    )


def test_enforce_policy_still_409s_on_a_docx_overwrite(monkeypatch, tmp_path):
    _seed_existing_target(tmp_path)
    service = _dispatch(monkeypatch, tmp_path)

    assert service._governed_path_exists("create_docx", RAW_FILENAME) is True
    with pytest.raises(HTTPException) as excinfo:
        _enforce(service, "create_docx", {"filename": RAW_FILENAME})
    assert excinfo.value.status_code == 409


def test_enforce_policy_ignores_a_file_sitting_at_the_raw_filename_path(monkeypatch, tmp_path):
    """The pre-change check resolved the raw argument, so this decoy would have
    produced a 409 for a call that in truth creates a brand-new document."""
    decoy = tmp_path / RAW_FILENAME
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("decoy", encoding="utf-8")
    service = _dispatch(monkeypatch, tmp_path)

    assert service._governed_path_exists("create_docx", RAW_FILENAME) is False
    policy = _enforce(service, "create_docx", {"filename": RAW_FILENAME})
    assert policy["risk"] == "write"


def test_enforce_policy_409s_on_a_local_write_overwrite(monkeypatch, tmp_path):
    existing = tmp_path / "home" / "notes.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("원본", encoding="utf-8")
    service = _dispatch(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        _enforce(service, "local_write", {"path": str(existing), "content": "새 내용"})
    assert excinfo.value.status_code == 409
    assert existing.read_text(encoding="utf-8") == "원본"


def test_both_surfaces_agree_on_the_same_call(monkeypatch, tmp_path):
    """Parity is the point: the loop must not be a softer door than the API."""
    _seed_existing_target(tmp_path)
    service = _dispatch(monkeypatch, tmp_path)
    runtime, executed, _ = _build_runtime(
        tmp_path, mode=PermissionMode.BYPASS.value,
        replies=[_docx_call(RAW_FILENAME), FINAL_REPLY],
    )

    with pytest.raises(HTTPException) as excinfo:
        _enforce(service, "create_docx", {"filename": RAW_FILENAME})
    ctx = _run_execute(runtime)

    assert excinfo.value.status_code == 409
    assert executed == []
    assert _errors(ctx)[0]["error"].startswith("NEEDS_REVIEW:")
