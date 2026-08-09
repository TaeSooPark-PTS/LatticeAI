"""``/mcp/*`` endpoints of the MCP router.

The router is built through its factory with injected fakes (the
``tests/unit/test_auth_router.py`` idiom); the registry fetchers and
``Path.home()`` are redirected so nothing leaves the process or ``tmp_path``.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import mcp as mcp_api
from latticeai.api.mcp import create_mcp_router
from latticeai.core import mcp_registry
from latticeai.core.tool_registry import MCP_TOOL_DESCRIPTIONS
from latticeai.services.tool_dispatch import EXPLICIT_CONSENT_TOOLS

EN = {"Accept-Language": "en"}

_DEFAULT_GOVERNANCE = {
    "risk": "read",
    "destructive": False,
    "shell": False,
    "network": False,
    "auto_approve": True,
    "sandbox": "workspace",
    "rollback": "none",
}


def build_mcp_client(tmp_path, **overrides):
    """A TestClient over the MCP router with every dependency injected."""

    async def _no_recommendations(*_args, **_kwargs):
        return []

    async def _no_install(*_args, **_kwargs):
        return {}

    deps = {
        "require_user": lambda _request: "alice@example.com",
        "require_admin": lambda _request: ("admin@example.com", {}),
        "append_audit_event": lambda *_args, **_kwargs: None,
        "load_mcp_installs": lambda: {"installed": {}},
        "recommend_mcps": _no_recommendations,
        "install_mcp": _no_install,
        "mcp_public_item": lambda item, installed: {**item, "installed": bool(installed)},
        "get_tool_permission": lambda name: {"tool": name, "requires_approval": False},
        "tool_governance": {},
        "tool_governance_default": dict(_DEFAULT_GOVERNANCE),
        "check_tool_role": lambda *_args: None,
        "tool_response": lambda *_args, **_kwargs: {},
        "require_graph": lambda: None,
        "knowledge_graph": None,
        "ingestion_pipeline": None,
        "data_dir": tmp_path,
    }
    deps.update(overrides)
    app = FastAPI()
    app.include_router(create_mcp_router(**deps))
    return TestClient(app)


def _registry(monkeypatch, entries):
    async def fake_registry():
        return entries

    monkeypatch.setattr(mcp_api, "_get_combined_registry", fake_registry)


def test_mcp_tools_publishes_governance_capability_and_scope(monkeypatch, tmp_path):
    _registry(monkeypatch, [{"id": "browser", "name": "Browser MCP"}])
    scoped = {**_DEFAULT_GOVERNANCE, "capability": "filesystem.read", "scope": "workspace"}
    client = build_mcp_client(tmp_path, tool_governance={"read_file": scoped})

    payload = client.get("/mcp/tools").json()

    by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert payload["status"] == "ok"
    assert payload["workspace"] == "."
    assert payload["installed_mcps"] == [
        {"id": "browser", "name": "Browser MCP", "installed": False}
    ]
    assert by_name["read_file"]["governance"]["capability"] == "filesystem.read"
    assert by_name["read_file"]["governance"]["scope"] == "workspace"
    assert by_name["read_file"]["permission"] == {
        "tool": "read_file",
        "requires_approval": False,
    }
    assert "capability" not in by_name["write_file"]["governance"]
    assert "scope" not in by_name["write_file"]["governance"]
    # Tools behind the dedicated local-file consent flow are never advertised here.
    assert not set(by_name) & set(EXPLICIT_CONSENT_TOOLS)
    assert set(by_name) == set(MCP_TOOL_DESCRIPTIONS) - set(EXPLICIT_CONSENT_TOOLS)


def test_mcp_recommend_passes_the_query_and_limit_through(tmp_path):
    seen = {}

    async def recommend(query, limit):
        seen["args"] = (query, limit)
        return [{"id": "filesystem", "score": 3}]

    client = build_mcp_client(tmp_path, recommend_mcps=recommend)

    response = client.post("/mcp/recommend", json={"query": "build an app", "limit": 2})

    assert response.json() == {"recommendations": [{"id": "filesystem", "score": 3}]}
    assert seen["args"] == ("build an app", 2)


def test_mcp_install_audits_the_admin_and_returns_the_installer_result(tmp_path):
    events = []

    async def install(mcp_id):
        return {"id": mcp_id, "status": "active"}

    client = build_mcp_client(
        tmp_path,
        install_mcp=install,
        append_audit_event=lambda action, **kwargs: events.append((action, kwargs)),
    )

    response = client.post("/mcp/install", json={"mcp_id": "browser"})

    assert response.json() == {"id": "browser", "status": "active"}
    assert events == [
        ("mcp_install", {"user_email": "admin@example.com", "mcp_id": "browser"})
    ]


def test_mcp_installed_projects_every_registry_entry(monkeypatch, tmp_path):
    _registry(monkeypatch, [{"id": "browser"}, {"id": "gmail"}])
    client = build_mcp_client(
        tmp_path, load_mcp_installs=lambda: {"installed": {"browser": {"installed": True}}}
    )

    payload = client.get("/mcp/installed").json()

    assert payload == {
        "installed": [
            {"id": "browser", "installed": True},
            {"id": "gmail", "installed": True},
        ]
    }


def test_mcp_connector_returns_setup_instructions(monkeypatch, tmp_path):
    _registry(monkeypatch, [
        {"id": "chrome", "name": "Chrome MCP", "install_mode": "connector"},
        {"id": "browser", "name": "Browser MCP", "install_mode": "bundled"},
    ])
    client = build_mcp_client(tmp_path)

    found = client.get("/mcp/connectors/chrome")
    not_a_connector = client.get("/mcp/connectors/browser", headers=EN)
    unknown = client.get("/mcp/connectors/nope", headers=EN)

    assert found.status_code == 200
    assert len(found.json()["instructions"]) == 3
    assert "Chrome MCP" in found.json()["instructions"][1]
    assert not_a_connector.status_code == 404
    assert not_a_connector.json()["detail"] == "That connector was not found."
    assert unknown.status_code == 404


def test_mcp_registry_refresh_clears_the_remote_stamp_and_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_registry, "_REMOTE_REGISTRY_CACHE", [{"id": "r1"}, {"id": "r2"}])
    monkeypatch.setattr(mcp_registry, "_REMOTE_REGISTRY_FETCHED_AT", "stale-stamp")
    _registry(monkeypatch, [{"id": "a"}, {"id": "b"}, {"id": "r1"}])
    client = build_mcp_client(tmp_path)

    payload = client.post("/mcp/registry/refresh").json()

    assert payload == {"status": "ok", "total": 3, "remote": 2}
    assert mcp_registry._REMOTE_REGISTRY_FETCHED_AT is None


def _write_claude_settings(monkeypatch, tmp_path, body):
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(body, encoding="utf-8")


def test_claude_code_servers_is_empty_without_a_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert build_mcp_client(tmp_path).get("/mcp/claude-code-servers").json() == {"servers": []}


def test_claude_code_servers_translates_mcp_server_entries(monkeypatch, tmp_path):
    _write_claude_settings(monkeypatch, tmp_path, json.dumps({"mcpServers": {
        "linear": {
            "command": "npx",
            "args": ["-y", "linear-mcp"],
            "env": {"LINEAR_API_KEY": "set", "OPTIONAL": ""},
        },
        "plain": {"command": "/usr/local/bin/plain-mcp"},
    }}))

    servers = build_mcp_client(tmp_path).get("/mcp/claude-code-servers").json()["servers"]

    assert servers[0] == {
        "id": "claude-code:linear",
        "name": "linear",
        "description": "Claude Code MCP: npx -y linear-mcp",
        "package": "npx -y linear-mcp",
        "icon": "🤖",
        "category": "Claude Code",
        "source": "claude-code",
        "installed": True,
        "env_vars": [
            {"name": "LINEAR_API_KEY", "configured": True},
            {"name": "OPTIONAL", "configured": False},
        ],
    }
    assert servers[1]["package"] == "/usr/local/bin/plain-mcp"
    assert servers[1]["env_vars"] == []


def test_claude_code_servers_swallows_an_unreadable_settings_file(monkeypatch, tmp_path):
    _write_claude_settings(monkeypatch, tmp_path, "{ this is not json")

    assert build_mcp_client(tmp_path).get("/mcp/claude-code-servers").json() == {"servers": []}


def test_custom_mcp_list_is_empty_when_the_store_is_missing_or_corrupt(tmp_path):
    client = build_mcp_client(tmp_path)
    assert client.get("/mcp/custom").json() == {"custom": []}

    (tmp_path / "custom_mcps.json").write_text("[[[", encoding="utf-8")
    assert client.get("/mcp/custom").json() == {"custom": []}


def test_custom_mcp_add_persists_an_entry_and_replaces_the_same_id(tmp_path):
    events = []
    client = build_mcp_client(
        tmp_path, append_audit_event=lambda action, **kwargs: events.append((action, kwargs))
    )

    created = client.post("/mcp/custom", json={
        "name": " My Server ",
        "package": " my-mcp ",
        "description": " does things ",
        "env_vars": [{"name": "TOKEN", "value": "secret"}],
    })
    replaced = client.post("/mcp/custom", json={"name": "my server", "package": "my-mcp@2"})

    entry = replaced.json()["entry"]
    stored = json.loads((tmp_path / "custom_mcps.json").read_text(encoding="utf-8"))
    assert created.json()["entry"]["id"] == "custom:my-server"
    assert created.json()["entry"]["name"] == "My Server"
    assert created.json()["entry"]["package"] == "my-mcp"
    assert created.json()["entry"]["description"] == "does things"
    assert entry["id"] == "custom:my-server"
    assert entry["install_mode"] == "npm"
    assert entry["installed"] is False
    assert [e["id"] for e in stored] == ["custom:my-server"]
    assert stored[0]["package"] == "my-mcp@2"
    assert events[0][0] == "mcp_custom_add"
    # The listing never echoes the stored secret back.
    assert client.get("/mcp/custom").json()["custom"][0]["env_vars"] == []


def test_custom_mcp_add_rejects_blank_name_or_package(tmp_path):
    client = build_mcp_client(tmp_path)

    blank_name = client.post("/mcp/custom", json={"name": "  ", "package": "x"}, headers=EN)
    blank_package = client.post("/mcp/custom", json={"name": "x", "package": " "}, headers=EN)

    assert blank_name.status_code == 400
    assert blank_name.json()["detail"] == "A name is required."
    assert blank_package.status_code == 400
    assert blank_package.json()["detail"] == "A package is required."
    assert not (tmp_path / "custom_mcps.json").exists()


def test_custom_mcp_delete_removes_only_a_known_entry(tmp_path):
    client = build_mcp_client(tmp_path)
    client.post("/mcp/custom", json={"name": "keeper", "package": "keeper-mcp"})

    missing = client.delete("/mcp/custom/custom:ghost", headers=EN)
    removed = client.delete("/mcp/custom/custom:keeper")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "That item was not found."
    assert removed.json() == {"status": "ok"}
    assert json.loads((tmp_path / "custom_mcps.json").read_text(encoding="utf-8")) == []
