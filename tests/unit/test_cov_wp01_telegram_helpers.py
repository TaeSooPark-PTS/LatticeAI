"""wp01 coverage: Telegram bridge helpers — outbound framing, host discovery,
the web→telegram mirror, file download, workspace resolution and the card
formatters.

Everything runs against doubles: no socket is opened, no subprocess is spawned
and nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import base64
import socket
import tempfile
import zipfile

import pytest

from latticeai.integrations import telegram_bot as bot

# ── doubles ───────────────────────────────────────────────────────────────


class _Res:
    """Minimal stand-in for an ``httpx.Response``."""

    def __init__(self, status_code=200, payload=None, text="", headers=None,
                 content=b"", json_error=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.content = content
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

    def __init__(self, stdout=b"", error=None):
        self.stdout_bytes = stdout
        self.error = error

    async def communicate(self):
        if self.error is not None:
            raise self.error
        return self.stdout_bytes, b""


def _messages(client):
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if call[1].endswith("/sendMessage")
    ]


# ── chat-id registry write failures ───────────────────────────────────────


def test_saving_chat_ids_logs_a_write_failure_instead_of_raising(tmp_path, monkeypatch, caplog):
    """The bot must survive an unwritable data dir: registration is best-effort."""
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "chats.json")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bot, "atomic_write_json", boom)

    with caplog.at_level("ERROR"):
        bot.save_chat_ids({1, 2})

    assert not (tmp_path / "chats.json").exists()
    assert any("저장 실패" in record.getMessage() for record in caplog.records)


# ── send_message ──────────────────────────────────────────────────────────


def test_send_message_splits_long_text_and_attaches_markup_to_the_last_chunk():
    client = _Client()
    markup = {"inline_keyboard": [[{"text": "확인", "callback_data": "ok"}]]}

    asyncio.run(bot.send_message(client, 42, "가" * 8000, reply_markup=markup))

    assert len(client.calls) == 3
    assert "reply_markup" not in client.calls[0][2]["json"]
    assert client.calls[-1][2]["json"]["reply_markup"] == markup


def test_send_message_swallows_a_transport_failure(caplog):
    client = _Client(error=OSError("connection reset"))

    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_message(client, 42, "안녕"))

    assert any("메시지 전송 실패" in record.getMessage() for record in caplog.records)


# ── send_photo ────────────────────────────────────────────────────────────


def test_send_photo_uploads_the_bytes_it_read(tmp_path):
    shot = tmp_path / "screen.jpg"
    shot.write_bytes(b"jpeg-bytes")
    client = _Client()

    asyncio.run(bot.send_photo(client, 42, shot, caption="현재 화면입니다."))

    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert method == "post"
    assert url.endswith("/sendPhoto")
    assert kwargs["files"]["photo"] == ("screen.jpg", b"jpeg-bytes")
    assert kwargs["data"]["chat_id"] == "42"


def test_send_photo_reports_a_rejected_upload_to_the_chat(tmp_path):
    shot = tmp_path / "screen.jpg"
    shot.write_bytes(b"jpeg")

    def reply(_method, url, _kwargs):
        return _Res(status_code=413) if url.endswith("/sendPhoto") else _Res()

    client = _Client(reply=reply)
    asyncio.run(bot.send_photo(client, 42, shot))

    assert any("사진 전송 실패 (413)" in text for text in _messages(client))


def test_send_photo_reports_a_read_failure_to_the_chat(tmp_path):
    missing = tmp_path / "gone.jpg"
    client = _Client()

    asyncio.run(bot.send_photo(client, 42, missing))

    assert any("사진 전송 오류" in text for text in _messages(client))


# ── send_document ─────────────────────────────────────────────────────────


def test_send_document_uploads_under_the_requested_filename(tmp_path):
    doc = tmp_path / "report.txt"
    doc.write_bytes(b"body")
    client = _Client()

    asyncio.run(bot.send_document(client, 42, doc, caption="생성 파일", filename="out.txt"))

    method, url, kwargs = client.calls[0]
    assert method == "post"
    assert url.endswith("/sendDocument")
    assert kwargs["files"]["document"] == ("out.txt", b"body")
    assert kwargs["data"]["caption"] == "생성 파일"


def test_send_document_logs_a_rejected_upload(tmp_path, caplog):
    doc = tmp_path / "report.txt"
    doc.write_bytes(b"body")
    client = _Client(reply=_Res(status_code=400, text="Bad Request"))

    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_document(client, 42, doc))

    assert any("파일 전송 실패" in record.getMessage() for record in caplog.records)


def test_send_document_logs_a_missing_source_file(tmp_path, caplog):
    with caplog.at_level("ERROR"):
        asyncio.run(bot.send_document(_Client(), 42, tmp_path / "absent.bin"))

    assert any("파일 전송 실패" in record.getMessage() for record in caplog.records)


# ── the small Telegram API wrappers ───────────────────────────────────────


def test_chat_action_failure_is_suppressed():
    asyncio.run(bot.send_chat_action(_Client(error=OSError("reset")), 42))


def test_callback_answer_failure_is_suppressed():
    asyncio.run(bot.answer_callback(_Client(error=OSError("reset")), "cbq-1"))


def test_edit_message_forwards_markup():
    client = _Client()
    markup = {"inline_keyboard": [[{"text": "↩", "callback_data": "cmd:menu"}]]}

    asyncio.run(bot.edit_message(client, 42, 7, "수정됨", reply_markup=markup))

    _method, url, kwargs = client.calls[0]
    assert url.endswith("/editMessageText")
    assert kwargs["json"] == {
        "chat_id": 42, "message_id": 7, "text": "수정됨", "reply_markup": markup,
    }


def test_edit_message_failure_is_suppressed():
    asyncio.run(bot.edit_message(_Client(error=OSError("reset")), 42, 7, "수정됨"))


# ── LAN address discovery ─────────────────────────────────────────────────


class _Sock:
    def __init__(self, name=None, error=None):
        self._name = name
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def connect(self, *_args):
        if self._error is not None:
            raise self._error

    def getsockname(self):
        return (self._name, 0)


def test_lan_ip_prefers_the_default_route_address(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock(name="10.0.0.5"))
    assert bot.get_lan_ip() == "10.0.0.5"


def test_lan_ip_falls_back_to_the_hostname_table(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock(error=OSError("no route")))
    monkeypatch.setattr(socket, "gethostname", lambda: "mac.local")
    monkeypatch.setattr(
        socket, "gethostbyname_ex",
        lambda _host: ("mac.local", [], ["127.0.0.1", "192.168.1.50"]),
    )
    assert bot.get_lan_ip() == "192.168.1.50"


def test_lan_ip_gives_loopback_when_every_probe_fails(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock(error=OSError("no route")))
    monkeypatch.setattr(socket, "gethostname", lambda: "mac.local")

    def boom(_host):
        raise OSError("dns down")

    monkeypatch.setattr(socket, "gethostbyname_ex", boom)
    assert bot.get_lan_ip() == "127.0.0.1"


def test_a_configured_public_url_wins_over_the_lan_address(monkeypatch):
    monkeypatch.setattr(bot, "PUBLIC_WEB_URL", "https://tunnel.example/")

    def unreachable():
        raise AssertionError("the LAN probe must not run when a public URL is set")

    monkeypatch.setattr(bot, "get_lan_ip", unreachable)

    assert bot.get_web_url() == "https://tunnel.example"
    assert bot.get_graph_url() == "https://tunnel.example/graph"


# ── web → telegram mirror ─────────────────────────────────────────────────


def test_broadcast_is_skipped_without_a_bot_token(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN", "")

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a tokenless bot must not mirror web chat")

    monkeypatch.setattr(bot, "send_message", unreachable)
    asyncio.run(bot.broadcast_web_chat("user", "안녕"))


def test_broadcast_is_skipped_when_no_registered_chat_is_still_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "TOKEN", "telegram-token")
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "chats.json")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42")
    bot.save_chat_ids({999})  # a stale registration, no longer on the allowlist

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("a de-listed chat must not receive the mirror")

    monkeypatch.setattr(bot, "send_message", unreachable)
    asyncio.run(bot.broadcast_web_chat("assistant", "답변"))


@pytest.mark.parametrize(("role", "label"), [("user", "사용자"), ("assistant", "Lattice AI")])
def test_broadcast_mirrors_to_every_still_allowed_chat(tmp_path, monkeypatch, role, label):
    monkeypatch.setattr(bot, "TOKEN", "telegram-token")
    monkeypatch.setattr(bot, "CHAT_IDS_FILE", tmp_path / "chats.json")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS", "42,43")
    bot.save_chat_ids({42, 999})

    sent = []

    async def capture(_client, chat_id, text, **_kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(bot, "send_message", capture)
    asyncio.run(bot.broadcast_web_chat(role, "본문"))

    assert sent == [(42, f"[Web] {label}\n본문")]


# ── file download ─────────────────────────────────────────────────────────


def test_downloading_a_file_returns_its_bytes():
    def reply(_method, url, _kwargs):
        if "getFile" in url:
            return _Res(payload={"result": {"file_path": "documents/a.pdf"}})
        return _Res(content=b"%PDF-1.4")

    client = _Client(reply=reply)
    assert asyncio.run(bot.download_telegram_file(client, "file-1")) == b"%PDF-1.4"


def test_downloading_a_file_returns_none_on_a_failed_fetch():
    def reply(_method, url, _kwargs):
        if "getFile" in url:
            return _Res(payload={"result": {"file_path": "documents/a.pdf"}})
        return _Res(status_code=404)

    assert asyncio.run(bot.download_telegram_file(_Client(reply=reply), "file-1")) is None


def test_downloading_a_file_returns_none_when_telegram_errors(caplog):
    client = _Client(error=OSError("connection reset"))
    with caplog.at_level("ERROR"):
        assert asyncio.run(bot.download_telegram_file(client, "file-1")) is None
    assert any("파일 다운로드 실패" in record.getMessage() for record in caplog.records)


def test_base64_download_encodes_the_payload_and_passes_through_failure(monkeypatch):
    async def one(_client, _file_id):
        return b"\x00\x01binary"

    monkeypatch.setattr(bot, "download_telegram_file", one)
    assert asyncio.run(bot.download_as_base64(_Client(), "f")) == base64.b64encode(
        b"\x00\x01binary"
    ).decode()

    async def none(_client, _file_id):
        return None

    monkeypatch.setattr(bot, "download_telegram_file", none)
    assert asyncio.run(bot.download_as_base64(_Client(), "f")) is None


# ── menu ──────────────────────────────────────────────────────────────────


def test_show_menu_sends_the_inline_keyboard():
    client = _Client()
    asyncio.run(bot.show_menu(client, 42))

    payload = client.calls[0][2]["json"]
    assert payload["reply_markup"] == bot.MAIN_MENU
    assert "원격 제어 메뉴" in payload["text"]


# ── macOS memory probe ────────────────────────────────────────────────────


VM_STAT_OUTPUT = (
    b"Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    b"Pages active:                    100.\n"
    b"Pages wired down:                 50.\n"
    b"Pages bogus:                     n/a.\n"
    b"a line with no colon\n"
)


def _fake_exec(monkeypatch, vm_output=VM_STAT_OUTPUT, mem_output=b"17179869184\n", error=None):
    async def create(program, *_args, **_kwargs):
        if error is not None:
            raise error
        if program == "vm_stat":
            return _Proc(vm_output)
        return _Proc(mem_output)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)


def test_ram_probe_parses_the_page_size_from_the_header(monkeypatch):
    _fake_exec(monkeypatch)
    # 150 pages × 16 KiB used, 16 GiB installed.
    assert asyncio.run(bot._mac_ram_used_gb()) == "0.0 GB / 17 GB"


def test_ram_probe_defaults_the_page_size_when_the_header_is_absent(monkeypatch):
    _fake_exec(
        monkeypatch,
        vm_output=b"Mach Virtual Memory Statistics:\nPages active: 1000000.\n",
    )
    assert asyncio.run(bot._mac_ram_used_gb()) == "4.1 GB / 17 GB"


def test_ram_probe_reports_na_when_the_tools_are_missing(monkeypatch):
    _fake_exec(monkeypatch, error=FileNotFoundError("vm_stat"))
    assert asyncio.run(bot._mac_ram_used_gb()) == "N/A"


# ── workspace file resolution ─────────────────────────────────────────────


def test_workspace_resolution_refuses_paths_outside_the_workspace(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")

    assert bot.resolve_workspace_file("../secret.txt") is None


def test_workspace_resolution_refuses_non_files(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    (workspace / "sub").mkdir(parents=True)
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)

    assert bot.resolve_workspace_file("absent.txt") is None
    assert bot.resolve_workspace_file("sub") is None


def test_workspace_resolution_refuses_a_file_telegram_cannot_carry(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(bot, "MAX_TELEGRAM_FILE_BYTES", 4)
    (workspace / "big.bin").write_bytes(b"0123456789")

    assert bot.resolve_workspace_file("big.bin") is None


def test_workspace_resolution_returns_a_sendable_file(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)
    (workspace / "out.txt").write_text("hi", encoding="utf-8")

    assert bot.resolve_workspace_file("out.txt") == workspace / "out.txt"


def test_generated_files_are_collected_once_from_producing_steps(tmp_path, monkeypatch):
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    monkeypatch.setattr(bot, "AGENT_WORKSPACE", workspace)
    (workspace / "out.txt").write_text("hi", encoding="utf-8")

    files = bot.collect_generated_files({
        "steps": [
            {"action": "read_file", "result": {"path": "out.txt"}},
            {"action": "write_file", "args": {}},
            {"action": "write_file", "result": {"path": "out.txt"}},
            {"action": "create_docx", "args": {"path": "out.txt"}},
            {"action": "create_pdf", "args": {"path": "missing.pdf"}},
        ]
    })

    assert files == [("out.txt", workspace / "out.txt")]


# ── card formatters ───────────────────────────────────────────────────────


def test_artifact_card_skips_rows_that_are_not_objects():
    text = bot.format_artifact_card({"artifacts": ["junk", 3, {"path": "a.txt"}]})
    assert text.count("• ") == 1
    assert "a.txt" in text


def test_artifact_card_is_sent_only_when_there_is_one():
    client = _Client()
    asyncio.run(bot.send_artifact_card(client, 42, {"artifacts": [{"path": "a.txt"}]}))
    assert any("a.txt" in text for text in _messages(client))

    quiet = _Client()
    asyncio.run(bot.send_artifact_card(quiet, 42, {}))
    assert quiet.calls == []


def test_grounding_badge_is_sent_only_when_the_server_issued_a_verdict():
    client = _Client()
    asyncio.run(bot.send_grounding_badge(client, 42, {"grounding": {"status": "supported"}}))
    assert any("근거 있음" in text for text in _messages(client))

    quiet = _Client()
    asyncio.run(bot.send_grounding_badge(quiet, 42, {}))
    assert quiet.calls == []


def test_run_explanation_is_sent_only_when_it_adds_something():
    client = _Client()
    asyncio.run(bot.send_run_explanation(client, 42, {
        "explanation": {
            "ok": False,
            "headline": {"ko": "완료로 처리하지 않았습니다.", "en": "Not complete."},
            "details": [{"ko": "형식을 2번 틀렸습니다.", "en": "Format broke twice."}],
        }
    }))
    assert any("완료로 처리하지 않았습니다." in text for text in _messages(client))

    quiet = _Client()
    asyncio.run(bot.send_run_explanation(quiet, 42, {}))
    assert quiet.calls == []


# ── preview links ─────────────────────────────────────────────────────────


def test_preview_urls_are_rewritten_to_the_lan_address_once(monkeypatch):
    monkeypatch.setattr(bot, "get_lan_ip", lambda: "192.168.0.7")
    monkeypatch.setattr(bot, "SERVER_PORT", 4825)

    urls = bot.collect_preview_urls({
        "steps": [
            {"action": "write_file", "result": {"local_url": "http://127.0.0.1:4825/x"}},
            {"action": "preview_url", "result": {}},
            {"action": "preview_url",
             "result": {"local_url": "http://127.0.0.1:4825/p/a.html", "path": "a.html"}},
            {"action": "preview_url",
             "result": {"local_url": "http://127.0.0.1:4825/p/a.html", "path": "a.html"}},
        ]
    })

    assert urls == [("a.html", "http://192.168.0.7:4825/p/a.html")]


def test_no_preview_links_means_no_message():
    client = _Client()
    asyncio.run(bot.send_preview_links(client, 42, []))
    assert client.calls == []


def test_preview_links_are_sent_as_buttons():
    client = _Client()
    asyncio.run(bot.send_preview_links(client, 42, [("a.html", "http://192.168.0.7:4825/p/a.html")]))

    payload = client.calls[0][2]["json"]
    assert "a.html: http://192.168.0.7:4825/p/a.html" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("/p/a.html")


# ── generated file delivery ───────────────────────────────────────────────


def test_no_generated_files_means_no_upload(monkeypatch):
    async def unreachable(*_args, **_kwargs):
        raise AssertionError("nothing was produced, so nothing may be uploaded")

    monkeypatch.setattr(bot, "send_document", unreachable)
    asyncio.run(bot.send_generated_files(_Client(), 42, []))


def test_a_single_generated_file_is_sent_as_itself(tmp_path, monkeypatch):
    produced = tmp_path / "out.txt"
    produced.write_text("hi", encoding="utf-8")
    sent = []

    async def capture(_client, chat_id, path, caption=None, filename=None):
        sent.append((chat_id, path, caption, filename))

    monkeypatch.setattr(bot, "send_document", capture)
    asyncio.run(bot.send_generated_files(_Client(), 42, [("out.txt", produced)]))

    assert sent == [(42, produced, "생성 파일: out.txt", None)]


def test_several_generated_files_are_zipped_under_their_workspace_names(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first = tmp_path / "a.txt"
    first.write_text("a", encoding="utf-8")
    second = tmp_path / "b.txt"
    second.write_text("b", encoding="utf-8")
    names = []

    async def capture(_client, _chat_id, path, caption=None, filename=None):
        with zipfile.ZipFile(path) as archive:
            names.extend(archive.namelist())
        names.append(filename)

    monkeypatch.setattr(bot, "send_document", capture)
    asyncio.run(bot.send_generated_files(
        _Client(), 42, [("docs/a.txt", first), ("docs/b.txt", second)]
    ))

    assert names == ["docs/a.txt", "docs/b.txt", "ltcai-files.zip"]
    assert list(tmp_path.glob("ltcai-*.zip")) == [], "the staged archive must be removed"


def test_an_oversized_archive_is_reported_rather_than_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(bot, "MAX_TELEGRAM_FILE_BYTES", 1)
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    async def unreachable(*_args, **_kwargs):
        raise AssertionError("an archive over the Telegram limit must not be uploaded")

    monkeypatch.setattr(bot, "send_document", unreachable)
    client = _Client()
    asyncio.run(bot.send_generated_files(client, 42, [
        ("a.txt", tmp_path / "a.txt"), ("b.txt", tmp_path / "b.txt"),
    ]))

    assert any("너무 커서" in text for text in _messages(client))


# ── approval pause identity ───────────────────────────────────────────────


def test_pause_id_prefers_the_run_id_and_falls_back_to_the_legacy_context():
    assert bot._approval_pause_id({"run_id": "r1", "context_id": "c1"}) == "r1"
    assert bot._approval_pause_id({"context_id": "c1"}) == "c1"
    assert bot._approval_pause_id({}) == ""


def test_both_approval_wire_contracts_are_recognised_as_a_pause():
    assert bot._is_approval_pause({"status": "waiting_approval"}) is True
    assert bot._is_approval_pause({"status": "awaiting_approval"}) is True
    assert bot._is_approval_pause({"status": "done"}) is False
    assert bot._is_approval_pause({}) is False
