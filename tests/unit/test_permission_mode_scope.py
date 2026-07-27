"""Permission-mode scoping and proposal-staging regressions (v9.9.8).

These cover three defects that made the autonomy dial either inert or unsafe:

1. Enforcement resolved the mode without a user/workspace scope, so a stored
   per-user override never reached ``enforce_policy`` — the dial was a no-op.
2. The agent-side governor wrapper staged a proposal and *then* let the tool
   run, leaving an orphan proposal in the Review Center for a change that was
   already applied.
3. ``AgentRunContext`` used ``__slots__`` without a ``permission_mode`` slot, so
   the documented per-run override could never be set.
"""

from __future__ import annotations

import threading
from pathlib import Path

from latticeai.core.agent import AgentRunContext
from latticeai.core.agent_permission import call_mode_source, resolve_deps_mode
from latticeai.core.permission_mode import PermissionMode
from latticeai.core.run_store import restore_run_context, serialize_run_context
from latticeai.services.permission_mode_service import PermissionModeService
from latticeai.services.tool_dispatch import ToolDispatchService


# ── 1. scoped resolution ────────────────────────────────────────────────


def test_call_mode_source_passes_scope_to_resolver():
    seen = {}

    def resolver(*, user_email=None, workspace_id=None):
        seen["user_email"] = user_email
        seen["workspace_id"] = workspace_id
        return "trusted"

    assert call_mode_source(resolver, user_email="a@b.c", workspace_id="ws1") == "trusted"
    assert seen == {"user_email": "a@b.c", "workspace_id": "ws1"}


def test_call_mode_source_tolerates_legacy_zero_arg_resolver():
    assert call_mode_source(lambda: "bypass", user_email="a@b.c") == "bypass"


def test_call_mode_source_accepts_static_value():
    assert call_mode_source("trusted") == "trusted"


def test_dispatch_resolves_per_user_override(tmp_path: Path):
    """The regression: a stored per-user mode must reach the dispatch gate."""
    svc = PermissionModeService(data_dir=tmp_path)
    svc.set_mode("trusted", user_email="a@b.c")

    dispatch = ToolDispatchService()
    dispatch.permission_mode = svc.resolve

    assert dispatch.resolve_permission_mode(user_email="a@b.c") is PermissionMode.TRUSTED
    # A different user keeps the strict default.
    assert dispatch.resolve_permission_mode(user_email="other@b.c") is PermissionMode.STRICT
    # No scope at all still resolves, and stays strict.
    assert dispatch.resolve_permission_mode() is PermissionMode.STRICT


def test_dispatch_workspace_override_wins(tmp_path: Path):
    svc = PermissionModeService(data_dir=tmp_path)
    svc.set_mode("bypass", user_email="a@b.c", acknowledge_risk=True)
    svc.set_mode("strict", user_email="a@b.c", workspace_id="ws1")

    dispatch = ToolDispatchService()
    dispatch.permission_mode = svc.resolve

    assert dispatch.resolve_permission_mode(user_email="a@b.c") is PermissionMode.BYPASS
    assert dispatch.resolve_permission_mode(
        user_email="a@b.c", workspace_id="ws1",
    ) is PermissionMode.STRICT


def test_dispatch_resolver_failure_falls_back_to_strict():
    def boom(**_kwargs):
        raise RuntimeError("store unavailable")

    dispatch = ToolDispatchService()
    dispatch.permission_mode = boom
    assert dispatch.resolve_permission_mode(user_email="a@b.c") is PermissionMode.STRICT


def test_set_mode_does_not_deadlock(tmp_path: Path):
    """``set_mode`` used to call ``resolve`` while holding a non-reentrant lock,
    so every POST /api/permission-mode hung the worker thread forever."""
    svc = PermissionModeService(data_dir=tmp_path)
    done = threading.Event()
    error: list = []

    def change():
        try:
            svc.set_mode("trusted", user_email="a@b.c")
            svc.set_mode("strict", user_email="a@b.c", workspace_id="ws1")
        except Exception as exc:  # noqa: BLE001
            error.append(exc)
        finally:
            done.set()

    threading.Thread(target=change, daemon=True).start()
    assert done.wait(timeout=10), "set_mode deadlocked"
    assert not error
    assert svc.resolve(user_email="a@b.c") is PermissionMode.TRUSTED


def test_set_mode_reports_the_previous_mode(tmp_path: Path):
    svc = PermissionModeService(data_dir=tmp_path)
    seen: list = []
    svc.rebind_audit(lambda event, **kw: seen.append(kw))

    svc.set_mode("trusted", user_email="a@b.c")
    svc.set_mode("bypass", user_email="a@b.c", acknowledge_risk=True)

    assert [s["previous"] for s in seen] == ["strict", "trusted"]


def test_rebind_data_dir_moves_the_store(tmp_path: Path):
    """An early lazy caller must not strand the store on the fallback path."""
    first, second = tmp_path / "a", tmp_path / "b"
    svc = PermissionModeService(data_dir=first)
    svc.set_mode("trusted", user_email="a@b.c")

    svc.rebind_data_dir(second)
    assert svc.resolve(user_email="a@b.c") is PermissionMode.STRICT

    svc.set_mode("bypass", user_email="a@b.c", acknowledge_risk=True)
    assert (second / "permission_mode.json").exists()


# ── 2. run-scoped context stamp ─────────────────────────────────────────


def test_context_carries_permission_mode_override():
    ctx = AgentRunContext()
    assert ctx.permission_mode is None
    ctx.permission_mode = "trusted"  # would raise AttributeError without the slot

    class _Deps:
        permission_mode = "strict"

    # The explicit per-run stamp wins over the process-wide resolver.
    assert resolve_deps_mode(_Deps(), ctx) is PermissionMode.TRUSTED
    assert resolve_deps_mode(_Deps(), AgentRunContext()) is PermissionMode.STRICT


def test_paused_run_resumes_under_the_mode_it_was_approved_with():
    ctx = AgentRunContext()
    ctx.permission_mode = "trusted"
    restored = restore_run_context(serialize_run_context(ctx))
    assert restored.permission_mode == "trusted"


def test_legacy_run_payload_without_mode_restores_as_none():
    ctx = AgentRunContext()
    payload = serialize_run_context(ctx)
    payload.pop("permission_mode", None)
    assert restore_run_context(payload).permission_mode is None


# ── 3. no orphan proposals under trusted/bypass ─────────────────────────


class _RecordingGovernor:
    """Stands in for ChangeGovernor; ``review`` persists a proposal."""

    governed_tools = frozenset({"write_file", "edit_file"})

    def __init__(self) -> None:
        self.reviews = 0

    def review(self, name, args, **kwargs):
        self.reviews += 1
        return {
            "decision": "proposed",
            "proposal": {"id": f"p{self.reviews}"},
            "classification": {"change_class": "mutation"},
        }


def _runtime_with(mode, governor):
    """A minimal object shaped like SingleAgentRuntime for the mode patch."""
    from latticeai.core.agent_mode_patch import apply_permission_mode_to_runtime

    class _Deps:
        change_governor = governor
        tool_governance: dict = {}
        permission_mode = mode

        def audit(self, *_a, **_kw):
            pass

    class _Runtime:
        deps = _Deps()

        def approval_requirements(self, ctx):
            return {}

        def _blocked_by_gates(self, *_a, **_kw):
            return False

        def _governor_review(self, ctx, name, thoughts, args, policy, risk,
                             current_user, request_workspace, conversation_id=None):
            self.deps.change_governor.review(name, args)
            ctx.transcript.append({"action": name, "result": {"proposed": True}})
            return True, False

        def _emit_step(self, *_a, **_kw):
            pass

    return apply_permission_mode_to_runtime(_Runtime())


def _review(runtime, ctx):
    return runtime._governor_review(
        ctx, "write_file", "t", {"path": "notes.md", "content": "x"},
        {"risk": "write", "destructive": False}, "medium", "a@b.c", None,
    )


def test_strict_still_stages_a_proposal():
    governor = _RecordingGovernor()
    runtime = _runtime_with("strict", governor)
    ctx = AgentRunContext()

    proposed, allows = _review(runtime, ctx)

    assert proposed is True
    assert governor.reviews == 1
    assert len(ctx.transcript) == 1


def test_trusted_never_creates_a_proposal_it_then_discards():
    """The regression: the old wrapper called review() (persisting a proposal)
    and then popped the transcript entry, so the change applied *and* an orphan
    proposal stayed pending in the Review Center."""
    governor = _RecordingGovernor()
    runtime = _runtime_with("trusted", governor)
    ctx = AgentRunContext()

    proposed, allows = _review(runtime, ctx)

    assert proposed is False
    assert allows is True
    assert governor.reviews == 0, "review() must not run — it persists a proposal"
    assert ctx.transcript == []


def test_bypass_never_creates_a_proposal_it_then_discards():
    governor = _RecordingGovernor()
    runtime = _runtime_with("bypass", governor)

    proposed, allows = _review(runtime, AgentRunContext())

    assert (proposed, allows) == (False, True)
    assert governor.reviews == 0


def test_trusted_defers_destructive_calls_to_the_downstream_gate():
    governor = _RecordingGovernor()
    runtime = _runtime_with("trusted", governor)

    proposed, allows = runtime._governor_review(
        AgentRunContext(), "write_file", "t", {"path": "notes.md"},
        {"risk": "destructive", "destructive": True}, "high", "a@b.c", None,
    )

    assert (proposed, allows) == (False, False)
    assert governor.reviews == 0


def test_ungoverned_tool_is_untouched_under_trusted():
    governor = _RecordingGovernor()
    runtime = _runtime_with("trusted", governor)

    proposed, allows = runtime._governor_review(
        AgentRunContext(), "run_command", "t", {"command": "ls"},
        {"risk": "exec", "destructive": False}, "medium", "a@b.c", None,
    )

    assert (proposed, allows) == (False, False)
    assert governor.reviews == 0
