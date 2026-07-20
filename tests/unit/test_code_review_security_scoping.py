"""Regression coverage for the 2026-07-11 security/scoping review."""

from __future__ import annotations

import os
import sqlite3
from http.cookies import SimpleCookie

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.api.auth import create_auth_router
from latticeai.api.health import create_health_router
from latticeai.api.static_routes import (
    INVITE_COOKIE_NAME,
    INVITE_COOKIE_TTL_SECONDS,
    _sign_invite_cookie,
    _verify_invite_cookie,
    create_static_routes_router,
)
from latticeai.core.config import Config
from latticeai.runtime.security_runtime import build_security_runtime


def _static_client(*, require_user=None) -> TestClient:
    bundle = create_static_routes_router(
        static_dir=Config.from_env({}).static_dir,
        invite_gate_enabled=True,
        invite_code="private-invite",
        invite_cookie_secret="s" * 64,
        secure_cookies=True,
        app_mode="public",
        model_router=type("Router", (), {"_current": "secret-model"})(),
        require_user=require_user or (lambda _request: "user@example.com"),
    )
    app = FastAPI()
    app.include_router(bundle.router)
    return TestClient(app, follow_redirects=False)


def _cookie_value(response, name: str) -> str:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return parsed[name].value


def test_invite_gate_rejects_plaintext_and_tampered_cookies():
    client = _static_client()

    assert client.get("/", headers={"Cookie": "authorized=true"}).status_code == 403
    issued = client.get("/?code=private-invite")
    assert issued.status_code == 308
    assert issued.headers["location"] == "/app#/account"
    assert "private-invite" not in issued.headers["location"]
    assert f"{INVITE_COOKIE_NAME}=" in issued.headers["set-cookie"]
    assert "HttpOnly" in issued.headers["set-cookie"]
    assert "Secure" in issued.headers["set-cookie"]
    assert "authorized=" not in issued.headers["set-cookie"]

    signed = _cookie_value(issued, INVITE_COOKIE_NAME)
    accepted = client.get("/", headers={"Cookie": f"{INVITE_COOKIE_NAME}={signed}"})
    assert accepted.status_code == 308
    assert accepted.headers["location"] == "/app#/account"

    tampered = f"{signed[:-1]}{'0' if signed[-1] != '0' else '1'}"
    assert client.get(
        "/",
        headers={"Cookie": f"{INVITE_COOKIE_NAME}={tampered}"},
    ).status_code == 403


def test_invite_cookie_expiry_is_signed_and_enforced():
    issued_at = 1_000_000
    cookie = _sign_invite_cookie("k" * 64, now=issued_at)

    assert _verify_invite_cookie(cookie, "k" * 64, now=issued_at + 1)
    assert not _verify_invite_cookie(
        cookie,
        "k" * 64,
        now=issued_at + INVITE_COOKIE_TTL_SECONDS,
    )
    assert not _verify_invite_cookie(cookie, "wrong-key", now=issued_at + 1)


def test_invite_gate_also_protects_direct_account_and_app_routes():
    client = _static_client()

    assert client.get("/account").status_code == 403
    assert client.get("/app").status_code == 403


def test_invite_gate_also_protects_direct_registration_api():
    users = {}
    secret = "r" * 64
    app = FastAPI()
    app.include_router(
        create_auth_router(
            load_users=lambda: users,
            save_users=lambda _users: None,
            hash_password=lambda value: value,
            verify_and_migrate=lambda *_args: True,
            create_session=lambda _email: "session-value",
            get_session_email=lambda _token: None,
            invalidate_session=lambda _token: None,
            extract_bearer_token=lambda _request: None,
            get_user_role=lambda _email, _users=None: "user",
            require_user=lambda _request: "user@example.com",
            check_ip_rate_limit=lambda *_args, **_kwargs: None,
            client_ip=lambda _request: "127.0.0.1",
            get_sso_settings=lambda: {},
            get_sso_discovery=lambda: None,
            public_sso_config=lambda: {},
            # Mirrors public/non-loopback mode: unrestricted registration is
            # closed, but a valid signed invite authorizes this one request.
            open_registration=False,
            session_ttl=3600,
            invite_gate_enabled=True,
            invite_authorized=lambda request: _verify_invite_cookie(
                request.cookies.get(INVITE_COOKIE_NAME),
                secret,
            ),
        )
    )
    client = TestClient(app)
    payload = {
        "email": "owner@example.com",
        "password": "password1",
        "name": "Owner",
        "nickname": "owner",
    }

    assert client.post("/register", json=payload).status_code == 403
    assert client.post(
        "/register",
        json=payload,
        headers={"Cookie": "authorized=true"},
    ).status_code == 403
    signed = _sign_invite_cookie(secret)
    assert client.post(
        "/register",
        json=payload,
        headers={"Cookie": f"{INVITE_COOKIE_NAME}={signed}"},
    ).status_code == 200


def test_public_and_non_loopback_config_cannot_disable_auth_or_reopen_registration():
    for env in (
        {"LATTICEAI_MODE": "public"},
        {"LATTICEAI_HOST": "0.0.0.0"},
    ):
        cfg = Config.from_env(
            {
                **env,
                "LATTICEAI_REQUIRE_AUTH": "false",
                "LATTICEAI_OPEN_REGISTRATION": "true",
            }
        )
        assert cfg.require_auth is True
        assert cfg.open_registration is False

    assert Config.from_env({}).invite_code == ""


def test_missing_invite_secrets_are_random_persistent_and_private(tmp_path, caplog):
    cfg = Config.from_env(
        {
            "LATTICEAI_MODE": "public",
            "LATTICEAI_INVITE_GATE_ENABLED": "true",
            "LATTICEAI_DATA_DIR": str(tmp_path),
        }
    )

    first = build_security_runtime(cfg)
    second = build_security_runtime(cfg)
    secret_file = tmp_path / "security_secrets.json"

    assert first["INVITE_CODE"]
    assert first["INVITE_CODE"] != "gemma-lattice-ai"
    assert first["INVITE_CODE"] == second["INVITE_CODE"]
    assert first["INVITE_COOKIE_SECRET"] == second["INVITE_COOKIE_SECRET"]
    assert first["SECURE_COOKIES"] is True
    assert secret_file.exists()
    if os.name == "posix":
        assert secret_file.stat().st_mode & 0o777 == 0o600
    assert "HTTPS/TLS" in caplog.text


def test_public_login_cookie_is_secure():
    users = {
        "user@example.com": {
            "password": "hash",
            "name": "User",
            "nickname": "user",
            "role": "user",
            "disabled": False,
        }
    }
    app = FastAPI()
    app.include_router(
        create_auth_router(
            load_users=lambda: users,
            save_users=lambda _users: None,
            hash_password=lambda value: value,
            verify_and_migrate=lambda *_args: True,
            create_session=lambda _email: "session-value",
            get_session_email=lambda _token: None,
            invalidate_session=lambda _token: None,
            extract_bearer_token=lambda _request: None,
            get_user_role=lambda _email, _users=None: "user",
            require_user=lambda _request: "user@example.com",
            check_ip_rate_limit=lambda *_args, **_kwargs: None,
            client_ip=lambda _request: "203.0.113.10",
            get_sso_settings=lambda: {},
            get_sso_discovery=lambda: None,
            public_sso_config=lambda: {},
            open_registration=False,
            session_ttl=3600,
            require_auth=True,
            secure_cookies=True,
        )
    )

    response = TestClient(app).post(
        "/login",
        json={"email": "user@example.com", "password": "password1"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


class _StatusService:
    def health_base(self, **_kwargs):
        return {"status": "online"}

    def health_full(self, base, engines):
        return {**base, "engines": engines}

    def runtime(self):
        return {"model": "private-model"}

    def engines_payload(self, engines):
        return {"engines": engines}


def test_sensitive_status_mode_and_engine_routes_require_auth():
    app = FastAPI()
    app.include_router(
        create_health_router(
            model_service=_StatusService(),
            engine_status=lambda: [{"engine": "private-engine"}],
            get_current_user=lambda request: request.headers.get("X-Test-User"),
            require_auth=True,
            app_version="test",
            app_mode="public",
        )
    )

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    for path in ("/mode", "/runtime_features", "/engines"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Test-User": "user@example.com"}).status_code == 200

    def deny_status(_request: Request):
        raise HTTPException(status_code=401, detail="auth required")

    assert _static_client(require_user=deny_status).get("/status").status_code == 401


def _scoped_graph(tmp_path):
    graph = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with graph._connect() as conn:
        graph._upsert_node(
            conn,
            "node:alpha",
            "Document",
            "alpha",
            workspace_id="org:alpha",
            visibility="workspace",
        )
        graph._upsert_node(
            conn,
            "node:beta",
            "Document",
            "beta",
            workspace_id="org:beta",
            visibility="workspace",
        )
        graph._upsert_node(conn, "node:legacy", "Document", "legacy")
    return graph


def test_scoped_graph_unknown_and_legacy_nodes_are_private_by_default(tmp_path):
    graph = _scoped_graph(tmp_path)
    candidates = [
        {"id": "node:alpha"},
        {"id": "node:beta"},
        {"id": "node:legacy"},
        {"id": "node:not-projected"},
    ]

    assert graph.filter_scoped_nodes(candidates, {"org:alpha"}) == [
        {"id": "node:alpha"}
    ]
    assert graph.filter_scoped_nodes(
        candidates,
        {"org:alpha"},
        include_legacy_global=True,
    ) == [{"id": "node:alpha"}, {"id": "node:legacy"}]


def test_scope_projection_query_failure_propagates_without_leaking(tmp_path, monkeypatch):
    graph = _scoped_graph(tmp_path)

    def fail_scope_query(_node_ids):
        raise sqlite3.OperationalError("nodes_v2 unavailable")

    monkeypatch.setattr(graph, "workspaces_of", fail_scope_query)
    with pytest.raises(sqlite3.OperationalError, match="nodes_v2 unavailable"):
        graph.filter_scoped_nodes(
            [{"id": "node:beta"}, {"id": "node:legacy"}],
            {"org:alpha"},
        )
