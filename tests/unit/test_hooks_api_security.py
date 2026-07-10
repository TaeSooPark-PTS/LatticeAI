from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.api.hooks import create_hooks_router


@pytest.fixture()
def hooks_client(tmp_path):
    registry = HooksRegistry(tmp_path / "hooks.json")
    custom = registry.register(name="custom", kind="post_run")
    audits = []

    def require_user(_request: Request) -> str:
        return "member@example.com"

    def require_admin(request: Request):
        if request.headers.get("X-Test-Role") != "admin":
            raise HTTPException(status_code=403, detail="admin required")
        return "admin@example.com", {}

    app = FastAPI()
    app.include_router(
        create_hooks_router(
            registry=registry,
            require_user=require_user,
            require_admin=require_admin,
            append_audit_event=lambda event, **payload: audits.append((event, payload)),
        )
    )
    return TestClient(app), registry, custom, audits


def test_hook_reads_remain_available_to_authenticated_members(hooks_client):
    client, _registry, custom, _audits = hooks_client

    assert client.get("/api/hooks").status_code == 200
    assert client.get(f"/api/hooks/{custom['id']}").status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/hooks/run", {"kind": "post_run"}),
        ("post", "/api/hooks/fire", {"kind": "post_run"}),
        ("post", "/api/hooks/enable", {"hook_id": "builtin:audit-agent-run", "enabled": True}),
        ("post", "/api/hooks/disable", {"hook_id": "builtin:audit-agent-run"}),
        ("post", "/api/hooks/reorder", {"kind": "post_run", "ordered_ids": []}),
        ("post", "/api/hooks/register", {"name": "blocked", "kind": "post_run", "command": "true"}),
        ("delete", "/api/hooks/user:custom", None),
    ],
)
def test_hook_execution_and_mutations_require_admin(hooks_client, method, path, payload):
    client, _registry, _custom, _audits = hooks_client

    response = client.request(method, path, json=payload)

    assert response.status_code == 403


def test_admin_can_register_and_remove_hook(hooks_client):
    client, _registry, _custom, audits = hooks_client
    headers = {"X-Test-Role": "admin"}

    created = client.post(
        "/api/hooks/register",
        headers=headers,
        json={"name": "admin hook", "kind": "post_run"},
    )
    assert created.status_code == 200
    hook_id = created.json()["hook"]["id"]

    removed = client.delete(f"/api/hooks/{hook_id}", headers=headers)
    assert removed.status_code == 200
    assert [event for event, _payload in audits] == ["hook_register", "hook_remove"]
