"""wp01 coverage: Telegram command dispatch, callbacks and the poll loop.

Everything on the delivery side of the bot: which screen a command or a menu
button reaches, and the poll loop itself. The loop is driven by a scripted
``get_updates`` and a patched ``asyncio.sleep`` that leaves after a fixed
number of iterations, so the test is deterministic and never sleeps for real.
The conversation flows are the twin suite,
``test_cov_wp01_telegram_flows.py``; both share the doubles in
``_telegram_flow_common``.
"""

from __future__ import annotations

import asyncio
import json
import runpy

import pytest

from latticeai.integrations import telegram_bot as bot

# v11.3.0 package split: a stub only reaches the code that reads the name when
# it is installed on that code's own module. Command and callback routing and
# the poll loop live in ``dispatch``; the chat-id store lives in ``helpers``.
from latticeai.integrations.telegram_bot import dispatch, helpers

from ._telegram_flow_common import (
    CONFIG_PATH,
    MODULE_PATH,
    _Client,
    _patch_ask_ai,
    _patch_handlers,
    _StopLoop,
    _texts,
)

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
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, command, args))
    assert [name for name, _a, _k in log] == expected


def test_an_unknown_command_points_at_the_help_text(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, "/nonsense", ""))
    assert "알 수 없는 명령어: /nonsense" in _texts(log)[0]


def test_help_lists_the_agent_command(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, "/help", ""))
    assert "/agent <작업>" in _texts(log)[0]


@pytest.mark.parametrize(("args", "expected"), [("7", 7), ("", 5), ("many", 5)])
def test_history_takes_its_count_from_the_argument(monkeypatch, args, expected):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, "/history", args))
    assert log[0][1][2] == expected


@pytest.mark.parametrize(("command", "args", "expected"),
                         [("/clear", "3", 3), ("/clear_history", "", 0), ("/forget", "x", 0)])
def test_clear_takes_the_number_of_entries_to_keep(monkeypatch, command, args, expected):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, command, args))
    assert log[0][0] == "clear_server_history"
    assert log[0][1][2] == expected


def test_a_bot_suffixed_command_is_still_routed(monkeypatch):
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, "/STATUS@LatticeBot", ""))
    assert [name for name, _a, _k in log] == ["show_status"]


def test_agent_without_a_task_prints_the_usage(monkeypatch):
    _patch_ask_ai(monkeypatch, {"response": "never"})
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(_Client(), 42, "/agent", ""))

    assert "사용법: /agent <작업 내용>" in _texts(log)[0]


def test_agent_flags_are_parsed_out_of_the_task_text(monkeypatch):
    calls = _patch_ask_ai(monkeypatch, {"response": "완료"}, dispatch)
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
    asyncio.run(bot.handle_command(
        _Client(), 42, "/agent", "쇼핑몰 페이지 --exec openai/gpt-4o --review together:Qwen",
    ))

    assert calls[0]["message"] == "쇼핑몰 페이지"
    assert calls[0]["executing_model"] == "openai/gpt-4o"
    assert calls[0]["reviewing_model"] == "together:Qwen"
    assert "완료" in _texts(log)[0]


def test_an_agent_run_that_pauses_shows_the_approval_card(monkeypatch):
    _patch_ask_ai(monkeypatch, {"status": "awaiting_approval", "run_id": "run-1"}, dispatch)
    log = _patch_handlers(monkeypatch, COMMAND_HANDLERS, dispatch)
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
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS, dispatch)
    client = _Client()

    asyncio.run(bot.handle_callback_query(client, {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": data,
    }))

    assert client.calls[0][1].endswith("/answerCallbackQuery")
    assert [name for name, _a, _k in log] == [expected]


def test_the_unload_button_carries_the_model_id(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS, dispatch)

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
    log = _patch_handlers(monkeypatch, [handler], dispatch)

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
    log = _patch_handlers(monkeypatch, CALLBACK_HANDLERS, dispatch)
    client = _Client()

    asyncio.run(bot.handle_callback_query(client, {
        "id": "cbq-1", "message": {"chat": {"id": 42}}, "data": "cmd:unknown",
    }))

    assert len(client.calls) == 1
    assert log == []


# ── poll loop ─────────────────────────────────────────────────────────────


def test_a_tokenless_bot_never_polls(monkeypatch, caplog):
    monkeypatch.setattr(dispatch, "TOKEN", "")

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a bot with no token must not poll Telegram")

    monkeypatch.setattr(dispatch, "get_updates", unreachable)
    with caplog.at_level("WARNING"):
        asyncio.run(bot.run_bot())

    assert any("텔레그램 봇을 시작하지 않습니다" in record.getMessage()
               for record in caplog.records)


def test_a_bot_without_a_server_capability_never_polls(monkeypatch, caplog):
    monkeypatch.setattr(dispatch, "TOKEN", "telegram-token")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a bot with no server capability must not poll Telegram")

    monkeypatch.setattr(dispatch, "get_updates", unreachable)
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
    monkeypatch.setattr(dispatch, "TOKEN", "telegram-token")
    monkeypatch.setattr(helpers, "CHAT_IDS_FILE", tmp_path / "chats.json")

    script = [OSError("connection reset"), None, {"ok": True, "result": POLL_UPDATES}]
    polls = []

    async def fake_get_updates(_client, offset=None):
        polls.append(offset)
        item = script[len(polls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(dispatch, "get_updates", fake_get_updates)

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

    monkeypatch.setattr(dispatch, "download_as_base64", base64_of)
    log = _patch_handlers(monkeypatch, [
        "send_message", "process_ai_request", "process_document_file",
        "handle_command", "handle_callback_query",
    ], dispatch)

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


def _isolate(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LATTICEAI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)


def _run_as_script(monkeypatch, tmp_path, on_run):
    """Execute the entry file with ``__name__ == "__main__"`` in an isolated cwd."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(asyncio, "run", on_run)
    runpy.run_path(str(MODULE_PATH), run_name="__main__")


def test_running_the_module_as_a_script_starts_the_bot(tmp_path, monkeypatch):
    started = []

    def fake_run(coro):
        started.append(coro.__name__)
        coro.close()

    _run_as_script(monkeypatch, tmp_path, fake_run)

    assert started == ["run_bot"]


def test_importing_the_bot_provisions_its_data_directory(tmp_path, monkeypatch):
    """v11.3.0 package split: the data directory is a *config* import side effect.

    It used to be observable through the script entry because the whole module
    ran again under ``runpy``. ``__main__.py`` imports an already-loaded package,
    so the side effect is exercised where it now lives — by running ``config.py``
    itself with a fresh environment.
    """
    _isolate(monkeypatch, tmp_path)

    runpy.run_path(str(CONFIG_PATH))

    assert (tmp_path / "data").is_dir(), "the data directory is provisioned at import"


def test_ctrl_c_stops_the_script_without_a_traceback(tmp_path, monkeypatch):
    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt

    _run_as_script(monkeypatch, tmp_path, fake_run)  # must not propagate
