"""T6.5: session tokens hashed at rest, real password policy, PKCE on SSO.

A process able to read sessions.json must not be able to hijack sessions;
pre-v4 plaintext session files migrate transparently. Passwords require
8+ chars with letters and digits. The SSO authorization request carries a
S256 PKCE challenge and the exchange sends the verifier.
"""

import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import latticeai.core.sessions as sessions_mod
from latticeai.api.auth import create_auth_router
from latticeai.core.sessions import SessionStore, _hash_token


# ── sessions hashed at rest ────────────────────────────────────────────────

def test_session_file_contains_no_raw_tokens(tmp_path):
    store = SessionStore(tmp_path)
    token = store.create("a@b.c")
    raw = (tmp_path / "sessions.json").read_text()
    assert token not in raw, "bearer token must never be persisted in plaintext"
    assert _hash_token(token) in raw
    assert store.get_email(token) == "a@b.c"
    store.invalidate(token)
    assert store.get_email(token) is None


def test_legacy_plaintext_sessions_migrate_and_survive(tmp_path):
    legacy_token = "legacy-raw-token-abc123"
    (tmp_path / "sessions.json").write_text(json.dumps({legacy_token: ["a@b.c", 9999999999.0]}))
    store = SessionStore(tmp_path)
    assert store.get_email(legacy_token) == "a@b.c", "pre-v4 sessions must survive the upgrade"
    raw = (tmp_path / "sessions.json").read_text()
    assert legacy_token not in raw, "migration must strip the plaintext token from disk"


def test_session_store_uses_injected_ttl(tmp_path, monkeypatch):
    store = SessionStore(tmp_path, ttl_seconds=10, refresh_threshold_seconds=999)
    base = 1_000_000.0
    monkeypatch.setattr(sessions_mod.time, "time", lambda: base)
    token = store.create("ttl@b.c")

    monkeypatch.setattr(sessions_mod.time, "time", lambda: base + 9)
    assert store.get_email(token) == "ttl@b.c"

    monkeypatch.setattr(sessions_mod.time, "time", lambda: base + 11)
    assert store.get_email(token) is None


# ── password policy ────────────────────────────────────────────────────────

def _client(users=None):
    users = users if users is not None else {}

    def require_user(_request: Request) -> str:
        return "a@b.c"

    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: users,
        save_users=lambda new: users.update(new),
        hash_password=lambda value: f"hashed:{value}",
        verify_and_migrate=lambda *_args: True,
        create_session=lambda email: f"session:{email}",
        get_session_email=lambda _token: None,
        invalidate_session=lambda _token: None,
        extract_bearer_token=lambda _request: None,
        get_user_role=lambda _email, _users=None: "user",
        require_user=require_user,
        check_ip_rate_limit=lambda *_args, **_kwargs: None,
        client_ip=lambda _request: "127.0.0.1",
        get_sso_settings=lambda: {},
        get_sso_discovery=lambda _settings: None,
        public_sso_config=lambda **_kwargs: {},
        open_registration=True,
        session_ttl=3600,
        require_auth=False,
    ))
    return TestClient(app)


def test_register_rejects_weak_passwords():
    client = _client()
    for weak in ("abc", "short1", "lettersonly", "12345678"):
        response = client.post("/register", json={"email": "x@y.z", "password": weak, "name": "X", "nickname": "x"})
        assert response.status_code == 400, f"weak password accepted: {weak!r}"


def test_register_accepts_policy_compliant_password():
    client = _client()
    response = client.post("/register", json={"email": "x@y.z", "password": "longenough1", "name": "X", "nickname": "x"})
    assert response.status_code == 200


def test_change_password_enforces_same_policy():
    users = {"a@b.c": {"password": "hashed:old", "role": "user"}}
    client = _client(users)
    response = client.post("/account/change-password", json={"current_password": "old", "new_password": "weak"})
    assert response.status_code == 400


# ── PKCE on the SSO flow ──────────────────────────────────────────────────

def test_sso_source_carries_pkce():
    import inspect
    import latticeai.api.auth as auth_mod
    src = inspect.getsource(auth_mod)
    assert 'code_challenge_method": "S256"' in src or "code_challenge_method\": \"S256\"" in src
    assert "code_verifier" in src
