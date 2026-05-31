"""The tool registry is the single source of truth.

These tests keep the three lists that used to drift in lock-step:
  * tools.TOOL_HANDLERS   — how a tool is invoked (dispatch)
  * ToolRegistry governance — how the agent is allowed to use it (policy)
  * ToolRegistry catalog — how the tool is described to the LLM (prompt)
"""

import re

import tools


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
