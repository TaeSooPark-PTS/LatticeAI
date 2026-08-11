"""v11.0.1 fixes for the Telegram bot: the right client, and honest unloads.

Two defects observed in v11.0.0 are pinned here by their fixed behaviour:

* ``send_web_link`` sent the Telegram API call on the *server* client, which
  shipped the local bearer capability to ``api.telegram.org`` and failed
  outright when that token was unset.
* ``do_unload_model``'s unload-all branch threw every delete result away and
  synthesized a 200, so a refused unload still reported total success.

No socket is opened: both the Telegram client and the server client are
doubles.
"""

from __future__ import annotations

import asyncio

from latticeai.integrations import telegram_bot as bot

# v11.3.0 package split: a stub only reaches the code that reads the name when
# it is installed on *that code's* module. ``send_web_link``/``do_unload_model``
# live in ``screens``; ``PUBLIC_WEB_URL`` is read by ``get_web_url`` in
# ``helpers``. Patch targets below say so explicitly.
from latticeai.integrations.telegram_bot import helpers, screens

# ── doubles ───────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self):
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
    monkeypatch.setattr(screens, "_server_client", lambda **_kwargs: client)
    return client


def _messages(client):
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if call[1].endswith("/sendMessage")
    ]


def _unload_replies(statuses):
    """Reply table: the loaded list, then a scripted status per delete."""
    pending = list(statuses)

    def reply(method, _url, _kwargs):
        if method == "get":
            return _Res(payload={"loaded": [mid for mid, _code in statuses]})
        return _Res(status_code=pending.pop(0)[1])

    return reply


# ── D1: the web link rides the Telegram client ────────────────────────────


def test_the_web_link_never_touches_the_server_client(monkeypatch, caplog):
    monkeypatch.setattr(helpers, "PUBLIC_WEB_URL", "https://tunnel.example")

    def forbidden(**_kwargs):
        raise AssertionError("send_web_link must not open the server client")

    monkeypatch.setattr(screens, "_server_client", forbidden)
    client = _Client()

    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_web_link(client, 42))

    assert [call[1] for call in client.calls] == [f"{bot.API_URL}/sendMessage"]
    assert client.calls[0][2]["json"]["chat_id"] == 42
    assert not [r for r in caplog.records if "웹 링크 전송 실패" in r.getMessage()]


def test_the_web_link_is_delivered_without_a_server_session_token(monkeypatch, caplog):
    """The real ``_server_client()`` refuses to build without a bearer token."""
    monkeypatch.setenv("LATTICEAI_SERVER_SESSION_TOKEN", "")
    monkeypatch.setattr(helpers, "PUBLIC_WEB_URL", "https://tunnel.example")
    client = _Client()

    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_web_link(client, 42))

    payload = client.calls[0][2]["json"]
    assert "https://tunnel.example" in payload["text"]
    assert caplog.records == []


# ── D2: unload-all reports what the server actually did ───────────────────


def test_unloading_everything_reports_success_only_when_every_delete_succeeds(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_unload_replies([("a", 200), ("b", 200)])))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    assert [c[1].rsplit("/", 1)[-1] for c in server.calls if c[0] == "delete"] == ["a", "b"]
    assert _messages(client) == ["✅ 모든 모델 언로드 완료. RAM이 해제되었습니다."]


def test_a_single_refused_delete_is_reported_instead_of_a_clean_sweep(monkeypatch):
    _server(monkeypatch, _Client(reply=_unload_replies([("qwen3-8b", 200), ("gemma-4b", 500)])))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    text = _messages(client)[0]
    assert "일부 모델 언로드 실패: gemma-4b (500)" in text
    assert "성공 1개 / 실패 1개" in text
    assert "언로드 완료" not in text


def test_every_failed_delete_is_named_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_unload_replies([("a", 409), ("b", 500)])))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    text = _messages(client)[0]
    assert "일부 모델 언로드 실패: a (409), b (500)" in text
    assert "성공 0개 / 실패 2개" in text


def test_an_unreadable_loaded_list_is_reported_as_a_failure_not_a_sweep(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(status_code=503)))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    assert [call[0] for call in server.calls] == ["get"]
    assert _messages(client) == ["언로드 실패 (503)"]


def test_unloading_nothing_still_reports_the_clean_sweep(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(payload={"loaded": []})))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    assert [call[0] for call in server.calls] == ["get"]
    assert _messages(client) == ["✅ 모든 모델 언로드 완료. RAM이 해제되었습니다."]


def test_the_single_model_path_is_untouched(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res()))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42, "qwen3-8b"))

    assert [call[0] for call in server.calls] == ["delete"]
    assert _messages(client) == ["✅ qwen3-8b 언로드 완료. RAM이 해제되었습니다."]


def test_the_unload_all_report_is_built_from_the_collected_statuses():
    assert bot._unload_all_report([]) == "✅ 모든 모델 언로드 완료. RAM이 해제되었습니다."
    assert bot._unload_all_report([("a", 200)]).startswith("✅ 모든 모델")
    assert bot._unload_all_report([("a", 200), ("b", 404)]) == (
        "일부 모델 언로드 실패: b (404)\n성공 1개 / 실패 1개"
    )
