"""The tool registry is the single source of truth.

These tests keep the three lists that used to drift in lock-step:
  * tools.TOOL_HANDLERS   — how a tool is invoked (dispatch)
  * ToolRegistry governance — how the agent is allowed to use it (policy)
  * ToolRegistry catalog — how the tool is described to the LLM (prompt)
"""

import re

from fastapi import HTTPException
import pytest
import latticeai.tools as tools
import latticeai.tools.knowledge as knowledge_tools

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


def test_governance_keys_are_all_dispatchable():
    import server
    registered = tools.registered_tools()
    stray = set(server.TOOL_GOVERNANCE) - set(registered)
    assert not stray, f"governance references tools that cannot be dispatched: {sorted(stray)}"


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

    saved = knowledge_tools.knowledge_save(
        "workspace-a secret",
        title="secret",
        workspace_id="workspace-a",
        user_email="alice@example.com",
    )
    assert str(tmp_path / ".lattice-scopes") in saved["path"]

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


def test_desktop_capability_is_admin_only_even_if_policy_approval_is_skipped():
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
        service.enforce_policy(
            "computer_screenshot",
            {},
            current_user="user@example.com",
            source="test",
            require_auto_approval=False,
        )
    assert denied.value.status_code == 403

    allowed = service.enforce_policy(
        "computer_screenshot",
        {},
        current_user="admin@example.com",
        source="test",
    )
    assert allowed["capability"] == "desktop:control"


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


def test_direct_tool_policy_blocks_unapproved_user_write_but_allows_admin():
    service = ToolDispatchService(registry=tools.DEFAULT_TOOL_REGISTRY)
    users = {
        "user@example.com": {"role": "user"},
        "admin@example.com": {"role": "admin"},
    }
    service.configure(
        load_users=lambda: users,
        get_user_role=lambda email, loaded: loaded[email]["role"],
    )

    with pytest.raises(HTTPException) as exc:
        service.enforce_policy(
            "write_file",
            {"path": "note.md", "content": "x"},
            current_user="user@example.com",
            source="http",
        )
    assert exc.value.status_code == 403
    assert "명시 승인" in str(exc.value.detail)

    policy = service.enforce_policy(
        "write_file",
        {"path": "note.md", "content": "x"},
        current_user="admin@example.com",
        source="http",
    )
    assert policy["risk"] == "write"

    with pytest.raises(HTTPException) as destructive:
        service.enforce_policy(
            "write_file",
            {"path": "/etc/passwd", "content": "x"},
            current_user="admin@example.com",
            source="http",
            trusted_admin=True,
        )
    assert destructive.value.status_code == 403


def test_build_agent_runtime_returns_single_agent_runtime():
    from latticeai.core.agent import SingleAgentRuntime
    from latticeai.services.tool_dispatch import build_agent_runtime

    class ModelRouter:
        async def generate_as(self, *args, **kwargs):
            return '{"action":"final","message":"ok"}'

        async def generate(self, *args, **kwargs):
            return '{"action":"memory","save_to_knowledge":false}'

    runtime = build_agent_runtime(
        model_router=ModelRouter(),
        execute_tool=lambda name, args: {"success": True},
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {"cleared": True},
        knowledge_save=lambda *args, **kwargs: {"status": "ok"},
        audit=lambda *args, **kwargs: None,
    )

    assert isinstance(runtime, SingleAgentRuntime)
    assert runtime.boundary()["runtime"] == "single_agent"


def test_catalog_brief_tokens_are_all_dispatchable():
    import server
    registered = tools.registered_tools()
    # meta-actions handled by the agent loop, not tools.py:
    meta = {"clear_history", "final"}
    tokens = set(re.findall(r"[a-z_]+", server._TOOL_CATALOG_BRIEF))
    # keep only things that look like tool names (appear in catalog rows, not labels)
    catalog_tools = {t for t in tokens if t in registered or t in meta}
    drift = (catalog_tools - meta) - set(registered)
    assert not drift, f"catalog brief lists undispatchable tools: {sorted(drift)}"
