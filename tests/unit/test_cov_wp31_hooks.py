"""wp31: the hooks router's dispatch, toggle, reorder and removal surfaces.

``tests/unit/test_hooks_api_security.py`` covers the admin gate and the
register/remove happy path; what never ran was the dispatch endpoint, the
``recent_runs`` listing, both enable/disable handlers, reorder, and every
error translation (unknown hook → 404, bad kind → 400).

The registry is the real :class:`~lattice_brain.runtime.hooks.HooksRegistry`
over ``tmp_path``. Hooks registered here carry no ``command``, so dispatch
resolves them as *advisory* — no subprocess, no wall-clock dependence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.api.hooks import create_hooks_router

ADMIN = "admin@example.com"
MEMBER = "member@example.com"


def _client(tmp_path, *, admin_returns_tuple: bool = True):
    registry = HooksRegistry(tmp_path / "hooks.json")
    audits: List[Tuple[str, Dict[str, Any]]] = []

    def append_audit_event(event_name: str, **payload: Any) -> None:
        # ``hook_run`` passes an ``event=`` field of its own, so the sink's own
        # first parameter cannot be called ``event``.
        audits.append((event_name, payload))

    def require_admin(request: Request):
        if request.headers.get("X-Test-Role") != "admin":
            raise HTTPException(status_code=403, detail="admin required")
        return (ADMIN, {}) if admin_returns_tuple else ADMIN

    app = FastAPI()
    app.include_router(
        create_hooks_router(
            registry=registry,
            require_user=lambda request: MEMBER,
            require_admin=require_admin,
            append_audit_event=append_audit_event,
        )
    )
    return TestClient(app), registry, audits


@pytest.fixture()
def hooks(tmp_path):
    return _client(tmp_path)


ADMIN_HEADERS = {"X-Test-Role": "admin"}


def test_listing_is_open_to_members_and_filterable_by_kind(hooks):
    client, registry, _audits = hooks
    registry.register(name="wp31 listed", kind="pre_index")

    everything = client.get("/api/hooks")
    by_kind = client.get("/api/hooks", params={"kind": "pre_index"})

    assert everything.status_code == 200
    assert len(everything.json()["hooks"]) > len(by_kind.json()["hooks"])
    assert {hook["kind"] for hook in by_kind.json()["hooks"]} == {"pre_index"}
    assert "user:wp31-listed" in {hook["id"] for hook in by_kind.json()["hooks"]}


def test_register_then_remove_records_both_audit_events(hooks):
    client, registry, audits = hooks

    created = client.post(
        "/api/hooks/register",
        headers=ADMIN_HEADERS,
        json={
            "name": "wp31 lifecycle",
            "kind": "pre_workflow",
            "description": "advisory",
            "order": 5,
        },
    )
    hook_id = created.json()["hook"]["id"]
    removed = client.delete("/api/hooks/" + hook_id, headers=ADMIN_HEADERS)

    assert created.status_code == 200
    assert created.json()["hook"]["order"] == 5
    assert removed.status_code == 200
    assert removed.json() == {"removed": hook_id}
    assert [event for event, _payload in audits] == ["hook_register", "hook_remove"]
    assert registry.get(hook_id) is None


def test_hook_runs_listing_reflects_dispatched_hooks(hooks):
    client, registry, audits = hooks
    custom = registry.register(name="wp31 advisory", kind="post_run")

    empty = client.get("/api/hooks/runs", params={"limit": 5}).json()
    dispatched = client.post(
        "/api/hooks/run",
        headers=ADMIN_HEADERS,
        json={"hook_id": custom["id"], "event": "manual", "payload": {"a": 1}},
    )
    after = client.get("/api/hooks/runs", params={"limit": 5, "kind": "post_run"}).json()

    assert empty["runs"] == []
    assert dispatched.status_code == 200
    assert dispatched.json()["hook_id"] == custom["id"]
    assert dispatched.json()["status"] == "advisory"
    assert [run["hook_id"] for run in after["runs"]] == [custom["id"]]
    assert audits[-1][0] == "hook_run"
    assert audits[-1][1]["user_email"] == ADMIN


def test_run_by_kind_dispatches_every_enabled_hook_of_that_kind(hooks):
    client, registry, _audits = hooks
    registry.register(name="wp31 first", kind="post_workflow")
    registry.register(name="wp31 second", kind="post_workflow", enabled=False)

    response = client.post(
        "/api/hooks/run", headers=ADMIN_HEADERS, json={"kind": "post_workflow"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "post_workflow"
    assert body["blocked"] is False
    ran = {result["hook_id"] for result in body["results"]}
    assert "user:wp31-first" in ran
    assert "user:wp31-second" not in ran


def test_fire_alias_shares_the_run_dispatch(hooks):
    client, registry, _audits = hooks
    custom = registry.register(name="wp31 fired", kind="post_run")

    fired = client.post(
        "/api/hooks/fire", headers=ADMIN_HEADERS, json={"hook_id": custom["id"]}
    )

    assert fired.status_code == 200
    assert fired.json()["hook_id"] == custom["id"]


def test_run_requires_a_kind_or_hook_id(hooks):
    client, _registry, audits = hooks

    response = client.post("/api/hooks/run", headers=ADMIN_HEADERS, json={})

    assert response.status_code == 400
    assert "kind" in response.json()["detail"]
    assert audits == []


def test_run_translates_unknown_hook_and_unknown_kind(hooks):
    client, _registry, _audits = hooks

    unknown_hook = client.post(
        "/api/hooks/run", headers=ADMIN_HEADERS, json={"hook_id": "user:ghost"}
    )
    unknown_kind = client.post(
        "/api/hooks/run", headers=ADMIN_HEADERS, json={"kind": "not_a_kind"}
    )

    assert unknown_hook.status_code == 404
    assert "Hook not found" in unknown_hook.json()["detail"]
    assert unknown_kind.status_code == 400
    assert "kind must be one of" in unknown_kind.json()["detail"]


def test_identity_only_admin_adapter_is_accepted(tmp_path):
    """Small adapters return just the email; production returns ``(email, users)``."""
    client, registry, audits = _client(tmp_path, admin_returns_tuple=False)
    custom = registry.register(name="wp31 identity", kind="post_run")

    response = client.post(
        "/api/hooks/run", headers=ADMIN_HEADERS, json={"hook_id": custom["id"]}
    )

    assert response.status_code == 200
    assert audits[-1][1]["user_email"] == ADMIN


def test_inspect_unknown_hook_is_404(hooks):
    client, registry, _audits = hooks
    custom = registry.register(name="wp31 inspected", kind="pre_tool")

    found = client.get("/api/hooks/" + custom["id"])
    missing = client.get("/api/hooks/user:nope")

    assert found.status_code == 200
    assert found.json()["hook"]["id"] == custom["id"]
    assert missing.status_code == 404
    assert "user:nope" in missing.json()["detail"]


def test_enable_and_disable_flip_the_persisted_flag(hooks):
    client, registry, audits = hooks
    custom = registry.register(name="wp31 toggled", kind="post_run")

    disabled = client.post(
        "/api/hooks/disable", headers=ADMIN_HEADERS, json={"hook_id": custom["id"]}
    )
    state_after_disable = registry.inspect(custom["id"])["enabled"]
    enabled = client.post(
        "/api/hooks/enable",
        headers=ADMIN_HEADERS,
        json={"hook_id": custom["id"], "enabled": True},
    )

    assert disabled.status_code == 200
    assert disabled.json()["hook"]["enabled"] is False
    assert state_after_disable is False
    assert enabled.status_code == 200
    assert enabled.json()["hook"]["enabled"] is True
    assert registry.inspect(custom["id"])["enabled"] is True
    assert [event for event, _payload in audits] == ["hook_toggle", "hook_toggle"]
    assert [payload["enabled"] for _event, payload in audits] == [False, True]


def test_enable_and_disable_404_on_unknown_hook(hooks):
    client, _registry, audits = hooks

    enabled = client.post(
        "/api/hooks/enable",
        headers=ADMIN_HEADERS,
        json={"hook_id": "user:ghost", "enabled": True},
    )
    disabled = client.post(
        "/api/hooks/disable", headers=ADMIN_HEADERS, json={"hook_id": "user:ghost"}
    )

    assert enabled.status_code == 404
    assert disabled.status_code == 404
    assert audits == []


def test_reorder_rewrites_the_dispatch_order(hooks):
    client, registry, _audits = hooks
    first = registry.register(name="wp31 alpha", kind="post_upload")
    second = registry.register(name="wp31 beta", kind="post_upload")

    response = client.post(
        "/api/hooks/reorder",
        headers=ADMIN_HEADERS,
        json={"kind": "post_upload", "ordered_ids": [second["id"], first["id"]]},
    )

    assert response.status_code == 200
    ordered = [hook["id"] for hook in response.json()["hooks"]]
    assert ordered.index(second["id"]) < ordered.index(first["id"])


def test_register_rejects_an_unknown_kind(hooks):
    client, _registry, audits = hooks

    response = client.post(
        "/api/hooks/register",
        headers=ADMIN_HEADERS,
        json={"name": "wp31 bad", "kind": "not_a_kind"},
    )

    assert response.status_code == 400
    assert "kind must be one of" in response.json()["detail"]
    assert audits == []


def test_remove_reports_unknown_and_refuses_builtin_hooks(hooks):
    client, _registry, audits = hooks

    unknown = client.delete("/api/hooks/user:ghost", headers=ADMIN_HEADERS)
    builtin = client.delete(
        "/api/hooks/builtin:audit-agent-run", headers=ADMIN_HEADERS
    )

    assert unknown.status_code == 404
    assert builtin.status_code == 400
    assert "Built-in hooks cannot be removed" in builtin.json()["detail"]
    assert audits == []
