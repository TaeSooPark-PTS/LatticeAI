"""wp31: the Plugin SDK router's read, validate and lifecycle handlers.

``tests/unit/test_registry_api_security.py`` proves the admin gate and the
execute boundary; the catalog, detail, manifest-validation, install, uninstall
and disable handlers were never executed. The registry is the real
:class:`~latticeai.core.plugins.PluginRegistry` over a ``tmp_path`` plugin
directory backed by a real ``WorkspaceOSStore``, so install/uninstall/disable
assert against persisted lifecycle state rather than a stub's echo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.plugins import create_plugins_router
from latticeai.core.plugins import PLUGIN_SDK_VERSION, PluginRegistry
from latticeai.core.workspace_os import WorkspaceOSStore

ADMIN = "admin@example.com"
MEMBER = "member@example.com"


def _write_plugin(plugins_dir: Path, plugin_id: str, **overrides: Any) -> Dict[str, Any]:
    body = {
        "id": plugin_id,
        "name": overrides.get("name", plugin_id.title()),
        "version": overrides.get("version", "1.0.0"),
        "description": "wp31 fixture plugin",
        "lattice_version": overrides.get("lattice_version", PLUGIN_SDK_VERSION),
        "permissions": overrides.get("permissions", ["read_workspace"]),
        "provides": overrides.get("provides", {"skills": ["demo_skill"]}),
    }
    target = plugins_dir / plugin_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.json").write_text(json.dumps(body), encoding="utf-8")
    return body


@pytest.fixture()
def plugins(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(plugins_dir, "demo-plugin")
    (plugins_dir / "broken").mkdir()
    (plugins_dir / "broken" / "plugin.json").write_text("{not json", encoding="utf-8")

    store = WorkspaceOSStore(tmp_path / "data")
    registry = PluginRegistry(plugins_dir, store=store)
    audits: List[Tuple[str, Dict[str, Any]]] = []
    registered_skills: List[Tuple[str, str]] = []

    def require_admin(request: Request):
        if request.headers.get("X-Test-Role") != "admin":
            raise HTTPException(status_code=403, detail="admin required")
        return ADMIN, {}

    app = FastAPI()
    app.include_router(
        create_plugins_router(
            registry=registry,
            require_user=lambda request: MEMBER,
            require_admin=require_admin,
            append_audit_event=lambda event, **payload: audits.append((event, payload)),
            register_skill=lambda skill, plugin_id: registered_skills.append(
                (skill, plugin_id)
            ),
        )
    )
    return TestClient(app), registry, audits, registered_skills


ADMIN_HEADERS = {"X-Test-Role": "admin"}


def test_sdk_page_redirects_into_the_spa_marketplace(plugins):
    client, _registry, _audits, _skills = plugins

    response = client.get("/plugins/sdk", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/app#/marketplace"


def test_registry_catalog_lists_valid_and_invalid_manifests(plugins):
    client, _registry, _audits, _skills = plugins

    body = client.get("/plugins/registry").json()

    assert body["sdk_version"] == PLUGIN_SDK_VERSION
    assert body["total"] == 1
    assert body["plugins"][0]["id"] == "demo-plugin"
    assert body["plugins"][0]["installed"] is False
    assert body["plugins"][0]["install_status"] == "available"
    assert [entry["path"].endswith("broken") for entry in body["invalid"]] == [True]


def test_plugin_detail_merges_manifest_with_registry_state(plugins):
    client, _registry, _audits, _skills = plugins

    before = client.get("/plugins/registry/demo-plugin").json()
    client.post(
        "/plugins/install", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )
    after = client.get("/plugins/registry/demo-plugin").json()

    assert before["plugin"]["id"] == "demo-plugin"
    assert before["registry"] == {}
    assert after["registry"]["installed"] is True
    assert after["registry"]["version"] == "1.0.0"


def test_plugin_detail_404s_for_an_unknown_plugin(plugins):
    client, _registry, _audits, _skills = plugins

    response = client.get("/plugins/registry/ghost-plugin")

    assert response.status_code == 404
    assert response.json()["detail"] == "Plugin not found: ghost-plugin"


def test_validate_reports_errors_without_installing_anything(plugins):
    client, registry, _audits, _skills = plugins

    good = client.post(
        "/plugins/validate",
        json={
            "manifest": {
                "id": "candidate",
                "name": "Candidate",
                "version": "2.1.0",
                "permissions": ["run_tools"],
            }
        },
    ).json()
    bad = client.post(
        "/plugins/validate", json={"manifest": {"id": "Bad ID!", "version": "nope"}}
    ).json()

    assert good["ok"] is True
    assert good["errors"] == []
    assert good["manifest"]["id"] == "candidate"
    assert bad["ok"] is False
    assert bad["manifest"] is None
    assert any("semantic version" in error for error in bad["errors"])
    assert registry.store.list_plugin_registry() == {}


def test_install_registers_bundled_skills_and_audits(plugins):
    client, registry, audits, skills = plugins

    response = client.post(
        "/plugins/install", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )

    assert response.status_code == 200
    assert response.json()["registered_skills"] == ["demo_skill"]
    assert skills == [("demo_skill", "demo-plugin")]
    assert registry.store.list_plugin_registry()["demo-plugin"]["installed"] is True
    assert audits == [
        ("plugin_install", {"user_email": ADMIN, "plugin": "demo-plugin"})
    ]


def test_install_translates_registry_errors_into_400(plugins):
    client, _registry, audits, _skills = plugins

    response = client.post(
        "/plugins/install", headers=ADMIN_HEADERS, json={"plugin_id": "ghost-plugin"}
    )

    assert response.status_code == 400
    assert "plugin not found or invalid" in response.json()["detail"]
    assert audits == []


def test_uninstall_clears_installed_state(plugins):
    client, registry, audits, _skills = plugins
    client.post(
        "/plugins/install", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )

    response = client.post(
        "/plugins/uninstall", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )

    assert response.status_code == 200
    assert registry.store.list_plugin_registry()["demo-plugin"]["installed"] is False
    assert [event for event, _payload in audits] == [
        "plugin_install",
        "plugin_uninstall",
    ]


def test_disable_then_enable_flips_persisted_enabled_flag(plugins):
    client, registry, audits, _skills = plugins
    client.post(
        "/plugins/install", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )

    disabled = client.post(
        "/plugins/disable", headers=ADMIN_HEADERS, json={"plugin_id": "demo-plugin"}
    )
    state_after_disable = registry.store.list_plugin_registry()["demo-plugin"]["enabled"]
    enabled = client.post(
        "/plugins/enable",
        headers=ADMIN_HEADERS,
        json={"plugin_id": "demo-plugin", "enabled": True},
    )

    assert disabled.status_code == 200
    assert disabled.json()["plugin"]["enabled"] is False
    assert state_after_disable is False
    assert enabled.json()["plugin"]["enabled"] is True
    assert [event for event, _payload in audits][-2:] == [
        "plugin_disable",
        "plugin_enable",
    ]


def test_lifecycle_mutations_stay_behind_the_admin_gate(plugins):
    client, _registry, audits, _skills = plugins

    statuses = [
        client.post(path, json={"plugin_id": "demo-plugin"}).status_code
        for path in ("/plugins/install", "/plugins/uninstall", "/plugins/disable")
    ]

    assert statuses == [403, 403, 403]
    assert audits == []
