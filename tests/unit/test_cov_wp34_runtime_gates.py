"""Coverage for the identity, key, dial and router-registration phases (wp34).

The process-wide dial singletons are rebound through ``monkeypatch.setattr`` on
the module globals so each test builds its own service under ``tmp_path`` and
the real process singleton is restored afterwards.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException

from latticeai.runtime import network_boundary_wiring as nbw
from latticeai.runtime import permission_mode_wiring as pmw
from latticeai.runtime.access_runtime import build_access_runtime
from latticeai.runtime.router_registration import (
    build_auth_admin_security_router_bundle,
)
from latticeai.runtime.user_key_runtime import build_user_key_runtime
from latticeai.services import cloud_egress_audit


class _Request:
    def __init__(self, *, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


def _access(*, users, sessions, require_auth=True, is_public=False, network_exposed=False):
    return build_access_runtime(
        config=SimpleNamespace(
            admin_emails=["owner@example.com"],
            is_public=is_public,
            network_exposed=network_exposed,
        ),
        require_auth=require_auth,
        http_exception=HTTPException,
        request_type=_Request,
        load_users=lambda: users,
        get_session_email=lambda token: sessions.get(token),
        user_id_for_email=lambda _users, email: f"id:{email}",
    )


# ── access_runtime: session identity resolution ──────────────────────────────


def test_unknown_token_resolves_to_no_identity():
    runtime = _access(users={"a@example.com": {"id": "uid-a"}}, sessions={})

    assert runtime["get_current_user"](_Request(cookies={"session_token": "ghost"})) is None


def test_session_identity_matches_a_non_normalized_account_key():
    users = {"Mixed@Example.com": {"id": "uid-mixed", "role": "user"}}
    runtime = _access(users=users, sessions={"tok": "Mixed@Example.com"})

    email = runtime["get_current_user"](_Request(headers={"Authorization": "Bearer tok"}))

    assert email == "mixed@example.com"


def test_session_identity_matches_an_account_by_stable_user_id():
    users = {"member@example.com": {"id": "uid-member", "role": "user"}, "other": "not-a-dict"}
    runtime = _access(users=users, sessions={"tok": "uid-member"})

    email = runtime["get_current_user"](_Request(headers={"Authorization": "Bearer tok"}))

    assert email == "member@example.com"


def test_session_for_a_disabled_account_is_refused():
    users = {"member@example.com": {"id": "uid-member", "disabled": True}}
    runtime = _access(users=users, sessions={"tok": "uid-member"})

    assert runtime["get_current_user"](_Request(headers={"Authorization": "Bearer tok"})) is None


# ── user_key_runtime: provider API keys ──────────────────────────────────────


class _Keyring:
    def __init__(self, *, read_error=None, write_error=None):
        self.store = {}
        self.read_error = read_error
        self.write_error = write_error

    def get_password(self, service, key):
        if self.read_error:
            raise self.read_error
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        if self.write_error:
            raise self.write_error
        self.store[(service, key)] = value


class _Logging:
    def __init__(self):
        self.warnings = []

    def warning(self, *args):
        self.warnings.append(args)


def _keys(*, users, keyring, allow_plaintext, logging=None):
    return build_user_key_runtime(
        load_users=lambda: users,
        save_users=lambda updated: users.update(updated),
        ensure_user_identity=lambda email, user: user.setdefault("id", f"id:{email}"),
        keyring=keyring,
        allow_plaintext_api_keys=allow_plaintext,
        logging=logging or _Logging(),
        http_exception=HTTPException,
    )


def test_anonymous_caller_has_no_provider_key():
    runtime = _keys(users={}, keyring=_Keyring(), allow_plaintext=False)

    assert runtime["get_user_api_key"](None, "openai") is None


def test_keyring_read_failure_falls_back_to_the_plaintext_policy():
    log = _Logging()
    users = {"member@example.com": {"api_keys": {"openai": " sk-plain "}}}
    runtime = _keys(
        users=users,
        keyring=_Keyring(read_error=RuntimeError("keyring locked")),
        allow_plaintext=True,
        logging=log,
    )

    assert runtime["get_user_api_key"]("member@example.com", "openai") == "sk-plain"
    assert log.warnings, "a keyring read failure must be reported"


def test_keyring_write_failure_without_plaintext_opt_in_is_refused():
    log = _Logging()
    runtime = _keys(
        users={},
        keyring=_Keyring(write_error=RuntimeError("keyring locked")),
        allow_plaintext=False,
        logging=log,
    )

    with pytest.raises(HTTPException) as excinfo:
        runtime["set_user_api_key"]("member@example.com", "openai", "sk-1")

    assert excinfo.value.status_code == 500
    assert log.warnings


def test_keyring_write_failure_with_plaintext_opt_in_persists_to_the_user_store():
    users = {}
    runtime = _keys(
        users=users,
        keyring=_Keyring(write_error=RuntimeError("keyring locked")),
        allow_plaintext=True,
    )

    runtime["set_user_api_key"]("member@example.com", "openai", "sk-1")

    assert users["member@example.com"]["api_keys"] == {"openai": "sk-1"}


# ── permission_mode_wiring / network_boundary_wiring singletons ──────────────


def test_permission_mode_default_data_dir_follows_the_env_then_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    assert pmw._default_data_dir() == tmp_path

    monkeypatch.delenv("LATTICEAI_DATA_DIR", raising=False)
    assert pmw._default_data_dir() == Path.home() / ".ltcai"


def test_network_boundary_default_data_dir_follows_the_env_then_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path))
    assert nbw._default_data_dir() == tmp_path

    monkeypatch.delenv("LATTICEAI_DATA_DIR", raising=False)
    assert nbw._default_data_dir() == Path.home() / ".ltcai"


def test_permission_mode_service_rebinds_a_lazily_created_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(pmw, "_SHARED", None)
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "fallback"))

    early = pmw.get_permission_mode_service()
    events = []
    late = pmw.get_permission_mode_service(
        data_dir=tmp_path / "real",
        audit=lambda *a, **k: events.append((a, k)),
    )

    assert late is early
    assert late._path == tmp_path / "real" / "permission_mode.json"
    assert late._audit is not None
    late.set_mode("plan", user_email="member@example.com")
    assert events, "the rebound audit sink must receive dial changes"


def test_network_boundary_services_rebind_a_lazily_created_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(nbw, "_SHARED", None)
    monkeypatch.setattr(nbw, "_POLICY", None)
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "fallback"))

    early_svc = nbw.get_network_boundary_service()
    early_policy = nbw.get_hybrid_policy_service()

    audit_calls = []
    svc = nbw.get_network_boundary_service(
        data_dir=tmp_path / "real", audit=lambda *a, **k: audit_calls.append(a)
    )
    policy = nbw.get_hybrid_policy_service(
        data_dir=tmp_path / "real", audit=lambda *a, **k: audit_calls.append(a)
    )

    assert svc is early_svc and policy is early_policy
    assert svc._path == tmp_path / "real" / "network_boundary.json"
    assert policy._path == tmp_path / "real" / "hybrid_policy.json"
    assert nbw.resolve_active_network_mode() is not None


def test_permission_mode_router_registration_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(pmw, "_SHARED", None)
    app = FastAPI()

    first = pmw.register_permission_mode_router(
        app, require_user=lambda request: "member@example.com", data_dir=tmp_path
    )
    routes_after_first = len(app.routes)
    second = pmw.register_permission_mode_router(
        app, require_user=lambda request: "member@example.com", data_dir=tmp_path
    )

    assert second is first
    assert len(app.routes) == routes_after_first


def test_network_boundary_router_registration_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(nbw, "_SHARED", None)
    monkeypatch.setattr(nbw, "_POLICY", None)
    monkeypatch.setattr(cloud_egress_audit, "_AUDIT", None)
    app = FastAPI()

    first = nbw.register_network_boundary_router(
        app, require_user=lambda request: "member@example.com", data_dir=tmp_path
    )
    routes_after_first = len(app.routes)
    second = nbw.register_network_boundary_router(
        app, require_user=lambda request: "member@example.com", data_dir=tmp_path
    )

    assert second is first
    assert len(app.routes) == routes_after_first


# ── router_registration: auth/admin/security helper closures ─────────────────


def _bundle(*, get_audit_log, knowledge_graph=None, enable_graph=False):
    recorded: dict = {}

    def _capture(name):
        def factory(**kwargs):
            recorded[name] = kwargs
            return f"{name}-router"

        return factory

    bundle = build_auth_admin_security_router_bundle(
        create_auth_router=_capture("auth"),
        load_users=dict,
        save_users=lambda _users: None,
        hash_password=lambda password: ("hash", "salt"),
        verify_and_migrate_password=lambda *a, **k: True,
        create_session=lambda email: "token",
        get_session_email=lambda token: None,
        invalidate_session=lambda token: None,
        extract_bearer_token=lambda request: None,
        get_user_role=lambda email, users=None: "user",
        require_user=lambda request: "member@example.com",
        check_ip_rate_limit=lambda *a, **k: True,
        client_ip=lambda request: "127.0.0.1",
        get_sso_settings=dict,
        get_sso_discovery=dict,
        public_sso_config=dict,
        open_registration=True,
        session_ttl=3600,
        require_auth=False,
        secure_cookies=False,
        invite_authorized=lambda request: True,
        ensure_identity=lambda email, user: None,
        create_admin_router=_capture("admin"),
        require_admin=lambda request: ("admin@example.com", {}),
        get_history=lambda *a, **k: [],
        get_audit_log=get_audit_log,
        audit_file="audit.jsonl",
        public_user=lambda email, user, users: {"email": email},
        load_vpc_config=dict,
        save_vpc_config=lambda config: None,
        build_admin_audit_report=lambda *a, **k: {},
        build_sensitivity_report=lambda *a, **k: {},
        append_audit_event=lambda *a, **k: None,
        save_sso_config=lambda config: None,
        knowledge_graph=knowledge_graph,
        enable_graph=enable_graph,
        logger=_Logging(),
        invite_code="invite",
        invite_gate_enabled=False,
        default_port=8000,
        policy_matrix={},
        build_product_hardening_status=lambda **kwargs: {"hardening": kwargs},
        config="config",
        kg_portability="portability",
        device_identity="device",
        create_invitations_router=_capture("invitations"),
        invitation_store="invitations",
        workspace_service="workspaces",
        user_id_for_email=lambda users, email: f"id:{email}",
        create_security_router=_capture("security"),
        classify_sensitive_message=lambda text: {"sensitivity": "none"},
    )
    return bundle, recorded


def test_graph_stats_helper_reports_disabled_and_live_graphs():
    disabled, _ = _bundle(get_audit_log=lambda *a: [], enable_graph=False)
    assert disabled["_graph_stats_safe"]() == {"disabled": True}

    graph = SimpleNamespace(stats=lambda: {"nodes": {"Concept": 3}})
    live, _ = _bundle(get_audit_log=lambda *a: [], knowledge_graph=graph, enable_graph=True)
    assert live["_graph_stats_safe"]() == {"nodes": {"Concept": 3}}


def test_product_hardening_status_helper_passes_the_wired_dependencies():
    bundle, _ = _bundle(get_audit_log=lambda *a: [])

    assert bundle["_product_hardening_status"]() == {
        "hardening": {
            "config": "config",
            "portability": "portability",
            "device_identity": "device",
        }
    }


def _upload_events():
    return [
        {
            "event_type": "document_upload",
            "filename": "notes.txt",
            "user_email": "member@example.com",
            "user_nickname": "member",
            "timestamp": "2026-01-01T00:00:00",
            "ext": ".txt",
            "bytes": 12,
            "sensitivity": "high",
            "sensitive_labels": ["secret"],
            "content_preview": "hello",
        },
        {"event_type": "login"},
        {"event_type": "document_upload"},
    ]


def test_uploaded_file_listing_keeps_only_upload_events():
    bundle, _ = _bundle(get_audit_log=lambda *a: _upload_events())

    files = bundle["_security_list_uploaded_files"]()

    assert [f["file_id"] for f in files] == ["notes.txt", "2"]
    assert files[0]["sensitivity"] == "high"
    assert files[1]["sensitivity"] == "none" and files[1]["sensitive_labels"] == []


def test_legacy_audit_readers_are_retried_with_the_audit_file():
    seen = []

    def legacy_get_audit_log(*args):
        if not args:
            raise TypeError("legacy reader needs a path")
        seen.append(args)
        return _upload_events()

    bundle, _ = _bundle(get_audit_log=legacy_get_audit_log)

    events = bundle["_security_audit_events_safe"]()

    assert seen == [("audit.jsonl",)]
    assert len(events) == 3
