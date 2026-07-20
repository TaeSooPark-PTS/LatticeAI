from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api import mcp as mcp_api
from latticeai.api.agent_registry import create_agent_registry_router
from latticeai.api.mcp import create_mcp_router
from latticeai.api.plugins import create_plugins_router
from latticeai.core.agent_registry import AgentRegistry
from latticeai.services.platform_runtime import PlatformRuntime
from latticeai.services.tool_dispatch import get_tool_permission
from latticeai.tools import AGENT_ROOT


def _admin_gate(state):
    def require_admin(_request):
        if not state["admin"]:
            raise HTTPException(status_code=403, detail="admin required")
        return "admin@example.com", {}

    return require_admin


def test_agent_registry_mutations_require_admin_and_reads_redact_secrets(tmp_path):
    registry = AgentRegistry(tmp_path / "agents.json")
    existing = registry.register(name="private", config={"api_key": "secret-value"})
    state = {"admin": False}
    app = FastAPI()
    app.include_router(create_agent_registry_router(
        registry=registry,
        require_user=lambda _request: "member@example.com",
        require_admin=_admin_gate(state),
        append_audit_event=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)

    listing = client.get("/agents/api/registry")
    denied_create = client.post("/agents/api/registry", json={"name": "blocked"})
    denied_update = client.patch(
        f"/agents/api/registry/{existing['id']}",
        json={"config": {"api_key": "replacement"}},
    )
    denied_delete = client.delete(f"/agents/api/registry/{existing['id']}")

    assert listing.status_code == 200
    private = next(item for item in listing.json()["agents"] if item["id"] == existing["id"])
    assert private["config"]["api_key"] == "[REDACTED_SECRET]"
    assert {denied_create.status_code, denied_update.status_code, denied_delete.status_code} == {403}

    state["admin"] = True
    created = client.post("/agents/api/registry", json={"name": "allowed"})
    assert created.status_code == 200


def test_plugin_state_changes_require_admin():
    class Registry:
        def set_enabled(self, plugin_id, enabled):
            return {"id": plugin_id, "enabled": enabled}

    state = {"admin": False}
    app = FastAPI()
    app.include_router(create_plugins_router(
        registry=Registry(),
        require_user=lambda _request: "member@example.com",
        require_admin=_admin_gate(state),
        append_audit_event=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)

    assert client.post("/plugins/enable", json={"plugin_id": "demo"}).status_code == 403
    assert client.post("/plugins/disable", json={"plugin_id": "demo"}).status_code == 403

    state["admin"] = True
    assert client.post("/plugins/enable", json={"plugin_id": "demo"}).json()["plugin"]["enabled"] is True


def test_plugin_execute_binds_authenticated_user_and_write_scope():
    seen = {}

    class Result:
        status = "ok"

        def as_dict(self):
            return {"status": self.status}

    class Registry:
        def execute_action(self, plugin_id, action, args, *, runners, workspace_id=None):
            seen["execution"] = (plugin_id, action, args, runners, workspace_id)
            return Result()

    def runner_factory(user, scope):
        seen["factory"] = (user, scope)
        return {"tools": lambda **kwargs: {}}

    app = FastAPI()
    app.include_router(create_plugins_router(
        registry=Registry(),
        require_user=lambda _request: "alice@example.com",
        require_admin=lambda _request: ("admin@example.com", {}),
        append_audit_event=lambda *args, **kwargs: None,
        gate_write=lambda _request: "org-one",
        plugin_runners_factory=runner_factory,
    ))

    response = TestClient(app).post(
        "/plugins/execute",
        json={"plugin_id": "demo", "action": "run_tool", "args": {"tool": "todo_read"}},
    )

    assert response.status_code == 200
    assert seen["factory"] == ("alice@example.com", "org-one")
    assert seen["execution"][4] == "org-one"


def _mcp_client(tmp_path):
    class Result:
        def as_dict(self):
            return {"status": "ok"}

    class Pipeline:
        item = None
        user = None

        def ingest(self, item, user_email=None):
            self.item = item
            self.user = user_email
            return Result()

    class Graph:
        calls = []

        def search(self, query, limit, *, allowed_workspaces=None):
            self.calls.append(("search", allowed_workspaces))
            return {"matches": []}

        def graph(self, limit, *, allowed_workspaces=None):
            self.calls.append(("graph", allowed_workspaces))
            return {"nodes": [], "edges": []}

        def context_for_query(self, query, limit, *, allowed_workspaces=None):
            self.calls.append(("context", allowed_workspaces))
            return ""

    class WorkspaceService:
        def resolve_write_scope(self, requested, user):
            if requested != "org-1":
                raise PermissionError("workspace denied")
            return requested

    pipeline = Pipeline()
    graph = Graph()
    app = FastAPI()
    app.include_router(create_mcp_router(
        require_user=lambda _request: "alice@example.com",
        require_admin=lambda _request: ("admin@example.com", {}),
        append_audit_event=lambda *args, **kwargs: None,
        load_mcp_installs=lambda: {"installed": {}},
        recommend_mcps=lambda *args, **kwargs: [],
        install_mcp=lambda *args, **kwargs: None,
        mcp_public_item=lambda item, installed: item,
        get_tool_permission=lambda name: "allow",
        tool_governance={},
        tool_governance_default={
            "risk": "low",
            "destructive": False,
            "shell": False,
            "network": False,
            "auto_approve": True,
            "sandbox": True,
            "rollback": False,
        },
        check_tool_role=lambda *args: None,
        tool_response=lambda *args, **kwargs: {},
        require_graph=lambda: None,
        knowledge_graph=graph,
        ingestion_pipeline=pipeline,
        data_dir=tmp_path,
        allowed_workspaces_for=lambda user: {"personal", "org-1"},
        workspace_service=WorkspaceService(),
    ))
    return TestClient(app), graph, pipeline


def test_mcp_graph_calls_are_workspace_scoped_and_ingest_identity_is_bound(tmp_path):
    client, graph, pipeline = _mcp_client(tmp_path)

    for action in ("knowledge_graph_search", "knowledge_graph_graph", "knowledge_graph_context"):
        response = client.post("/mcp/call", json={"action": action, "args": {"query": "x"}})
        assert response.status_code == 200
    assert graph.calls == [
        ("search", {"personal", "org-1"}),
        ("graph", {"personal", "org-1"}),
        ("context", {"personal", "org-1"}),
    ]

    spoofed = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "x", "workspace_id": "org-1", "user_email": "mallory@example.com"},
    })
    denied_scope = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "x", "workspace_id": "org-2"},
    })
    accepted = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "x", "workspace_id": "org-1"},
    })

    assert spoofed.status_code == 403
    assert denied_scope.status_code == 403
    assert accepted.status_code == 200
    assert pipeline.user == "alice@example.com"
    assert pipeline.item.owner == "alice@example.com"
    assert pipeline.item.workspace_id == "org-1"


def test_mcp_tool_discovery_masks_absolute_workspace_path(tmp_path, monkeypatch):
    async def empty_registry():
        return []

    monkeypatch.setattr(mcp_api, "_get_combined_registry", empty_registry)
    client, _graph, _pipeline = _mcp_client(tmp_path)

    response = client.get("/mcp/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"] == "."
    assert str(AGENT_ROOT) not in json.dumps(payload, ensure_ascii=False)


def test_mcp_cannot_bypass_local_file_approval(tmp_path):
    client, _graph, _pipeline = _mcp_client(tmp_path)

    response = client.post("/mcp/call", json={
        "action": "local_read",
        "args": {"path": "/etc/hosts"},
    })

    assert response.status_code == 403
    assert "local-file approval" in response.json()["detail"]


def test_local_file_tools_are_not_auto_approved():
    assert get_tool_permission("local_list")["requires_approval"] is True
    assert get_tool_permission("local_read")["requires_approval"] is True
    assert get_tool_permission("read_document")["requires_approval"] is True


def test_plugin_runner_cannot_bypass_local_file_approval():
    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.get_tool_permission = lambda tool, args=None: {
        "tool": tool,
        "requires_approval": False,
    }
    runtime.hooks = None
    runner = runtime.plugin_capability_runners("alice@example.com", "personal")["tools"]
    manifest = type("Manifest", (), {"provides": {"tools": ["local_read"]}})()

    with pytest.raises(HTTPException) as exc:
        runner(
            plugin_id="demo",
            action="run_tool",
            args={"tool": "local_read", "path": "/etc/hosts"},
            manifest=manifest,
        )

    assert exc.value.status_code == 403


def test_custom_mcp_listing_never_returns_environment_values(tmp_path):
    (tmp_path / "custom_mcps.json").write_text(json.dumps([{
        "id": "custom:test",
        "name": "test",
        "env_vars": [{"name": "API_KEY", "value": "super-secret"}],
    }]), encoding="utf-8")
    client, _graph, _pipeline = _mcp_client(tmp_path)

    payload = client.get("/mcp/custom").json()["custom"][0]["env_vars"][0]

    assert payload == {"name": "API_KEY", "configured": True}
