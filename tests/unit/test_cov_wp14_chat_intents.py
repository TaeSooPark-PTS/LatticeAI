"""Chat fast-path intents — network/clear handlers, file writes, agent routing.

``test_project_manifest_loop`` covers the happy multi-file bundle. This file
drives the rest of ``ChatIntentController``: the deterministic collision
ceiling, the funnel counter, the public-deployment "no model" answer, the
network and clear commands, single-file writes (renamed / repaired / streamed
/ tool-denied), Brain ingestion of generated files, and ``route_file_to_agent``
in both its JSON and live-SSE shapes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from latticeai.api.chat_intents import ChatIntentController, next_available_path
from latticeai.tools import ToolError

VALID_HTML = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Todo</title>'
    '<link rel="stylesheet" href="style.css"></head>'
    '<body><h1>Todo</h1><script src="app.js"></script></body></html>'
)
HTML_WITH_MISSING_ICON = VALID_HTML.replace(
    "</head>", '<link rel="icon" href="favicon.ico"></head>'
)
VALID_CSS = "body { font-family: sans-serif; }\n"
VALID_JS = "console.log('todo');\n"
REFUSAL = "죄송하지만 요청하신 작업은 도와드릴 수 없습니다."


# ── fakes ───────────────────────────────────────────────────────────────


class _ScriptedRouter:
    """Answers per-file generation prompts by matching the target filename."""

    current_model_id = "local-test"

    def __init__(self, replies: Optional[Dict[str, str]] = None, default: str = "") -> None:
        self.replies = replies or {}
        self.default = default
        self.calls: List[Dict[str, Any]] = []

    async def generate_as(self, model_id, *, message, context, max_tokens, temperature):
        head = context.splitlines()[0]
        self.calls.append({
            "model_id": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        for name, reply in self.replies.items():
            if name in head:
                return reply
        return self.default


class _ChatService:
    def __init__(self) -> None:
        self.exchanges: List[Dict[str, Any]] = []

    async def persist_exchange(self, **kwargs):
        self.exchanges.append(kwargs)


class _Funnel:
    def __init__(self, *, boom=()) -> None:
        self.counts: Dict[str, int] = {}
        self.boom = set(boom)

    def increment(self, name: str) -> None:
        if name in self.boom:
            raise RuntimeError("metrics backend down")
        self.counts[name] = self.counts.get(name, 0) + 1


class _IngestResult:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


class _Ingestion:
    def __init__(self, *, boom=False) -> None:
        self.boom = boom
        self.items: List[Any] = []

    def ingest(self, item, user_email=None):
        self.items.append((item, user_email))
        if self.boom:
            raise RuntimeError("index offline")
        return _IngestResult({
            "status": "ok",
            "node_id": "node-" + Path(item.path).name,
            "chunk_count": 3,
            "duplicate": False,
        })


class _AgentController:
    def __init__(self, result: Dict[str, Any], *, steps=()) -> None:
        self.result = result
        self.steps = list(steps)
        self.calls: List[Any] = []

    async def agent(self, req, request, on_step=None):
        self.calls.append(req)
        for step in self.steps:
            if on_step is not None:
                on_step(step)
        return dict(self.result)


class _Graph:
    def __init__(self, *, boom=False) -> None:
        self.boom = boom
        self.events: List[Dict[str, Any]] = []

    def ingest_event(self, kind, text, **kwargs):
        if self.boom:
            raise RuntimeError("graph offline")
        self.events.append({"kind": kind, "text": text, **kwargs})


def _writer(root: Path, *, boom: bool = False):
    written: Dict[str, str] = {}

    def execute_tool(name, args):
        assert name == "write_file"
        if boom:
            raise ToolError("write refused: outside the workspace")
        path = root / args["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        written[args["path"]] = args["content"]
        return {"path": args["path"], "bytes": len(args["content"].encode("utf-8"))}

    return execute_tool, written


def _controller(tmp_path, **overrides) -> ChatIntentController:
    execute_tool, _ = _writer(tmp_path)
    kwargs: Dict[str, Any] = {
        "model_router": _ScriptedRouter(),
        "config": SimpleNamespace(is_public=False, require_auth=False),
        "public_model": "qwen2.5-7b",
        "chat_service": _ChatService(),
        "notify": lambda *a, **k: None,
        "clear_history": lambda *a, **k: {"removed": 0, "kept": 0},
        "clear_conversation": lambda *a, **k: {"removed": 0, "kept": 0},
        "history_scope_for_user": lambda email: {"user_email": email},
        "append_audit_event": lambda *a, **k: None,
        "enable_graph": False,
        "knowledge_graph": None,
        "enforce_tool_policy": lambda *a, **k: None,
        "network_status": lambda: {},
        "tool_error": ToolError,
        "execute_tool": execute_tool,
        "agent_controller": None,
        "agent_root": tmp_path,
        "ingestion_pipeline": None,
    }
    kwargs.update(overrides)
    return ChatIntentController(**kwargs)


def _req(message="", **overrides):
    payload: Dict[str, Any] = {
        "message": message,
        "stream": False,
        "max_tokens": 0,
        "temperature": 0.2,
        "source": "web",
        "conversation_id": None,
        "user_nickname": None,
        "image_data": None,
        "client_url": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _body(response) -> Dict[str, Any]:
    return json.loads(response.body)


def _run_and_drain(make_coro):
    """Await a handler and fully drain the StreamingResponse it returned."""

    async def run():
        response = await make_coro()
        chunks = [chunk async for chunk in response.body_iterator]
        return response, chunks

    return asyncio.run(run())


def _frames(chunks: List[Any]):
    text = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks
    )
    out = []
    event = "message"
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            out.append((event, line[6:].strip()))
            event = "message"
    return out


# ── deterministic name collision ceiling ────────────────────────────────

def test_exhausted_name_variants_report_a_conflict(tmp_path):
    (tmp_path / "page.html").touch()
    for index in range(2, 100):
        (tmp_path / f"page_{index}.html").touch()
    with pytest.raises(HTTPException) as excinfo:
        next_available_path(tmp_path, "page.html")
    assert excinfo.value.status_code == 409
    assert "page.html" in excinfo.value.detail


# ── funnel counter ──────────────────────────────────────────────────────

def test_funnel_counter_is_advisory_and_never_raises(tmp_path):
    counting = _controller(tmp_path, funnel_metrics=_Funnel())
    counting._funnel_increment("real_file_delivered")
    counting._funnel_increment("real_file_delivered")
    assert counting.funnel_metrics.counts == {"real_file_delivered": 2}

    broken = _controller(tmp_path, funnel_metrics=_Funnel(boom={"boom"}))
    broken._funnel_increment("boom")  # must not raise
    assert broken.funnel_metrics.counts == {}

    _controller(tmp_path)._funnel_increment("ignored")


# ── no-model answer ─────────────────────────────────────────────────────

def test_public_deployment_names_the_missing_public_model(tmp_path):
    controller = _controller(
        tmp_path,
        config=SimpleNamespace(is_public=True, require_auth=True),
        public_model="qwen2.5-7b",
    )
    response = asyncio.run(
        controller.direct_file_action(_req("page.html 만들어줘"), model_id=None)
    )
    assert response.status_code == 400
    payload = _body(response)
    assert payload["error"] == "no_model_loaded"
    assert "qwen2.5-7b" in payload["detail"]
    assert payload["action"] == "load_model"


def test_local_deployment_reports_the_plain_no_model_answer(tmp_path):
    controller = _controller(tmp_path)
    response = asyncio.run(
        controller.direct_file_action(_req("page.html 만들어줘"), model_id=None)
    )
    assert response.status_code == 400
    assert "qwen2.5-7b" not in _body(response)["detail"]


# ── network intent ──────────────────────────────────────────────────────

def test_network_intent_persists_and_returns_the_formatted_status(tmp_path):
    service = _ChatService()
    policy_calls: List[Dict[str, Any]] = []
    controller = _controller(
        tmp_path,
        chat_service=service,
        network_status=lambda: {"local_ip": "10.0.0.5", "public_ip": "203.0.113.9",
                               "hostname": "box"},
        enforce_tool_policy=lambda name, args, **kw: policy_calls.append(
            {"name": name, **kw}
        ),
    )
    response = asyncio.run(controller.network(
        _req("내 아이피 알려줘", image_data="data:image/png;base64,AAA"),
        current_user="owner@example.com",
        history_meta={"conversation_id": None},
        history_user={"user_email": "owner@example.com"},
    ))
    assert policy_calls[0]["name"] == "network_status"
    assert policy_calls[0]["current_user"] == "owner@example.com"
    assert policy_calls[0]["source"] == "chat_intent"
    # image attachments are recorded on the stored user message only
    assert service.exchanges[0]["stored_user_message"].endswith("[Image attached]")
    answer = _body(response)["response"]
    assert "내부 IP: 10.0.0.5" in answer
    assert "외부 IP: 203.0.113.9" in answer


def test_network_intent_answers_honestly_when_the_probe_fails(tmp_path):
    def boom():
        raise ToolError("no route to host")

    controller = _controller(tmp_path, network_status=boom)
    response = asyncio.run(controller.network(
        _req("네트워크 상태"),
        current_user="owner@example.com",
        history_meta={},
        history_user={},
    ))
    assert _body(response)["response"] == "네트워크 정보를 확인하지 못했습니다: no route to host"


# ── clear command ───────────────────────────────────────────────────────

def _clear_controller(tmp_path, **overrides):
    audits: List[Dict[str, Any]] = []
    notifies: List[tuple] = []
    cleared: List[tuple] = []

    def clear_history(keep_last, **scope):
        cleared.append(("all", keep_last, scope))
        return {"removed": 7, "kept": 1}

    def clear_conversation(conversation_id, **scope):
        cleared.append(("conversation", conversation_id, scope))
        return {"removed": 2, "kept": 5}

    kwargs = {
        "clear_history": clear_history,
        "clear_conversation": clear_conversation,
        "append_audit_event": lambda event, **kw: audits.append({"event": event, **kw}),
        "notify": lambda *args: notifies.append(args),
    }
    kwargs.update(overrides)
    return _controller(tmp_path, **kwargs), audits, notifies, cleared


def test_clear_all_wipes_every_conversation_and_audits_the_command(tmp_path):
    controller, audits, notifies, cleared = _clear_controller(tmp_path)
    response = asyncio.run(controller.clear(
        _req("/clear_all", conversation_id="conv-1"),
        effective_email="owner@example.com",
        workspace_id="ws-1",
    ))
    assert cleared == [("all", 0, {"user_email": "owner@example.com"})]
    answer = _body(response)["response"]
    assert answer.startswith("채팅창을 정리했습니다. 화면에서 제거 7개.")
    assert audits[0]["event"] == "clear_command"
    assert audits[0]["scope"] == "all"
    assert (audits[0]["removed"], audits[0]["kept"]) == (7, 1)
    assert [call[0] for call in notifies] == ["user", "assistant"]


def test_clear_inside_a_conversation_only_clears_that_room(tmp_path):
    controller, audits, _, cleared = _clear_controller(tmp_path)
    response = asyncio.run(controller.clear(
        _req("/clear", conversation_id="conv-1"),
        effective_email="owner@example.com",
        workspace_id=None,
    ))
    assert cleared == [("conversation", "conv-1", {"user_email": "owner@example.com"})]
    assert _body(response)["response"].startswith("현재 대화방 채팅창을 정리했습니다.")
    assert audits[0]["scope"] == "conversation"


def test_clear_without_a_conversation_falls_back_to_the_whole_history(tmp_path):
    controller, _, _, cleared = _clear_controller(tmp_path)
    response = asyncio.run(controller.clear(
        _req("/clear", conversation_id=None),
        effective_email=None,
        workspace_id=None,
    ))
    assert cleared == [("all", 0, {"user_email": None})]
    assert _body(response)["response"].startswith("채팅창을 정리했습니다.")


def test_clear_records_a_graph_event_when_the_graph_is_enabled(tmp_path):
    graph = _Graph()
    controller, _, _, _ = _clear_controller(
        tmp_path, enable_graph=True, knowledge_graph=graph
    )
    asyncio.run(controller.clear(
        _req("/clear_all", conversation_id="conv-1", user_nickname="Tae"),
        effective_email="owner@example.com",
        workspace_id="ws-1",
    ))
    assert graph.events[0]["kind"] == "ClearEvent"
    assert graph.events[0]["metadata"] == {"command": "/clear_all", "scope": "all"}
    assert graph.events[0]["workspace_id"] == "ws-1"


def test_clear_still_works_when_the_graph_audit_ingest_fails(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller, audits, _, cleared = _clear_controller(
        tmp_path, enable_graph=True, knowledge_graph=_Graph(boom=True)
    )
    response = asyncio.run(controller.clear(
        _req("/clear_all"), effective_email="owner@example.com", workspace_id=None
    ))
    assert cleared and audits  # the authoritative audit event still fired
    assert _body(response)["response"].startswith("채팅창을 정리했습니다.")
    assert "knowledge graph clear event ingest failed" in caplog.text


# ── single-file direct writes ───────────────────────────────────────────

def test_request_without_any_inferable_target_is_not_a_file_action(tmp_path):
    controller = _controller(tmp_path)
    assert asyncio.run(
        controller.direct_file_action(_req("무언가 만들어줘"), model_id="local-test")
    ) is None


def test_existing_target_is_written_under_a_new_name(tmp_path):
    (tmp_path / "report.md").write_text("old", encoding="utf-8")
    controller = _controller(tmp_path)
    response = asyncio.run(controller.direct_file_action(
        _req("report.md 파일 만들어줘 내용은 새 보고서"), model_id=None
    ))
    payload = _body(response)
    assert payload["created_files"][0]["path"] == "report_2.md"
    assert "새 이름으로 저장했습니다" in payload["response"]
    assert (tmp_path / "report_2.md").read_text(encoding="utf-8") == "새 보고서"
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "old"


def test_repaired_model_output_is_disclosed_in_the_answer(tmp_path):
    controller = _controller(tmp_path, model_router=_ScriptedRouter(default=REFUSAL))
    response = asyncio.run(
        controller.direct_file_action(_req("page.html 만들어줘"), model_id="local-test")
    )
    payload = _body(response)
    assert payload["generation"]["repaired"] is True
    assert "자동 보정" in payload["response"]
    assert payload["artifacts"][0]["repaired"] is True
    assert (tmp_path / "page.html").read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_a_refused_write_tool_becomes_a_bad_request(tmp_path):
    execute_tool, _ = _writer(tmp_path, boom=True)
    controller = _controller(tmp_path, execute_tool=execute_tool)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(controller.direct_file_action(
            _req("report.md 만들어줘 내용은 hi"), model_id=None
        ))
    assert excinfo.value.status_code == 400
    assert "write refused" in excinfo.value.detail


def test_generation_returning_no_content_is_a_bad_request(tmp_path, monkeypatch):
    async def no_content(generate, *, target_path, user_request, **kwargs):
        return None, {"attempts": [], "repaired": False}

    monkeypatch.setattr(
        "latticeai.api.chat_intents.generate_file_content", no_content
    )
    controller = _controller(tmp_path)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            controller.direct_file_action(_req("page.html 만들어줘"), model_id="local-test")
        )
    assert excinfo.value.status_code == 400


def test_streamed_file_action_ends_with_the_terminal_payload(tmp_path):
    controller = _controller(tmp_path, funnel_metrics=_Funnel())
    response, chunks = _run_and_drain(lambda: controller.direct_file_action(
        _req("report.md 만들어줘 내용은 hi", stream=True), model_id="local-test"
    ))
    assert response.media_type == "text/event-stream"
    assert response.headers["X-Routed-To"] == "agent"
    frames = _frames(chunks)
    assert frames[-1] == ("message", "[DONE]")
    terminal = json.loads(frames[-2][1])["agent"]
    assert terminal["action_route"] == "direct_write_file"
    assert terminal["created_files"][0]["path"] == "report.md"
    assert controller.funnel_metrics.counts == {"real_file_delivered": 1}


# ── Brain ingestion of generated files ──────────────────────────────────

def test_generated_file_is_indexed_into_the_brain(tmp_path):
    pipeline = _Ingestion()
    controller = _controller(tmp_path, ingestion_pipeline=pipeline)
    response = asyncio.run(controller.direct_file_action(
        _req("report.md 만들어줘 내용은 hi", conversation_id="conv-7"),
        model_id=None,
        effective_email="owner@example.com",
        workspace_id="ws-1",
    ))
    payload = _body(response)
    assert payload["brain_ingest"] == {
        "status": "ok",
        "node_id": "node-report.md",
        "chunk_count": 3,
        "duplicate": False,
    }
    item, user_email = pipeline.items[0]
    assert user_email == "owner@example.com"
    assert item.source_type == "file"
    assert item.title == "report.md"
    assert item.source_uri == "workspace://report.md"
    assert item.workspace_id == "ws-1"
    assert item.conversation_id == "conv-7"
    assert item.metadata == {"origin": "generated_file", "route": "direct_write_file"}


def test_ingestion_failure_is_reported_without_failing_the_write(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    controller = _controller(tmp_path, ingestion_pipeline=_Ingestion(boom=True))
    response = asyncio.run(controller.direct_file_action(
        _req("report.md 만들어줘 내용은 hi"), model_id=None
    ))
    payload = _body(response)
    assert payload["status"] == "ok"
    assert payload["brain_ingest"]["status"] == "failed"
    assert "index offline" in payload["brain_ingest"]["detail"]
    assert (tmp_path / "report.md").exists()
    assert "generated-file ingest failed" in caplog.text


def test_ingestion_is_skipped_when_the_toggle_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_INGEST_GENERATED", "0")
    pipeline = _Ingestion()
    controller = _controller(tmp_path, ingestion_pipeline=pipeline)
    response = asyncio.run(controller.direct_file_action(
        _req("report.md 만들어줘 내용은 hi"), model_id=None
    ))
    assert "brain_ingest" not in _body(response)
    assert pipeline.items == []


# ── multi-file project bundles ──────────────────────────────────────────

PROJECT_MESSAGE = "todo 앱 html+css+js로 만들어줘"


def test_bundle_warning_is_surfaced_when_a_reference_cannot_be_resolved(tmp_path):
    router = _ScriptedRouter({
        "index.html": HTML_WITH_MISSING_ICON,
        "style.css": VALID_CSS,
        "app.js": VALID_JS,
    })
    controller = _controller(tmp_path, model_router=router)
    response = asyncio.run(
        controller.direct_file_action(_req(PROJECT_MESSAGE), model_id="local-test")
    )
    payload = _body(response)
    validation = payload["project"]["bundle_validation"]
    assert validation["ok"] is False
    assert any("favicon.ico" in issue for issue in validation["issues"])
    assert "검증 경고" in payload["response"]


def test_repaired_bundle_member_is_disclosed_and_indexed(tmp_path):
    router = _ScriptedRouter({
        "index.html": VALID_HTML,
        "style.css": VALID_CSS,
        "app.js": REFUSAL,  # forces the deterministic repair path
    })
    pipeline = _Ingestion()
    controller = _controller(
        tmp_path, model_router=router, ingestion_pipeline=pipeline,
        funnel_metrics=_Funnel(),
    )
    response = asyncio.run(controller.direct_file_action(
        _req(PROJECT_MESSAGE), model_id="local-test",
        effective_email="owner@example.com", workspace_id="ws-1",
    ))
    payload = _body(response)
    assert payload["generation"]["repaired"] is True
    assert "자동 보정" in payload["response"]
    repaired = [a["filename"] for a in payload["artifacts"] if a["repaired"]]
    assert repaired == ["app.js"]
    assert sorted(item["path"] for item in payload["brain_ingest"]) == [
        "todo-app/app.js", "todo-app/index.html", "todo-app/style.css",
    ]
    assert all(item["status"] == "ok" for item in payload["brain_ingest"])
    assert controller.funnel_metrics.counts == {"real_file_delivered": 1}


def test_a_refused_write_aborts_the_bundle_with_a_bad_request(tmp_path):
    execute_tool, _ = _writer(tmp_path, boom=True)
    router = _ScriptedRouter({
        "index.html": VALID_HTML, "style.css": VALID_CSS, "app.js": VALID_JS,
    })
    controller = _controller(tmp_path, model_router=router, execute_tool=execute_tool)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            controller.direct_file_action(_req(PROJECT_MESSAGE), model_id="local-test")
        )
    assert excinfo.value.status_code == 400
    assert "write refused" in excinfo.value.detail


def test_streamed_bundle_ends_with_the_project_payload(tmp_path):
    router = _ScriptedRouter({
        "index.html": VALID_HTML, "style.css": VALID_CSS, "app.js": VALID_JS,
    })
    controller = _controller(tmp_path, model_router=router)
    response, chunks = _run_and_drain(lambda: controller.direct_file_action(
        _req(PROJECT_MESSAGE, stream=True), model_id="local-test"
    ))
    assert response.headers["X-Model"] == "local-test"
    frames = _frames(chunks)
    assert frames[-1] == ("message", "[DONE]")
    terminal = json.loads(frames[-2][1])["agent"]
    assert terminal["action_route"] == "direct_project_bundle"
    assert terminal["project"]["dir"] == "todo-app"
    assert terminal["project"]["zip_url"] == "/tools/download_zip?path=todo-app"


def test_project_bundle_needs_a_model(tmp_path):
    controller = _controller(tmp_path)
    response = asyncio.run(
        controller.direct_file_action(_req(PROJECT_MESSAGE), model_id=None)
    )
    assert response.status_code == 400
    assert _body(response)["error"] == "no_model_loaded"


# ── routing a file request to the agent loop ────────────────────────────

AGENT_OK = {
    "status": "ok",
    "response": "파일을 만들었습니다.",
    "created_files": [{"path": "notes.md", "filename": "notes.md"}],
    "artifacts": [{"kind": "file", "path": "notes.md"}],
}


def test_agent_route_returns_the_run_payload_and_counts_a_delivered_file(tmp_path):
    agent = _AgentController(AGENT_OK)
    notifies: List[tuple] = []
    controller = _controller(
        tmp_path, agent_controller=agent, funnel_metrics=_Funnel(),
        notify=lambda *args: notifies.append(args),
    )
    response = asyncio.run(controller.route_file_to_agent(
        _req("notes.md 만들어줘", conversation_id="conv-3", user_nickname="Tae"),
        SimpleNamespace(headers={}, query_params={}),
        effective_email="owner@example.com",
        workspace_id="ws-1",
        model_id="local-test",
    ))
    forwarded = agent.calls[0]
    assert forwarded.message == "notes.md 만들어줘"
    assert forwarded.workspace_id == "ws-1"
    assert forwarded.user_email == "owner@example.com"
    assert forwarded.max_steps == 25
    assert forwarded.temperature == 0.2

    payload = _body(response)
    assert payload["routed_to_agent"] is True
    assert controller.funnel_metrics.counts == {"real_file_delivered": 1}
    assert [call[0] for call in notifies] == ["user", "assistant"]
    assert notifies[1][1] == "파일을 만들었습니다."


def test_a_finished_run_with_no_artifacts_counts_as_a_code_only_answer(tmp_path):
    agent = _AgentController({"status": "ok", "response": "```py\nprint(1)\n```"})
    controller = _controller(
        tmp_path, agent_controller=agent, funnel_metrics=_Funnel()
    )
    response = asyncio.run(controller.route_file_to_agent(
        _req("notes.md 만들어줘"),
        SimpleNamespace(headers={}, query_params={}),
        effective_email=None, workspace_id=None, model_id=None,
    ))
    assert _body(response)["routed_to_agent"] is True
    assert controller.funnel_metrics.counts == {"code_only_responses": 1}


def test_a_paused_run_counts_neither_way(tmp_path):
    agent = _AgentController({"status": "awaiting_approval", "response": "승인 필요"})
    controller = _controller(
        tmp_path, agent_controller=agent, funnel_metrics=_Funnel()
    )
    asyncio.run(controller.route_file_to_agent(
        _req("notes.md 만들어줘"),
        SimpleNamespace(headers={}, query_params={}),
        effective_email=None, workspace_id=None, model_id=None,
    ))
    assert controller.funnel_metrics.counts == {}


def test_streamed_agent_route_emits_live_steps_then_the_terminal_payload(tmp_path):
    agent = _AgentController(
        AGENT_OK,
        steps=[{"phase": "plan", "action": "plan"},
               {"phase": "execute", "action": "write_file"}],
    )
    controller = _controller(
        tmp_path, agent_controller=agent, funnel_metrics=_Funnel()
    )
    response, chunks = _run_and_drain(lambda: controller.route_file_to_agent(
        _req("notes.md 만들어줘", stream=True),
        SimpleNamespace(headers={}, query_params={}),
        effective_email="owner@example.com",
        workspace_id="ws-1",
        model_id="local-test",
    ))
    assert response.media_type == "text/event-stream"
    assert response.headers["X-Model"] == "local-test"
    frames = _frames(chunks)
    steps = [json.loads(body) for name, body in frames if name == "agent_step"]
    assert [step["action"] for step in steps] == ["plan", "write_file"]
    assert frames[-1] == ("message", "[DONE]")
    terminal = json.loads(frames[-2][1])["agent"]
    assert terminal["routed_to_agent"] is True
    assert controller.funnel_metrics.counts == {"real_file_delivered": 1}
