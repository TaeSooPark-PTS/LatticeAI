"""wp31: the marketplace template router, built from its factory.

``create_marketplace_router`` had no direct test — every handler body was
unexecuted. The catalog and the store are the real ones (``TemplateCatalog``
over the built-in templates, ``WorkspaceOSStore`` under ``tmp_path``) because
the branches that were missing are the ``MarketplaceError`` → HTTP status
translations, and a fake catalog would only prove the fake raises.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.marketplace import create_marketplace_router
from latticeai.core.marketplace import TemplateCatalog
from latticeai.core.workspace_os import WorkspaceOSStore

ADMIN = "owner@example.com"


class _Gates:
    """Records what each handler asked of the read/write workspace gates."""

    def __init__(self, scope: Optional[str] = "personal") -> None:
        self.scope = scope
        self.reads: List[str] = []
        self.writes: List[str] = []

    def read(self, request: Request) -> Optional[str]:
        self.reads.append(request.url.path)
        return self.scope

    def write(self, request: Request) -> Optional[str]:
        self.writes.append(request.url.path)
        return self.scope


@pytest.fixture()
def market(tmp_path):
    store = WorkspaceOSStore(tmp_path / "data")
    gates = _Gates()
    seen_users: List[str] = []

    def require_user(request: Request) -> str:
        if request.headers.get("X-Test-Auth") == "off":
            raise HTTPException(status_code=401, detail="sign in first")
        seen_users.append(ADMIN)
        return ADMIN

    app = FastAPI()
    app.include_router(
        create_marketplace_router(
            store=store,
            catalog=TemplateCatalog(),
            require_user=require_user,
            gate_read=gates.read,
            gate_write=gates.write,
            workspace_graph=lambda: None,
        )
    )
    return TestClient(app), store, gates, seen_users


def test_list_templates_filters_by_kind_and_rejects_unknown_kind(market):
    client, _store, gates, _users = market

    everything = client.get("/marketplace/templates")
    agents = client.get("/marketplace/templates", params={"kind": "agent"})
    bogus = client.get("/marketplace/templates", params={"kind": "nope"})

    assert everything.status_code == 200
    assert everything.json()["total"] == len(everything.json()["templates"]) > 4
    assert agents.status_code == 200
    assert {t["kind"] for t in agents.json()["templates"]} == {"agent"}
    assert bogus.status_code == 400
    assert "unknown template kind" in bogus.json()["detail"]
    # The read gate ran on every listing, including the failing one.
    assert gates.reads.count("/marketplace/templates") == 3


def test_export_template_returns_artifact_and_404s_unknown_id(market):
    client, _store, _gates, _users = market

    ok = client.get("/marketplace/templates/agent/agent-research-assistant/export")
    missing = client.get("/marketplace/templates/agent/no-such-template/export")

    assert ok.status_code == 200
    body = ok.json()
    assert body["kind"] == "agent"
    assert body["template"]["id"] == "agent-research-assistant"
    assert body["metadata"]["template_id"] == "agent-research-assistant"
    assert missing.status_code == 404
    assert "template not found" in missing.json()["detail"]


def test_import_template_marks_imported_and_400s_invalid_payload(market):
    client, _store, _gates, _users = market

    ok = client.post(
        "/marketplace/templates/import",
        json={"data": {"kind": "agent", "id": "imported-agent", "name": "Imported"}},
    )
    missing_name = client.post(
        "/marketplace/templates/import",
        json={"data": {"kind": "agent", "id": "no-name"}},
    )

    assert ok.status_code == 200
    assert ok.json()["template"]["metadata"]["imported"] is True
    assert ok.json()["template"]["version"] == "1.0.0"
    assert missing_name.status_code == 400
    assert missing_name.json()["detail"] == "template missing name"


def test_install_template_writes_registry_and_400s_unknown_kind(market):
    client, store, gates, _users = market

    installed = client.post(
        "/marketplace/templates/install",
        json={"data": {"kind": "agent", "id": "agent-x", "name": "Agent X"}},
    )
    rejected = client.post(
        "/marketplace/templates/install",
        json={"data": {"kind": "unsupported", "id": "x", "name": "X"}},
    )

    assert installed.status_code == 200
    assert installed.json()["installed"]["template_id"] == "agent-x"
    assert installed.json()["installed"]["registry"]
    assert rejected.status_code == 400
    assert gates.writes == [
        "/marketplace/templates/install",
        "/marketplace/templates/install",
    ]
    registry = store.list_template_registry(workspace_id="personal")
    assert any("agent-x" in str(key) for key in registry)


def test_install_workflow_template_creates_workflow(market):
    client, store, _gates, _users = market

    export = client.get(
        "/marketplace/templates/workflow/workflow-agent-plugin-review/export"
    ).json()
    installed = client.post(
        "/marketplace/templates/install", json={"data": export["template"]}
    )

    assert installed.status_code == 200
    payload = installed.json()["installed"]
    assert payload["kind"] == "workflow"
    assert payload["workflow_id"]
    assert any(
        wf["id"] == payload["workflow_id"]
        for wf in store.list_workflows(workspace_id="personal")["workflows"]
    )


def test_clone_template_creates_editable_copy_and_404s_unknown(market):
    client, _store, _gates, _users = market

    cloned = client.post(
        "/marketplace/templates/agent/agent-coding-assistant/clone",
        json={"name": "My Coder"},
    )
    missing = client.post(
        "/marketplace/templates/agent/ghost/clone", json={"name": "ghost"}
    )

    assert cloned.status_code == 200
    template = cloned.json()["template"]
    assert template["name"] == "My Coder"
    assert template["metadata"]["cloned_from"] == "agent-coding-assistant"
    assert template["metadata"]["editable"] is True
    assert template["id"] != "agent-coding-assistant"
    assert missing.status_code == 404


def test_template_registry_is_workspace_scoped(market):
    client, _store, gates, _users = market

    client.post(
        "/marketplace/templates/install",
        json={"data": {"kind": "agent", "id": "scoped-agent", "name": "Scoped"}},
    )
    mine = client.get("/marketplace/templates/registry").json()["registry"]

    gates.scope = "other-workspace"
    theirs = client.get("/marketplace/templates/registry").json()["registry"]

    assert any("scoped-agent" in str(key) for key in mine)
    assert not any("scoped-agent" in str(key) for key in theirs)


def test_interop_bridges_report_the_unified_pipeline(market):
    client, _store, _gates, _users = market

    body = client.get("/marketplace/interop/bridges").json()

    assert body["pipeline"] == "unified-ingestion"
    assert body["total"] == len(body["bridges"]) >= 2
    assert {bridge["kind"] for bridge in body["bridges"]} == {"ingestion_bridge"}


def test_every_marketplace_route_requires_a_signed_in_user(market):
    client, _store, _gates, _users = market
    headers = {"X-Test-Auth": "off"}
    calls: List[Dict[str, Any]] = [
        {"method": "GET", "url": "/marketplace/templates"},
        {"method": "GET", "url": "/marketplace/templates/agent/a/export"},
        {"method": "POST", "url": "/marketplace/templates/import", "json": {"data": {}}},
        {"method": "POST", "url": "/marketplace/templates/install", "json": {"data": {}}},
        {"method": "POST", "url": "/marketplace/templates/agent/a/clone", "json": {}},
        {"method": "GET", "url": "/marketplace/templates/registry"},
        {"method": "GET", "url": "/marketplace/interop/bridges"},
    ]

    statuses = [
        client.request(call["method"], call["url"], json=call.get("json"), headers=headers).status_code
        for call in calls
    ]

    assert statuses == [401] * len(calls)
