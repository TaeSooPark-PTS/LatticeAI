"""wp01 coverage: the Telegram conversation flows.

``ask_ai`` and everything downstream of it — the approval handshake, the
review centre, the proposal decisions and ``process_ai_request``. Command
dispatch, callback routing, the poll loop and the script entry are the twin
suite, ``test_cov_wp01_telegram_polling.py``; both share the doubles in
``_telegram_flow_common``.
"""

from __future__ import annotations

import asyncio

from latticeai.integrations import telegram_bot as bot

# v11.3.0 package split: a stub only reaches the code that reads the name when
# it is installed on that code's own module. ``ask_ai`` and everything it feeds
# (the approval handshake, the review centre) live in ``flows``; the chat-id
# store and the agent-artifact plumbing live in ``helpers``.
from latticeai.integrations.telegram_bot import flows, helpers

from ._telegram_flow_common import (
    _Client,
    _messages,
    _patch_ask_ai,
    _patch_handlers,
    _Res,
    _server,
    _texts,
)

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

    monkeypatch.setattr(flows, "_server_client", unreachable)
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
    monkeypatch.setattr(flows, "_bot_pending_plans", {})
    client = _Client()
    asyncio.run(bot.send_plan_for_approval(client, 42, {"plan": {"goal": "무언가"}}))

    assert "승인할 계획을 식별할 수 없습니다" in _messages(client)[0]
    assert flows._bot_pending_plans == {}


def test_a_modern_plan_is_shown_with_its_steps_and_stored_for_resume(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {})
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
    assert flows._bot_pending_plans["run-1"] == {
        "chat_id": 42, "run_id": "run-1", "context_id": None,
        "approval_token": "tok", "legacy": False,
        "executing_model": "e", "reviewing_model": "r",
    }


def test_a_legacy_plan_is_keyed_by_its_context_id(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {})
    client = _Client()
    asyncio.run(bot.send_plan_for_approval(client, 42, {
        "status": "waiting_approval",
        "context_id": "ctx-9",
        "approval": {"plan_summary": "레거시 목표"},
        "non_auto_steps": [{"action": "write_file"}],
    }))

    assert "*목표:* 레거시 목표" in client.calls[0][2]["json"]["text"]
    assert flows._bot_pending_plans["ctx-9"]["legacy"] is True


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

    monkeypatch.setattr(flows, "_server_client", unreachable)
    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve"))


def test_cancelling_a_plan_tells_the_server_it_was_refused(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "approval_token": "tok",
                  "executing_model": None, "reviewing_model": None},
    })
    server = _server(monkeypatch, _Client())
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:cancel:run-1"))

    assert server.calls[0][1] == bot.AGENT_RESUME_URL
    assert server.calls[0][2]["json"]["approved"] is False
    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]
    assert flows._bot_pending_plans == {}


def test_a_cancel_still_confirms_when_the_server_cannot_be_told(monkeypatch, caplog):
    monkeypatch.setattr(flows, "_bot_pending_plans", {
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
    monkeypatch.setattr(flows, "_bot_pending_plans", {})

    def unreachable(**_kwargs):
        raise AssertionError("there is nothing to cancel on the server")

    monkeypatch.setattr(flows, "_server_client", unreachable)
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:cancel:gone"))

    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]


def test_approving_an_expired_plan_reports_it_as_cancelled(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {})

    def unreachable(**_kwargs):
        raise AssertionError("an unknown plan must not be resumed")

    monkeypatch.setattr(flows, "_server_client", unreachable)
    client = _Client()
    asyncio.run(bot.handle_plan_callback(client, 42, "plan:approve:gone"))

    assert "❌ 작업이 취소되었습니다." in _messages(client)[0]


def test_approving_a_plan_runs_it_and_delivers_everything_it_produced(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    (workspace / "out.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(helpers, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(helpers, "get_lan_ip", lambda: "192.168.0.7")
    monkeypatch.setattr(helpers, "SERVER_PORT", 4825)
    monkeypatch.setattr(flows, "_bot_pending_plans", {
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
    monkeypatch.setattr(flows, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "executing_model": None,
                  "reviewing_model": None},
    })
    _server(monkeypatch, _Client(reply=_Res(status_code=503)))
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve:run-1"))

    assert "❌ 서버 에러 (503)" in _texts(log)


def test_an_unreachable_server_is_reported_as_a_run_error(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {
        "run-1": {"chat_id": 42, "run_id": "run-1", "executing_model": None,
                  "reviewing_model": None},
    })
    _server(monkeypatch, _Client(error=OSError("refused")))
    log = _patch_handlers(monkeypatch, ["send_message", "send_chat_action"])

    asyncio.run(bot.handle_plan_callback(_Client(), 42, "plan:approve:run-1"))

    assert any("❌ 실행 중 오류" in text for text in _texts(log))


# ── process_ai_request ────────────────────────────────────────────────────


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

    monkeypatch.setattr(flows, "send_message", broken)
    asyncio.run(bot.process_ai_request(_Client(), 42, "안녕"))


