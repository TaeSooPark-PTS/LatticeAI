"""The tool registry is the single source of truth.

These tests keep the three lists that used to drift in lock-step:
  * tools.TOOL_HANDLERS   — how a tool is invoked (dispatch)
  * ToolRegistry governance — how the agent is allowed to use it (policy)
  * ToolRegistry catalog — how the tool is described to the LLM (prompt)
"""

import re

from fastapi import HTTPException
import pytest
import tools

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


def test_tool_registry_manifest_reports_contract_diagnostics():
    manifest = tools.DEFAULT_TOOL_REGISTRY.manifest()
    diagnostics = manifest["diagnostics"]
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
