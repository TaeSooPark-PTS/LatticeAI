"""v3.5.0 runtime hook-coverage: previously-bypassing tool paths now go through
the unified pre_tool/post_tool lifecycle.

The computer-use router is exercised end-to-end through a Starlette TestClient
with a real HooksRegistry. Building the full tools router needs the whole app, so
the read_file/edit_file/grep/clear_history fix is covered by asserting the
kwargs-forwarding dispatch path that those routes now use.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.runtime.hooks import HooksRegistry, dispatch_tool
from latticeai.api.computer_use import create_computer_use_router
from latticeai.services.tool_dispatch import configure_tool_dispatch


@pytest.fixture()
def registry(tmp_path):
    return HooksRegistry(tmp_path / "hooks.json")


def _app(
    registry,
    *,
    user="admin@example.com",
    role="admin",
    audit_events=None,
    model_router=None,
    save_to_history=None,
    workspace_service=None,
):
    configure_tool_dispatch(
        load_users=lambda: {},
        get_user_role=lambda email, _users=None: role if email == user else "user",
    )
    app = FastAPI()
    app.include_router(create_computer_use_router(
        model_router=model_router or type("M", (), {"current_model_id": None})(),
        require_user=lambda request: user,
        tool_response=lambda fn, *a, **k: {"status": "ok", "result": fn(*a, **k)},
        save_to_history=save_to_history or (lambda *a, **k: None),
        hooks=registry,
        append_audit_event=(lambda event_type, **payload: audit_events.append((event_type, payload)))
        if audit_events is not None else None,
        workspace_service=workspace_service,
    ))
    return app


def test_cu_status_runs_through_pre_and_post_tool(registry):
    events = []
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: events.append(("pre", ctx.event)))
    post = registry.register(name="cu post probe", kind="post_tool")
    registry.register_hook(post["id"], lambda ctx: events.append(("post", ctx.event, ctx.payload.get("status"))))
    client = TestClient(_app(registry), raise_server_exceptions=False)
    # We don't assert the HTTP status (computer_status availability is
    # platform-dependent) — only that the tool lifecycle fired around it.
    client.get("/cu/status")
    assert ("pre", "tool.computer_status") in events
    assert any(e[0] == "post" and e[1] == "tool.computer_status" for e in events)


def test_cu_status_blocked_by_pre_tool_returns_403(registry):
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("policy denied"))
    client = TestClient(_app(registry), raise_server_exceptions=False)
    resp = client.get("/cu/status")
    assert resp.status_code == 403
    assert "denied" in resp.json()["detail"]


def test_cu_screenshot_goes_through_lifecycle(registry):
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("no screenshots"))
    client = TestClient(_app(registry), raise_server_exceptions=False)
    resp = client.get("/cu/screenshot")
    assert resp.status_code == 403


def test_cu_direct_actions_require_registry_policy_admin_role(registry):
    audit_events = []
    client = TestClient(
        _app(registry, user="user@example.com", role="user", audit_events=audit_events),
        raise_server_exceptions=False,
    )

    resp = client.post("/cu/click", json={"x": 1, "y": 2})

    assert resp.status_code == 403
    assert audit_events[-1][0] == "computer_use_tool"
    assert audit_events[-1][1]["status"] == "blocked"
    assert audit_events[-1][1]["tool"] == "computer_click"


def test_cu_type_audit_does_not_store_typed_text(registry, monkeypatch):
    audit_events = []
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_type",
        lambda text, interval=0.04: {"typed": len(text), "interval": interval},
    )
    client = TestClient(_app(registry, audit_events=audit_events), raise_server_exceptions=False)

    resp = client.post("/cu/type", json={"text": "secret-token-value", "interval": 0.01})

    assert resp.status_code == 200
    event = audit_events[-1][1]
    assert event["status"] == "ok"
    assert event["args"] == {"text_length": len("secret-token-value"), "interval": 0.01}
    assert "secret-token-value" not in str(event)


def test_dispatch_tool_forwards_kwargs_into_hook_payload(registry):
    """read_file/grep route their kwargs through dispatch_tool; the pre_tool
    payload must carry the argument keys so hooks can inspect them."""
    seen = {}
    registry.register_hook("builtin:tool-permission-gate",
                           lambda ctx: seen.update(ctx.payload))

    def _run():
        return {"ok": True}

    # Mirrors api/tools.py _tool_response: kwargs become the dispatch args dict.
    kwargs = {"path": "notes.md", "offset": 0, "limit": 0, "line_numbers": True}
    out = dispatch_tool(registry, "read_file", dict(kwargs), _run, source="http")
    assert out == {"ok": True}
    assert seen.get("tool") == "read_file"
    assert "path" in seen.get("args_keys", [])


class _WorkspaceResolver:
    def __init__(self, resolved="org:resolved", error=None):
        self.resolved = resolved
        self.error = error
        self.calls = []

    def resolve_write_scope(self, requested, user):
        self.calls.append((requested, user))
        if self.error is not None:
            raise PermissionError(self.error)
        return self.resolved


def test_cu_agent_fast_path_persists_authenticated_workspace_scope(registry, monkeypatch):
    saved = []
    workspace = _WorkspaceResolver()
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_open_app",
        lambda app: {"opened": app},
    )
    client = TestClient(
        _app(
            registry,
            save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
            workspace_service=workspace,
        )
    )

    response = client.post(
        "/cu/agent",
        headers={"X-Workspace-Id": "org:requested"},
        json={
            "task": "Chrome 열어",
            "conversation_id": "conversation-fast",
        },
    )

    assert response.status_code == 200
    assert "event: final" in response.text
    assert workspace.calls == [("org:requested", "admin@example.com")]
    assert [entry[0][0] for entry in saved] == ["user", "assistant"]
    assert [entry[0][1] for entry in saved] == [
        "Chrome 열어",
        "Google Chrome을 열었습니다.",
    ]
    for _args, kwargs in saved:
        assert kwargs == {
            "source": "web",
            "conversation_id": "conversation-fast",
            "user_email": "admin@example.com",
            "workspace_id": "org:resolved",
        }


def test_cu_agent_model_final_persists_authenticated_workspace_scope(registry):
    class FinalModel:
        current_model_id = "deterministic-model"

        def __init__(self):
            self.calls = 0

        async def generate(self, **_kwargs):
            self.calls += 1
            return '{"action":"final","message":"model final"}'

    saved = []
    model = FinalModel()
    workspace = _WorkspaceResolver()
    client = TestClient(
        _app(
            registry,
            model_router=model,
            save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
            workspace_service=workspace,
        )
    )

    response = client.post(
        "/cu/agent?workspace_id=org:query",
        json={
            "task": "complete through the model",
            "conversation_id": "conversation-model",
        },
    )

    assert response.status_code == 200
    assert "model final" in response.text
    assert model.calls == 1
    assert workspace.calls == [("org:query", "admin@example.com")]
    assert [entry[0] for entry in saved] == [
        ("user", "complete through the model"),
        ("assistant", "model final"),
    ]
    for _args, kwargs in saved:
        assert kwargs == {
            "source": "web",
            "conversation_id": "conversation-model",
            "user_email": "admin@example.com",
            "workspace_id": "org:resolved",
        }


def test_cu_agent_rejects_unauthorized_workspace_before_streaming(registry):
    saved = []
    workspace = _WorkspaceResolver(error="workspace write denied")
    client = TestClient(
        _app(
            registry,
            save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
            workspace_service=workspace,
        )
    )

    response = client.post(
        "/cu/agent",
        headers={"X-Workspace-Id": "org:forbidden"},
        json={"task": "Chrome 열어"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace write denied"
    assert workspace.calls == [("org:forbidden", "admin@example.com")]
    assert saved == []


def test_cu_agent_no_auth_without_scope_preserves_legacy_history_shape(registry, monkeypatch):
    class UnexpectedWorkspaceResolution:
        def resolve_write_scope(self, _requested, _user):
            raise AssertionError("unscoped no-auth CU should remain legacy local")

    saved = []
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_open_app",
        lambda app: {"opened": app},
    )
    client = TestClient(
        _app(
            registry,
            user="",
            role="owner",
            save_to_history=lambda *args, **kwargs: saved.append((args, kwargs)),
            workspace_service=UnexpectedWorkspaceResolution(),
        )
    )

    response = client.post("/cu/agent", json={"task": "Chrome 열어"})

    assert response.status_code == 200
    assert len(saved) == 2
    assert all(kwargs["user_email"] == "" for _args, kwargs in saved)
    assert all(kwargs["workspace_id"] is None for _args, kwargs in saved)
