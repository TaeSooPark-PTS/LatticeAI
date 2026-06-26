import asyncio

from latticeai.core import mcp_registry
from latticeai.core.mcp_registry import create_mcp_install_state


def test_mcp_install_state_round_trips_and_projects_public_items(tmp_path):
    state = create_mcp_install_state(tmp_path)
    assert state["load_mcp_installs"]() == {"installed": {}, "updated_at": None}

    state["save_mcp_installs"]({"installed": {"filesystem": {"installed": True, "status": "active"}}})
    loaded = state["load_mcp_installs"]()
    assert loaded["updated_at"]
    assert loaded["installed"]["filesystem"]["installed"] is True

    item = {
        "id": "filesystem",
        "name": "Filesystem MCP",
        "install_mode": "builtin",
        "description": "Local files",
    }
    public = state["mcp_public_item"](item, loaded["installed"])
    assert public["id"] == "filesystem"
    assert public["installed"] is True
    assert public["status"] == "active"


def test_connector_install_preserves_needs_auth_without_external_install(monkeypatch, tmp_path):
    async def fake_registry():
        return [{
            "id": "gmail",
            "name": "Gmail",
            "install_mode": "connector",
            "description": "Mail connector",
        }]

    monkeypatch.setattr(mcp_registry, "_get_combined_registry", fake_registry)
    state = create_mcp_install_state(tmp_path)

    result = asyncio.run(state["install_mcp"]("gmail"))

    assert result["id"] == "gmail"
    assert result["installed"] is True
    assert result["status"] == "needs_auth"
    assert result["authenticated"] is False
    saved = state["load_mcp_installs"]()["installed"]["gmail"]
    assert saved["installed"] is True
    assert saved["status"] == "needs_auth"
