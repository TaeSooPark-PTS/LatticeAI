"""wpb03: the "caller already decided" branches of the extracted app-factory runtimes.

Every helper in :mod:`latticeai.runtime.history_runtime` and
:mod:`latticeai.runtime.user_key_runtime` resolves a scope *only when the caller
left it unset*.  The existing suite always calls them the lazy way, so the
"caller passed an explicit scope" direction — the one the HTTP layer actually
uses when it has already resolved the workspace set — was never executed.  The
same holds for :mod:`latticeai.runtime.lifespan_runtime`: shutdown with no local
knowledge watcher and with an already-exited local server process is the normal
production path on a machine that never connected a folder.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from latticeai.runtime.history_runtime import build_history_query_runtime
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.runtime.user_key_runtime import build_user_key_runtime


class _Conversations:
    """Durable conversation store stand-in that records its scope arguments."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def history(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append({"call": "history", **kwargs})
        return [{"role": "user", "content": "안녕"}]

    def clear_all(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"call": "clear_all", **kwargs})
        return {"removed": 3, "kept": 0}

    def clear_conversation(self, conversation_id: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"call": "clear_conversation", "id": conversation_id, **kwargs})
        return {"removed": 1, "kept": 2}


class _WorkspaceService:
    def __init__(self) -> None:
        self.asked: List[str] = []

    def readable_workspaces(self, user_email: str):
        self.asked.append(user_email)
        return ["org:resolved"]


class _Logging:
    def __init__(self) -> None:
        self.warnings: List[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message % args if args else message)


def _history_runtime(**overrides: Any):
    conversations = overrides.pop("conversations", None) or _Conversations()
    workspace_service = overrides.pop("workspace_service", None) or _WorkspaceService()
    logs = overrides.pop("logging", None) or _Logging()
    runtime = build_history_query_runtime(
        conversations=conversations,
        workspace_service=workspace_service,
        require_auth=overrides.pop("require_auth", True),
        logging=logs,
    )
    return runtime, conversations, workspace_service


# ── history_runtime: explicit scopes bypass resolution ──────────────────────


def test_a_caller_supplied_legacy_flag_is_passed_through_untouched():
    runtime, conversations, workspaces = _history_runtime()

    entries = runtime["get_history"](
        user_email="owner@example.com",
        allowed_workspaces={"org:caller"},
        include_legacy_global=True,
    )

    assert entries == [{"role": "user", "content": "안녕"}]
    assert conversations.calls == [{
        "call": "history",
        "user_email": "owner@example.com",
        "allowed_workspaces": {"org:caller"},
        "include_legacy_global": True,
    }]
    # Nothing was re-resolved: the caller's scope wins outright.
    assert workspaces.asked == []


def test_clear_history_never_re_resolves_a_scope_the_caller_already_pinned():
    runtime, conversations, workspaces = _history_runtime()

    result = runtime["clear_history"](
        keep_last=2,
        user_email="owner@example.com",
        allowed_workspaces={"org:caller"},
        include_legacy_global=False,
    )

    assert result == {"removed": 3, "kept": 0}
    assert conversations.calls[0]["allowed_workspaces"] == {"org:caller"}
    assert conversations.calls[0]["include_legacy_global"] is False
    assert conversations.calls[0]["keep_last"] == 2
    assert workspaces.asked == []


def test_clear_conversation_never_re_resolves_a_scope_the_caller_already_pinned():
    runtime, conversations, workspaces = _history_runtime()

    result = runtime["clear_conversation"](
        "conv-9",
        started_at="2026-08-01T00:00:00Z",
        user_email="owner@example.com",
        allowed_workspaces={"org:caller"},
        include_legacy_global=False,
    )

    assert result == {"removed": 1, "kept": 2}
    assert conversations.calls[0]["id"] == "conv-9"
    assert conversations.calls[0]["started_at"] == "2026-08-01T00:00:00Z"
    assert conversations.calls[0]["allowed_workspaces"] == {"org:caller"}
    assert conversations.calls[0]["include_legacy_global"] is False
    assert workspaces.asked == []


# ── user_key_runtime ────────────────────────────────────────────────────────


class _Keyring:
    """OS keyring stand-in with per-test scripted answers."""

    def __init__(self, *, stored: Optional[Dict[str, str]] = None) -> None:
        self.stored: Dict[str, str] = dict(stored or {})
        self.reads: List[str] = []
        self.writes: List[tuple] = []

    def get_password(self, service: str, key: str) -> Optional[str]:
        self.reads.append(key)
        return self.stored.get(key)

    def set_password(self, service: str, key: str, value: str) -> None:
        self.writes.append((service, key, value))
        self.stored[key] = value


def _key_runtime(users: Dict[str, Any], *, keyring: Any, allow_plaintext: bool = True):
    saved: List[Dict[str, Any]] = []
    identities: List[str] = []

    def _http_exception(*_args: Any, **kwargs: Any) -> Exception:
        return RuntimeError(kwargs.get("detail", ""))

    runtime = build_user_key_runtime(
        load_users=lambda: users,
        save_users=lambda payload: saved.append(payload),
        ensure_user_identity=lambda email, _user: identities.append(email),
        keyring=keyring,
        allow_plaintext_api_keys=allow_plaintext,
        logging=_Logging(),
        http_exception=_http_exception,
    )
    return runtime, saved, identities


def test_an_empty_keyring_slot_falls_through_to_the_stored_plaintext_key():
    keyring = _Keyring(stored={"owner@example.com:openai": ""})
    users = {"owner@example.com": {"api_keys": {"openai": "  sk-plain  "}}}
    runtime, _saved, _ids = _key_runtime(users, keyring=keyring)

    assert runtime["get_user_api_key"]("owner@example.com", "openai") == "sk-plain"
    assert keyring.reads == ["owner@example.com:openai"]


def test_moving_a_key_into_the_keyring_leaves_the_users_other_providers_alone():
    keyring = _Keyring()
    users = {
        "owner@example.com": {"api_keys": {"openai": "sk-old", "groq": "gsk-keep"}},
    }
    runtime, saved, _ids = _key_runtime(users, keyring=keyring)

    runtime["set_user_api_key"]("owner@example.com", "openai", "sk-new")

    assert keyring.writes == [("LatticeAI", "owner@example.com:openai", "sk-new")]
    # The migrated provider is dropped from the file; the rest survives, so the
    # ``api_keys`` map is rewritten rather than removed.
    assert users["owner@example.com"]["api_keys"] == {"groq": "gsk-keep"}
    assert saved == [users]


def test_a_user_with_no_plaintext_keys_needs_no_file_rewrite_after_a_keyring_write():
    keyring = _Keyring()
    users: Dict[str, Any] = {"owner@example.com": {"nickname": "Owner"}}
    runtime, saved, _ids = _key_runtime(users, keyring=keyring)

    runtime["set_user_api_key"]("owner@example.com", "openai", "sk-new")

    assert keyring.writes == [("LatticeAI", "owner@example.com:openai", "sk-new")]
    assert saved == [], "nothing had to be scrubbed from users.json"
    assert "api_keys" not in users["owner@example.com"]


def test_plaintext_fallback_updates_an_existing_user_instead_of_inventing_one():
    users: Dict[str, Any] = {
        "owner@example.com": {"nickname": "Owner", "role": "admin", "id": "user:1"},
    }
    runtime, saved, identities = _key_runtime(users, keyring=None, allow_plaintext=True)

    runtime["set_user_api_key"]("owner@example.com", "groq", "gsk-1")

    assert users["owner@example.com"]["api_keys"] == {"groq": "gsk-1"}
    # The existing record kept its role — no default skeleton overwrote it.
    assert users["owner@example.com"]["role"] == "admin"
    assert identities == ["owner@example.com"]
    assert saved == [users]


# ── lifespan_runtime ────────────────────────────────────────────────────────


class _LifespanRouter:
    def __init__(self, *, idle: Optional[List[str]] = None) -> None:
        self.idle = list(idle or [])
        self.idle_calls: List[int] = []
        self.unloaded_all = 0

    def unload_idle_models(self, seconds: int) -> List[str]:
        self.idle_calls.append(seconds)
        return list(self.idle)

    def unload_all(self) -> None:
        self.unloaded_all += 1


class _Proc:
    def __init__(self, poll_value: Optional[int]) -> None:
        self._poll = poll_value
        self.terminated = False

    def poll(self) -> Optional[int]:
        return self._poll

    def terminate(self) -> None:  # pragma: no cover - asserted never to run
        self.terminated = True

    def wait(self, timeout: Optional[float] = None) -> int:  # pragma: no cover
        return 0


def _lifespan_runtime(**overrides: Any) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "app_mode": "local",
        "enable_telegram": False,
        "autoload_models": False,
        "is_public_mode": False,
        "public_model": "",
        "allow_local_models": True,
        "local_model": "org/model",
        "local_draft_model": "",
        "model_idle_unload_seconds": 0,
        "model_router": _LifespanRouter(),
        "local_kg_watcher": None,
        "local_server_processes": {},
        "logger": _Logging(),
    }
    fields.update(overrides)
    return build_lifespan_runtime(**fields)


class _StopLoop(Exception):
    """Ends the otherwise-infinite idle sweep on its second tick."""


def test_an_idle_sweep_that_frees_nothing_goes_straight_back_to_waiting(monkeypatch):
    ticks: List[float] = []

    async def _fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) > 1:
            raise _StopLoop

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    router = _LifespanRouter(idle=[])
    runtime = _lifespan_runtime(model_idle_unload_seconds=900, model_router=router)

    with pytest.raises(_StopLoop):
        asyncio.run(runtime["unload_idle_models_loop"]())

    # One full pass with nothing to unload, then the loop waited again.
    assert router.idle_calls == [900]
    assert ticks == [60, 60], "the sweep is capped at a minute between passes"


def test_shutdown_without_a_watcher_leaves_an_already_exited_server_alone(capsys):
    router = _LifespanRouter()
    proc = _Proc(poll_value=0)
    runtime = _lifespan_runtime(
        model_router=router,
        local_kg_watcher=None,
        local_server_processes={"vllm": proc},
    )

    async def _drive() -> None:
        async with runtime["lifespan"](None):
            # Let the fire-and-forget startup tasks finish before shutdown.
            await asyncio.sleep(0)

    asyncio.run(_drive())

    assert router.unloaded_all == 1
    assert proc.terminated is False, "a process that already exited is not terminated"
    out = capsys.readouterr().out
    assert "Telegram Bot Bridge disabled" in out
    assert "Local knowledge watchers restored" not in out
