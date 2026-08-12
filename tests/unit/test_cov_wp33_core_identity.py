"""Coverage for the identity/persistence primitives: user file migration,
atomic IO helpers, the hashed session store, app config parsing, invitation
lifecycle, timezone resolution, suppressed-exception reporting and the
open-core edition seam.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from latticeai.core import enterprise as enterprise_mod
from latticeai.core import io_utils, sessions, timezones, users
from latticeai.core.config import Config, _bool
from latticeai.core.enterprise import (
    CapabilityProvider,
    Edition,
    EnterpriseCapability,
    capability_registry,
    detect_edition,
)
from latticeai.core.invitations import InvitationStore
from latticeai.core.quiet import format_suppressed, quiet_summary

# ── users.py ───────────────────────────────────────────────────────────────


def test_migrate_users_skips_junk_and_merges_duplicate_emails():
    migrated, email_to_id, changed = users.migrate_users({
        "Owner@Example.com": {"email": "Owner@Example.com", "api_keys": {"openai": "a"}},
        "owner@example.com": {"name": "Owner", "api_keys": {"groq": "b"}},
        "broken": "not-a-dict",
    })

    assert changed is True
    assert list(migrated) == ["owner@example.com"]
    merged = migrated["owner@example.com"]
    assert merged["name"] == "Owner"
    assert merged["api_keys"] == {"openai": "a", "groq": "b"}
    assert merged["id"] == users.stable_user_id("owner@example.com")
    assert email_to_id == {"owner@example.com": merged["id"]}


def test_load_users_file_handles_absence_bad_shapes_and_backup_failure(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    assert users.load_users_file(missing) == {}

    non_dict = tmp_path / "list.json"
    non_dict.write_text("[]", encoding="utf-8")
    assert users.load_users_file(non_dict) == {}

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert users.load_users_file(corrupt) == {}

    legacy = tmp_path / "users.json"
    legacy.write_text(json.dumps({"Owner@Example.com": {"name": "Owner"}}), encoding="utf-8")

    def _no_backup(*_args, **_kwargs):
        raise OSError("read-only volume")

    monkeypatch.setattr(shutil, "copy2", _no_backup)

    migrated = users.load_users_file(legacy)

    # The migration still lands even though the pre-migration backup failed.
    assert list(migrated) == ["owner@example.com"]
    assert json.loads(legacy.read_text(encoding="utf-8"))["owner@example.com"]["id"].startswith("user:")
    assert not list(tmp_path.glob("users.json.pre-user-uuid.*"))


def test_save_users_file_normalizes_before_writing(tmp_path):
    path = tmp_path / "users.json"
    users.save_users_file(path, {"Owner@Example.com": {"name": "Owner"}})

    written = json.loads(path.read_text(encoding="utf-8"))
    assert list(written) == ["owner@example.com"]
    assert written["owner@example.com"]["email"] == "owner@example.com"


def test_user_id_lookup_is_total():
    store = {"owner@example.com": {"id": "user:abc", "email": "owner@example.com"}}

    assert users.user_id_for_email(store, "owner@example.com") == "user:abc"
    # Unknown users still get their deterministic namespace id.
    assert users.user_id_for_email(store, "ghost@example.com") == users.stable_user_id("ghost@example.com")


def test_kg_identity_migration_is_a_noop_without_a_database_or_mapping(tmp_path):
    assert users.migrate_knowledge_graph_identity(tmp_path / "absent.db", {"a@b.c": "user:1"}) == 0

    present = tmp_path / "kg.db"
    present.write_bytes(b"")
    assert users.migrate_knowledge_graph_identity(present, {}) == 0


# ── io_utils.py ────────────────────────────────────────────────────────────


def test_atomic_write_json_survives_a_filesystem_without_mode_bits(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "payload.json"

    def _no_chmod(self, _mode):
        raise OSError("chmod unsupported")

    monkeypatch.setattr(Path, "chmod", _no_chmod)
    io_utils.atomic_write_json(target, {"k": "값"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"k": "값"}
    assert not list(tmp_path.glob("nested/*.tmp"))


def test_parse_iso_returns_none_for_unparseable_values():
    assert io_utils.parse_iso("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5)
    assert io_utils.parse_iso(None) is None
    assert io_utils.parse_iso("") is None
    assert io_utils.parse_iso("not-a-date") is None
    assert io_utils.parse_iso(object()) is None


def test_sha256_file_streams_content_in_blocks(tmp_path):
    blob = tmp_path / "blob.bin"
    payload = b"lattice" * 20000  # larger than the 64KiB read block
    blob.write_bytes(payload)

    assert io_utils.sha256_file(blob) == hashlib.sha256(payload).hexdigest()


# ── sessions.py ────────────────────────────────────────────────────────────


def test_sessions_file_falls_back_to_the_env_data_dir(tmp_path, monkeypatch):
    import latticeai.core.config as config_mod

    def _no_config(*_args, **_kwargs):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config_mod.Config, "from_env", staticmethod(_no_config))
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "fallback"))

    path = sessions._sessions_file()

    assert path == tmp_path / "fallback" / "sessions.json"
    assert path.parent.is_dir()


def test_load_sessions_starts_empty_when_the_file_is_corrupt(tmp_path):
    (tmp_path / "sessions.json").write_text("{not json", encoding="utf-8")

    assert sessions.load_sessions(tmp_path) == {}


def test_persist_sessions_never_raises_when_the_write_fails(tmp_path, monkeypatch):
    def _no_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sessions, "atomic_write_json", _no_write)

    sessions.persist_sessions({"abc": ("owner@example.com", 1.0, "owner@example.com")}, tmp_path)

    assert not (tmp_path / "sessions.json").exists()


def test_entry_created_at_defaults_for_legacy_single_field_entries():
    assert sessions._entry_created_at(("owner@example.com",)) == 0.0
    assert sessions._entry_created_at(("owner@example.com", 12.5)) == 12.5


def test_session_read_refreshes_a_stale_entry_and_persists_the_bump(tmp_path):
    store = sessions.SessionStore(tmp_path, ttl_seconds=10_000, refresh_threshold_seconds=60)
    token = store.create("user:1", email="owner@example.com")
    key = sessions._hash_token(token)
    store._sessions[key] = ("user:1", time.time() - 600, "owner@example.com")

    assert store.get_email(token) == "owner@example.com"

    refreshed_at = store._sessions[key][1]
    assert time.time() - refreshed_at < 60
    on_disk = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert on_disk[key][1] == refreshed_at


# ── config.py ──────────────────────────────────────────────────────────────


def test_bool_parses_explicit_falsey_words_and_keeps_the_default_otherwise():
    assert _bool({"FLAG": "off"}, "FLAG", default=True) is False
    assert _bool({"FLAG": "NO"}, "FLAG", default=True) is False
    assert _bool({"FLAG": "0"}, "FLAG", default=True) is False
    assert _bool({"FLAG": "maybe"}, "FLAG", default=True) is True
    assert _bool({}, "FLAG", default=True) is True


def test_config_falls_back_to_the_packaged_static_dir(tmp_path, monkeypatch):
    packaged = tmp_path / "prefix"
    (packaged / "static").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(packaged))

    config = Config.from_env(
        {"LATTICEAI_STATIC_DIR": str(tmp_path / "missing-static")}, base_dir=tmp_path
    )

    assert config.static_dir == packaged / "static"


# ── invitations.py ─────────────────────────────────────────────────────────


def test_invitation_store_reads_a_corrupt_file_as_empty(tmp_path):
    path = tmp_path / "invitations.json"
    path.write_text("{not json", encoding="utf-8")
    store = InvitationStore(path)

    assert store.list() == []

    path.write_text(json.dumps(["unexpected", "shape"]), encoding="utf-8")
    assert store.list() == []


def test_accepting_an_expired_invitation_marks_it_and_refuses(tmp_path):
    path = tmp_path / "invitations.json"
    store = InvitationStore(path)
    created = store.create(email=None, workspace_id="org:acme", role="member", created_by="admin@example.com")

    data = json.loads(path.read_text(encoding="utf-8"))
    data["invitations"][0]["expires_at"] = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PermissionError, match="expired"):
        store.accept(created["token"], accepted_by="user:1", email="member@example.com")

    assert json.loads(path.read_text(encoding="utf-8"))["invitations"][0]["status"] == "expired"


def test_accepting_with_the_wrong_email_is_refused(tmp_path):
    store = InvitationStore(tmp_path / "invitations.json")
    created = store.create(
        email="Invited@Example.com", workspace_id="org:acme", role="member", created_by="admin@example.com"
    )

    with pytest.raises(PermissionError, match="different email"):
        store.accept(created["token"], accepted_by="user:2", email="someone-else@example.com")

    accepted = store.accept(created["token"], accepted_by="user:1", email="invited@example.com")
    assert accepted["status"] == "accepted"
    assert accepted["accepted_by"] == "user:1"
    assert "token_hash" not in accepted


def test_an_unparseable_expiry_is_treated_as_already_expired(tmp_path):
    path = tmp_path / "invitations.json"
    store = InvitationStore(path)
    store.create(email=None, workspace_id=None, role="member", created_by="admin@example.com")

    data = json.loads(path.read_text(encoding="utf-8"))
    data["invitations"][0]["expires_at"] = "whenever"
    path.write_text(json.dumps(data), encoding="utf-8")

    listed = store.list()

    assert [item["status"] for item in listed] == ["expired"]
    assert json.loads(path.read_text(encoding="utf-8"))["invitations"][0]["status"] == "expired"


# ── timezones.py ───────────────────────────────────────────────────────────


def test_timezone_falls_back_to_system_local_without_zoneinfo(monkeypatch):
    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")
    monkeypatch.setattr(timezones, "ZoneInfo", None)

    tz = timezones.get_timezone()

    assert tz == datetime.now().astimezone().tzinfo


def test_tz_name_reports_local_when_no_override_is_set(monkeypatch):
    for var in timezones._TZ_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    assert timezones.tz_name() == "local"

    monkeypatch.setenv("LTCAI_TZ", "Asia/Seoul")
    assert timezones.tz_name() == "Asia/Seoul"


# ── quiet.py ───────────────────────────────────────────────────────────────


def test_quiet_reporters_are_empty_outside_an_exception_handler():
    assert quiet_summary("probe") == ""
    assert format_suppressed() == ""


def test_quiet_reporters_describe_the_live_exception():
    try:
        raise ValueError("optional probe failed")
    except ValueError:
        summary = quiet_summary("gpu probe")
        traceback_text = format_suppressed()

    assert summary == "gpu probe: optional probe failed"
    assert "ValueError: optional probe failed" in traceback_text
    assert "test_quiet_reporters_describe_the_live_exception" in traceback_text


# ── enterprise.py ──────────────────────────────────────────────────────────


class _EnterpriseProvider(CapabilityProvider):
    """Stand-in for a separately distributed Enterprise plugin."""

    def edition(self) -> Edition:
        # The protocol's declared body is an inert placeholder.
        assert super().edition() is None
        return Edition.ENTERPRISE

    def is_enabled(self, capability: EnterpriseCapability) -> bool:
        assert super().is_enabled(capability) is None
        return capability is EnterpriseCapability.SCIM


def test_detect_edition_requires_both_the_optin_and_a_registered_provider(monkeypatch):
    monkeypatch.setenv("LATTICE_EDITION", "enterprise")
    # Opt-in alone is not enough — Community ships no provider.
    assert detect_edition() is Edition.COMMUNITY

    try:
        capability_registry.register_provider(_EnterpriseProvider())
        assert detect_edition() is Edition.ENTERPRISE
        assert capability_registry.is_capability_enabled(EnterpriseCapability.SCIM) is True

        monkeypatch.delenv("LATTICE_EDITION", raising=False)
        # Without the opt-in the registry still answers honestly.
        assert detect_edition() is Edition.ENTERPRISE
    finally:
        capability_registry.reset()

    assert detect_edition() is Edition.COMMUNITY
    assert enterprise_mod.is_capability_enabled(EnterpriseCapability.SCIM) is False
