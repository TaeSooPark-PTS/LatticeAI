from __future__ import annotations

import sys
import types

import ltcai_cli


def test_cli_telegram_token_presence_does_not_start_notification(monkeypatch):
    calls = []

    class FakeThread:
        def start(self):
            calls.append("started")

    def fake_thread(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return FakeThread()

    monkeypatch.setenv("LATTICEAI_TUNNEL", "false")
    monkeypatch.setenv("LATTICEAI_ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_BOT_TOKEN", "present")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_CHAT_ID", "present")
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--host", "127.0.0.1", "--port", "8999"])
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *args, **kwargs: None))
    monkeypatch.setattr(ltcai_cli.threading, "Thread", fake_thread)

    ltcai_cli.main()

    assert calls == []
