"""wpb03: Telegram surfaces on hosts and payloads the suite never produced.

Four "the probe answered, just not usefully" directions:

* the default-route socket resolving to loopback (a laptop with no LAN, or a
  container with only ``lo``), and a hostname table that lists no address at
  all — together these are the only way ``get_lan_ip`` reaches its final
  ``127.0.0.1``;
* ``vm_stat`` returning nothing to parse;
* an approval card for a plan whose goal was never filled in;
* a ``/agent/resume`` body that decodes to something other than a ``dict`` —
  the answer must still be delivered, and the attachment fan-out skipped.
"""

from __future__ import annotations

import asyncio
import socket
from collections import UserDict
from typing import Any, Dict, List

from latticeai.integrations import telegram_bot as bot

# v11.3.0 package split: the pending-plan registry and the approval flow live
# in ``flows``, so both the stubs and the reads below target that module — a
# rebind on the re-export hub would leave the real dictionary untouched.
from latticeai.integrations.telegram_bot import flows

# ── doubles ─────────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _Client:
    """Async HTTP double recording every Telegram/server call."""

    def __init__(self, reply: Any = None) -> None:
        self.calls: List[tuple] = []
        self._reply = reply

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._reply if self._reply is not None else _Res()

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._reply if self._reply is not None else _Res()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Sock:
    def __init__(self, name: str) -> None:
        self._name = name

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def connect(self, *_args):
        return None

    def getsockname(self):
        return (self._name, 0)


class _Proc:
    def __init__(self, out: bytes) -> None:
        self.out = out

    async def communicate(self):
        return self.out, b""


def _messages(client: _Client) -> List[str]:
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if str(call[1]).endswith("/sendMessage")
    ]


# ── LAN address ─────────────────────────────────────────────────────────────


def test_a_loopback_only_host_reports_loopback(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: _Sock("127.0.0.1"))
    monkeypatch.setattr(socket, "gethostname", lambda: "sandbox")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda _host: ("sandbox", [], []))

    assert bot.get_lan_ip() == "127.0.0.1"


# ── macOS memory probe ──────────────────────────────────────────────────────


def test_an_empty_vm_stat_reply_falls_back_to_the_default_page_size(monkeypatch):
    async def _create(program, *_args, **_kwargs):
        return _Proc(b"" if program == "vm_stat" else b"17179869184\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _create)

    # No page-size header and no page counters: 0 used out of 16 GiB installed.
    assert asyncio.run(bot._mac_ram_used_gb()) == "0.0 GB / 17 GB"


# ── plan approval card ──────────────────────────────────────────────────────


def test_a_plan_with_no_stated_goal_still_lists_its_steps(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {})
    client = _Client()
    data: Dict[str, Any] = {
        "run_id": "run-7",
        "plan": {"steps": [{"description": "파일 읽기"}, "요약 쓰기"]},
        "approval": {"token": "tok-7"},
        "executing_model": "local-a",
    }

    asyncio.run(bot.send_plan_for_approval(client, 4242, data))

    text = _messages(client)[0]
    assert "플래닝 완료" in text
    assert "*목표:*" not in text, "an empty goal is not rendered as an empty heading"
    assert "1. 파일 읽기" in text
    assert "2. 요약 쓰기" in text
    pending = flows._bot_pending_plans["run-7"]
    assert pending["chat_id"] == 4242
    assert pending["approval_token"] == "tok-7"
    assert pending["legacy"] is False


# ── resume callback ─────────────────────────────────────────────────────────


class _MappingBody(UserDict):
    """A JSON body that is a mapping but not a ``dict``."""


def test_a_non_dict_resume_body_still_delivers_the_answer(monkeypatch):
    monkeypatch.setattr(flows, "_bot_pending_plans", {
        "run-9": {
            "chat_id": 77,
            "run_id": "run-9",
            "context_id": None,
            "approval_token": "tok-9",
            "legacy": False,
            "executing_model": None,
            "reviewing_model": None,
        },
    })
    server = _Client(reply=_Res(payload=_MappingBody({"response": "작업을 마쳤습니다."})))
    monkeypatch.setattr(flows, "_server_client", lambda **_kwargs: server)

    def _forbidden(*_args: Any, **_kwargs: Any):
        raise AssertionError("the attachment fan-out needs a dict payload")

    monkeypatch.setattr(flows, "send_run_explanation", _forbidden)
    monkeypatch.setattr(flows, "send_artifact_card", _forbidden)

    telegram = _Client()
    asyncio.run(bot.handle_plan_callback(telegram, 77, "plan:approve:run-9"))

    assert _messages(telegram) == ["⚙️ 실행 중입니다. 잠시 기다려주세요...", "작업을 마쳤습니다."]
    assert flows._bot_pending_plans == {}, "the approved plan is consumed"
    resumed = [call for call in server.calls if str(call[1]).endswith("/agent/resume")]
    assert len(resumed) == 1
    assert resumed[0][2]["json"]["approved"] is True
