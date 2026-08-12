"""wp24 coverage — ``lattice_brain.runtime.hooks``.

Two seams are replaced and nothing else: the registry's state files live under
``tmp_path``, and the ``subprocess`` module a user hook shells out through is
swapped per test, so a "command" hook never spawns a process. What is exercised
for real is the contract the platform depends on — a corrupt state file
degrades to the built-in set instead of losing the registry, a gate hook that
times out fails closed while a non-gate one does not, the child environment
never inherits provider secrets, and every mutation refuses an id the registry
does not own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lattice_brain.runtime import hooks as hooks_mod
from lattice_brain.runtime.hooks import (
    BUILTIN_HOOKS,
    HookContext,
    HookResult,
    HooksRegistry,
    dispatch_tool,
)


def _registry(tmp_path: Path, **kwargs) -> HooksRegistry:
    return HooksRegistry(tmp_path / "hooks.json", **kwargs)


def _fake_subprocess(run):
    """A stand-in for the ``subprocess`` module the registry calls."""
    return SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)


class _RecordingHooks:
    """Minimal hooks port for :func:`dispatch_tool` (records every dispatch)."""

    def __init__(self, *, block_reason: str = ""):
        self.calls: list[tuple] = []
        self._block_reason = block_reason

    def fire_hook(self, kind, event, *, payload=None, user_email=None, workspace_id=None):
        self.calls.append((kind, event, dict(payload or {})))
        blocked = bool(self._block_reason) and kind.startswith("pre_")
        return {"kind": kind, "blocked": blocked, "block_reason": self._block_reason}


class _OpaqueArgs(dict):
    """Dict-shaped tool args whose keys cannot be inspected."""

    def keys(self):
        raise RuntimeError("args are not introspectable")


# ── context / result value objects ──────────────────────────────────────────


def test_hook_context_records_mutations_notes_and_blocks():
    ctx = HookContext(
        "pre_tool", "tool.write_file", {"tool": "write_file"},
        user_email="u@example.com", workspace_id="ws-1",
    )

    assert ctx.set("redacted", True) is ctx
    assert ctx.note("2 secret fields stripped") is ctx
    assert ctx.block("write not approved") is ctx

    payload = ctx.as_dict()
    assert payload["payload"] == {"tool": "write_file", "redacted": True}
    assert payload["notes"] == ["2 secret fields stripped"]
    assert payload["blocked"] is True
    assert payload["block_reason"] == "write not approved"
    assert payload["event"] == "tool.write_file"
    assert payload["workspace_id"] == "ws-1"


def test_hook_result_stamps_a_start_time():
    res = HookResult(hook_id="user:ping", name="Ping", kind="post_run")

    assert res.as_dict()["hook_id"] == "user:ping"
    assert res.status == "ok"
    assert res.started_at


# ── dispatch_tool ───────────────────────────────────────────────────────────


def test_dispatch_tool_still_runs_when_argument_metadata_is_unreadable():
    hooks = _RecordingHooks()

    result = dispatch_tool(
        hooks, "write_file", _OpaqueArgs(path="a.txt"), lambda: {"ok": True},
        source="test",
    )

    assert result == {"ok": True}
    assert [call[0] for call in hooks.calls] == ["pre_tool", "post_tool"]
    assert hooks.calls[0][2]["args_keys"] == []
    assert hooks.calls[0][2]["tool"] == "write_file"


# ── persistence ─────────────────────────────────────────────────────────────


def test_registry_degrades_to_the_builtin_set_when_state_is_corrupt(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text("{not json at all", encoding="utf-8")

    listing = HooksRegistry(path).list()

    assert listing["total"] == len(BUILTIN_HOOKS)
    assert {hook["source"] for hook in listing["hooks"]} == {"builtin"}


def test_save_removes_its_temp_file_when_the_atomic_replace_fails(tmp_path, monkeypatch):
    registry = _registry(tmp_path)

    def boom(_src, _dst):
        raise OSError("replace refused")

    monkeypatch.setattr(hooks_mod.os, "replace", boom)

    with pytest.raises(OSError, match="replace refused"):
        registry.register(name="Doomed hook", kind="pre_tool")

    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / "hooks.json").exists()


def test_recent_runs_degrades_when_the_run_history_is_corrupt(tmp_path):
    (tmp_path / "hooks_runs.json").write_text("[[[", encoding="utf-8")
    registry = _registry(tmp_path)
    assert registry.recent_runs()["runs"] == []

    registry.register_hook("builtin:redact-secrets", lambda _ctx: None)
    registry.run_hook("builtin:redact-secrets", payload={"goal": "x"})

    assert registry.recent_runs(limit=1)["total"] == 1
    assert registry.recent_runs(kind="pre_run")["runs"][0]["hook_id"] == "builtin:redact-secrets"


# ── ordering / enablement ───────────────────────────────────────────────────


def test_a_builtin_order_override_moves_the_hook_and_survives_a_reload(tmp_path):
    registry = _registry(tmp_path)
    registry.register(name="Slack ping", kind="post_run", order=5)

    updated = registry.set_order("builtin:audit-agent-run", 1)
    assert updated["order"] == 1

    reloaded = HooksRegistry(tmp_path / "hooks.json")
    post_run = reloaded.list(kind="post_run")["hooks"]
    assert [hook["id"] for hook in post_run] == ["builtin:audit-agent-run", "user:slack-ping"]
    assert {hook["kind"] for hook in post_run} == {"post_run"}


def test_custom_hook_enablement_and_order_are_persisted(tmp_path):
    registry = _registry(tmp_path)
    entry = registry.register(
        name="Nightly export", kind="post_workflow", command="lattice-export",
    )
    assert entry["id"] == "user:nightly-export"

    assert registry.set_enabled(entry["id"], False)["enabled"] is False
    assert registry.set_order(entry["id"], 42)["order"] == 42

    reloaded = HooksRegistry(tmp_path / "hooks.json").get(entry["id"])
    assert reloaded["enabled"] is False
    assert reloaded["order"] == 42
    # A command makes the hook genuinely executable rather than advisory.
    assert reloaded["executable"] is True
    assert reloaded["advisory"] is False


def test_reorder_applies_known_ids_and_skips_the_rest(tmp_path):
    registry = _registry(tmp_path)
    registry.register(name="Guard A", kind="pre_tool", order=100)

    listing = registry.reorder(
        "pre_tool", ["user:guard-a", "user:ghost", "builtin:tool-permission-gate"],
    )

    assert [hook["id"] for hook in listing["hooks"]][0] == "user:guard-a"
    assert registry.get("user:guard-a")["order"] == 10
    assert registry.get("builtin:tool-permission-gate")["order"] == 30


def test_every_mutation_refuses_an_unknown_hook_id(tmp_path):
    registry = _registry(tmp_path)

    for call in (
        lambda: registry.set_enabled("user:ghost", True),
        lambda: registry.set_order("user:ghost", 1),
        lambda: registry.inspect("user:ghost"),
        lambda: registry.run_hook("user:ghost"),
        lambda: registry.remove("user:ghost"),
    ):
        with pytest.raises(KeyError):
            call()

    with pytest.raises(ValueError, match="cannot be removed"):
        registry.remove("builtin:audit-agent-run")


def test_register_requires_a_name_and_a_known_kind(tmp_path):
    registry = _registry(tmp_path)

    with pytest.raises(ValueError, match="name is required"):
        registry.register(name="   ", kind="pre_tool")
    with pytest.raises(ValueError, match="kind must be one of"):
        registry.register(name="Bad kind", kind="whenever")


def test_registering_the_same_name_twice_keeps_both_hooks(tmp_path):
    registry = _registry(tmp_path)

    first = registry.register(name="Audit ping", kind="post_run")
    second = registry.register(name="Audit ping", kind="post_run")

    assert first["id"] == "user:audit-ping"
    assert second["id"] == "user:audit-ping-2"
    assert {hook["id"] for hook in registry.list(kind="post_run")["hooks"]} >= {
        "user:audit-ping", "user:audit-ping-2",
    }


# ── execution ───────────────────────────────────────────────────────────────


def test_register_hook_requires_a_callable_and_binds_the_runner(tmp_path):
    registry = _registry(tmp_path)

    with pytest.raises(TypeError, match="runner must be callable"):
        registry.register_hook("builtin:redact-secrets", "not-callable")

    assert registry.has_runner("builtin:redact-secrets") is False
    assert registry.get("builtin:redact-secrets")["advisory"] is True

    assert registry.register_hook("builtin:redact-secrets", lambda _ctx: None) is registry
    assert registry.has_runner("builtin:redact-secrets") is True
    assert registry.get("builtin:redact-secrets")["executable"] is True


def test_a_disabled_hook_is_skipped_rather_than_run(tmp_path):
    registry = _registry(tmp_path)
    ran: list[str] = []
    registry.register_hook("builtin:redact-secrets", lambda _ctx: ran.append("ran"))
    registry.set_enabled("builtin:redact-secrets", False)

    res = registry.run_hook("builtin:redact-secrets", payload={"goal": "x"})

    assert res["status"] == "skipped"
    assert res["detail"] == "hook disabled"
    assert ran == []
    assert registry.recent_runs(limit=1)["runs"][0]["status"] == "skipped"


def test_a_runner_returning_text_records_it_as_hook_output(tmp_path):
    registry = _registry(tmp_path)
    registry.register_hook("builtin:redact-secrets", lambda _ctx: "2 fields redacted")

    res = registry.run_hook("builtin:redact-secrets", payload={"goal": "x"})

    assert res["status"] == "ok"
    assert res["output"] == "2 fields redacted"
    assert res["blocked"] is False


def test_a_runner_that_blocks_while_returning_text_still_gates(tmp_path):
    registry = _registry(tmp_path)

    def runner(ctx):
        ctx.block("tool needs approval")
        return "checked 1 policy"

    registry.register_hook("builtin:tool-permission-gate", runner)
    dispatch = registry.run_hooks("pre_tool", payload={"tool": "write_file"})

    gate = dispatch["results"][0]
    assert gate["status"] == "blocked"
    assert gate["output"] == "checked 1 policy"
    assert dispatch["blocked"] is True
    assert dispatch["block_reason"] == "tool needs approval"
    # A blocking gate short-circuits the remaining pre_tool hooks.
    assert dispatch["ran"] == 1


def test_a_user_hook_with_an_unparseable_command_reports_an_error(tmp_path):
    registry = _registry(tmp_path)
    hook = registry.register(name="Broken quote", kind="post_run", command='echo "oops')

    res = registry.run_hook(hook["id"])

    assert res["status"] == "error"
    assert res["detail"].startswith("invalid command")
    assert res["blocked"] is False


def test_a_command_that_parses_to_nothing_is_skipped(tmp_path):
    registry = _registry(tmp_path)
    ctx = HookContext("post_run", "agent.run")

    assert registry._run_command({"id": "user:blank", "command": "   "}, ctx) == (
        "skipped", "empty command", "", False,
    )


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_block"),
    [("pre_tool", "blocked", True), ("post_run", "error", False)],
)
def test_a_timed_out_command_hook_fails_closed_only_for_gates(
    tmp_path, monkeypatch, kind, expected_status, expected_block,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    registry = _registry(tmp_path, command_timeout=3.0)
    registry.register(name=f"Slow {kind}", kind=kind, command="lattice-slow-hook --wait")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd=argv, timeout=3.0)

    monkeypatch.setattr(hooks_mod, "subprocess", _fake_subprocess(fake_run))

    dispatch = registry.run_hooks(kind, payload={"tool": "write_file"})

    entry = dispatch["results"][-1]
    assert entry["status"] == expected_status
    assert entry["detail"] == "timed out after 3s"
    assert entry["blocked"] is expected_block
    assert dispatch["blocked"] is expected_block
    assert seen["argv"] == ["lattice-slow-hook", "--wait"]
    assert seen["timeout"] == 3.0
    # The child environment carries hook metadata, never process secrets.
    assert "OPENAI_API_KEY" not in seen["env"]
    assert seen["env"]["LATTICE_HOOK_KIND"] == kind


def test_a_command_hook_that_cannot_launch_is_an_error_without_blocking(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    hook = registry.register(
        name="Missing binary", kind="pre_run", command="lattice-hook-does-not-exist",
    )

    def fake_run(argv, **_kwargs):
        raise FileNotFoundError(f"No such file or directory: {argv[0]!r}")

    monkeypatch.setattr(hooks_mod, "subprocess", _fake_subprocess(fake_run))

    res = registry.run_hook(hook["id"])

    assert res["status"] == "error"
    assert "lattice-hook-does-not-exist" in res["detail"]
    assert res["blocked"] is False
