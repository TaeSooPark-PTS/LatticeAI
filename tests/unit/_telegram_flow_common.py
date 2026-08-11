"""Shared doubles for the wp01 Telegram coverage suites.

The Telegram tests split across two files — the conversation flows
(``ask_ai`` and everything downstream of it) and the dispatch/poll side —
but both drive the bot through the same doubles: a scripted async HTTP
client, a response stand-in, and the recorders that replace the bot's own
coroutines so a call can be observed instead of performed.

This module holds nothing that pytest collects; it is imported by
``test_cov_wp01_telegram_*.py`` so each double has exactly one definition.

v11.3.0 package split: a stub only reaches the code that reads the name when
it is installed on that code's own module. ``ask_ai`` and everything it feeds
(the approval handshake, the review centre) live in ``flows``; command and
callback routing and the poll loop live in ``dispatch``; the chat-id store and
the agent-artifact plumbing live in ``helpers``.
"""

from __future__ import annotations

from pathlib import Path

from latticeai.integrations import telegram_bot as bot
from latticeai.integrations.telegram_bot import flows

#: The script entry moved to ``__main__.py`` — ``__init__`` is never ``__main__``.
MODULE_PATH = Path(bot.__file__).parent / "__main__.py"
#: Import-time side effects (the data directory) still live in ``config``.
CONFIG_PATH = Path(bot.__file__).parent / "config.py"

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
    monkeypatch.setattr(flows, "_server_client", lambda **_kwargs: client)
    return client


def _recorder(log, name):
    async def record(*args, **kwargs):
        log.append((name, args, kwargs))

    return record


def _patch_handlers(monkeypatch, names, module=flows):
    """Replace the named coroutines with call recorders on ``module``.

    ``module`` is the submodule whose function is under test: it holds the
    globals that function resolves, so recording anywhere else would leave the
    real coroutine in place.
    """
    log = []
    for name in names:
        monkeypatch.setattr(module, name, _recorder(log, name))
    return log


def _texts(log):
    return [args[2] for name, args, _kwargs in log if name == "send_message"]


def _messages(client):
    return [
        call[2].get("json", {}).get("text", "")
        for call in client.calls
        if call[1].endswith("/sendMessage")
    ]


def _patch_ask_ai(monkeypatch, result, module=flows):
    """Stand in for ``ask_ai`` on ``module`` — ``flows`` for
    ``process_ai_request``, ``dispatch`` for the ``/agent`` command."""
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

    monkeypatch.setattr(module, "ask_ai", ask)
    return calls
