"""Unit tests for core config parsing, session storage, quiet mode, and permissions."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from latticeai.core.config import (
    Config,
    _bool,
    _int,
    _port,
    _str,
    _value,
    default_data_dir,
)
from latticeai.core.quiet import quiet
from latticeai.core.sessions import (
    SessionStore,
    _entry_created_at,
    _entry_email,
    _entry_subject,
    _hash_token,
    _looks_hashed,
    _sessions_file,
    load_sessions,
    persist_sessions,
)
from latticeai.core.users import migrate_users


def test_quiet_remaining():
    from latticeai.core import quiet as quiet_mod

    quiet()

    quiet_mod.logger.setLevel(logging.DEBUG)

    def inner():
        raise ValueError("deep-fail")

    def outer():
        inner()

    try:
        outer()
    except ValueError:
        quiet("optional probe")
        quiet()


def test_sessions_remaining(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.config.Config.from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("no cfg")),
    )
    monkeypatch.delenv("LATTICEAI_DATA_DIR", raising=False)
    path = _sessions_file(None)
    assert path.name == "sessions.json"

    data_dir = tmp_path / "sess"
    raw = {
        "not-a-hash-token": ["user@x.com", time.time(), "user@x.com"],
        _hash_token("already"): ["b@c.d", time.time(), "b@c.d"],
    }
    (data_dir).mkdir()
    (data_dir / "sessions.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_sessions(data_dir)
    assert all(_looks_hashed(k) for k in loaded)

    (data_dir / "sessions.json").write_text("not-json", encoding="utf-8")
    assert load_sessions(data_dir) == {}

    monkeypatch.setattr(
        "latticeai.core.sessions.atomic_write_json",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")),
    )
    persist_sessions({}, data_dir)

    assert _entry_subject(()) is None
    assert _entry_email(("s",)) == "s"
    assert _entry_email(("s", 1.0, "e@x")) == "e@x"
    assert _entry_created_at(()) == 0.0
    assert _entry_created_at(("s", 3.0)) == 3.0
    assert _looks_hashed("0" * 64) is True
    assert _looks_hashed("zz") is False

    store_dir = tmp_path / "store"
    store = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=0)
    token = store.create("sub", email="e@x.com")
    assert store.get_email(token) == "e@x.com"
    assert store.get_subject(token) == "sub"
    assert store.get_email("missing") is None

    store2 = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=10**9)
    token2 = store2.create("s2", email="f@x.com")
    assert store2.get_subject(token2)

    expired = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=0)
    token3 = expired.create("s3")
    expired._sessions[_hash_token(token3)] = ("s3", time.time() - 10, "s3")
    assert expired.get_subject(token3) is None
    expired.invalidate(token3)


def test_config_remaining(tmp_path: Path, monkeypatch):
    assert _value({}, "K", "d") == "d"
    assert _str({"K": ""}, "K", "d") == ""
    assert _str({}, "K", "d") == "d"
    assert _bool({}, "K", True) is True
    assert _bool({"K": "YES"}, "K") is True
    assert _bool({"K": "off"}, "K") is False
    assert _bool({"K": "maybe"}, "K", True) is True
    assert _int({}, "K", 3) == 3
    assert _int({"K": "  "}, "K", 3) == 3
    assert _int({"K": "9"}, "K", 3) == 9
    assert _int({"K": "no"}, "K", 3) == 3
    assert _port({"K": "0"}, "K", 80) == 80
    assert _port({"K": "443"}, "K", 80) == 443

    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "from-env"))
    assert "from-env" in str(default_data_dir(None))

    packaged = tmp_path / "prefix" / "static"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "prefix"))
    cfg = Config.from_env(
        {
            "LATTICEAI_MODE": "weird",
            "LATTICEAI_HOST": "0.0.0.0",
            "LATTICEAI_PORT": "99999",
            "LATTICEAI_ENABLE_TELEGRAM": "maybe",
            "LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS": "nope",
            "LATTICE_TZ": "",
            "LATTICEAI_STATIC_DIR": str(tmp_path / "missing-static"),
            "LATTICEAI_CORS_ALLOWED_ORIGINS": "http://a.com, http://b.com",
            "LATTICEAI_CSRF_TRUSTED_ORIGINS": "https://app.example",
            "LATTICEAI_ADMIN_EMAILS": "A@B.COM",
            "LATTICEAI_TRUSTED_PROXIES": "10.0.0.1",
            "LATTICEAI_RATE_LIMIT": "0",
            "LATTICEAI_DATA_DIR": str(tmp_path / "data"),
            "LATTICEAI_REQUIRE_AUTH": "false",
            "LATTICEAI_OPEN_REGISTRATION": "true",
            "LATTICEAI_STORAGE_ENGINE": "  ",
        },
        base_dir=tmp_path,
    )
    assert cfg.app_mode == "local"
    assert cfg.network_exposed is True
    assert cfg.require_auth is True
    assert cfg.open_registration is False
    assert cfg.rate_limit_enabled is False
    assert cfg.timezone == "UTC"
    assert cfg.static_dir == packaged
    assert cfg.storage_engine == "sqlite"

    public = Config.from_env(
        {"LATTICEAI_MODE": "public", "LATTICEAI_HOST": "127.0.0.1"}, base_dir=tmp_path
    )
    assert public.is_public is True
    assert public.require_auth is True

    live = Config.from_env(None, base_dir=tmp_path)
    assert live.app_mode in {"local", "public"}


def test_core_leftovers_users_sessions_config_quiet_permission(
    tmp_path: Path, monkeypatch
):
    # merge without api_keys dicts
    migrated, _, changed = migrate_users(
        {
            "a@b.com": {"email": "a@b.com", "id": "user:1"},
            "A@B.COM": {"email": "A@B.COM", "id": "user:2"},
        }
    )
    assert changed is True
    assert migrated["a@b.com"]["id"] == "user:1"

    # sessions refresh path (0 is falsy in ctor)
    store = SessionStore(tmp_path / "s", ttl_seconds=1000, refresh_threshold_seconds=1)
    store._refresh_threshold_seconds = 0
    token = store.create("sub", email="e@x.com")
    assert store.get_subject(token) == "sub"

    # static dir missing and packaged static also missing
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "noprefix"))
    cfg = Config.from_env(
        {
            "LATTICEAI_STATIC_DIR": str(tmp_path / "no-static"),
            "LATTICEAI_DATA_DIR": str(tmp_path / "d"),
        },
        base_dir=tmp_path,
    )
    assert cfg.static_dir.name == "no-static"

    # quiet with exception but no traceback
    from latticeai.core import quiet as quiet_mod

    monkeypatch.setattr(
        quiet_mod.sys, "exc_info", lambda: (ValueError, ValueError("x"), None)
    )
    quiet_mod.logger.setLevel(logging.DEBUG)
    quiet("no-tb")

    from latticeai.core.permission_mode import effective_auto_approve

    assert (
        effective_auto_approve(
            "trusted",
            "write_file",
            {"risk": "exec", "sandbox": "workspace"},
            change_class="other",
        )
        is False
    )
    assert (
        effective_auto_approve(
            "trusted",
            "write_file",
            {"risk": "destructive", "sandbox": "workspace"},
        )
        is False
    )
    assert (
        effective_auto_approve(
            "bypass",
            "shell_exec",
            {"risk": "exec", "sandbox": "system"},
        )
        is False
    )
