"""The tool registry is the single source of truth.

These tests keep the three lists that used to drift in lock-step:
  * tools.TOOL_HANDLERS   — how a tool is invoked (dispatch)
  * ToolRegistry governance — how the agent is allowed to use it (policy)
  * ToolRegistry catalog — how the tool is described to the LLM (prompt)
"""

import re

import pytest
from fastapi import HTTPException

import latticeai.tools as tools
import latticeai.tools.knowledge as knowledge_tools
from latticeai.core.tool_registry import TOOL_CATALOG_BRIEF, TOOL_GOVERNANCE
from latticeai.services.tool_dispatch import ToolDispatchService


def test_execute_tool_uses_registry():
    assert "read_file" in tools.registered_tools()
    assert callable(tools.TOOL_HANDLERS["read_file"])
    assert tools.DEFAULT_TOOL_REGISTRY.registered_tools() == tools.registered_tools()


def test_unknown_action_raises():
    try:
        tools.execute_tool("does_not_exist", {})
        assert False, "expected ToolError"
    except tools.ToolError:
        pass


def test_dispatchable_tools_are_governed_and_historical_writes_are_not_dispatched():
    registered = tools.registered_tools()
    missing = set(registered) - set(TOOL_GOVERNANCE)
    assert not missing, f"handlers without governance: {sorted(missing)}"
    assert tools.DEFAULT_TOOL_REGISTRY.governance is not None
    # Write tools may remain in governance for the native loop, but the
    # worker table is read-only + pointer tools.
    for name in ("knowledge_save", "local_write", "write_file"):
        assert name in TOOL_GOVERNANCE
        assert name not in registered
        assert name not in tools.TOOL_HANDLERS


def test_tool_registry_owns_permission_views():
    permission = tools.DEFAULT_TOOL_REGISTRY.permission("run_command", {"command": "ls"})
    assert permission["risk"] == "high"
    assert permission["requires_approval"] is True


def test_desktop_and_knowledge_tools_require_consent_capability_and_scope():
    for name in {
        "computer_screenshot",
        "computer_status",
        "chrome_status",
        "computer_use_status",
    }:
        policy = tools.DEFAULT_TOOL_REGISTRY.policy_for(name, {})
        assert policy["auto_approve"] is False
        assert policy["capability"] == "desktop:control"
        assert policy["scope"] == "host"

    for name in {
        "knowledge_save",
        "knowledge_search",
        "knowledge_tree",
        "obsidian_save",
        "obsidian_search",
        "obsidian_tree",
    }:
        policy = tools.DEFAULT_TOOL_REGISTRY.policy_for(name, {})
        assert policy["auto_approve"] is False
        assert policy["sandbox"] == "workspace"
        assert policy["scope"] == "workspace_user"
        assert policy["capability"] in {"workspace:read", "workspace:write"}


def test_knowledge_vault_is_partitioned_by_workspace_and_user(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_tools, "BRAIN_DIR", tmp_path)

    own_root = knowledge_tools.knowledge_scope_root(
        workspace_id="workspace-a",
        user_email="alice@example.com",
    )
    note_dir = own_root / "00_Raw"
    note_dir.mkdir(parents=True)
    (note_dir / "secret.md").write_text("workspace-a secret", encoding="utf-8")
    assert str(tmp_path / ".lattice-scopes") in str(own_root)

    own = knowledge_tools.knowledge_search(
        "workspace-a secret",
        workspace_id="workspace-a",
        user_email="alice@example.com",
    )
    other_workspace = knowledge_tools.knowledge_search(
        "workspace-a secret",
        workspace_id="workspace-b",
        user_email="alice@example.com",
    )
    other_user = knowledge_tools.knowledge_search(
        "workspace-a secret",
        workspace_id="workspace-a",
        user_email="bob@example.com",
    )

    assert len(own["results"]) == 1
    assert other_workspace["results"] == []
    assert other_user["results"] == []


def test_registry_knowledge_execution_fails_closed_without_scope():
    with pytest.raises(tools.ToolError, match="authenticated workspace"):
        tools.execute_tool("knowledge_search", {"query": "secret"})


def test_desktop_capability_is_admin_only():
    users = {
        "user@example.com": {"role": "user"},
        "admin@example.com": {"role": "admin"},
    }
    service = ToolDispatchService(registry=tools.DEFAULT_TOOL_REGISTRY)
    service.configure(
        load_users=lambda: users,
        get_user_role=lambda email, loaded: loaded[email]["role"],
    )

    with pytest.raises(HTTPException) as denied:
        service.check_role("computer_screenshot", "user@example.com")
    assert denied.value.status_code == 403

    service.check_role("computer_screenshot", "admin@example.com")
    assert service.policy_for("computer_screenshot", {})["capability"] == "desktop:control"


def test_tool_registry_manifest_reports_contract_diagnostics():
    manifest = tools.DEFAULT_TOOL_REGISTRY.manifest()
    diagnostics = manifest["diagnostics"]
    assert manifest["schema_version"] == "tool-registry-contract/v1"
    assert manifest["boundary"]["owner"] == "latticeai.core.tool_registry.ToolRegistry"
    assert manifest["boundary"]["permission_owner"] == "latticeai.services.tool_dispatch.ToolDispatchService"
    assert manifest["status"] in {"ok", "degraded"}
    assert diagnostics["registered_tools"] == len(tools.registered_tools())
    read_file = next(item for item in manifest["tools"] if item["name"] == "read_file")
    assert read_file["registered"] is True
    assert read_file["governed"] is True
    assert read_file["permission"]["risk"] == "low"


def test_tool_dispatch_service_isolates_role_callbacks():
    service = ToolDispatchService(registry=tools.DEFAULT_TOOL_REGISTRY)
    service.configure(
        load_users=lambda: {"user@example.com": {"role": "user"}},
        get_user_role=lambda email, users: users[email]["role"],
    )

    service.check_role("read_file", "user@example.com")

    with pytest.raises(HTTPException) as exc:
        service.check_role("run_command", "user@example.com")
    assert exc.value.status_code == 403

    admin_service = ToolDispatchService(registry=tools.DEFAULT_TOOL_REGISTRY)
    admin_service.configure(
        load_users=lambda: {"admin@example.com": {"role": "admin"}},
        get_user_role=lambda email, users: users[email]["role"],
    )
    admin_service.check_role("run_command", "admin@example.com")


def test_catalog_brief_still_names_the_dispatchable_read_tools():
    registered = tools.registered_tools()
    tokens = set(re.findall(r"[a-z_]+", TOOL_CATALOG_BRIEF))
    expected = {
        "list_dir",
        "workspace_tree",
        "read_file",
        "grep",
        "search_files",
        "knowledge_search",
        "knowledge_tree",
        "git_status",
        "computer_screenshot",
    }
    assert expected <= registered
    assert expected <= tokens
