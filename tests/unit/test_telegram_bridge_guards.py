"""The Telegram bridge is the one surface that accepts input from the internet.

Everything else in this product is localhost-only. A bot token is a public
endpoint: anyone who learns the bot's name can message it. The allowlist is
therefore the whole security model, and it has to fail *closed* — an absent,
empty, or malformed configuration must deny everyone rather than admit them.

These tests exercise that boundary plus the framing helpers, with a fake HTTP
client so nothing reaches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.integrations import telegram_bot as bot


# ── the allowlist ─────────────────────────────────────────────────────────
def test_a_well_formed_allowlist_parses_to_ints():
    assert bot.parse_allowed_chat_ids("1, 2 ,3") == frozenset({1, 2, 3})


def test_an_empty_allowlist_admits_nobody():
    """Fail closed: no configuration must not mean "allow all"."""
    assert bot.parse_allowed_chat_ids("") == frozenset()
    assert bot.parse_allowed_chat_ids(None) == frozenset()
    assert bot.parse_allowed_chat_ids("   ") == frozenset()


def test_garbage_entries_are_dropped_without_taking_the_valid_ones_with_them():
    assert bot.parse_allowed_chat_ids("1,not-an-id,3") == frozenset({1, 3})


def test_negative_group_ids_are_accepted():
    """Telegram group chat ids are negative; rejecting them would break groups."""
    assert bot.parse_allowed_chat_ids("-1001234567890") == frozenset({-1001234567890})


def test_a_chat_is_allowed_only_when_it_is_on_the_list(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42,-100")
    assert bot.is_chat_allowed(42) is True
    assert bot.is_chat_allowed("42") is True, "ids arrive as strings from JSON"
    assert bot.is_chat_allowed(-100) is True
    assert bot.is_chat_allowed(43) is False


def test_an_unparseable_chat_id_is_denied_rather_than_raising(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    assert bot.is_chat_allowed(None) is False
    assert bot.is_chat_allowed("abc") is False
    assert bot.is_chat_allowed({"id": 42}) is False


def test_with_no_allowlist_configured_every_chat_is_denied(monkeypatch):
    monkeypatch.delenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(bot, "env_value", lambda *a, **k: "")
    assert bot.is_chat_allowed(42) is False


# ── registration ──────────────────────────────────────────────────────────
def test_registration_is_refused_for_a_chat_not_on_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "chats.json")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")

    assert bot.register_chat_id(999) is False
    assert not (tmp_path / "chats.json").exists(), "a denied chat must not be persisted"


def test_registration_persists_an_allowed_chat_once(tmp_path, monkeypatch):
    store = tmp_path / "chats.json"
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", store)
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")

    assert bot.register_chat_id(42) is True
    assert bot.register_chat_id(42) is True
    assert json.loads(store.read_text(encoding="utf-8"))["chat_ids"] == [42]


def test_a_missing_chat_store_reads_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "nope.json")
    assert bot.load_chat_ids() == set()


def test_a_corrupt_chat_store_reads_as_empty_rather_than_crashing(tmp_path, monkeypatch):
    store = tmp_path / "chats.json"
    store.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", store)
    assert bot.load_chat_ids() == set()


def test_saving_to_an_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "missing" / "chats.json")
    bot.save_chat_ids({1, 2})  # logged, not raised


# ── configuration helpers ─────────────────────────────────────────────────
def test_env_value_prefers_the_environment_over_the_default(monkeypatch):
    monkeypatch.setenv("LATTICEAI_TEST_KEY", "from-env")
    assert bot.env_value("LATTICEAI_TEST_KEY", "fallback") == "from-env"


def test_env_value_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("LATTICEAI_TEST_KEY", raising=False)
    assert bot.env_value("LATTICEAI_TEST_KEY", "fallback") == "fallback"


def test_load_env_file_ignores_comments_and_blanks(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nLATTICEAI_SAMPLE=value\n  \nOTHER = spaced \n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LATTICEAI_SAMPLE", raising=False)
    bot.load_env_file(str(env))
    import os

    assert os.environ.get("LATTICEAI_SAMPLE") == "value"


def test_load_env_file_on_a_missing_path_is_a_no_op(tmp_path):
    bot.load_env_file(str(tmp_path / "absent.env"))  # must not raise


def test_web_and_graph_urls_point_at_the_same_host():
    web, graph = bot.get_web_url(), bot.get_graph_url()
    assert web.startswith("http")
    assert graph.startswith("http")
    assert graph != web, "the graph deep-link should not be the bare app url"


def test_lan_ip_returns_something_usable_even_with_no_network(monkeypatch):
    """Called while composing a QR/link; a failure here must not break the bot."""
    import socket

    class _Sock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, *a):
            raise OSError("no route to host")

        def getsockname(self):
            raise OSError("unreachable")

        def close(self):
            return None

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
    value = bot.get_lan_ip()
    assert isinstance(value, str) and value


# ── outbound framing ──────────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {"ok": True, "result": {}}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeClient:
    """Records what would have gone to Telegram."""

    def __init__(self, payload=None):
        self.posts: list = []
        self.gets: list = []
        self._payload = payload

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse(self._payload)

    async def get(self, url, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return _FakeResponse(self._payload)


def test_send_message_targets_the_send_endpoint_with_the_chat_and_text():
    import asyncio

    client = _FakeClient()
    asyncio.run(bot.send_message(client, 42, "안녕하세요"))

    assert len(client.posts) == 1
    sent = client.posts[0]
    assert sent["url"].endswith("/sendMessage")
    body = sent.get("json") or sent.get("data") or {}
    assert body.get("chat_id") == 42
    assert "안녕하세요" in str(body.get("text"))


def test_a_reply_markup_is_forwarded_when_given():
    import asyncio

    client = _FakeClient()
    markup = {"inline_keyboard": [[{"text": "승인", "callback_data": "ok"}]]}
    asyncio.run(bot.send_message(client, 42, "확인해주세요", reply_markup=markup))

    body = client.posts[0].get("json") or {}
    assert body.get("reply_markup")


def test_chat_action_reports_typing_so_the_user_sees_progress():
    import asyncio

    client = _FakeClient()
    asyncio.run(bot.send_chat_action(client, 42, "typing"))
    assert client.posts[0]["url"].endswith("/sendChatAction")


def test_answering_a_callback_query_closes_the_spinner():
    import asyncio

    client = _FakeClient()
    asyncio.run(bot.answer_callback(client, "cbq-1", "승인했습니다"))
    assert client.posts[0]["url"].endswith("/answerCallbackQuery")


def test_get_updates_passes_the_offset_so_messages_are_not_replayed():
    import asyncio

    client = _FakeClient(payload={"ok": True, "result": []})
    asyncio.run(bot.get_updates(client, offset=17))
    assert "offset=17" in client.gets[0]["url"]


def test_get_updates_omits_the_offset_on_the_first_poll():
    import asyncio

    client = _FakeClient(payload={"ok": True, "result": []})
    asyncio.run(bot.get_updates(client))
    assert "offset=" not in client.gets[0]["url"]


def test_a_failing_poll_returns_none_rather_than_raising():
    """The poll loop must survive a dropped connection."""
    import asyncio

    class _Broken:
        async def get(self, *a, **k):
            raise OSError("connection reset")

    assert asyncio.run(bot.get_updates(_Broken())) is None


def test_downloading_a_file_returns_none_when_telegram_has_no_path():
    import asyncio

    client = _FakeClient(payload={"ok": True, "result": {}})
    assert asyncio.run(bot.download_telegram_file(client, "file-1")) is None
