"""Fail-closed security boundaries for the Telegram bridge."""

from __future__ import annotations

import asyncio

import pytest

from latticeai.integrations import telegram_bot


def test_allowlist_parsing_and_default_deny(monkeypatch):
    monkeypatch.delenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    assert telegram_bot.allowed_chat_ids() == frozenset()
    assert telegram_bot.is_chat_allowed(123) is False

    monkeypatch.setenv(
        "LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS",
        "123, -456,invalid,123",
    )
    assert telegram_bot.allowed_chat_ids() == frozenset({123, -456})
    assert telegram_bot.is_chat_allowed("-456") is True


def test_register_chat_id_refuses_unlisted_sender(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "CHAT_IDS_FILE", tmp_path / "telegram_chats.json")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "100")

    assert telegram_bot.register_chat_id(999) is False
    assert not telegram_bot.CHAT_IDS_FILE.exists()

    assert telegram_bot.register_chat_id(100) is True
    assert telegram_bot.load_chat_ids() == {100}
    assert telegram_bot.CHAT_IDS_FILE.stat().st_mode & 0o777 == 0o600


def test_server_session_must_be_explicit_and_never_scans_session_files(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "DATA_DIR", tmp_path)
    (tmp_path / "sessions.json").write_text(
        '{"plaintext-web-session": ["admin@example.com", 9999999999]}',
        encoding="utf-8",
    )
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)

    assert telegram_bot._get_server_session() == ""
    with pytest.raises(RuntimeError, match="LATTICEAI_SERVER_SESSION_TOKEN"):
        telegram_bot._server_client()

    monkeypatch.setenv("LATTICEAI_SERVER_SESSION_TOKEN", "dedicated-bot-token")
    assert telegram_bot._get_server_session() == "dedicated-bot-token"
    client = telegram_bot._server_client()
    try:
        assert client.headers["Authorization"] == "Bearer dedicated-bot-token"
        assert "session_token" not in client.cookies
    finally:
        asyncio.run(client.aclose())


def test_callback_query_applies_same_chat_acl(monkeypatch):
    calls = []

    class Client:
        async def post(self, url, json=None, **_kwargs):
            calls.append((url, json))

    async def forbidden_action(*_args, **_kwargs):
        raise AssertionError("an unauthorized callback executed a desktop action")

    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "100")
    monkeypatch.setattr(telegram_bot, "take_screenshot", forbidden_action)

    asyncio.run(
        telegram_bot.handle_callback_query(
            Client(),
            {
                "id": "callback-1",
                "message": {"chat": {"id": 999}},
                "data": "cmd:screenshot",
            },
        )
    )

    assert len(calls) == 1
    assert calls[0][0].endswith("/answerCallbackQuery")
    assert "허용되지 않은" in calls[0][1]["text"]


def test_plan_callback_is_bound_to_originating_allowed_chat(monkeypatch):
    messages = []

    async def capture_message(_client, chat_id, text, **_kwargs):
        messages.append((chat_id, text))

    telegram_bot._bot_pending_plans.clear()
    telegram_bot._bot_pending_plans["ctx-1"] = {
        "chat_id": 100,
        "executing_model": None,
        "reviewing_model": None,
    }
    monkeypatch.setattr(telegram_bot, "send_message", capture_message)

    asyncio.run(
        telegram_bot.handle_plan_callback(
            object(),
            200,
            "plan:approve:ctx-1",
        )
    )

    assert "ctx-1" in telegram_bot._bot_pending_plans
    assert messages and "다른 채팅" in messages[0][1]


def test_bot_does_not_poll_without_acl_or_server_token(monkeypatch):
    async def unexpected_poll(*_args, **_kwargs):
        raise AssertionError("bot polled Telegram without complete security configuration")

    monkeypatch.setattr(telegram_bot, "TOKEN", "telegram-token")
    monkeypatch.setattr(telegram_bot, "get_updates", unexpected_poll)
    monkeypatch.delenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("LATTICEAI_SERVER_SESSION_TOKEN", raising=False)

    asyncio.run(telegram_bot.run_bot())


# ── Run explanation parity (v9.9.6) ──────────────────────────────────────────
# Review 2026-07-27 P0 #1: Telegram used to render a NEEDS_REVIEW run exactly
# like a success. It now repeats the server's plain-language outcome.

def test_telegram_reports_a_non_success_run_honestly():
    from latticeai.integrations.telegram_bot import format_run_explanation

    text = format_run_explanation({
        "explanation": {
            "ok": False,
            "headline": {"ko": "완료로 처리하지 않았습니다.", "en": "Not complete."},
            "details": [{"ko": "형식을 2번 틀렸습니다.", "en": "Format broke twice."}],
        }
    })
    assert text.startswith("⚠️")
    assert "완료로 처리하지 않았습니다." in text
    assert "· 형식을 2번 틀렸습니다." in text


def test_telegram_adds_nothing_for_a_clean_verified_run():
    from latticeai.integrations.telegram_bot import format_run_explanation

    assert format_run_explanation({
        "explanation": {"ok": True, "headline": {"ko": "끝", "en": "Done"}, "details": []}
    }) == ""
    for bad in (None, {}, {"explanation": "x"}, 7):
        assert format_run_explanation(bad) == ""


def test_telegram_explains_a_strained_but_successful_run():
    from latticeai.integrations.telegram_bot import format_run_explanation

    text = format_run_explanation({
        "explanation": {
            "ok": True,
            "headline": {"ko": "끝났습니다.", "en": "Done."},
            "details": [{"ko": "형식을 3번 고쳤습니다.", "en": "Fixed 3 times."}],
        }
    })
    assert text.startswith("✅")
    assert "형식을 3번 고쳤습니다." in text
