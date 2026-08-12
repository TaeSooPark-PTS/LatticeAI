"""wp35: ToolDispatchService policy gates and the module-level façade.

``AGENT_ROOT`` is rebound to ``tmp_path`` for every filesystem test (idiom:
tests/unit/test_snapshot_rollback_ports.py), so nothing here writes into the
real agent workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import latticeai.services.tool_dispatch as td
from latticeai.core.permission_mode import PermissionMode
from latticeai.tools import ToolError


def _service(role: str = "user") -> td.ToolDispatchService:
    service = td.ToolDispatchService()
    service.configure(
        load_users=lambda: {"u@e.co": {"role": role}},
        get_user_role=lambda email, users=None: role,
    )
    return service


# ── module defaults + registry passthrough ───────────────────────────────────


def test_module_default_callables_are_permissive_but_explicit():
    assert td._default_load_users() == {}
    assert td._default_get_user_role("anyone") == "user"
    assert td._default_permission_mode() is td.DEFAULT_MODE


def test_registry_facts_are_exposed_through_the_service():
    service = td.ToolDispatchService()

    assert service.risk_level_map["read"] == service.registry.risk_level_map["read"]
    assert service.risk_level_for_policy(service.policy_for("read_file", {})) == "low"


def test_configure_can_replace_the_permission_mode_source():
    service = td.ToolDispatchService()

    service.configure(
        load_users=dict,
        get_user_role=lambda email, users=None: "user",
        permission_mode=PermissionMode.TRUSTED,
    )

    assert service.permission_mode is PermissionMode.TRUSTED
    assert service.resolve_permission_mode() is PermissionMode.TRUSTED


def test_user_role_falls_back_to_user_when_the_lookup_raises():
    service = td.ToolDispatchService()

    def boom(email, users=None):
        raise RuntimeError("user store offline")

    service.configure(load_users=dict, get_user_role=boom)

    assert service.user_role("u@e.co") == "user"


# ── role / capability gates ──────────────────────────────────────────────────


def test_capability_gate_rejects_a_role_without_the_capability():
    service = _service(role="viewer")

    with pytest.raises(HTTPException) as excinfo:
        service.check_role("knowledge_save", "viewer@e.co")

    assert excinfo.value.status_code == 403
    assert "workspace:write" in excinfo.value.detail


def test_module_level_facade_delegates_to_the_default_service():
    assert td.agent_risk("read_file", {}) == "low"
    # list_dir carries no capability and is not admin-only: the gate is a no-op.
    assert td.check_tool_role("list_dir", "u@e.co") is None


def test_destructive_policies_are_blocked_by_their_own_gate(monkeypatch):
    """The destructive check is a second, independent gate.

    ``is_circuit_breaker`` normally answers first for the same policies, so the
    upstream gate is neutralized here to prove the destructive branch refuses
    on its own rather than relying on the breaker having run.
    """
    monkeypatch.setattr(td, "is_circuit_breaker", lambda name, policy, args: None)
    service = _service()

    with pytest.raises(HTTPException) as excinfo:
        service.enforce_policy(
            "local_write",
            {"path": "/etc/passwd", "content": "x"},
            current_user="u@e.co",
            source="api",
            trusted_admin=True,
        )

    assert excinfo.value.status_code == 403
    assert "파괴적" in excinfo.value.detail


def test_governed_path_existence_never_raises(monkeypatch):
    def boom(tool_name, path):
        raise RuntimeError("output target resolution broke")

    monkeypatch.setattr(td, "document_output_target", boom)

    assert td.ToolDispatchService()._governed_path_exists("create_docx", "a.docx") is False


# ── rollback / snapshot ports ────────────────────────────────────────────────


def test_rollback_file_reports_the_git_checkout_result(tmp_path, monkeypatch):
    calls: list = []

    class FakeCompleted:
        returncode = 1
        stderr = "error: pathspec 'notes.txt' did not match"

    class FakeSubprocess:
        @staticmethod
        def run(command, **kwargs):
            calls.append((command, kwargs["cwd"]))
            return FakeCompleted()

    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    monkeypatch.setattr(td, "subprocess", FakeSubprocess)

    result = td.ToolDispatchService().rollback_file("notes.txt")

    assert result == {
        "path": "notes.txt",
        "ok": False,
        "stderr": "error: pathspec 'notes.txt' did not match",
    }
    assert calls == [(["git", "checkout", "--", "notes.txt"], str(tmp_path))]


def test_workspace_path_returns_none_when_resolution_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)

    def boom(self, *args, **kwargs):
        raise OSError(40, "too many levels of symbolic links")

    monkeypatch.setattr(Path, "resolve", boom)

    snapshot = td.ToolDispatchService().snapshot_file("notes.txt")

    assert snapshot["error"] == "path escapes the agent workspace"
    assert snapshot["existed"] is False


def test_snapshot_of_a_missing_file_reports_no_prior_state(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)

    assert td.ToolDispatchService().snapshot_file("never-written.txt") == {
        "existed": False,
        "content": None,
        "too_large": False,
    }


def test_snapshot_refuses_to_hold_an_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    target = tmp_path / "big.txt"
    target.write_text("x" * (td.ToolDispatchService._SNAPSHOT_MAX_BYTES + 1), encoding="utf-8")

    snapshot = td.ToolDispatchService().snapshot_file("big.txt")

    assert snapshot == {"existed": True, "content": None, "too_large": True}


def test_snapshot_reports_an_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    target = tmp_path / "locked.txt"
    target.write_text("secret", encoding="utf-8")
    real_read_text = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "locked.txt":
            raise OSError(13, "permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    snapshot = td.ToolDispatchService().snapshot_file("locked.txt")

    assert snapshot["existed"] is True
    assert snapshot["too_large"] is True
    assert "permission denied" in snapshot["error"]


def test_restore_refuses_a_path_outside_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path / "root")

    result = td.ToolDispatchService().restore_snapshot(str(tmp_path / "elsewhere.txt"), "x")

    assert result == {
        "path": str(tmp_path / "elsewhere.txt"),
        "ok": False,
        "error": "path escapes the agent workspace",
    }


def test_restore_reports_a_filesystem_error(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "AGENT_ROOT", tmp_path)
    (tmp_path / "blocker").write_text("a file, not a directory", encoding="utf-8")

    result = td.ToolDispatchService().restore_snapshot("blocker/child.txt", "x")

    assert result["ok"] is False
    assert result["error"]


# ── transcript projections ───────────────────────────────────────────────────


def test_collect_created_files_expands_multi_file_results():
    transcript = [
        {
            "action": "create_web_project",
            "result": {"created_files": ["site/index.html", "site/app.js"]},
        },
        {"action": "write_file", "result": {"path": "notes.md", "bytes": 12}},
        {"action": "read_file", "result": {"path": "ignored.md"}},
    ]

    files = td.collect_created_files(transcript)

    assert [f["path"] for f in files] == ["site/index.html", "site/app.js", "notes.md"]
    assert files[0]["filename"] == "index.html"
    assert files[0]["bytes"] == 0
    assert files[2]["bytes"] == 12


def test_collect_artifacts_skips_steps_without_a_dict_result():
    transcript = [
        {"action": "write_file", "result": "not a dict"},
        {
            "action": "write_file",
            "result": {"path": "notes.md", "bytes": 3},
            "content_sanitize": {"repaired": True},
        },
    ]

    artifacts = td.collect_artifacts(transcript)

    assert len(artifacts) == 1
    assert artifacts[0]["path"] == "notes.md"
    assert artifacts[0]["repaired"] is True


# ── tool_response ────────────────────────────────────────────────────────────


def test_tool_response_wraps_success_and_translates_tool_errors():
    ok = td.tool_response(lambda value: {"echo": value}, "hi")

    assert ok["status"] == "ok"
    assert ok["result"] == {"echo": "hi"}
    assert ok["workspace"] == str(td.AGENT_ROOT)

    def failing():
        raise ToolError("bad argument")

    with pytest.raises(HTTPException) as excinfo:
        td.tool_response(failing)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "bad argument"
