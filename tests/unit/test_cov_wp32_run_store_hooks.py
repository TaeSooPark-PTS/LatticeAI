"""wp32 coverage — durable paused-run persistence and the built-in hook runners.

``AgentRunStore`` is the file that decides whether an approval survives a
restart, so its promises are tested against a real directory in ``tmp_path``:
a crafted run id never reaches the filesystem, a save failure never breaks the
pause response, and a corrupt or expired record reads as "not found" rather
than as a resumable run.

The built-in hook runners are bound through the same
``register_builtin_hook_runners`` entry point production uses, then invoked
with a real :class:`HookContext`, so the status dicts under test are the ones
dispatch will see.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lattice_brain.runtime.hooks import HookContext
from latticeai.core.agent import AgentRunContext, AgentState
from latticeai.core.agent_trace import LoopTrace
from latticeai.core.builtin_hooks import register_builtin_hook_runners
from latticeai.core.run_store import (
    AgentRunStore,
    hash_approval_token,
    restore_run_context,
    serialize_run_context,
)

# ── serialize / restore ─────────────────────────────────────────────────────


def test_restore_falls_back_to_waiting_approval_on_an_unknown_state():
    ctx = restore_run_context({"state": "SOMETHING_ELSE", "plan": {"goal": "g"}})

    assert ctx.state is AgentState.WAITING_APPROVAL
    assert ctx.plan == {"goal": "g"}


def test_round_trip_preserves_the_dial_and_the_trace():
    ctx = AgentRunContext()
    ctx.state = AgentState.WAITING_APPROVAL
    ctx.plan = {"goal": "ship it", "steps": []}
    ctx.permission_mode = "trusted"
    ctx.trace = LoopTrace()
    ctx.trace.decision("approve", decision="auto_approved")
    ctx.transcript = [{"state": "EXECUTING", "action": "write_file"}]

    restored = restore_run_context(serialize_run_context(ctx))

    assert restored.state is AgentState.WAITING_APPROVAL
    assert restored.permission_mode == "trusted"
    assert restored.transcript == [{"state": "EXECUTING", "action": "write_file"}]
    assert restored.trace.events == ctx.trace.events


# ── path safety ─────────────────────────────────────────────────────────────


def _paused_ctx():
    ctx = AgentRunContext()
    ctx.state = AgentState.WAITING_APPROVAL
    ctx.plan = {"goal": "write a report"}
    return ctx


def _save(store, run_id, *, req_payload=None, expires_epoch=4102444800.0):
    return store.save(
        run_id,
        ctx=_paused_ctx(),
        req_payload=req_payload if req_payload is not None else {"message": "hi"},
        language_hint="ko",
        user="u@example.com",
        token="tok-1",
        expires_epoch=expires_epoch,
        expires_at="2100-01-01T00:00:00Z",
    )


def test_a_traversing_run_id_never_reaches_the_filesystem(tmp_path):
    store = AgentRunStore(tmp_path / "runs")

    assert _save(store, "../../etc/passwd") is False
    assert store.load("../../etc/passwd") is None
    assert store.delete("../../etc/passwd") is None
    assert not (tmp_path / "runs").exists()


def test_save_and_load_round_trip_hashes_the_token(tmp_path):
    store = AgentRunStore(tmp_path / "runs")

    assert _save(store, "run-abcdefgh") is True
    record = store.load("run-abcdefgh")

    assert record["token_hash"] == hash_approval_token("tok-1")
    assert "tok-1" not in json.dumps(record)
    assert record["ctx"]["plan"] == {"goal": "write a report"}


def test_save_returns_false_when_the_record_cannot_be_serialized(tmp_path):
    store = AgentRunStore(tmp_path / "runs")

    saved = _save(store, "run-unserial", req_payload={"handle": object()})

    assert saved is False
    assert store.load("run-unserial") is None
    # the temp file the failed write left behind was cleaned up
    assert list((tmp_path / "runs").glob("*")) == []


def test_save_survives_a_temp_file_it_cannot_remove(tmp_path, monkeypatch):
    store = AgentRunStore(tmp_path / "runs")
    (tmp_path / "runs").mkdir(parents=True)
    real_unlink = os.unlink

    def refuse(path, *args, **kwargs):
        if "run-locked" in str(path):
            raise OSError("temp file is locked")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse)

    assert _save(store, "run-locked", req_payload={"handle": object()}) is False


def test_delete_reports_but_survives_an_unremovable_file(tmp_path, monkeypatch, caplog):
    store = AgentRunStore(tmp_path / "runs")
    _save(store, "run-abcdefgh")

    def refuse(self, missing_ok=False):
        raise OSError("read-only volume")

    monkeypatch.setattr(Path, "unlink", refuse)
    with caplog.at_level("WARNING"):
        store.delete("run-abcdefgh")

    assert "agent run store delete failed" in caplog.text
    assert store.load("run-abcdefgh") is not None


def test_delete_removes_the_record(tmp_path):
    store = AgentRunStore(tmp_path / "runs")
    _save(store, "run-abcdefgh")

    store.delete("run-abcdefgh")

    assert store.load("run-abcdefgh") is None


def test_load_treats_a_corrupt_or_mismatched_record_as_missing(tmp_path):
    root = tmp_path / "runs"
    root.mkdir(parents=True)
    (root / "run-corrupt1.json").write_text("{not json", encoding="utf-8")
    (root / "run-listrec1.json").write_text("[1, 2, 3]", encoding="utf-8")
    (root / "run-swapped1.json").write_text(
        json.dumps({"run_id": "run-otherid"}), encoding="utf-8",
    )
    store = AgentRunStore(root)

    assert store.load("run-corrupt1") is None
    assert store.load("run-listrec1") is None
    assert store.load("run-swapped1") is None


# ── pending summaries ───────────────────────────────────────────────────────


def test_pending_summaries_skips_corrupt_expired_and_foreign_records(tmp_path):
    root = tmp_path / "runs"
    store = AgentRunStore(root)
    _save(store, "run-liveaaaa")
    _save(store, "run-expiredx", expires_epoch=1.0)
    root.mkdir(parents=True, exist_ok=True)
    (root / "run-corrupt2.json").write_text("}{", encoding="utf-8")
    (root / "run-listrec2.json").write_text("[]", encoding="utf-8")

    mine = store.pending_summaries("u@example.com")
    other = store.pending_summaries("someone-else@example.com")

    assert [row["run_id"] for row in mine] == ["run-liveaaaa"]
    assert mine[0]["goal"] == "write a report"
    assert other == []


def test_pending_summaries_returns_empty_when_the_directory_is_unreadable(tmp_path, monkeypatch):
    store = AgentRunStore(tmp_path / "runs")

    def refuse(self, pattern):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "glob", refuse)

    assert store.pending_summaries() == []


# ── sweep ───────────────────────────────────────────────────────────────────


def test_sweep_removes_long_expired_and_unreadable_records_only(tmp_path):
    root = tmp_path / "runs"
    store = AgentRunStore(root)
    _save(store, "run-liveaaaa")
    _save(store, "run-recentxx", expires_epoch=1_000_000.0)
    _save(store, "run-ancientx", expires_epoch=1.0)
    (root / "run-garbage1.json").write_text("not json at all", encoding="utf-8")

    removed = store.sweep_expired(1_000_050.0, retention_seconds=100.0)

    assert removed == 2  # the ancient record and the unreadable one
    assert store.load("run-liveaaaa") is not None
    assert store.load("run-recentxx") is not None  # still inside the retention window
    assert store.load("run-ancientx") is None
    assert not (root / "run-garbage1.json").exists()


def test_sweep_counts_nothing_when_a_file_cannot_be_removed(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    store = AgentRunStore(root)
    _save(store, "run-ancientx", expires_epoch=1.0)

    def refuse(self, missing_ok=False):
        raise OSError("read-only volume")

    monkeypatch.setattr(Path, "unlink", refuse)

    assert store.sweep_expired(1_000_000.0) == 0
    assert (root / "run-ancientx.json").exists()


def test_sweep_returns_zero_when_the_directory_is_unreadable(tmp_path, monkeypatch):
    store = AgentRunStore(tmp_path / "runs")

    def refuse(self, pattern):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "glob", refuse)

    assert store.sweep_expired() == 0


# ── built-in hook runners ───────────────────────────────────────────────────


class _Registry:
    def __init__(self):
        self.runners: dict = {}

    def register_hook(self, hook_id, runner):
        self.runners[hook_id] = runner
        return self


def _bound(*, permission=None, classifier=None):
    registry = _Registry()
    audits: list = []

    def get_tool_permission(tool):
        if isinstance(permission, Exception):
            raise permission
        return (permission or {}).get(tool, {"risk": "low", "requires_approval": False})

    def classify_sensitive_message(message, _index):
        if isinstance(classifier, Exception):
            raise classifier
        return classifier or {"sensitivity": "low", "labels": []}

    register_builtin_hook_runners(
        registry,
        append_audit_event=lambda event, **kwargs: audits.append((event, kwargs)),
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=classify_sensitive_message,
    )
    return registry.runners, audits


def test_every_builtin_hook_id_gets_a_real_runner():
    runners, _audits = _bound()

    assert set(runners) == {
        "builtin:redact-secrets",
        "builtin:audit-agent-run",
        "builtin:pipeline-index-status",
        "builtin:research-memory-snapshot",
        "builtin:tool-permission-gate",
        "builtin:sensitive-data-guard",
        "builtin:workflow-replay-log",
    }


def test_redact_secrets_hook_rewrites_the_payload_in_place():
    runners, _audits = _bound()
    context = HookContext("pre_run", payload={"api_key": "sk-live-123", "goal": "ship"})

    result = runners["builtin:redact-secrets"](context)

    assert context.payload["api_key"] == "[REDACTED_SECRET]"
    assert context.payload["goal"] == "ship"
    assert result["status"] == "ok"
    assert result["output"] == "redacted 1 field(s)"


def test_redact_secrets_hook_reports_a_clean_payload():
    runners, _audits = _bound()

    result = runners["builtin:redact-secrets"](HookContext("pre_run", payload={"goal": "ship"}))

    assert result["output"] == "no secrets present"


def test_redact_secrets_hook_tolerates_a_non_dict_payload():
    runners, _audits = _bound()
    context = HookContext("pre_run")
    context.payload = ["not", "a", "dict"]

    assert runners["builtin:redact-secrets"](context)["status"] == "ok"


def test_audit_agent_run_hook_appends_the_completed_run():
    runners, audits = _bound()
    context = HookContext(
        "post_run",
        payload={"run_id": "run-7", "agent_id": "agent:executor", "status": "DONE"},
        user_email="u@example.com",
    )

    result = runners["builtin:audit-agent-run"](context)

    assert result["output"] == "audited run run-7"
    assert audits == [(
        "hook_post_run",
        {
            "user_email": "u@example.com", "run_id": "run-7",
            "agent_id": "agent:executor", "status": "DONE",
        },
    )]


def test_pipeline_and_memory_and_workflow_hooks_summarize_their_payloads():
    runners, _audits = _bound()

    index = runners["builtin:pipeline-index-status"](
        HookContext("post_index", "post_index", payload={"indexed": 12})
    )
    memory = runners["builtin:research-memory-snapshot"](
        HookContext("agent", payload={"context_items": 4})
    )
    empty_memory = runners["builtin:research-memory-snapshot"](HookContext("agent"))
    workflow = runners["builtin:workflow-replay-log"](
        HookContext("post_workflow", payload={"workflow_id": "wf-1", "status": "ok", "steps": 3})
    )
    bare_workflow = runners["builtin:workflow-replay-log"](HookContext("post_workflow"))

    assert index["output"] == "pipeline post_index: indexed=12"
    assert memory["output"] == "memory snapshot recorded (4 context items)"
    assert empty_memory["output"] == "memory snapshot recorded (0 context items)"
    assert workflow["output"] == "workflow wf-1 -> ok (3 steps)"
    assert bare_workflow["output"] == "workflow ? -> recorded (? steps)"


def test_tool_permission_gate_surfaces_the_policy_and_blocks_a_denied_tool():
    runners, _audits = _bound(permission={
        "run_command": {"risk": "high", "requires_approval": True},
        "rm_rf": {"policy": "deny"},
        "wipe": {"risk": "deny"},
    })

    allowed = runners["builtin:tool-permission-gate"](
        HookContext("pre_tool", payload={"tool": "run_command"})
    )
    denied = runners["builtin:tool-permission-gate"](
        HookContext("pre_tool", payload={"tool": "rm_rf"})
    )
    risk_denied = runners["builtin:tool-permission-gate"](
        HookContext("pre_tool", payload={"tool": "wipe"})
    )

    assert allowed["status"] == "ok"
    assert allowed["output"] == "policy[run_command]: risk=high approval=True"
    assert denied == {
        "status": "blocked", "block": True,
        "detail": "governance policy denies 'rm_rf'",
    }
    assert risk_denied["block"] is True


def test_sensitive_data_guard_reports_the_classifier_verdict():
    runners, _audits = _bound(classifier={"sensitivity": "high", "labels": ["pii", "secret"]})

    result = runners["builtin:sensitive-data-guard"](
        HookContext("pre_tool", payload={"tool": "send_email", "count": 2, "body": "SSN 1"})
    )

    assert result["output"] == "sensitivity=high labels=pii,secret"


def test_sensitive_data_guard_says_none_when_no_labels_came_back():
    runners, _audits = _bound()

    result = runners["builtin:sensitive-data-guard"](
        HookContext("pre_tool", payload={"tool": "read_file"})
    )

    assert result["output"] == "sensitivity=low labels=none"
