"""wp01 coverage: the Telegram screens that read the local server.

Every one of these renders a reply from a ``/status``-style endpoint, so each
is exercised through an injected ``_server_client`` double across its success,
error-status and transport-failure paths. No socket is opened and no
screenshot subprocess is spawned.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from latticeai.integrations import telegram_bot as bot

# ── doubles ───────────────────────────────────────────────────────────────


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


class _Proc:
    """Stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, error=None):
        self.error = error

    async def communicate(self):
        if self.error is not None:
            raise self.error
        return b"", b""


def _server(monkeypatch, client):
    """Route ``_server_client()`` at a double instead of the real HTTP client."""
    monkeypatch.setattr(bot, "_server_client", lambda **_kwargs: client)
    return client


def _messages(client):
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if call[1].endswith("/sendMessage")
    ]


def _last_markup(client):
    for call in reversed(client.calls):
        if call[1].endswith("/sendMessage"):
            return call[2].get("json", {}).get("reply_markup")
    return None


# ── /status ───────────────────────────────────────────────────────────────


def test_status_renders_the_servers_reply(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={
        "loaded_model": "qwen3-8b", "mode": "local", "status": "online",
    })))

    async def ram(*_args):
        return "12.0 GB / 32 GB"

    monkeypatch.setattr(bot, "_mac_ram_used_gb", ram)
    client = _Client()
    asyncio.run(bot.show_status(client, 42))

    text = _messages(client)[0]
    assert "🟢 온라인" in text
    assert "qwen3-8b" in text
    assert "12.0 GB / 32 GB" in text


def test_status_reports_offline_when_the_server_rejects_the_probe(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=503)))

    async def ram(*_args):
        return "N/A"

    monkeypatch.setattr(bot, "_mac_ram_used_gb", ram)
    client = _Client()
    asyncio.run(bot.show_status(client, 42))

    text = _messages(client)[0]
    assert "🔴 오프라인" in text
    assert "모델: 없음" in text


def test_status_reports_offline_when_the_server_is_unreachable(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("connection refused")))

    async def ram(*_args):
        return "N/A"

    monkeypatch.setattr(bot, "_mac_ram_used_gb", ram)
    client = _Client()
    asyncio.run(bot.show_status(client, 42))

    assert "🔴 오프라인" in _messages(client)[0]


# ── /model ────────────────────────────────────────────────────────────────


def test_model_info_offers_an_unload_button_per_loaded_model(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={
        "current": "qwen3-8b", "loaded": ["qwen3-8b", "gemma-4b"],
    })))
    client = _Client()
    asyncio.run(bot.show_model_info(client, 42))

    rows = _last_markup(client)["inline_keyboard"]
    assert [row[0]["callback_data"] for row in rows[:2]] == [
        "model:unload:qwen3-8b", "model:unload:gemma-4b",
    ]
    assert rows[-1][0]["callback_data"] == "cmd:menu"


def test_model_info_has_no_buttons_when_nothing_is_loaded(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=500)))
    client = _Client()
    asyncio.run(bot.show_model_info(client, 42))

    assert _last_markup(client) is None
    assert "현재 모델: 없음" in _messages(client)[0]


def test_model_info_survives_an_unreachable_server(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.show_model_info(client, 42))

    assert "현재 모델: 없음" in _messages(client)[0]


# ── /unload ───────────────────────────────────────────────────────────────


def test_unloading_one_model_targets_that_model(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res()))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42, "qwen3-8b"))

    assert server.calls[0][0] == "delete"
    assert server.calls[0][1].endswith("/models/unload/qwen3-8b")
    assert "✅ qwen3-8b 언로드 완료" in _messages(client)[0]


def test_unloading_everything_walks_the_loaded_list(monkeypatch):
    def reply(method, _url, _kwargs):
        if method == "get":
            return _Res(payload={"loaded": ["a", "b"]})
        return _Res()

    server = _server(monkeypatch, _Client(reply=reply))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42))

    deleted = [call[1] for call in server.calls if call[0] == "delete"]
    assert [url.rsplit("/", 1)[-1] for url in deleted] == ["a", "b"]
    assert "모든 모델 언로드 완료" in _messages(client)[0]


def test_a_rejected_unload_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=404)))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42, "ghost"))

    assert "언로드 실패 (404)" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_an_unload_error(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.do_unload_model(client, 42, "ghost"))

    assert "언로드 오류" in _messages(client)[0]


# ── /graph ────────────────────────────────────────────────────────────────


def test_graph_stats_sorts_by_count_and_links_to_the_graph(monkeypatch):
    monkeypatch.setattr(bot, "get_lan_ip", lambda: "192.168.0.7")
    monkeypatch.setattr(bot, "SERVER_PORT", 4825)
    monkeypatch.setattr(bot, "PUBLIC_WEB_URL", "")
    _server(monkeypatch, _Client(reply=_Res(payload={
        "nodes": {"Document": 3, "Person": 9},
        "edges": {"MENTIONS": 4},
    })))
    client = _Client()
    asyncio.run(bot.show_graph_stats(client, 42))

    text = _messages(client)[0]
    assert "노드 총 12개" in text
    assert text.index("Person") < text.index("Document")
    assert "엣지 총 4개" in text
    button = _last_markup(client)["inline_keyboard"][0][0]
    assert button["url"] == "http://192.168.0.7:4825/graph"


def test_graph_stats_says_none_when_the_graph_is_empty(monkeypatch):
    monkeypatch.setattr(bot, "get_lan_ip", lambda: "192.168.0.7")
    _server(monkeypatch, _Client(reply=_Res(status_code=500)))
    client = _Client()
    asyncio.run(bot.show_graph_stats(client, 42))

    assert "노드 총 0개" in _messages(client)[0]


def test_graph_stats_survives_an_unreachable_server(monkeypatch):
    monkeypatch.setattr(bot, "get_lan_ip", lambda: "192.168.0.7")
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.show_graph_stats(client, 42))

    assert "엣지 총 0개" in _messages(client)[0]


# ── /screenshot ───────────────────────────────────────────────────────────


def _capture_exec(monkeypatch, *, write=None, error=None, communicate_error=None):
    async def create(_program, *args, **_kwargs):
        if error is not None:
            raise error
        if write is not None:
            Path(args[-1]).write_bytes(write)
        return _Proc(error=communicate_error)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)


def test_a_captured_screen_is_sent_as_a_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch, write=b"jpeg-bytes")
    sent = []

    async def capture(_client, chat_id, path, caption=""):
        sent.append((chat_id, path.read_bytes(), caption))

    monkeypatch.setattr(bot, "send_photo", capture)
    asyncio.run(bot.take_screenshot(_Client(), 42))

    assert sent == [(42, b"jpeg-bytes", "현재 화면입니다.")]
    assert list(tmp_path.glob("*.jpg")) == [], "the staged capture must be removed"


def test_an_empty_capture_tells_the_user_the_tool_may_be_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch)
    client = _Client()
    asyncio.run(bot.take_screenshot(client, 42))

    assert "스크린샷 파일이 생성되지 않았습니다" in _messages(client)[0]


def test_a_hung_capture_is_reported_as_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch, communicate_error=asyncio.TimeoutError())
    client = _Client()
    asyncio.run(bot.take_screenshot(client, 42))

    assert "스크린샷 시간 초과" in _messages(client)[0]


def test_a_missing_screencapture_binary_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch, error=FileNotFoundError("screencapture"))
    client = _Client()
    asyncio.run(bot.take_screenshot(client, 42))

    assert "screencapture 명령이 없습니다" in _messages(client)[0]


def test_any_other_capture_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch, error=PermissionError("no screen recording permission"))
    client = _Client()
    asyncio.run(bot.take_screenshot(client, 42))

    assert "스크린샷 오류" in _messages(client)[0]


def test_a_capture_that_cannot_be_deleted_does_not_break_the_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _capture_exec(monkeypatch, write=b"jpeg-bytes")
    real_unlink = Path.unlink

    def stubborn(self, *args, **kwargs):
        if self.suffix == ".jpg":
            raise OSError("resource busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stubborn)
    sent = []

    async def capture(_client, chat_id, path, caption=""):
        sent.append((chat_id, path.name, caption))

    monkeypatch.setattr(bot, "send_photo", capture)
    asyncio.run(bot.take_screenshot(_Client(), 42))

    assert len(sent) == 1


# ── /history ──────────────────────────────────────────────────────────────


def test_history_shows_only_the_last_n_user_messages(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload=[
        {"role": "user", "content": "첫 질문", "timestamp": "2026-08-01T09:00:00", "source": "web"},
        {"role": "assistant", "content": "답"},
        {"role": "user", "content": "두 번째 질문\n줄바꿈", "timestamp": "2026-08-01T10:00:00"},
    ])))
    client = _Client()
    asyncio.run(bot.show_history_summary(client, 42, 1))

    text = _messages(client)[0]
    assert "최근 사용자 메시지 1건" in text
    assert "첫 질문" not in text
    assert "[2026-08-01T10:00] (web) 두 번째 질문 줄바꿈" in text, (
        "the timestamp is trimmed, an absent source defaults to web, "
        "and newlines are flattened so one entry stays one line"
    )


def test_history_says_so_when_the_server_has_none(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=500)))
    client = _Client()
    asyncio.run(bot.show_history_summary(client, 42))

    assert "저장된 대화 기록이 없습니다." in _messages(client)[0]


def test_history_survives_an_unreachable_server(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.show_history_summary(client, 42))

    assert "저장된 대화 기록이 없습니다." in _messages(client)[0]


# ── /clear ────────────────────────────────────────────────────────────────


def test_clearing_history_reports_the_servers_counts(monkeypatch):
    server = _server(monkeypatch, _Client(reply=_Res(
        payload={"removed": 7, "kept": 3},
        headers={"content-type": "application/json"},
    )))
    client = _Client()
    asyncio.run(bot.clear_server_history(client, 42, keep_last=3))

    assert server.calls[0][2]["params"] == {"keep_last": 3}
    assert "삭제 7개, 유지 3개" in _messages(client)[0]


def test_a_rejected_clear_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=403, text="nope")))
    client = _Client()
    asyncio.run(bot.clear_server_history(client, 42))

    assert "대화 기록 정리 실패: 403" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_a_clear_error(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.clear_server_history(client, 42))

    assert "대화 기록 정리 오류" in _messages(client)[0]


# ── /web ──────────────────────────────────────────────────────────────────


def test_the_web_link_carries_both_deep_links(monkeypatch):
    monkeypatch.setattr(bot, "PUBLIC_WEB_URL", "https://tunnel.example")
    server = _server(monkeypatch, _Client())
    asyncio.run(bot.send_web_link(_Client(), 42))

    payload = server.calls[0][2]["json"]
    assert payload["chat_id"] == 42
    assert "https://tunnel.example" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [button["url"] for button in buttons] == [
        "https://tunnel.example", "https://tunnel.example/graph",
    ]


def test_a_failed_web_link_send_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(bot, "PUBLIC_WEB_URL", "https://tunnel.example")
    _server(monkeypatch, _Client(error=OSError("refused")))

    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_web_link(_Client(), 42))

    assert any("웹 링크 전송 실패" in record.getMessage() for record in caplog.records)


# ── /mcp ──────────────────────────────────────────────────────────────────


def test_mcp_tool_names_are_listed(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={
        "tools": [{"name": "read_file"}, {"name": "write_file"}],
    })))
    client = _Client()
    asyncio.run(bot.send_mcp_tools(client, 42))

    text = _messages(client)[0]
    assert "- read_file" in text
    assert "- write_file" in text


def test_an_empty_mcp_registry_says_none(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(payload={"tools": []})))
    client = _Client()
    asyncio.run(bot.send_mcp_tools(client, 42))

    assert _messages(client)[0].endswith("없음")


def test_a_rejected_mcp_listing_is_reported_with_its_status(monkeypatch):
    _server(monkeypatch, _Client(reply=_Res(status_code=401)))
    client = _Client()
    asyncio.run(bot.send_mcp_tools(client, 42))

    assert "MCP 도구 목록을 가져오지 못했습니다: 401" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_an_mcp_failure(monkeypatch):
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.send_mcp_tools(client, 42))

    assert "MCP 도구 조회 실패" in _messages(client)[0]


# ── document ingestion ────────────────────────────────────────────────────


def _downloads(monkeypatch, payload):
    async def download(_client, _file_id):
        return payload

    monkeypatch.setattr(bot, "download_telegram_file", download)


def test_a_document_that_cannot_be_downloaded_is_reported(monkeypatch):
    _downloads(monkeypatch, None)
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "report.pdf"))

    assert _messages(client) == ["파일 다운로드 실패"]


def test_an_unsupported_extension_is_refused_with_the_allowed_list(monkeypatch):
    _downloads(monkeypatch, b"MZ")
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "tool.exe"))

    text = _messages(client)[0]
    assert "지원하지 않는 파일 형식입니다(.exe)" in text
    assert ".pdf" in text


def test_an_ingested_document_reports_the_graph_node_it_produced(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _downloads(monkeypatch, b"%PDF-1.4 body")
    server = _server(monkeypatch, _Client(reply=_Res(payload={
        "chars": 12, "preview": "본문 미리보기", "knowledge_graph": {"node_id": "doc-7"},
    })))
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "report.pdf", "설명"))

    assert server.calls[0][2]["files"]["file"] == ("report.pdf", b"%PDF-1.4 body")
    text = _messages(client)[0]
    assert "✅ report.pdf 수집 완료" in text
    assert "노드: doc-7" in text
    assert "본문 미리보기" in text
    assert list(tmp_path.glob("*.pdf")) == [], "the staged upload must be removed"


def test_a_rejected_upload_reports_the_servers_json_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _downloads(monkeypatch, b"text")
    _server(monkeypatch, _Client(reply=_Res(
        status_code=422,
        payload={"detail": "빈 문서입니다"},
        headers={"content-type": "application/json"},
    )))
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "notes.txt"))

    assert "업로드 실패 (422): 빈 문서입니다" in _messages(client)[0]


def test_a_rejected_upload_falls_back_to_the_raw_body(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _downloads(monkeypatch, b"text")
    _server(monkeypatch, _Client(reply=_Res(
        status_code=500, text="Internal Server Error", headers={"content-type": "text/plain"},
    )))
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "notes.md"))

    assert "업로드 실패 (500): Internal Server Error" in _messages(client)[0]


def test_an_unreachable_server_is_reported_as_a_processing_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _downloads(monkeypatch, b"text")
    _server(monkeypatch, _Client(error=OSError("refused")))
    client = _Client()
    asyncio.run(bot.process_document_file(client, 42, "f1", "notes.csv"))

    assert "문서 처리 오류" in _messages(client)[0]
    assert list(tmp_path.glob("*.csv")) == [], "the staged upload must be removed"
