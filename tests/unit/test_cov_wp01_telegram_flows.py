"""wp01 coverage: the Telegram conversation flows.

``ask_ai`` and everything downstream of it — the approval handshake, the review
centre, command dispatch, callback routing and the poll loop itself. The poll
loop is driven by a scripted ``get_updates`` and a patched ``asyncio.sleep``
that leaves the loop after a fixed number of iterations, so the test is
deterministic and never sleeps for real.
"""

from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path

import pytest

from latticeai.integrations import telegram_bot as bot

MODULE_PATH = Path(bot.__file__)

# ── doubles ───────────────────────────────────────────────────────────────


class _StopLoop(Exception):
    """Sentinel that leaves ``run_bot``'s infinite poll loop."""


class _Res:
    """Minimal stand-in for an ``httpx.Response``."""

    def __init__(self, status_code=200, payload=None, text="", headers=None,
                 json_error=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = {} if payload is None else payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Client:
    """Async HTTP double: records every call, replies from a scripted table."""

    def __init__(self, reply=None, error=None):
        self.calls = []
        self._reply = reply
        self._error = error

    async def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self._error is not None:
            raise self._error
        if callable(self._reply):
            return self._reply(method, url, kwargs)
        if self._reply is None:
            return _Res()
        return self._reply

    async def get(self, url, **kwargs):
        return await self._call("get", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._call("post", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._call("delete", url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _server(monkeypatch, client):
    """Route ``_server_client()`` at a double instead of the real HTTP client."""
    monkeypatch.setattr(bot, "_server_client", lambda **_kwargs: client)
    return client


def _recorder(log, name):
    async def record(*args, **kwargs):
        log.append((name, args, kwargs))

    return record


def _patch_handlers(monkeypatch, names):
    """Replace the named coroutines on the module with call recorders."""
    log = []
    for name in names:
        monkeypatch.setattr(bot, name, _recorder(log, name))
    return log


def _texts(log):
    return [args[2] for name, args, _kwargs in log if name == "send_message"]


def _messages(client):
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if call[1].endswith("/sendMessage")
    ]


# ── ask_ai ────────────────────────────────────────────────────────────────


def test_agent_mode_posts_the_plan_request_to_the_agent_endpoint(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(payload={"response": "완료"})))

    result = asyncio.run(bot.ask_ai(
        _Client(), "보고서 만들어줘", agent_mode=True,
        planning_model="p", executing_model="e", reviewing_model="r",
    ))

    method, url, kwargs = server.calls[0]
    assert method == "post"
    assert url == bot.AGENT_URL
    assert kwargs["json"] == {
        "message": "보고서 만들어줘", "source": "telegram", "human_in_loop": True,
        "planning_model": "p", "executing_model": "e", "reviewing_model": "r",
    }
    assert result == {"response": "완료"}


def test_an_image_question_always_goes_to_the_chat_endpoint(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(payload={"response": "고양이입니다"})))

    asyncio.run(bot.ask_ai(_Client(), "이건 뭐야", image_data="b64", agent_mode=True))

    _method, url, kwargs = server.calls[0]
    assert url == bot.CHAT_URL
    assert kwargs["json"]["image_data"] == "b64"
    assert kwargs["json"]["stream"] is False


def test_an_event_stream_reply_is_reassembled_and_bad_frames_are_skipped(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(
        headers={"content-type": "text/event-stream"},
        text='data: {"chunk": "안녕"}\ndata: not-json\nevent: ping\ndata: {"chunk": "하세요"}\n',
    )))

    assert asyncio.run(bot.ask_ai(_Client(), "안녕")) == {"response": "안녕하세요"}


def test_an_empty_event_stream_still_answers_something(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(
        headers={"content-type": "text/event-stream"}, text="event: done\n",
    )))

    assert asyncio.run(bot.ask_ai(_Client(), "안녕")) == {"response": "⚠️ 빈 응답"}


def test_a_missing_model_is_explained_as_a_next_step(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(
        status_code=400, payload={"detail": "No model loaded"},
    )))

    answer = asyncio.run(bot.ask_ai(_Client(), "안녕"))
    assert "/model" in answer["response"]


def test_any_other_server_error_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(
        status_code=500, json_error=ValueError("not json"),
    )))

    assert asyncio.run(bot.ask_ai(_Client(), "안녕")) == {
        "response": "❌ 서버 에러 (500)"
    }


def test_a_detailed_error_keeps_the_servers_explanation(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(
        status_code=422, payload={"detail": "message too long"},
    )))

    assert "message too long" in asyncio.run(bot.ask_ai(_Client(), "안녕"))["response"]


def test_an_unreachable_server_is_reported_as_a_connection_failure(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("connection refused")))

    assert "서버 연결 실패" in asyncio.run(bot.ask_ai(_Client(), "안녕"))["response"]


# ── review centre ─────────────────────────────────────────────────────────


def test_the_review_centre_offers_approve_and_reject_per_proposal(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={"items": [
        {"id": "p1", "title": "Update README.md", "payload": {"change_class": "modify_existing"}},
    ]})))
    client = _Client()
    asyncio.run(bot.show_review_center(client, 42))

    payload = client.calls[0][2]["json"]
    assert "Update README.md (modify_existing)" in payload["text"]
    row = payload["reply_markup"]["inline_keyboard"][0]
    assert [button["callback_data"] for button in row] == [
        "proposal:approve:p1", "proposal:reject:p1",
    ]


def test_the_review_centre_caps_the_list_and_says_how_many_it_hid(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={"items": [
        {"id": f"p{i}", "title": f"file{i}.py"} for i in range(11)
    ]})))
    client = _Client()
    asyncio.run(bot.show_review_center(client, 42))

    payload = client.calls[0][2]["json"]
    assert len(payload["reply_markup"]["inline_keyboard"]) == 8
    assert "… 외 3건" in payload["text"]


def test_an_empty_review_centre_says_so(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={"items": []})))
    client = _Client()
    asyncio.run(bot.show_review_center(client, 42))

    assert "검토할 변경 제안이 없습니다" in _messages(client)[0]


def test_a_rejected_proposal_listing_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=401)))
    client = _Client()
    asyncio.run(bot.show_review_center(client, 42))

    assert "검토함을 불러오지 못했습니다 (401)" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_a_review_failure(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.show_review_center(client, 42))

    assert "검토함 조회 실패" in _messages(client)[0]


# ── proposal decisions ────────────────────────────────────────────────────


def test_a_malformed_proposal_callback_is_ignored(monkeypatch):
    def unreachable(**_kwargs):
        raise AssertionError("a malformed callback must not reach the server")

    monkeypatch.setattr(bot, "_server_client", unreachable)
    asyncio.run(bot.handle_proposal_callback(_Client(), 42, "proposal-approve-p1"))
    asyncio.run(bot.handle_proposal_callback(_Client(), 42, "proposal:approve:"))


def test_approving_a_proposal_reports_the_path_the_server_applied(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(payload={"path": "README.md"})))
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:approve:p1"))

    assert server.calls[0][1].endswith("/p1/approve")
    assert "✅ 적용했습니다: README.md" in _messages(client)[0]


def test_approving_a_proposal_falls_back_to_the_id_when_the_reply_is_not_json(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(json_error=ValueError("not json"))))
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:approve:p1"))

    assert "✅ 적용했습니다: p1" in _messages(client)[0]


def test_rejecting_a_proposal_says_nothing_was_applied(monkeypatch):
    server = _server(monkeypatch, _Client())
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:reject:p1"))

    assert server.calls[0][1].endswith("/p1/reject")
    assert "🚫 제안을 거절했습니다." in _messages(client)[0]


def test_a_stale_proposal_is_refused_and_the_user_is_told_nothing_was_written(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=409)))
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:approve:p1"))

    text = _messages(client)[0]
    assert "파일이 바뀌어서 적용하지 않았습니다" in text
    assert "아무것도 쓰지 않았습니다" in text


def test_any_other_proposal_error_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=500)))
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:approve:p1"))

    assert "❌ 서버 에러 (500)" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_a_proposal_failure(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.handle_proposal_callback(client, 42, "proposal:approve:p1"))

    assert "❌ 처리 실패" in _messages(client)[0]


# ── plan approval ─────────────────────────────────────────────────────────


def test_a_plan_without_an_identifier_cannot_be_approved(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {})
    client = _Client()
    asyncio.run(bot.send_plan_for_approval(client, 42, {"plan": {"goal": "무언가"}}))

    assert "승인할 계획을 식별할 수 없습니다" in _messages(client)[0]
    assert bot._bot_pending_plans == {}


def test_a_modern_plan_is_shown_with_its_steps_and_stored_for_resume(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {})
    client = _Client()
    asyncio.run(bot.send_plan_for_approval(client, 42, {
        "status": "awaiting_approval",
        "run_id": "run-1",
        "plan": {"goal": "보고서 작성", "steps": [{"description": "자료 수집"}, "정리하기"]},
        "planning_model": "p", "executing_model": "e", "reviewing_model": "r",
        "approval": {"token": "tok", "expires_at": "2026-08-09T12:00:00"},
    }))

    payload = client.calls[0][2]["json"]
    assert "*목표:* 보고서 작성" in payload["text"]
    assert "1. 자료 수집" in payload["text"]
    assert "2. 정리하기" in payload["text"]
    assert "⏳ 승인 만료: `2026-08-09T12:00:00`" in payload["text"]
    assert [button["callback_data"] for button in
            payload["reply_markup"]["inline_keyboard"][0]] == [
        "plan:approve:run-1", "plan:cancel:run-1",
    ]
    assert bot._bot_pending_plans["run-1"] == {
        "chat_id": 42, "run_id": "run-1", "context_id": None,
        "approval_token": "tok", "legacy": False,
        "executing_model": "e", "reviewing_model": "r",
    }


def test_a_legacy_plan_is_keyed_by_its_context_id(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {})
    client = _Client()
    asyncio.run(bot.send_plan_for_approval(client, 42, {
        "status": "waiting_approval",
        "context_id": "ctx-9",
        "approval": {"plan_summary": "레거시 목표"},
        "non_auto_steps": [{"action": "write_file"}],
    }))

    assert "*목표:* 레거시 목표" in client.calls[0][2]["json"]["text"]
    assert bot._bot_pending_plans["ctx-9"]["legacy"] is True


def test_the_resume_body_prefers_the_token_and_falls_back_to_the_context():
    assert bot._resume_payload(
        {"run_id": "run-1", "approval_token": "tok", "executing_model": "e"},
        approved=True,
    ) == {
        "approved": True, "executing_model": "e", "reviewing_model": None,
        "run_id": "run-1", "approval_token": "tok",
    }
    assert bot._resume_payload({"run_id": "run-1"}, approved=False) == {
        "approved": False, "executing_model": None, "reviewing_model": None,
        "context_id": "run-1",
    }
    assert bot._resume_payload(
        {"context_id": "ctx-9"}, approved=True,
    )["context_id"] == "ctx-9"


def test_a_malformed_plan_callback_is_ignored(monkeypatch):
    def unreachable(**_kwargs):
        raise AssertionError("a malformed callback must not reach the server")

    monkeypatch.setattr(bot, "_server_client", unreachable)
    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve"))


def test_cancelling_a_plan_tells_the_server_it_was_refused(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "approval_token": "tok",
                  "executing_model": None, "reviewing_model": None},
    })
    server = _server(monkeypatch, _Client())
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:cancel:run-1"))

    assert server.calls[0][1] == bot.AGENT_RESUME_URL
    assert server.calls[0][2]["json"]["approved"] is False
    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]
    assert bot._bot_pending_plans == {}


def test_a_cancel_still_confirms_when_the_server_cannot_be_told(monkeypatch, caplog):
    monkeypatch.setattr(bot, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "executing_model": None,
                  "reviewing_model": None},
    })
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    with caplog.at_level("WARNING"):
        asyncio.run(bot.handle_plan_callback(client, 42, "plan:cancel:run-1"))

    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]
    assert any("cancel resume failed" in record.getMessage() for record in caplog.records)


def test_cancelling_an_unknown_plan_does_not_call_the_server(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {})

    def unreachable(**_kwargs):
        raise AssertionError("there is nothing to cancel on the server")

    monkeypatch.setattr(bot, "_server_client", unreachable)
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:cancel:gone"))

    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]


def test_approving_an_expired_plan_reports_it_as_cancelled(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {})

    def unreachable(**_kwargs):
        raise AssertionError("an unknown plan must not be resumed")

    monkeypatch.setattr(bot, "_server_client", unreachable)
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:approve:gone"))

    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]


def test_approving_a_plan_runs_it_and_delivers_everything_it_produced(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    (workspace / "out.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(bot, "get_lan_ip", lambda: "192.168.0.7")
    monkeypatch.setattr(bot, "SERVER_PORT", 4825)
    monkeypatch.setattr(bot, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "approval_token": "tok",
                  "executing_model": "e", "reviewing_model": "r"},
    })
    server = _server(monkeypatch, _Client(reply=_Res(payload={
        "response": "다 만들었습니다",
        "explanation": {"ok": False, "headline": {"ko": "검토가 필요합니다."}, "details": []},
        "artifacts": [{"path": "out.txt", "bytes": 2}],
        "steps": [
            {"action": "write_file", "result": {"path": "out.txt"}},
            {"action": "preview_url", "result": {
                "local_url": "http://127.0.0.1:4825/p/a.html", "path": "a.html"}},
        ],
    })))
    log = _patch_handlers(monkeypatch, [
        "send_message", "send_chat_action", "send_generated_files", "send_preview_links",
    ])

    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve:run-1"))

    assert server.calls[0][2]["json"]["approved"] is True
    texts = _texts(log)
    assert "⚙️ 실행 중입니다. 잠시 기다려주세요..." in texts
    assert "다 만들었습니다" in texts
    assert any("검토가 필요합니다." in text for text in texts)
    assert any("out.txt" in text for text in texts), "the artifact card is rendered"
    delivered = {name: args for name, args, _kwargs in log}
    assert delivered["send_generated_files"][2] == [("out.txt", workspace / "out.txt")]
    assert delivered["send_preview_links"][2] == [
        ("a.html", "http://192.168.0.7:4825/p/a.html")
    ]


def test_a_failed_resume_is_reported_with_its_status(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "executing_model": None,
                  "reviewing_model": None},
    })
    _server(monkeypatch, _Client(reply=_Res(status_code=503)))
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve:run-1"))

    assert "❌ 서버 에러 (503)" in _texts(log)


def test_an_unreachable_server_is_reported_as_a_run_error(monkeypatch):
    monkeypatch.setattr(bot, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "executing_model": None,
                  "reviewing_model": None},
    })
    _server(monkeypatch, _Client(error=OSError("refused")))
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve:run-1"))

    assert any("❌ 실행 중 오류" in text for text in _texts(log))


# ── process_ai_request ────────────────────────────────────────────────────


def _patch_ask_ai(monkeypatch, result):
    calls = []

    async def ask(_client, message, image_data=None, agent_mode=False,
                  planning_model=None, executing_model=None, reviewing_model=None):
        calls.append({
            "message": message, "image_data": image_data, "agent_mode": agent_mode,
            "planning_model": planning_model, "executing_model": executing_model,
            "reviewing_model": reviewing_model,
        })
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bot, "ask_ai", ask)
    return calls


def test_a_text_question_runs_in_agent_mode_and_delivers_its_output(monkeypatch):
    calls = _patch_ask_ai(monkeypatch, {
        "response": "답변입니다",
        "grounding": {"status": "supported", "cited": [{"title": "예산 계획"}]},
        "steps": [],
    })
    log = _patch_handlers(monkeypatch, [
        "send_message", "send_chat_action", "send_generated_files", "send_preview_links",
    ])

    asyncio.run(bot.process_ai_request(_Client(), 42, "예산 알려줘"))

    assert calls[0]["agent_mode"] is True
    texts = _texts(log)
    assert "답변입니다" in texts
    assert any("근거 있음" in text for text in texts)
    assert [name for name, _a, _k in log].count("send_generated_files") == 1


def test_an_image_question_skips_the_agent_only_deliverables(monkeypatch):
    calls = _patch_ask_ai(monkeypatch, {"response": "고양이입니다"})
    log = _patch_handlers(monkeypatch, [
        "send_message", "send_chat_action", "send_generated_files", "send_preview_links",
    ])

    asyncio.run(bot.process_ai_request(_Client(), 42, "이건 뭐야", image_data="b64"))

    assert calls[0]["agent_mode"] is False
    names = [name for name, _a, _k in log]
    assert "send_generated_files" not in names
    assert "send_preview_links" not in names


def test_a_paused_plan_short_circuits_into_the_approval_card(monkeypatch):
    _patch_ask_ai(monkeypatch, {"status": "awaiting_approval", "run_id": "run-1"})
    log = _patch_handlers(monkeypatch, [
        "send_message", "send_chat_action", "send_plan_for_approval",
    ])

    asyncio.run(bot.process_ai_request(_Client(), 42, "보고서 만들어줘"))

    names = [name for name, _a, _k in log]
    assert "send_plan_for_approval" in names
    assert "send_message" not in names, "a paused plan must not also send an answer"


def test_an_empty_answer_is_replaced_with_an_honest_notice(monkeypatch):
    _patch_ask_ai(monkeypatch, {"response": "   "})
    log = _patch_handlers(monkeypatch, [
        "send_message", "send_chat_action", "send_generated_files", "send_preview_links",
    ])

    asyncio.run(bot.process_ai_request(_Client(), 42, "안녕"))

    assert "⚠️ AI가 답변을 생성하지 못했습니다." in _texts(log)


def test_a_non_dict_answer_is_stringified(monkeypatch):
    _patch_ask_ai(monkeypatch, "raw string reply")
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    asyncio.run(bot.process_ai_request(_Client(), 42, "안녕"))

    assert "raw string reply" in _texts(log)


def test_an_unexpected_failure_still_answers_the_user(monkeypatch, caplog):
    _patch_ask_ai(monkeypatch, RuntimeError("boom"))
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    with caplog.at_level("ERROR"):
        asyncio.run(bot.process_ai_request(_Client(), 42, "안녕"))

    assert any("처리 중 오류가 발생했습니다" in text for text in _texts(log))
    assert any("process_ai_request 실패" in record.getMessage() for record in caplog.records)


def test_a_failure_that_cannot_even_be_reported_is_swallowed(monkeypatch):
    _patch_ask_ai(monkeypatch, RuntimeError("boom"))
    _patch_handlers(monkeypatch, ["send_chat_action"])

    async def broken(*_args, **_kwargs):
        raise OSError("telegram unreachable")

    monkeypatch.setattr(bot, "send_message", broken)
    asyncio.run(bot.process_ai_request(_Client(), 42, "안녕"))


# ── command dispatch ──────────────────────────────────────────────────────

COMMAND_HANDLERS = [
    "show_menu", "show_status", "show_model_info", "do_unload_model",
    "show_graph_stats", "take_screenshot", "show_history_summary",
    "clear_server_history", "send_web_link", "send_mcp_tools",
    "show_review_center", "send_message", "send_chat_action",
    "send_plan_for_approval",
]

COMMAND_ROUTES = [
    ("/start", "", ["send_message", "show_menu"]),
    ("/menu", "", ["show_menu"]),
    ("/status", "", ["show_status"]),
    ("/model", "", ["show_model_info"]),
    ("/unload", "", ["do_unload_model"]),
    ("/graph", "", ["show_graph_stats"]),
    ("/ss", "", ["take_screenshot"]),
    ("/screenshot", "", ["take_screenshot"]),
    ("/web", "", ["send_web_link"]),
    ("/mcp", "", ["send_mcp_tools"]),
    ("/review", "", ["show_review_center"]),
    ("/proposals", "", ["show_review_center"]),
    ("/help", "", ["send_message"]),
    ("/h", "", ["send_message"]),
    ("/nonsense", "", ["send_message"]),
]


@pytest.mark.parametrize(("command", "args", "expected"), COMMAND_ROUTES)
def test_each_command_reaches_its_own_screen(monkeypatch, command, args, expected):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, command, args))
    assert [name for name, _a, _k in log] == expected


def test_an_unknown_command_points_at_the_help_text(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/nonsense", ""))
    assert "알 수 없는 명령어: /nonsense" in _texts(log)[0]


def test_help_lists_the_agent_command(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/help", ""))
    assert "/agent <작업>" in _texts(log)[0]


@pytest.mark.parametrize(("args", "expected"), [("7", 7), ("", 5), ("many", 5)])
def test_history_takes_its_count_from_the_argument(monkeypatch, args, expected):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/history", args))
    assert log[0][1][2] == expected


@pytest.mark.parametrize(("command", "args", "expected"),
                         [("/clear", "3", 3), ("/clear_history", "", 0), ("/forget", "x", 0)])
def test_clear_takes_the_number_of_entries_to_keep(monkeypatch, command, args, expected):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, command, args))
    assert log[0][0] == "clear_server_history"
    assert log[0][1][2] == expected


def test_a_bot_suffixed_command_is_still_routed(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/STATUS@LatticeBot", ""))
    assert [name for name, _a, _k in log] == ["show_status"]


def test_agent_without_a_task_prints_the_usage(monkeypatch):
    _patch_ask_ai(monkeypatch, {"response": "never"})
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/agent", ""))

    assert "사용법: /agent <작업 내용>" in _texts(log)[0]


def test_agent_flags_are_parsed_out_of_the_task_text(monkeypatch):
    calls = _patch_ask_ai(monkeypatch, {"response": "완료"})
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(
        _Client(), 42, "/agent", "쇼핑몰 페이지 --exec openai/gpt-4o --review together:Qwen",
    ))

    assert calls[0]["message"] == "쇼핑몰 페이지"
    assert calls[0]["executing_model"] == "openai/gpt-4o"
    assert calls[0]["reviewing_model"] == "together:Qwen"
    assert "완료" in _texts(log)[0]


def test_an_agent_run_that_pauses_shows_the_approval_card(monkeypatch):
    _patch_ask_ai(monkeypatch, {"status": "awaiting_approval", "run_id": "run-1"})
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS)
    asyncio.run(bot.handle_command(_Client(), 42, "/agent", "보고서"))

    assert "send_plan_for_approval" in [name for name, _a, _k in log]


# ── callback routing ──────────────────────────────────────────────────────

CALLBACK_HANDLERS = [
    "show_status", "show_model_info", "show_graph_stats", "take_screenshot",
    "show_history_summary", "clear_server_history", "send_web_link",
    "send_mcp_tools", "show_review_center", "show_menu", "do_unload_model",
]

CALLBACK_ROUTES = [
    ("cmd:status", "show_status"),
    ("cmd:model", "show_model_info"),
    ("cmd:graph", "show_graph_stats"),
    ("cmd:screenshot", "take_screenshot"),
    ("cmd:history", "show_history_summary"),
    ("cmd:clear", "clear_server_history"),
    ("cmd:web", "send_web_link"),
    ("cmd:mcp", "send_mcp_tools"),
    ("cmd:review", "show_review_center"),
    ("cmd:menu", "show_menu"),
    ("model:unload:qwen3-8b", "do_unload_model"),
]


@pytest.mark.parametrize(("data", "expected"), CALLBACK_ROUTES)
def test_each_menu_button_reaches_its_own_screen(monkeypatch, data, expected):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS)
    client = _Client()

    asyncio.run(bot.handle_callback_query(client, {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": data,
    }))

    assert client.calls[0][1].endswith("/answerCallbackQuery")
    assert [name for name, _a, _k in log] == [expected]


def test_the_unload_button_carries_the_model_id(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS)

    asyncio.run(bot.handle_callback_query(_Client(), {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": "model:unload:qwen3-8b",
    }))

    assert log[0][1][2] == "qwen3-8b"


@pytest.mark.parametrize(("data", "handler"), [
    ("plan:approve:run-1", "handle_plan_callback"),
    ("proposal:approve:p1", "handle_proposal_callback"),
])
def test_decision_buttons_run_in_the_background(monkeypatch, data, handler):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    log = _patch_handlers(monkeypatch, [handler])

    async def main():
        await bot.handle_callback_query(_Client(), {
            "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": data,
        })
        for _ in range(4):
            await asyncio.sleep(0)

    asyncio.run(main())

    assert log == [(handler, (log[0][1][0], 42, data), {})]


def test_an_unrecognised_callback_is_acknowledged_and_dropped(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS)
    client = _Client()

    asyncio.run(bot.handle_callback_query(client, {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": "cmd:unknown",
    }))

    assert len(client.calls) == 1
    assert log == []


# ── poll loop ─────────────────────────────────────────────────────────────


def test_a_tokenless_bot_never_polls(monkeypatch, caplog):
    monkeypatch.setattr(bot, "TOKEN", "")

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a bot with no token must not poll Telegram")

    monkeypatch.setattr(bot, "get_updates", unreachable)
    with caplog.at_level("WARNING"):
        asyncio.run(bot.run_bot())

    assert any("텔레그램 봇을 시작하지 않습니다" in record.getMessage()
               for record in caplog.records)


def test_a_bot_without_a_server_capability_never_polls(monkeypatch, caplog):
    monkeypatch.setattr(bot, "TOKEN", "telegram-token")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a bot with no server capability must not poll Telegram")

    monkeypatch.setattr(bot, "get_updates", unreachable)
    with caplog.at_level("ERROR"):
        asyncio.run(bot.run_bot())

    assert any("LATTICEAI_SERVER_SESSION_TOKEN" in record.getMessage()
               for record in caplog.records)


POLL_UPDATES = [
    {"update_id": 1, "callback_query": {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": "cmd:menu"}},
    {"update_id": 2},
    {"update_id": 3, "message": {"chat": {"id": 999}, "text": "몰래 보낸 메시지"}},
    {"update_id": 4, "message": {
        "chat": {"id": 42},
        "photo": [{"file_id": "small"}, {"file_id": "large"}],
        "caption": "이 사진 설명해줘"}},
    {"update_id": 5, "message": {
        "chat": {"id": 42},
        "document": {"file_id": "img", "mime_type": "image/png", "file_name": "shot.png"}}},
    {"update_id": 6, "message": {
        "chat": {"id": 42},
        "document": {"file_id": "doc", "mime_type": "application/pdf",
                     "file_name": "report.pdf"},
        "caption": "정리해줘"}},
    {"update_id": 7, "message": {"chat": {"id": 42}, "voice": {"file_id": "v"}}},
    {"update_id": 8, "message": {"chat": {"id": 42}}},
    {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/history 3"}},
    {"update_id": 10, "message": {"chat": {"id": 42}, "text": "안녕하세요"}},
    {},
]


def test_the_poll_loop_dispatches_every_update_shape(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.setenv("LATTICEAI_SERVER_SESSION_TOKEN", "bot-capability")
    monkeypatch.setattr(bot, "TOKEN", "telegram-token")
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "chats.json")

    script = [OSError("connection reset"), None, {"ok": True, "result": POLL_UPDATES}]
    polls = []

    async def fake_get_updates(_client, offset=None):
        polls.append(offset)
        item = script[len(polls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", fake_get_updates)

    real_sleep = asyncio.sleep
    slept = []

    async def fake_sleep(delay, *_args, **_kwargs):
        slept.append(delay)
        for _ in range(4):
            await real_sleep(0)
        if len(slept) >= 3:
            raise _StopLoop

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def base64_of(_client, file_id):
        return f"b64:{file_id}"

    monkeypatch.setattr(bot, "download_as_base64", base64_of)
    log = _patch_handlers(monkeypatch, [
        "send_message", "process_ai_request", "process_document_file",
        "handle_command", "handle_callback_query",
    ])

    with caplog.at_level("WARNING"), pytest.raises(_StopLoop):
        asyncio.run(bot.run_bot())

    # One poll per loop pass: the failed poll, the empty poll, the real batch.
    assert polls == [None, None, None]
    assert slept == [1, 0.5, 0.5], "the retry backoff starts at one second"

    by_name = {}
    for name, args, _kwargs in log:
        by_name.setdefault(name, []).append(args)

    assert [args[2:] for args in by_name["process_ai_request"]] == [
        ("이 사진 설명해줘", "b64:large"),          # photo → vision
        ("이 이미지를 분석해줘.", "b64:img"),        # image document → vision
        ("안녕하세요",),                             # plain text → agent
    ]
    assert by_name["process_document_file"][0][2:] == ("doc", "report.pdf", "정리해줘")
    assert by_name["handle_command"][0][2:] == ("/history", "3")
    assert by_name["handle_callback_query"][0][1]["data"] == "cmd:menu"

    notices = [args[2] for args in by_name["send_message"]]
    assert any("📸 사진을 받았습니다" in text for text in notices)
    assert any("report.pdf 을 Knowledge Graph에 수집합니다" in text for text in notices)
    assert any("음성 인식(Whisper)이 설정되어 있지 않습니다" in text for text in notices)
    assert not any("몰래 보낸 메시지" in text for text in notices)

    assert json.loads((tmp_path / "chats.json").read_text(encoding="utf-8")) == {
        "chat_ids": [42]
    }
    logged = [record.getMessage() for record in caplog.records]
    assert any("get_updates 실패" in text for text in logged)
    assert any("업데이트 처리 중 예외" in text for text in logged)
    assert any("허용되지 않은 텔레그램 메시지 차단" in text for text in logged)


def test_a_failed_background_task_is_logged_rather_than_lost(caplog):
    async def boom():
        raise RuntimeError("background failure")

    async def main():
        task = asyncio.create_task(boom())
        with pytest.raises(RuntimeError):
            await task
        bot._log_task_exception(task)

    with caplog.at_level("ERROR"):
        asyncio.run(main())

    assert any("백그라운드 태스크 예외" in record.getMessage() for record in caplog.records)


def test_a_successful_background_task_logs_nothing(caplog):
    async def fine():
        return None

    async def main():
        task = asyncio.create_task(fine())
        await task
        bot._log_task_exception(task)

    with caplog.at_level("ERROR"):
        asyncio.run(main())

    assert not [record for record in caplog.records
                if "백그라운드 태스크 예외" in record.getMessage()]


# ── module entrypoint ─────────────────────────────────────────────────────


def _run_as_script(monkeypatch, tmp_path, on_run):
    """Execute the module with ``__name__ == "__main__"`` in an isolated cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LATTICEAI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(asyncio, "run", on_run)
    runpy.run_path(str(MODULE_PATH), run_name="__main__")


def test_running_the_module_as_a_script_starts_the_bot(tmp_path, monkeypatch):
    started = []

    def fake_run(coro):
        started.append(coro.__name__)
        coro.close()

    _run_as_script(monkeypatch, tmp_path, fake_run)

    assert started == ["run_bot"]
    assert (tmp_path / "data").is_dir(), "the data directory is provisioned at start"


def test_ctrl_c_stops_the_script_without_a_traceback(tmp_path, monkeypatch):
    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt

    _run_as_script(monkeypatch, tmp_path, fake_run)  # must not propagate
