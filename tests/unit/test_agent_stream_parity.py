"""`/agent` live SSE — surface parity for non-web clients (v9.9.7).

The web app already watches the loop work: file-intent chat routes through
``agent_live_stream`` and emits named ``agent_step`` frames. `/agent` clients
(VS Code) had no such endpoint, so the editor could only report *after* a run.

`AgentRequest.stream=True` now serves the same frames from `/agent`:

* every observed step arrives as a named ``agent_step`` frame while EXECUTING;
* the terminal payload is byte-identical to the JSON response, so a client
  that ignores named events sees the historical stream shape;
* nothing about the loop itself changes — the observer is pure telemetry.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.chat_agent_http import AgentHTTPController
from latticeai.api.chat_contracts import AgentRequest
from latticeai.core.agent import AgentDeps, SingleAgentRuntime

PLAN_JSON = json.dumps({
    "action": "plan", "state": "PLAN", "goal": "write a note",
    "steps": [{"id": 1, "description": "write notes.md", "action": "write_file"}],
    "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
})
VERDICT_JSON = json.dumps({
    "action": "verdict", "verdict": "PASS", "next_state": "DONE",
    "reason": "file written", "corrections": [],
})


def _runtime(written):
    replies = [
        json.dumps({"thoughts": "write", "action": "write_file",
                    "args": {"path": "notes.md", "content": "# Notes\n\nbody"}}),
        json.dumps({"thoughts": "done", "action": "final", "message": "완료"}),
    ]
    state = {"i": 0}

    async def generate_as(model_id, *, message, context, max_tokens, temperature):
        if "execution plan" in message:
            # A new plan starts a new run: reset the executor reply cursor so
            # two runs against the same fake behave identically.
            state["i"] = 0
            return PLAN_JSON
        if message == "Execute the next step.":
            reply = replies[min(state["i"], len(replies) - 1)]
            state["i"] += 1
            return reply
        return VERDICT_JSON

    async def generate(*, message, context, max_tokens, temperature):
        return json.dumps({"action": "memory", "learnings": [], "save_to_knowledge": False})

    def execute_tool(name, args):
        written.append(dict(args))
        return {"success": True, "path": args.get("path")}

    deps = AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: {
            "risk": "write", "destructive": False, "shell": False, "network": False,
            "auto_approve": True, "sandbox": "workspace", "rollback": "none",
        },
        risk_level=lambda policy: "low",
        check_role=lambda name, user: None,
        tool_governance={"write_file": {"auto_approve": True}},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=None,
    )
    return SingleAgentRuntime(deps)


@pytest.fixture()
def client(tmp_path):
    controller = AgentHTTPController(
        runtime=_runtime([]),
        model_router=SimpleNamespace(current_model_id="local-test"),
        require_user=lambda request: "owner@example.com",
        require_admin=None,
        enforce_rate_limit=lambda *a, **k: None,
        authenticated_identity=lambda current, claimed, language="ko": current,
        write_workspace=lambda requested, user: requested,
        save_to_history=lambda *a, **k: None,
        workspace_store=SimpleNamespace(record_agent_run=lambda **kw: {"id": "r"}),
        workspace_graph=lambda: None,
        hooks=None,
        execute_tool=lambda name, args: {},
        base_dir=tmp_path,
        agent_root=tmp_path,
        ensure_agent_root=lambda: None,
    )
    router = APIRouter()
    controller.register_routes(router)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _frames(text: str):
    """Parse an SSE body into ``[(event_name, payload)]``."""
    out = []
    event = "message"
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            body = line[6:].strip()
            out.append((event, body))
            event = "message"
    return out


def test_streaming_agent_emits_named_step_frames(client):
    response = client.post("/agent", json={"message": "write notes.md", "stream": True})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    steps = [json.loads(body) for name, body in frames if name == "agent_step"]
    assert steps, "the loop must stream its steps, not only report at the end"
    phases = {step.get("phase") for step in steps}
    assert "plan" in phases and "execute" in phases
    assert any(step.get("action") == "write_file" for step in steps)


def test_stream_terminal_payload_matches_the_json_response(client):
    streamed = client.post("/agent", json={"message": "write notes.md", "stream": True})
    plain = client.post("/agent", json={"message": "write notes.md"})
    assert plain.status_code == 200
    frames = _frames(streamed.text)
    finals = [
        json.loads(body).get("agent")
        for name, body in frames
        if name == "message" and body != "[DONE]"
    ]
    finals = [item for item in finals if isinstance(item, dict)]
    assert finals, "the stream must end with the same terminal payload"
    terminal = finals[-1]
    body = plain.json()
    # The contract clients rely on: identical shape, identical verdict.
    assert terminal["status"] == body["status"]
    assert terminal["final_state"] == body["final_state"]
    assert terminal["explanation"]["code"] == body["explanation"]["code"]
    assert set(terminal) >= {"steps", "created_files", "artifacts", "loop", "explanation"}


def test_stream_ends_with_the_historical_done_sentinel(client):
    response = client.post("/agent", json={"message": "write notes.md", "stream": True})
    assert response.text.rstrip().endswith("data: [DONE]")


def test_non_streaming_requests_are_unchanged(client):
    response = client.post("/agent", json={"message": "write notes.md"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["final_state"] == "DONE"


def test_stream_flag_defaults_to_off():
    assert AgentRequest(message="x").stream is False


def test_streaming_still_produces_the_run_artifacts(tmp_path):
    written: list = []
    controller = AgentHTTPController(
        runtime=_runtime(written),
        model_router=SimpleNamespace(current_model_id="local-test"),
        require_user=lambda request: "owner@example.com",
        require_admin=None,
        enforce_rate_limit=lambda *a, **k: None,
        authenticated_identity=lambda current, claimed, language="ko": current,
        write_workspace=lambda requested, user: requested,
        save_to_history=lambda *a, **k: None,
        workspace_store=SimpleNamespace(record_agent_run=lambda **kw: {"id": "r"}),
        workspace_graph=lambda: None,
        hooks=None,
        execute_tool=lambda name, args: {},
        base_dir=tmp_path,
        agent_root=tmp_path,
        ensure_agent_root=lambda: None,
    )
    router = APIRouter()
    controller.register_routes(router)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        test_client.post("/agent", json={"message": "write notes.md", "stream": True})
    assert [item["path"] for item in written] == ["notes.md"]


def test_observer_failure_never_breaks_the_run(tmp_path):
    """The step observer is telemetry: a broken one must not fail the loop."""
    runtime = _runtime([])

    async def run():
        from latticeai.core.agent import AgentRunContext

        ctx = AgentRunContext()
        ctx.on_step = lambda event: (_ for _ in ()).throw(RuntimeError("observer boom"))
        runtime._emit_step(ctx, "execute", "tool", action="write_file")

    asyncio.run(run())
