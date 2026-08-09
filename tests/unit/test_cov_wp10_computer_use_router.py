"""Coverage for the computer-use router (latticeai/api/computer_use.py).

The router is built through its factory with injected fakes (idiom:
tests/unit/test_auth_router.py) and driven with a TestClient.  Every desktop
seam — screenshot, pointer, keyboard, app launch, and the agent loop's
``execute_tool`` — is replaced, so no test can reach a real screen and the
whole file runs headless.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.api.computer_use import create_computer_use_router
from latticeai.services.tool_dispatch import (
    DEFAULT_TOOL_DISPATCH_SERVICE,
    configure_tool_dispatch,
)
from latticeai.tools import ToolError

ADMIN = "admin@example.com"


@pytest.fixture()
def policy(monkeypatch):
    """Configure the process-wide dispatch service, restoring it afterwards."""
    service = DEFAULT_TOOL_DISPATCH_SERVICE
    for field in ("load_users", "get_user_role", "permission_mode"):
        monkeypatch.setattr(service, field, getattr(service, field))

    def _configure(role: str = "admin") -> None:
        configure_tool_dispatch(
            load_users=lambda: {},
            get_user_role=lambda _email, _users=None: role,
        )

    _configure()
    return _configure


class _Model:
    """Scripted model router: each generate() pops the next raw response."""

    def __init__(self, script=(), current_model_id="test-model"):
        self.current_model_id = current_model_id
        self.script = list(script)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return '{"action":"final","message":"script exhausted"}'
        return self.script.pop(0)


def _client(
    tmp_path,
    *,
    user=ADMIN,
    model=None,
    audit=None,
    saved=None,
    workspace_service=None,
):
    app = FastAPI()
    app.include_router(create_computer_use_router(
        model_router=model if model is not None else _Model(current_model_id=None),
        require_user=lambda _request: user,
        tool_response=lambda fn, *a, **k: {"status": "ok", "result": fn(*a, **k)},
        save_to_history=(lambda *a, **k: saved.append((a, k))) if saved is not None else (lambda *a, **k: None),
        hooks=HooksRegistry(tmp_path / "hooks.json"),
        append_audit_event=(lambda event, **payload: audit.append((event, payload)))
        if audit is not None else None,
        workspace_service=workspace_service,
    ))
    return TestClient(app, raise_server_exceptions=False)


def _events(body: str):
    """Parse an SSE body into [(event, data-dict), ...]."""
    parsed = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        payload = "{}"
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                payload = line[len("data: "):]
        parsed.append((name, json.loads(payload)))
    return parsed


# ── status routes ────────────────────────────────────────────────────────────
def test_status_routes_wrap_the_tool_payload(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(
        "latticeai.api.computer_use.desktop_bridge_status",
        lambda: {"bridge": "connected"},
    )
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_status",
        lambda: {"available": True},
    )
    client = _client(tmp_path)

    chrome = client.get("/tools/chrome_status")
    cu = client.get("/tools/computer_use_status")

    assert chrome.status_code == 200
    assert chrome.json()["status"] == "ok"
    assert chrome.json()["result"] == {"bridge": "connected"}
    assert cu.status_code == 200
    assert cu.json()["result"] == {"available": True}


def test_cu_status_propagates_the_policy_http_error(tmp_path, policy):
    policy("user")  # computer_status is admin-only in the tool registry
    audit = []

    response = _client(tmp_path, user="viewer@example.com", audit=audit).get("/cu/status")

    assert response.status_code == 403
    assert "관리자 전용" in response.json()["detail"]
    assert audit[-1][1]["status"] == "blocked"


def test_cu_status_maps_tool_error_to_400(tmp_path, monkeypatch, policy):
    def _unavailable():
        raise ToolError("Accessibility permission missing")

    monkeypatch.setattr("latticeai.api.computer_use.computer_status", _unavailable)

    response = _client(tmp_path).get("/cu/status")

    assert response.status_code == 400
    assert response.json()["detail"] == "Accessibility permission missing"


def test_cu_screenshot_propagates_the_policy_http_error(tmp_path, policy):
    policy("user")

    response = _client(tmp_path, user="viewer@example.com").get("/cu/screenshot")

    assert response.status_code == 403


def test_cu_screenshot_maps_tool_error_to_400(tmp_path, monkeypatch, policy):
    def _no_display():
        raise ToolError("스크린샷 불가")

    monkeypatch.setattr("latticeai.api.computer_use.computer_screenshot", _no_display)

    response = _client(tmp_path).get("/cu/screenshot")

    assert response.status_code == 400
    assert response.json()["detail"] == "스크린샷 불가"


def test_cu_status_returns_the_raw_tool_result(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_status",
        lambda: {"available": True, "failsafe": True},
    )

    response = _client(tmp_path).get("/cu/status")

    assert response.status_code == 200
    assert response.json() == {"available": True, "failsafe": True}


# ── direct action routes ─────────────────────────────────────────────────────
def test_direct_action_routes_forward_arguments_to_the_desktop_tools(tmp_path, monkeypatch, policy):
    seen = []

    def _stub(name):
        def _call(*args):
            seen.append((name, args))
            return {"action": name, "args": list(args)}

        return _call

    for name in (
        "computer_open_app", "computer_open_url", "computer_click", "computer_type",
        "computer_key", "computer_scroll", "computer_move", "computer_drag",
    ):
        monkeypatch.setattr("latticeai.api.computer_use." + name, _stub(name))

    client = _client(tmp_path)
    calls = [
        ("/cu/open_app", {"app": "Notes"}, ("Notes",)),
        ("/cu/open_url", {"url": "https://example.com", "app": "Safari"},
         ("https://example.com", "Safari")),
        ("/cu/click", {"x": 5, "y": 6, "button": "right", "double": True}, (5, 6, "right", True)),
        ("/cu/type", {"text": "hello", "interval": 0.02}, ("hello", 0.02)),
        ("/cu/key", {"key": "command+c"}, ("command+c",)),
        ("/cu/scroll", {"x": 1, "y": 2, "direction": "up", "clicks": 4}, (1, 2, "up", 4)),
        ("/cu/move", {"x": 9, "y": 10}, (9, 10)),
        ("/cu/drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, (1, 2, 3, 4)),
    ]

    for path, payload, expected_args in calls:
        response = client.post(path, json=payload)
        assert response.status_code == 200, (path, response.text)
        assert response.json()["status"] == "ok"
        assert response.json()["result"]["args"] == list(expected_args)

    assert [name for name, _args in seen] == [
        "computer_open_app", "computer_open_url", "computer_click", "computer_type",
        "computer_key", "computer_scroll", "computer_move", "computer_drag",
    ]
    assert [args for _name, args in seen] == [expected for _p, _pl, expected in calls]


# ── /cu/agent fast path ──────────────────────────────────────────────────────
def test_cu_agent_fast_path_opens_the_url_found_in_the_task(tmp_path, monkeypatch, policy):
    opened = []
    saved = []
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_open_url",
        lambda url, app: opened.append((url, app)) or {"opened": url, "app": app},
    )
    # workspace_service=None: the unscoped legacy write path returns the header
    # verbatim instead of resolving a workspace.
    client = _client(tmp_path, saved=saved)

    response = client.post(
        "/cu/agent",
        headers={"X-Workspace-Id": "org:legacy"},
        json={"task": "크롬으로 https://example.com 열어줘", "conversation_id": "c1"},
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _data in events] == ["start", "action", "result", "final"]
    assert opened == [("https://example.com", "Google Chrome")]
    action = dict(events)["action"]
    assert action["action"] == "computer_open_url"
    assert action["args"] == {"url": "https://example.com", "app": "Google Chrome"}
    assert "https://example.com" in dict(events)["final"]["message"]
    assert [kwargs["workspace_id"] for _args, kwargs in saved] == ["org:legacy", "org:legacy"]


def test_cu_agent_fast_path_streams_a_policy_block_as_tool_error(tmp_path, policy):
    policy("user")

    response = _client(tmp_path, user="viewer@example.com").post(
        "/cu/agent", json={"task": "Chrome 실행해줘"},
    )

    assert response.status_code == 200
    events = dict(_events(response.text))
    assert "tool_error" in events
    assert "관리자 전용" in events["tool_error"]["error"]
    assert "final" not in events


def test_cu_agent_fast_path_streams_a_tool_error(tmp_path, monkeypatch, policy):
    def _fails(_app):
        raise ToolError("Google Chrome is not installed")

    monkeypatch.setattr("latticeai.api.computer_use.computer_open_app", _fails)

    response = _client(tmp_path).post("/cu/agent", json={"task": "크롬 켜줘"})

    assert response.status_code == 200
    events = dict(_events(response.text))
    assert events["tool_error"]["error"] == "Google Chrome is not installed"
    assert "final" not in events


def test_cu_agent_reports_when_no_model_is_loaded(tmp_path, policy):
    response = _client(tmp_path).post("/cu/agent", json={"task": "summarise my screen"})

    assert response.status_code == 200
    events = _events(response.text)
    assert events == [("error", {"error": "No model loaded."})]


# ── /cu/agent model loop ─────────────────────────────────────────────────────
def test_cu_agent_loop_streams_screenshots_results_and_both_error_shapes(
    tmp_path, monkeypatch, policy,
):
    model = _Model(script=[
        '{"action":"computer_screenshot","args":{}}',
        '{"action":"vision_analyze","args":{"image_b64":"QUJD","prompt":"what is open?"}}',
        '{"action":"computer_click","args":{"x":1,"y":2}}',
        '{"action":"run_command","args":{"command":"rm -rf /"}}',
        "I am afraid I cannot comply.",
    ])

    def _execute(name, _args):
        if name == "computer_screenshot":
            return {"screenshot_b64": "QUJD", "screen_width": 100, "screen_height": 50}
        if name == "vision_analyze":
            return {"description": "a browser window"}
        raise ToolError("mouse control is blocked")

    monkeypatch.setattr("latticeai.api.computer_use.execute_tool", _execute)
    audit = []

    response = _client(tmp_path, model=model, audit=audit).post(
        "/cu/agent", json={"task": "inspect the screen", "max_steps": 5},
    )

    assert response.status_code == 200
    events = _events(response.text)
    names = [name for name, _data in events]
    assert names == [
        "start",
        "action", "screenshot",
        "action", "result",
        "action", "tool_error",
        "action", "tool_error",
        "error",
        "done",
    ]

    by_name = {}
    for name, data in events:
        by_name.setdefault(name, []).append(data)

    assert by_name["screenshot"][0]["screenshot_b64"] == "QUJD"
    assert by_name["screenshot"][0]["width"] == 100
    # The captured frame is handed to the next generate() call as image_data.
    assert model.calls[0]["image_data"] is None
    assert model.calls[1]["image_data"] == "QUJD"
    assert model.calls[2]["image_data"] is None  # cleared after a non-screenshot step

    assert by_name["result"][0]["result"] == {"description": "a browser window"}
    assert by_name["tool_error"][0]["error"] == "mouse control is blocked"
    assert "circuit breaker" in by_name["tool_error"][1]["error"]
    assert "did not return valid JSON" in by_name["error"][0]["error"]

    done = by_name["done"][0]
    assert done["steps"] == 4
    assert [step["action"] for step in done["transcript"]] == [
        "computer_screenshot", "vision_analyze", "computer_click", "run_command",
    ]
    assert done["transcript"][0]["result"] == {
        "screen_width": 100, "screen_height": 50, "screenshot_captured": True,
    }

    # vision_analyze audit records only sizes, never the image or the prompt.
    vision_audit = [payload for _event, payload in audit if payload["tool"] == "vision_analyze"]
    assert vision_audit[0]["args"] == {"image_b64_length": 4, "prompt_length": len("what is open?")}
    assert "QUJD" not in str(vision_audit[0]["args"])


def test_cu_agent_loop_runs_out_of_steps_and_emits_done(tmp_path, monkeypatch, policy):
    model = _Model(script=['{"action":"computer_key","args":{"key":"tab"}}'] * 3)
    monkeypatch.setattr(
        "latticeai.api.computer_use.execute_tool",
        lambda name, args: {"action": name, "args": args},
    )

    response = _client(tmp_path, model=model).post(
        "/cu/agent", json={"task": "press tab a few times", "max_steps": 2},
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[0][0] == "start"
    assert events[0][1]["max_steps"] == 2
    assert events[-1][0] == "done"
    assert events[-1][1]["steps"] == 2
    assert model.calls and len(model.calls) == 2


def test_cu_agent_rejects_an_unauthorised_workspace(tmp_path, policy):
    class _Denying:
        def resolve_write_scope(self, _requested, _user):
            raise PermissionError("workspace write denied")

    response = _client(tmp_path, workspace_service=_Denying()).post(
        "/cu/agent",
        headers={"X-Workspace-Id": "org:forbidden"},
        json={"task": "크롬 열어"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "workspace write denied"


def test_cu_agent_resolves_the_workspace_from_the_query_string(tmp_path, monkeypatch, policy):
    class _Resolver:
        def __init__(self):
            self.calls = []

        def resolve_write_scope(self, requested, user):
            self.calls.append((requested, user))
            return "org:resolved"

    resolver = _Resolver()
    saved = []
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_open_app",
        lambda app: {"opened": app},
    )

    response = _client(tmp_path, saved=saved, workspace_service=resolver).post(
        "/cu/agent?workspace_id=org:query", json={"task": "크롬 띄워줘"},
    )

    assert response.status_code == 200
    assert resolver.calls == [("org:query", ADMIN)]
    assert {kwargs["workspace_id"] for _args, kwargs in saved} == {"org:resolved"}


def test_dispatch_reraises_a_hook_block_as_permission_error(tmp_path, policy):
    audit = []
    registry_app = FastAPI()
    hooks = HooksRegistry(tmp_path / "hooks.json")
    hooks.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("hook says no"))
    registry_app.include_router(create_computer_use_router(
        model_router=_Model(current_model_id=None),
        require_user=lambda _request: ADMIN,
        tool_response=lambda fn, *a, **k: fn(*a, **k),
        save_to_history=lambda *a, **k: None,
        hooks=hooks,
        append_audit_event=lambda event, **payload: audit.append((event, payload)),
    ))

    response = TestClient(registry_app, raise_server_exceptions=False).get("/cu/status")

    assert response.status_code == 403
    assert "hook says no" in response.json()["detail"]
    assert audit[-1][1]["status"] == "error"


def test_cu_agent_keeps_an_unscoped_no_auth_call_unscoped(tmp_path, monkeypatch, policy):
    class _NeverCalled:
        def resolve_write_scope(self, _requested, _user):
            raise AssertionError("an unscoped no-auth call must not resolve a workspace")

    saved = []
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_open_app", lambda app: {"opened": app},
    )

    response = _client(tmp_path, user="", saved=saved, workspace_service=_NeverCalled()).post(
        "/cu/agent", json={"task": "크롬 열어"},
    )

    assert response.status_code == 200
    assert [kwargs["workspace_id"] for _args, kwargs in saved] == [None, None]


def test_cu_type_audit_records_only_the_length_of_the_typed_text(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(
        "latticeai.api.computer_use.computer_type",
        lambda text, interval: {"typed": len(text)},
    )
    audit = []

    response = _client(tmp_path, audit=audit).post(
        "/cu/type", json={"text": "hunter2-secret", "interval": 0.01},
    )

    assert response.status_code == 200
    assert audit[-1][1]["args"] == {"text_length": 14, "interval": 0.01}
    assert "hunter2-secret" not in str(audit[-1])


def test_cu_screenshot_maps_a_hook_block_to_403(tmp_path, policy):
    app = FastAPI()
    hooks = HooksRegistry(tmp_path / "hooks.json")
    hooks.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("no screenshots"))
    app.include_router(create_computer_use_router(
        model_router=_Model(current_model_id=None),
        require_user=lambda _request: ADMIN,
        tool_response=lambda fn, *a, **k: fn(*a, **k),
        save_to_history=lambda *a, **k: None,
        hooks=hooks,
    ))

    response = TestClient(app, raise_server_exceptions=False).get("/cu/screenshot")

    assert response.status_code == 403
    assert "no screenshots" in response.json()["detail"]


def test_cu_agent_loop_finishes_on_a_final_action(tmp_path, policy):
    model = _Model(script=['{"action":"final","message":"모두 마쳤습니다."}'])
    saved = []

    response = _client(tmp_path, model=model, saved=saved).post(
        "/cu/agent", json={"task": "wrap up", "conversation_id": "c9", "max_steps": 4},
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _data in events] == ["start", "final"]
    assert events[-1][1] == {"message": "모두 마쳤습니다.", "steps": []}
    assert [args for args, _kwargs in saved] == [
        ("user", "wrap up"), ("assistant", "모두 마쳤습니다."),
    ]
    assert {kwargs["conversation_id"] for _args, kwargs in saved} == {"c9"}


def test_http_exception_from_a_desktop_tool_is_not_swallowed(tmp_path, monkeypatch, policy):
    def _teapot():
        raise HTTPException(status_code=418, detail="desktop bridge is a teapot")

    monkeypatch.setattr("latticeai.api.computer_use.computer_status", _teapot)

    response = _client(tmp_path).get("/cu/status")

    assert response.status_code == 418
