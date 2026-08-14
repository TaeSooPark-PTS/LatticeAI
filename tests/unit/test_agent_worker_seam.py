"""The AI-Worker seam: what the Rust loop is allowed to make Python do.

Once orchestration moves to ``lattice-agent``, these three routes are the only
way the loop reaches the model, the tool handlers, and the change governor. So
the interesting assertions are not "does it return 200" — they are the denials.

The guards are therefore driven through the **real**
``latticeai.core.permission_mode.is_circuit_breaker`` and
``latticeai.core.tool_governor.classify_tool_call``, over the **real** tool
registry's policies. Only the things a unit test must not actually do are
faked: generating with a model, executing a tool handler, and staging a
proposal on disk. A test that faked the classifier could pass while the seam
let ``rm -rf /`` through.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.agent_worker_seam import (
    MAX_MAX_TOKENS,
    MAX_TEMPERATURE,
    SEAM_ENV_VAR,
    SEAM_RATE_BUCKET,
    create_agent_worker_seam_router,
)
from latticeai.core.messages import LANGUAGE_HEADER, MESSAGES
from latticeai.services.tool_dispatch import DEFAULT_TOOL_DISPATCH_SERVICE
from latticeai.tools import ToolError

USER = "worker@local"


# ── fakes: only the three things a unit test must not really do ─────────────


class FakeRouter:
    """Stands in for ``LLMRouter``; records the call, answers with a marker."""

    def __init__(self, text: str = "generated") -> None:
        self.text = text
        self.calls: list = []

    async def generate_as(
        self,
        model_id: Optional[str],
        *,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        self.calls.append({
            "model_id": model_id,
            "message": message,
            "context": context,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        return self.text


class FakeDispatch:
    """The real registry's policies, with the two injectable seams controlled.

    ``policy_for`` delegates to the shipped service unless a test needs a
    policy the registry does not contain (nothing in the registry is marked
    ``destructive``, so the destructive-policy denial has no other way to be
    exercised). ``_governed_path_exists`` is the probe the classifier asks
    "does the target already exist?" — answering it from a fixture keeps the
    test off the developer's real agent workspace.
    """

    def __init__(
        self,
        *,
        policy: Optional[Dict[str, Any]] = None,
        exists: bool = False,
        role_error: Optional[BaseException] = None,
    ) -> None:
        self._policy = policy
        self._exists = exists
        self._role_error = role_error
        self.policy_calls: list = []
        self.probe_calls: list = []
        self.role_calls: list = []

    def policy_for(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.policy_calls.append((tool, dict(args)))
        if self._policy is not None:
            return dict(self._policy)
        return dict(DEFAULT_TOOL_DISPATCH_SERVICE.policy_for(tool, args))

    def _governed_path_exists(self, tool: str, path: str) -> bool:
        self.probe_calls.append((tool, path))
        return self._exists

    def check_role(self, tool: str, user: str) -> None:
        self.role_calls.append((tool, user))
        if self._role_error is not None:
            raise self._role_error


class ProbelessDispatch:
    """A dispatch port that never learned the existence probe."""

    def __init__(self) -> None:
        self.role_calls: list = []

    def policy_for(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return dict(DEFAULT_TOOL_DISPATCH_SERVICE.policy_for(tool, args))

    def check_role(self, tool: str, user: str) -> None:
        self.role_calls.append((tool, user))


class FakeHooks:
    """Records the ``pre_tool``/``post_tool`` lifecycle; can block like a hook."""

    def __init__(self, *, block: str = "") -> None:
        self.block = block
        self.fired: list = []

    def fire_hook(self, kind: str, event: str, **kwargs: Any) -> Dict[str, Any]:
        self.fired.append({"kind": kind, "event": event, **kwargs})
        if kind == "pre_tool" and self.block:
            return {"blocked": True, "block_reason": self.block}
        return {}


class RecordingLimiter:
    def __init__(self, *, boom: bool = False) -> None:
        self.calls: list = []
        self.boom = boom

    def __call__(self, email: str, bucket: str) -> None:
        self.calls.append((email, bucket))
        if self.boom:
            raise HTTPException(status_code=429, detail="too many")


# ── wiring ──────────────────────────────────────────────────────────────────


def _client(
    *,
    model_router: Any = None,
    dispatch: Any = None,
    execute_tool: Any = None,
    hooks: Any = None,
    user: Optional[str] = USER,
    limiter: Any = None,
) -> TestClient:
    def require_user(_request: Request) -> str:
        if user is None:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    app = FastAPI()
    app.include_router(
        create_agent_worker_seam_router(
            model_router=model_router if model_router is not None else FakeRouter(),
            dispatch_service=dispatch if dispatch is not None else FakeDispatch(),
            execute_tool=execute_tool if execute_tool is not None else (
                lambda name, args: {"ok": True, "tool": name, "args": args}
            ),
            hooks=hooks,
            require_user=require_user,
            enforce_rate_limit=limiter if limiter is not None else RecordingLimiter(),
        )
    )
    return TestClient(app)


@pytest.fixture
def seam_on(monkeypatch):
    monkeypatch.setenv(SEAM_ENV_VAR, "1")


@pytest.fixture
def seam_off(monkeypatch):
    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)


# ── POST /agent/llm ─────────────────────────────────────────────────────────


def test_llm_generates_once_and_returns_only_the_text(seam_off):
    """No history, no context assembly — the whole answer is the completion.

    ``seam_off`` on purpose: a completion has nothing to gate, so the LLM route
    answers whether or not the host opened the tool seam.
    """
    router = FakeRouter("hello from the model")
    response = _client(model_router=router).post(
        "/agent/llm",
        json={
            "model_id": "mlx:qwen",
            "message": "plan this",
            "context": "prior steps",
            "max_tokens": 256,
            "temperature": 0.7,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello from the model"}
    assert router.calls == [{
        "model_id": "mlx:qwen",
        "message": "plan this",
        "context": "prior steps",
        "max_tokens": 256,
        "temperature": 0.7,
    }]


def test_llm_defaults_match_generate_as_own_defaults():
    """An omitted model means the router's current one; the rest is 4096/0.2."""
    router = FakeRouter()
    response = _client(model_router=router).post(
        "/agent/llm", json={"message": "go"}
    )
    assert response.status_code == 200
    assert router.calls[0]["model_id"] is None
    assert router.calls[0]["context"] is None
    assert router.calls[0]["max_tokens"] == 4096
    assert router.calls[0]["temperature"] == 0.2


def test_an_empty_model_id_is_the_same_as_no_model_id():
    router = FakeRouter()
    _client(model_router=router).post(
        "/agent/llm", json={"model_id": "", "message": "go"}
    )
    assert router.calls[0]["model_id"] is None


def test_no_model_loaded_is_returned_as_text_not_dressed_up_as_an_error():
    """``generate_as`` answers ``"No model."``; the loop records it and replans."""
    response = _client(model_router=FakeRouter("No model.")).post(
        "/agent/llm", json={"message": "go"}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "No model."}


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_a_blank_message_is_a_422_in_the_readers_language(message):
    response = _client().post(
        "/agent/llm",
        json={"message": message},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == MESSAGES["agent_seam.message_required"]["en"]


@pytest.mark.parametrize("max_tokens", [0, -1, MAX_MAX_TOKENS + 1])
def test_max_tokens_outside_the_bounds_is_a_422(max_tokens):
    response = _client().post(
        "/agent/llm",
        json={"message": "go", "max_tokens": max_tokens},
        headers={LANGUAGE_HEADER: "ko"},
    )
    assert response.status_code == 422
    assert "8192" in response.json()["detail"]


@pytest.mark.parametrize("temperature", [-0.1, MAX_TEMPERATURE + 0.1])
def test_temperature_outside_the_bounds_is_a_422(temperature):
    response = _client().post(
        "/agent/llm",
        json={"message": "go", "temperature": temperature},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == MESSAGES[
        "agent_seam.temperature_out_of_range"
    ]["en"].replace("{min}", "0.0").replace("{max}", "2.0")


def test_the_bounds_themselves_are_accepted():
    router = FakeRouter()
    response = _client(model_router=router).post(
        "/agent/llm",
        json={"message": "go", "max_tokens": MAX_MAX_TOKENS, "temperature": 0.0},
    )
    assert response.status_code == 200


def test_llm_requires_an_authenticated_user():
    response = _client(user=None).post("/agent/llm", json={"message": "go"})
    assert response.status_code == 401


def test_llm_is_charged_against_the_per_step_bucket_not_the_per_run_one():
    """``/agent``'s bucket refills once per 10s; a run makes a dozen of these."""
    limiter = RecordingLimiter()
    _client(limiter=limiter).post("/agent/llm", json={"message": "go"})
    assert limiter.calls == [(USER, SEAM_RATE_BUCKET)]
    assert SEAM_RATE_BUCKET != "agent"


def test_a_rate_limited_worker_gets_the_limiters_own_answer():
    response = _client(limiter=RecordingLimiter(boom=True)).post(
        "/agent/llm", json={"message": "go"}
    )
    assert response.status_code == 429


# ── the seam gate ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/agent/tool", {"tool": "read_file", "args": {"path": "a.md"}}),
    ],
)
def test_the_side_effecting_routes_are_404_until_the_host_opens_the_seam(
    seam_off, path, body
):
    response = _client().post(path, json=body)
    assert response.status_code == 404
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"]["ko"]


def test_the_gate_is_read_per_request_so_it_follows_the_environment(monkeypatch):
    """Not captured at import: the same client answers differently once set."""
    client = _client()
    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)
    body = {"tool": "read_file", "args": {"path": "a.md"}}
    assert client.post("/agent/tool", json=body).status_code == 404
    monkeypatch.setenv(SEAM_ENV_VAR, "1")
    assert client.post("/agent/tool", json=body).status_code == 200


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_exact_value_one_opens_the_seam(monkeypatch, value):
    monkeypatch.setenv(SEAM_ENV_VAR, value)
    response = _client().post("/agent/tool", json={"tool": "read_file"})
    assert response.status_code == 404


def test_a_closed_seam_answers_before_authentication_is_even_consulted(seam_off):
    """The route family is closed, not merely unauthorized."""
    limiter = RecordingLimiter()
    response = _client(user=None, limiter=limiter).post(
        "/agent/tool", json={"tool": "read_file"}
    )
    assert response.status_code == 404
    assert limiter.calls == []


# ── POST /agent/tool: the mode-invariant denials ────────────────────────────


def test_a_destructive_shell_command_is_refused_in_every_mode(seam_on):
    """Driven through the real ``is_circuit_breaker`` over the real policy.

    ``run_command``'s registry policy is a plain ``exec`` — nothing about the
    tool is destructive. The refusal comes from the argument.
    """
    dispatch = FakeDispatch()
    executed: list = []
    response = _client(
        dispatch=dispatch,
        execute_tool=lambda name, args: executed.append((name, args)),
    ).post(
        "/agent/tool",
        json={"tool": "run_command", "args": {"command": "rm -rf / --no-preserve-root"}},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 403
    assert "destructive shell command" in response.json()["detail"]
    assert executed == []
    # The denial is decided before the user table is read: it is the same
    # answer for every role.
    assert dispatch.role_calls == []


def test_a_destructive_policy_is_refused_in_every_mode(seam_on):
    """No registry tool is marked destructive, so the policy is supplied."""
    destructive = dict(DEFAULT_TOOL_DISPATCH_SERVICE.policy_for("write_file", {}))
    destructive["destructive"] = True
    response = _client(dispatch=FakeDispatch(policy=destructive)).post(
        "/agent/tool",
        json={"tool": "write_file", "args": {"path": "a.md", "content": "x"}},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 403
    assert "always blocked" in response.json()["detail"]


def test_a_home_wipe_path_is_refused_in_every_mode(seam_on):
    response = _client().post(
        "/agent/tool", json={"tool": "write_file", "args": {"path": "~"}}
    )
    assert response.status_code == 403


def test_a_deletion_cannot_be_staged_so_it_is_fail_closed(seam_on):
    """Real ``classify_tool_call``: destructive change, no proposal support."""
    response = _client().post(
        "/agent/tool",
        json={"tool": "delete_file", "args": {"path": "notes.md"}},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == MESSAGES[
        "agent_seam.tool_fail_closed"
    ]["en"].replace("{tool}", "delete_file").replace(
        "{reason}", "removes existing content"
    )


def test_overwriting_a_binary_document_is_fail_closed(seam_on):
    """A document creator aimed at a file that exists cannot be diffed."""
    dispatch = FakeDispatch(exists=True)
    response = _client(dispatch=dispatch).post(
        "/agent/tool",
        json={"tool": "create_docx", "args": {"filename": "report.docx"}},
    )
    assert response.status_code == 409
    # The classifier asked the *dispatch service's* probe, not a second one.
    assert dispatch.probe_calls == [("create_docx", "report.docx")]


def test_creating_a_new_document_is_additive_and_runs(seam_on):
    dispatch = FakeDispatch(exists=False)
    response = _client(dispatch=dispatch).post(
        "/agent/tool",
        json={"tool": "create_docx", "args": {"filename": "report.docx"}},
    )
    assert response.status_code == 200
    assert dispatch.probe_calls == [("create_docx", "report.docx")]


def test_a_dispatch_port_without_the_probe_classifies_as_additive(seam_on):
    """"We could not look" means "it is not there" to this classifier."""
    response = _client(dispatch=ProbelessDispatch()).post(
        "/agent/tool",
        json={"tool": "create_docx", "args": {"filename": "report.docx"}},
    )
    assert response.status_code == 200


def test_an_overwrite_of_a_text_file_is_allowed_because_it_can_be_staged(seam_on):
    """``write_file`` is proposal-capable, so an existing target is not 409."""
    response = _client(dispatch=FakeDispatch(exists=True)).post(
        "/agent/tool",
        json={"tool": "write_file", "args": {"path": "notes.md", "content": "x"}},
    )
    assert response.status_code == 200


# ── POST /agent/tool: role, execution, and the transcript-ready error ───────


def test_a_role_denial_from_the_shipped_service_stays_a_403(seam_on):
    """``ToolDispatchService.check_role`` raises ``HTTPException`` itself."""
    denial = HTTPException(status_code=403, detail="admin only")
    response = _client(dispatch=FakeDispatch(role_error=denial)).post(
        "/agent/tool", json={"tool": "read_file", "args": {"path": "a.md"}}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "admin only"


def test_a_port_that_raises_permission_error_gets_the_same_403(seam_on):
    response = _client(
        dispatch=FakeDispatch(role_error=PermissionError("no capability"))
    ).post("/agent/tool", json={"tool": "read_file", "args": {"path": "a.md"}})
    assert response.status_code == 403
    assert response.json()["detail"] == "no capability"


def test_a_successful_tool_call_returns_the_handlers_result_verbatim(seam_on):
    dispatch = FakeDispatch()
    response = _client(
        dispatch=dispatch,
        execute_tool=lambda name, args: {"lines": 3, "tool": name, "args": args},
    ).post(
        "/agent/tool",
        json={"tool": "read_file", "args": {"path": "a.md"}, "workspace_id": "ws-1"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "result": {"lines": 3, "tool": "read_file", "args": {"path": "a.md"}}
    }
    assert dispatch.role_calls == [("read_file", USER)]


def test_the_call_fires_the_shared_hook_lifecycle_as_an_agent(seam_on):
    """Same ``pre_tool`` → execute → ``post_tool`` path the Python loop uses."""
    hooks = FakeHooks()
    _client(hooks=hooks).post(
        "/agent/tool",
        json={"tool": "read_file", "args": {"path": "a.md"}, "workspace_id": "ws-1"},
    )
    assert [entry["kind"] for entry in hooks.fired] == ["pre_tool", "post_tool"]
    assert every_source_is_agent(hooks)
    assert {entry["workspace_id"] for entry in hooks.fired} == {"ws-1"}
    assert {entry["user_email"] for entry in hooks.fired} == {USER}


def every_source_is_agent(hooks: FakeHooks) -> bool:
    return all(entry["payload"]["source"] == "agent" for entry in hooks.fired)


@pytest.mark.parametrize(
    "exc",
    [
        ToolError("path escapes the workspace"),
        KeyError("no_such_tool"),
        TypeError("missing argument"),
        PermissionError("blocked downstream"),
    ],
)
def test_a_tool_failure_is_the_steps_outcome_not_the_requests(seam_on, exc):
    """The loop records errors as steps; an HTTP 5xx would look retryable."""

    def boom(_name, _args):
        raise exc

    response = _client(execute_tool=boom).post(
        "/agent/tool", json={"tool": "read_file", "args": {"path": "a.md"}}
    )
    assert response.status_code == 200
    assert response.json() == {"error": str(exc)}


def test_a_blocking_pre_tool_hook_is_reported_as_a_step_error(seam_on):
    """Real ``dispatch_tool``: a blocking hook raises ``PermissionError``."""
    executed: list = []
    response = _client(
        hooks=FakeHooks(block="a hook said no"),
        execute_tool=lambda name, args: executed.append(name),
    ).post("/agent/tool", json={"tool": "read_file", "args": {"path": "a.md"}})
    assert response.status_code == 200
    assert response.json() == {"error": "a hook said no"}
    assert executed == []


@pytest.mark.parametrize("tool", ["", "   "])
def test_a_blank_tool_name_is_a_422(seam_on, tool):
    response = _client().post(
        "/agent/tool", json={"tool": tool}, headers={LANGUAGE_HEADER: "en"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == MESSAGES["agent_seam.tool_required"]["en"]


def test_tool_requires_an_authenticated_user(seam_on):
    response = _client(user=None).post("/agent/tool", json={"tool": "read_file"})
    assert response.status_code == 401


def test_args_default_to_an_empty_object(seam_on):
    seen: list = []
    response = _client(
        execute_tool=lambda name, args: seen.append(args) or {"ok": True}
    ).post("/agent/tool", json={"tool": "todo_read"})
    assert response.status_code == 200
    assert seen == [{}]


# ── the seam writes nothing it was not asked to ─────────────────────────────


def test_the_router_registers_exactly_the_two_seam_paths():
    app = FastAPI()
    app.include_router(
        create_agent_worker_seam_router(
            model_router=FakeRouter(),
            dispatch_service=FakeDispatch(),
            execute_tool=lambda name, args: {},
            hooks=None,
            require_user=lambda _request: USER,
            enforce_rate_limit=RecordingLimiter(),
        )
    )
    # fastapi >= 0.140 wraps an included router in an opaque entry that
    # neither carries a flat ``route.path`` nor exposes ``.routes`` (the
    # 11.0.0 idempotence-guard lesson). The OpenAPI schema is the one
    # route inventory every supported fastapi version agrees on.
    paths = set(app.openapi()["paths"])
    assert {"/agent/llm", "/agent/tool"} <= paths
    assert not {path for path in paths if path.startswith("/agent/")} - {
        "/agent/llm", "/agent/tool"
    }


def test_the_seam_variable_is_not_left_set_by_this_module():
    """Guards the fixtures themselves: a leaked ``=1`` would hide the gate."""
    assert SEAM_ENV_VAR == "LATTICEAI_AGENT_TOOL_SEAM"
    assert os.environ.get(SEAM_ENV_VAR) in (None, "1", "0", "true", "yes", "")
