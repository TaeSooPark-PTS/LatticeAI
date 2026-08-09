"""Coverage for the app-factory runtime seams: history, SSO config, lifespan,
audit and context assembly.

Each seam is a closure factory: build it with fakes, then drive the returned
callables and assert what they actually did (payloads, cache invalidation,
scope kwargs, spawned-task bookkeeping) — not merely that they ran.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import types

import pytest

from latticeai.runtime.audit_runtime import build_audit_runtime
from latticeai.runtime.context_runtime import build_context_runtime
from latticeai.runtime.history_runtime import build_history_query_runtime
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.runtime.sso_config_runtime import build_sso_config_runtime


class _Logging:
    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *args):
        self.warnings.append(fmt % args if args else fmt)


class _Conversations:
    def __init__(self, history=None):
        self._history = list(history or [])
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(("history", kwargs))
        return list(self._history)

    def clear_all(self, **kwargs):
        self.calls.append(("clear_all", kwargs))
        return {"cleared": len(self._history)}

    def clear_conversation(self, conversation_id, **kwargs):
        self.calls.append(("clear_conversation", conversation_id, kwargs))
        return {"cleared": 1, "conversation_id": conversation_id}


class _WorkspaceService:
    def __init__(self, workspaces=("ws-1",), boom=False):
        self._workspaces = list(workspaces)
        self._boom = boom

    def readable_workspaces(self, email):
        if self._boom:
            raise RuntimeError("membership backend down")
        return [f"{item}:{email}" for item in self._workspaces]


# ── history_runtime ────────────────────────────────────────────────────────


def _history_runtime(history=None, *, require_auth=True, workspace_service=None, logging=None):
    conversations = _Conversations(history)
    log = logging or _Logging()
    runtime = build_history_query_runtime(
        conversations=conversations,
        workspace_service=workspace_service or _WorkspaceService(),
        require_auth=require_auth,
        logging=log,
    )
    return runtime, conversations, log


def test_history_scope_resolution_is_open_without_auth_and_fails_closed_on_error():
    open_runtime, _conv, _log = _history_runtime(require_auth=False)
    assert open_runtime["_history_allowed_workspaces_for"]("owner@example.com") is None
    assert open_runtime["_history_include_legacy_global"]("owner@example.com") is True

    scoped, _conv2, _log2 = _history_runtime()
    assert scoped["_history_allowed_workspaces_for"](None) is None
    assert scoped["_history_allowed_workspaces_for"]("owner@example.com") == {"ws-1:owner@example.com"}
    assert scoped["_history_include_legacy_global"]("owner@example.com") is False
    assert scoped["_history_include_legacy_global"](None) is True

    log = _Logging()
    broken, _conv3, _log3 = _history_runtime(
        workspace_service=_WorkspaceService(boom=True), logging=log
    )
    # Fail closed: an unresolvable membership set is the empty set, never None.
    assert broken["_history_allowed_workspaces_for"]("owner@example.com") == set()
    assert "membership backend down" in log.warnings[0]


def test_get_history_resolves_scope_then_degrades_to_empty_on_store_failure():
    runtime, conversations, _log = _history_runtime([{"content": "hi"}])

    assert runtime["get_history"]("owner@example.com") == [{"content": "hi"}]
    assert conversations.calls[0][1] == {
        "user_email": "owner@example.com",
        "allowed_workspaces": {"ws-1:owner@example.com"},
        "include_legacy_global": False,
    }

    log = _Logging()
    broken = build_history_query_runtime(
        conversations=types.SimpleNamespace(
            history=lambda **_kw: (_ for _ in ()).throw(RuntimeError("store gone"))
        ),
        workspace_service=_WorkspaceService(),
        require_auth=False,
        logging=log,
    )
    assert broken["get_history"]() == []
    assert "store gone" in log.warnings[0]


def test_conversation_title_collapses_whitespace_and_defaults():
    runtime, _conv, _log = _history_runtime()
    title = runtime["conversation_title"]
    assert title({"content": "  hello\n\n  world  "}) == "hello world"
    assert title({"content": ""}) == "새 대화"
    assert len(title({"content": "x" * 200})) == 48


def test_group_history_conversations_buckets_legacy_and_upgrades_titles():
    history = [
        {"content": "orphan", "timestamp": "2026-01-01T00:00:00", "role": "user"},
        {"conversation_id": "c1", "content": "", "timestamp": "2026-01-02T00:00:00", "role": "assistant", "source": "web"},
        {"conversation_id": "c1", "content": "real question", "timestamp": "2026-01-03T00:00:00", "role": "user"},
    ]
    runtime, _conv, _log = _history_runtime(history, require_auth=False)

    grouped = runtime["group_history_conversations"]()
    by_id = {item["id"]: item for item in grouped}

    assert by_id["legacy-previous-history"]["title"] == "이전 대화 기록"
    assert by_id["legacy-previous-history"]["message_count"] == 1
    assert by_id["c1"]["message_count"] == 2
    assert by_id["c1"]["title"] == "real question"
    assert by_id["c1"]["source"] == "web"
    # newest updated_at first
    assert [item["id"] for item in grouped] == ["c1", "legacy-previous-history"]

    # An explicit history argument bypasses the store entirely.
    assert runtime["group_history_conversations"]([]) == []


def test_get_conversation_messages_separates_legacy_from_identified():
    history = [
        {"content": "orphan"},
        {"conversation_id": "c1", "content": "a"},
        {"conversation_id": "c2", "content": "b"},
    ]
    runtime, _conv, _log = _history_runtime(history, require_auth=False)

    assert runtime["get_conversation_messages"]("legacy-previous-history") == [{"content": "orphan"}]
    assert runtime["get_conversation_messages"]("c1") == [{"conversation_id": "c1", "content": "a"}]


def test_clear_helpers_forward_resolved_scope_to_the_store():
    runtime, conversations, _log = _history_runtime([{"content": "x"}])

    assert runtime["clear_history"](keep_last=2, user_email="owner@example.com") == {"cleared": 1}
    kind, kwargs = conversations.calls[-1]
    assert kind == "clear_all"
    assert kwargs == {
        "keep_last": 2,
        "user_email": "owner@example.com",
        "allowed_workspaces": {"ws-1:owner@example.com"},
        "include_legacy_global": False,
    }

    result = runtime["clear_conversation"]("c1", "2026-01-01", user_email="owner@example.com")
    assert result == {"cleared": 1, "conversation_id": "c1"}
    kind, conversation_id, kwargs = conversations.calls[-1]
    assert (kind, conversation_id) == ("clear_conversation", "c1")
    assert kwargs["started_at"] == "2026-01-01"
    assert kwargs["allowed_workspaces"] == {"ws-1:owner@example.com"}
    assert kwargs["include_legacy_global"] is False


# ── sso_config_runtime ─────────────────────────────────────────────────────


def _sso_runtime(tmp_path, *, logging=None, **overrides):
    kwargs = {
        "sso_file": tmp_path / "sso_config.json",
        "discovery_url": "",
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://localhost:4825/auth/sso/callback",
        "provider_name": "SSO",
        "logging": logging or _Logging(),
    }
    kwargs.update(overrides)
    return build_sso_config_runtime(**kwargs)


def test_sso_env_defaults_enable_only_with_a_complete_triple(tmp_path):
    bare = _sso_runtime(tmp_path)
    assert bare["_sso_env_defaults"]()["enabled"] is False

    full = _sso_runtime(
        tmp_path,
        discovery_url="https://idp.test/.well-known/openid-configuration",
        client_id="cid",
        client_secret="secret",
        provider_name="Okta",
    )
    defaults = full["_sso_env_defaults"]()
    assert defaults["enabled"] is True
    assert defaults["provider_name"] == "Okta"
    assert defaults["scopes"] == "openid email profile"


def test_load_sso_config_merges_file_over_env_and_survives_corruption(tmp_path):
    sso_file = tmp_path / "sso_config.json"
    sso_file.write_text(
        json.dumps({"discovery_url": "https://file.test/d", "client_id": "file-cid",
                    "client_secret": "file-secret", "enabled": True, "provider_name": None}),
        encoding="utf-8",
    )
    runtime = _sso_runtime(tmp_path, sso_file=sso_file, provider_name="EnvIdP")
    config = runtime["load_sso_config"]()
    assert config["discovery_url"] == "https://file.test/d"
    assert config["client_id"] == "file-cid"
    # None values in the file never clobber the env default.
    assert config["provider_name"] == "EnvIdP"
    assert config["enabled"] is True

    log = _Logging()
    sso_file.write_text("{not json", encoding="utf-8")
    broken = _sso_runtime(tmp_path, sso_file=sso_file, logging=log)
    fallback = broken["load_sso_config"]()
    assert fallback["enabled"] is False
    assert fallback["provider_name"] == "SSO"
    assert fallback["redirect_uri"] == "http://localhost:4825/auth/sso/callback"
    assert log.warnings and "load_sso_config failed" in log.warnings[0]


def test_public_sso_config_never_exposes_the_client_secret(tmp_path):
    runtime = _sso_runtime(
        tmp_path,
        discovery_url="https://idp.test/d",
        client_id="cid",
        client_secret="super-secret",
    )
    public = runtime["public_sso_config"]()
    assert public["secret_configured"] is True
    assert "client_secret" not in public
    assert public["client_id"] == "cid"
    assert runtime["get_sso_settings"]()["client_secret"] == "super-secret"

    explicit = runtime["public_sso_config"]({"enabled": False})
    assert explicit["secret_configured"] is False
    assert explicit["redirect_uri"] == "http://localhost:4825/auth/sso/callback"


def test_save_sso_config_keeps_existing_secret_and_persists(tmp_path):
    sso_file = tmp_path / "sso_config.json"
    runtime = _sso_runtime(
        tmp_path,
        sso_file=sso_file,
        discovery_url="https://idp.test/d",
        client_id="cid",
        client_secret="kept-secret",
    )
    saved = runtime["save_sso_config"]({"provider_name": "Okta", "client_secret": "", "scopes": None})

    assert saved["provider_name"] == "Okta"
    # An empty secret means "unchanged", not "erase".
    assert saved["client_secret"] == "kept-secret"
    assert saved["enabled"] is True
    on_disk = json.loads(sso_file.read_text(encoding="utf-8"))
    assert on_disk["provider_name"] == "Okta"


def _fake_httpx(payload=None, *, boom=None):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return dict(payload or {})

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url, timeout=None):
            if boom is not None:
                raise boom
            _AsyncClient.calls.append((url, timeout))
            return _Response()

    _AsyncClient.calls = []
    return types.SimpleNamespace(AsyncClient=_AsyncClient), _AsyncClient


def test_sso_discovery_is_cached_and_invalidated_by_a_config_save(tmp_path, monkeypatch):
    sso_file = tmp_path / "sso_config.json"
    runtime = _sso_runtime(
        tmp_path,
        sso_file=sso_file,
        discovery_url="https://idp.test/.well-known/openid-configuration",
        client_id="cid",
        client_secret="secret",
    )
    fake, client = _fake_httpx({"issuer": "https://idp.test"})
    monkeypatch.setitem(sys.modules, "httpx", fake)

    first = asyncio.run(runtime["_get_sso_discovery"]())
    second = asyncio.run(runtime["_get_sso_discovery"]())

    assert first == {"issuer": "https://idp.test"}
    assert second == first
    assert len(client.calls) == 1  # second call served from cache

    runtime["save_sso_config"]({"provider_name": "Okta"})
    third = asyncio.run(runtime["_get_sso_discovery"]())
    assert third == {"issuer": "https://idp.test"}
    assert len(client.calls) == 2  # save invalidated the cached document


def test_sso_discovery_returns_none_without_url_and_on_transport_failure(tmp_path, monkeypatch):
    bare = _sso_runtime(tmp_path)
    assert asyncio.run(bare["_get_sso_discovery"]()) is None

    log = _Logging()
    runtime = _sso_runtime(
        tmp_path,
        discovery_url="https://idp.test/d",
        client_id="cid",
        client_secret="secret",
        logging=log,
    )
    fake, _client = _fake_httpx(boom=RuntimeError("connect refused"))
    monkeypatch.setitem(sys.modules, "httpx", fake)

    assert asyncio.run(runtime["_get_sso_discovery"]()) is None
    assert "SSO discovery failed" in log.warnings[0]


# ── lifespan_runtime ───────────────────────────────────────────────────────


class _Router:
    def __init__(self, *, unloaded_names=(), load_error=None):
        self.loads = []
        self.unload_all_called = False
        self._unloaded_names = list(unloaded_names)
        self._load_error = load_error

    async def load_model(self, model_id, draft_model_id=None):
        self.loads.append((model_id, draft_model_id))
        if self._load_error is not None:
            raise self._load_error
        return f"loaded {model_id}"

    def unload_idle_models(self, _seconds):
        return list(self._unloaded_names)

    def unload_all(self):
        self.unload_all_called = True


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *args):
        self.warnings.append(fmt % args if args else fmt)


def _lifespan(**overrides):
    kwargs = {
        "app_mode": "local",
        "enable_telegram": False,
        "autoload_models": True,
        "is_public_mode": False,
        "public_model": "openai:gpt-4o-mini",
        "allow_local_models": True,
        "local_model": "mlx-community/test",
        "local_draft_model": "",
        "model_idle_unload_seconds": 0,
        "model_router": _Router(),
        "local_kg_watcher": None,
        "local_server_processes": {},
        "logger": _Logger(),
    }
    kwargs.update(overrides)
    return build_lifespan_runtime(**kwargs)


def test_public_autoload_waits_for_the_provider_key_then_loads(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    router = _Router()
    runtime = _lifespan(is_public_mode=True, public_model="openai:gpt-4o-mini", model_router=router)

    asyncio.run(runtime["autoload_default_model"]())
    assert router.loads == []
    assert "Set OPENAI_API_KEY" in capsys.readouterr().out

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    asyncio.run(runtime["autoload_default_model"]())
    assert router.loads == [("openai:gpt-4o-mini", None)]
    assert "loaded openai:gpt-4o-mini" in capsys.readouterr().out


def test_local_autoload_is_skipped_when_local_models_are_disallowed(capsys):
    router = _Router()
    runtime = _lifespan(allow_local_models=False, model_router=router)

    asyncio.run(runtime["autoload_default_model"]())

    assert router.loads == []
    assert "LATTICEAI_ALLOW_LOCAL_MODELS=false" in capsys.readouterr().out


def test_local_autoload_reports_draft_model_presence(capsys):
    with_draft = _Router()
    runtime = _lifespan(local_draft_model="mlx-community/draft", model_router=with_draft)
    asyncio.run(runtime["autoload_default_model"]())
    assert with_draft.loads == [("mlx-community/test", "mlx-community/draft")]
    assert "Draft:  mlx-community/draft" in capsys.readouterr().out

    without_draft = _Router()
    runtime = _lifespan(local_draft_model="", model_router=without_draft)
    asyncio.run(runtime["autoload_default_model"]())
    assert without_draft.loads == [("mlx-community/test", None)]
    assert "Draft:  disabled" in capsys.readouterr().out


def test_idle_unload_loop_is_disabled_at_zero_and_reports_unloads(monkeypatch, capsys):
    runtime = _lifespan(model_idle_unload_seconds=0)
    asyncio.run(runtime["unload_idle_models_loop"]())
    assert "Model idle unload disabled" in capsys.readouterr().out

    class _StopLoop(Exception):
        pass

    delays = []

    async def _fake_sleep(delay):
        delays.append(delay)
        if len(delays) > 1:
            raise _StopLoop

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    runtime = _lifespan(
        model_idle_unload_seconds=120,
        model_router=_Router(unloaded_names=["mlx-community/test"]),
    )
    with pytest.raises(_StopLoop):
        asyncio.run(runtime["unload_idle_models_loop"]())

    assert delays == [60, 60]  # min(60, 120)
    assert "Idle model unload: mlx-community/test" in capsys.readouterr().out


def test_spawn_logs_failures_and_stays_silent_on_cancellation():
    logger = _Logger()
    runtime = _lifespan(logger=logger)
    spawn = runtime["_spawn"]

    async def _drive():
        async def _boom():
            raise RuntimeError("task exploded")

        failing = spawn(_boom(), name="boom")
        with contextlib.suppress(RuntimeError):
            await failing

        async def _forever():
            await asyncio.Event().wait()

        cancelled = spawn(_forever(), name="forever")
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        # let both done-callbacks run
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_drive())

    assert logger.warnings == ["background task 'boom' failed: task exploded"]


def test_lifespan_starts_telegram_restores_watchers_and_terminates_processes(monkeypatch, capsys):
    started = {"bot": False}

    async def _run_bot():
        started["bot"] = True

    monkeypatch.setitem(
        sys.modules,
        "latticeai.integrations.telegram_bot",
        types.SimpleNamespace(run_bot=_run_bot),
    )

    class _Watcher:
        def __init__(self):
            self.stopped = False

        def restore_enabled_sources(self):
            return {"restored": ["~/notes"]}

        def stop_all(self):
            self.stopped = True

    class _LiveProc:
        def __init__(self):
            self.terminated = False
            self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = True

    class _BrokenProc:
        def poll(self):
            raise OSError("process handle gone")

    watcher = _Watcher()
    live = _LiveProc()
    router = _Router()
    runtime = _lifespan(
        enable_telegram=True,
        autoload_models=False,
        model_router=router,
        local_kg_watcher=watcher,
        local_server_processes={"live": live, "broken": _BrokenProc()},
    )

    async def _drive():
        async with runtime["lifespan"](object()):
            await asyncio.sleep(0)

    asyncio.run(_drive())

    out = capsys.readouterr().out
    assert "Telegram Bot Bridge activated" in out
    assert "Local knowledge watchers restored" in out
    assert started["bot"] is True
    assert watcher.stopped is True
    assert router.unload_all_called is True
    assert (live.terminated, live.waited) == (True, True)


# ── audit_runtime ──────────────────────────────────────────────────────────


def test_audit_read_tolerates_a_missing_file_corrupt_json_and_bad_lines(tmp_path):
    audit_file = tmp_path / "audit.json"
    runtime = build_audit_runtime(audit_file=audit_file, logging=_Logging())
    assert runtime["get_audit_log"]() == []

    audit_file.write_text("{not json", encoding="utf-8")
    jsonl = tmp_path / "audit.json.jsonl"
    jsonl.write_text(
        "\n".join(["", json.dumps({"event_type": "ok"}), "  ", "{broken", json.dumps({"event_type": "never"})]),
        encoding="utf-8",
    )

    events = runtime["get_audit_log"]()
    # The good line before the corruption survives; the tail is dropped rather
    # than taking the whole read down.
    assert [event["event_type"] for event in events] == ["ok"]


def test_audit_append_redacts_configured_fields_and_survives_a_broken_redactor(tmp_path):
    audit_file = tmp_path / "audit.json"
    runtime = build_audit_runtime(
        audit_file=audit_file,
        logging=_Logging(),
        redact_fn=lambda text: text.replace("sk-live-1234", "[redacted]"),
    )
    runtime["append_audit_event"]("chat", content="key sk-live-1234", message="sk-live-1234", count=3)

    events = runtime["get_audit_log"]()
    assert events[0]["content"] == "key [redacted]"
    assert events[0]["message"] == "[redacted]"
    assert events[0]["count"] == 3
    assert events[0]["timestamp"]

    def _broken_redactor(_text):
        raise ValueError("redactor exploded")

    broken = build_audit_runtime(
        audit_file=tmp_path / "audit2.json", logging=_Logging(), redact_fn=_broken_redactor
    )
    broken["append_audit_event"]("chat", content="still recorded")
    assert broken["get_audit_log"]()[0]["content"] == "still recorded"


def test_audit_append_never_raises_when_the_log_path_is_unusable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    log = _Logging()
    runtime = build_audit_runtime(audit_file=blocker / "audit.json", logging=log)

    runtime["append_audit_event"]("chat", content="dropped")

    assert log.warnings and "audit append failed" in log.warnings[0]


# ── context_runtime ────────────────────────────────────────────────────────


class _Gardener:
    def __init__(self):
        self.calls = []

    def get_relevant_context(self, query, allowed_workspaces=None, **kwargs):
        self.calls.append((query, allowed_workspaces, kwargs))
        return "notes-context"


def _context_runtime(*, require_auth, gardener, scopes=("ws-1",)):
    return build_context_runtime(
        graph_store=object(),
        ingestion_pipeline=None,
        memory_service=types.SimpleNamespace(recall=lambda **_kw: {}),
        gardener=gardener,
        require_auth=require_auth,
        allowed_scopes_for_user=lambda email: {f"{item}:{email}" for item in scopes},
    )


def test_context_runtime_scopes_search_by_workspace_then_by_membership(monkeypatch):
    gardener = _Gardener()
    runtime = _context_runtime(require_auth=True, gardener=gardener)
    seen = []
    monkeypatch.setattr(
        runtime["SEARCH_SERVICE"],
        "hybrid_search",
        lambda query, allowed_workspaces=None, **kw: seen.append((query, allowed_workspaces, kw)) or {"results": []},
    )
    search = runtime["_scoped_hybrid_search"]

    search("q1", workspace_id="org:acme")
    search("q2", user_email="owner@example.com")
    search("q3", limit=5)

    assert seen[0][1] == {"org:acme"}
    assert seen[1][1] == {"ws-1:owner@example.com"}
    assert seen[2][1] is None
    assert seen[2][2] == {"limit": 5}
    assert runtime["ARTIFACT_LEDGER"] is not None
    assert runtime["BRAIN_MEMORY"] is not None


def test_context_runtime_notes_scope_is_open_when_auth_is_off():
    gardener = _Gardener()
    scoped = _context_runtime(require_auth=True, gardener=gardener)
    notes = scoped["CONTEXT_ASSEMBLER"]._notes_context

    assert notes("q", workspace_id="org:acme") == "notes-context"
    assert notes("q", user_email="owner@example.com", limit=2) == "notes-context"
    assert gardener.calls[0][1] == {"org:acme"}
    assert gardener.calls[1][1] == {"ws-1:owner@example.com"}
    assert gardener.calls[1][2] == {"limit": 2}

    open_gardener = _Gardener()
    unscoped = _context_runtime(require_auth=False, gardener=open_gardener)
    unscoped["CONTEXT_ASSEMBLER"]._notes_context("q", user_email="owner@example.com")
    assert open_gardener.calls[0][1] is None
