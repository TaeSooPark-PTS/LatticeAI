"""wp17 — auth router refusals, account maintenance and the SSO callback.

The router is built through its factory with injected fakes (the idiom in
``tests/unit/test_auth_router.py``); the SSO tests drive the real
``/auth/sso/login`` → ``/auth/sso/callback`` handshake so the server-side state,
nonce and PKCE verifier are the ones the endpoint actually minted.

Localized refusals are asserted in both catalog languages, since the language
comes from the request header rather than the call site.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.auth import create_auth_router
from latticeai.core.oidc import OIDCValidationError

SSO_EMAIL = "sso-user@example.com"


# ── local account router ───────────────────────────────────────────────────

def _account_client(
    *,
    users: Optional[Dict[str, Any]] = None,
    open_registration: bool = True,
    require_auth: bool = True,
    current_user: str = "",
    password_ok: bool = True,
    ensure_identity: Any = None,
    bearer_token: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
):
    live_users: Dict[str, Any] = {} if users is None else users
    tracker = state if state is not None else {}
    tracker.setdefault("users", live_users)
    tracker.setdefault("saved", [])
    tracker.setdefault("invalidated", [])
    tracker.setdefault("sso_config_calls", [])

    def _save_users(updated: Dict[str, Any]) -> None:
        tracker["saved"].append({email: dict(user) for email, user in updated.items()})

    def _public_sso_config(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        tracker["sso_config_calls"].append(True)
        return {"enabled": False, "provider_name": ""}

    async def _discovery() -> Optional[Dict[str, Any]]:
        return None

    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: live_users,
        save_users=_save_users,
        hash_password=lambda value: f"hashed:{value}",
        verify_and_migrate=lambda *_args: password_ok,
        create_session=lambda email: f"session:{email}",
        get_session_email=lambda _token: None,
        invalidate_session=lambda token: tracker["invalidated"].append(token),
        extract_bearer_token=lambda _request: bearer_token,
        get_user_role=lambda email, all_users=None: (all_users or live_users).get(email, {}).get("role", "user"),
        require_user=lambda _request: current_user,
        check_ip_rate_limit=lambda *_args, **_kwargs: None,
        client_ip=lambda _request: "127.0.0.1",
        get_sso_settings=lambda: {},
        get_sso_discovery=_discovery,
        public_sso_config=_public_sso_config,
        open_registration=open_registration,
        session_ttl=3600,
        require_auth=require_auth,
        ensure_identity=ensure_identity,
    ))
    return TestClient(app), tracker


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ko", "회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요."),
        ("en", "Sign-up is turned off. Ask an administrator to enable it."),
    ],
)
def test_register_refuses_when_registration_is_closed(language, expected):
    client, tracker = _account_client(open_registration=False)

    response = client.post(
        "/register",
        json={"email": "new@example.com", "password": "longenough1", "name": "N", "nickname": "n"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == expected
    assert tracker["saved"] == []


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "이미 존재하는 이메일입니다."), ("en", "That email address is already registered.")],
)
def test_register_refuses_a_taken_email(language, expected):
    client, _ = _account_client(users={"taken@example.com": {"role": "admin"}})

    response = client.post(
        "/register",
        json={"email": " Taken@Example.com ", "password": "longenough1", "name": "N", "nickname": "n"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected


def test_register_provisions_identity_for_the_first_user():
    provisioned: List[tuple] = []
    client, tracker = _account_client(ensure_identity=lambda email, user: provisioned.append((email, user["role"])))

    body = client.post(
        "/register",
        json={"email": "First@Example.com", "password": "longenough1", "name": "First", "nickname": "f"},
    ).json()

    assert body["role"] == "admin"
    assert provisioned == [("first@example.com", "admin")]
    assert tracker["users"]["first@example.com"]["password"] == "hashed:longenough1"


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "이메일 또는 비밀번호가 틀렸습니다."), ("en", "That email address or password is not correct.")],
)
def test_login_refuses_bad_credentials(language, expected):
    client, _ = _account_client(
        users={"user@example.com": {"password": "hashed:x", "role": "user", "name": "U", "nickname": "u"}},
        password_ok=False,
    )

    response = client.post(
        "/login",
        json={"email": "user@example.com", "password": "wrong"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == expected
    assert "session_token" not in response.cookies


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "비활성화된 계정입니다."), ("en", "This account has been disabled.")],
)
def test_login_refuses_a_disabled_account(language, expected):
    client, _ = _account_client(users={
        "user@example.com": {"password": "hashed:x", "role": "user", "name": "U", "nickname": "u", "disabled": True},
    })

    response = client.post(
        "/login",
        json={"email": "user@example.com", "password": "longenough1"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == expected


def test_sso_config_endpoint_returns_the_public_projection():
    client, tracker = _account_client()

    body = client.get("/auth/sso/config").json()

    assert body == {"enabled": False, "provider_name": ""}
    assert tracker["sso_config_calls"] == [True]


def test_logout_invalidates_the_bearer_session_and_clears_the_cookie():
    client, tracker = _account_client(bearer_token="session:abc")

    response = client.post("/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert tracker["invalidated"] == ["session:abc"]
    assert 'session_token=""' in response.headers["set-cookie"] or "session_token=;" in response.headers["set-cookie"]


def test_logout_without_a_token_still_clears_the_cookie():
    client, tracker = _account_client(bearer_token=None)

    response = client.post("/logout")

    assert response.status_code == 200
    assert tracker["invalidated"] == []


# ── change password ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "인증이 필요합니다."), ("en", "You need to sign in first.")],
)
def test_change_password_requires_a_signed_in_user(language, expected):
    client, _ = _account_client(current_user="")

    response = client.post(
        "/account/change-password",
        json={"current_password": "old1pass", "new_password": "new1pass"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == expected


def test_change_password_404s_for_a_vanished_user():
    client, _ = _account_client(users={}, current_user="ghost@example.com")

    response = client.post(
        "/account/change-password",
        json={"current_password": "old1pass", "new_password": "new1pass"},
        headers={"X-Lattice-Language": "en"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No such user."


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "현재 비밀번호가 틀렸습니다."), ("en", "Your current password is not correct.")],
)
def test_change_password_refuses_a_wrong_current_password(language, expected):
    users = {"user@example.com": {"password": "hashed:old1pass", "role": "user"}}
    client, tracker = _account_client(users=users, current_user="user@example.com", password_ok=False)

    response = client.post(
        "/account/change-password",
        json={"current_password": "nope1234", "new_password": "new1pass"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == expected
    assert tracker["saved"] == []
    assert users["user@example.com"]["password"] == "hashed:old1pass"


def test_change_password_rehashes_and_persists():
    users = {"user@example.com": {"password": "hashed:old1pass", "role": "user"}}
    client, tracker = _account_client(users=users, current_user="User@Example.com")

    response = client.post(
        "/account/change-password",
        json={"current_password": "old1pass", "new_password": "brandnew1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert users["user@example.com"]["password"] == "hashed:brandnew1"
    assert tracker["saved"], "the new hash must be written"


# ── profile ────────────────────────────────────────────────────────────────

def test_update_profile_requires_a_signed_in_user():
    client, _ = _account_client(current_user="")

    response = client.patch("/account/profile", json={"name": "New"}, headers={"X-Lattice-Language": "en"})

    assert response.status_code == 401
    assert response.json()["detail"] == "You need to sign in first."


@pytest.mark.parametrize(
    ("payload", "language", "expected"),
    [
        ({"name": "   "}, "ko", "이름을 입력해주세요."),
        ({"name": ""}, "en", "Please enter a name."),
        ({"nickname": "  "}, "ko", "닉네임을 입력해주세요."),
        ({"nickname": ""}, "en", "Please enter a nickname."),
    ],
)
def test_update_profile_rejects_blank_fields(payload, language, expected):
    users = {"user@example.com": {"name": "Old", "nickname": "old", "role": "user"}}
    client, _ = _account_client(users=users, current_user="user@example.com")

    response = client.patch("/account/profile", json=payload, headers={"X-Lattice-Language": language})

    assert response.status_code == 400
    assert response.json()["detail"] == expected
    assert users["user@example.com"]["name"] == "Old"


def test_update_profile_404s_for_a_vanished_user():
    client, _ = _account_client(users={}, current_user="ghost@example.com")

    response = client.patch("/account/profile", json={"name": "New"})

    assert response.status_code == 404
    assert response.json()["detail"] == "사용자를 찾을 수 없습니다."


def test_update_profile_trims_and_persists_both_fields():
    users = {"user@example.com": {"name": "Old", "nickname": "old", "role": "user"}}
    client, tracker = _account_client(users=users, current_user="user@example.com")

    body = client.patch("/account/profile", json={"name": "  New Name  ", "nickname": " nn "}).json()

    assert body == {"status": "ok", "name": "New Name", "nickname": "nn"}
    assert users["user@example.com"] == {"name": "New Name", "nickname": "nn", "role": "user"}
    assert tracker["saved"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "인증이 필요합니다."), ("en", "You need to sign in first.")],
)
def test_get_profile_refuses_an_anonymous_request_when_auth_is_required(language, expected):
    client, _ = _account_client(current_user="", require_auth=True)

    response = client.get("/account/profile", headers={"X-Lattice-Language": language})

    assert response.status_code == 401
    assert response.json()["detail"] == expected


def test_get_profile_404s_for_a_vanished_user():
    client, _ = _account_client(users={}, current_user="ghost@example.com")

    response = client.get("/account/profile", headers={"X-Lattice-Language": "en"})

    assert response.status_code == 404
    assert response.json()["detail"] == "No such user."


def test_get_profile_returns_the_stored_identity_and_admin_flag():
    users = {"boss@example.com": {"name": "Boss", "nickname": "b", "role": "admin"}}
    client, _ = _account_client(users=users, current_user="Boss@Example.com")

    assert client.get("/account/profile").json() == {
        "email": "boss@example.com",
        "name": "Boss",
        "nickname": "b",
        "role": "admin",
        "is_admin": True,
    }


# ── SSO login / callback ───────────────────────────────────────────────────

class _SSOWorld:
    """Mutable provider state so a test can change it between login and callback."""

    def __init__(self, *, users: Optional[Dict[str, Any]] = None):
        self.users: Dict[str, Any] = {} if users is None else users
        self.settings: Dict[str, Any] = {
            "enabled": True,
            "client_id": "lattice-client",
            "client_secret": "provider-secret",
            "redirect_uri": "https://lattice.example/auth/sso/callback",
            "scopes": "openid email profile",
        }
        self.discovery: Optional[Dict[str, Any]] = {
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
            "jwks_uri": "https://identity.example/jwks",
            "issuer": "https://identity.example",
        }
        self.token_response: Dict[str, Any] = {"id_token": "signed-id-token"}
        self.payload: Dict[str, Any] = {"email": SSO_EMAIL, "name": "SSO User", "given_name": "SSO"}
        self.jwks_error: Optional[Exception] = None
        self.verify_error: Optional[Exception] = None
        self.identities: List[str] = []
        self.sessions: List[str] = []
        self.saved: List[Dict[str, Any]] = []


def _sso_client(world: _SSOWorld, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    class _TokenResponse:
        def json(self):
            return dict(world.token_response)

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return _TokenResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    async def _discovery():
        return world.discovery

    async def _fetch_jwks(_url):
        if world.jwks_error is not None:
            raise world.jwks_error
        return {"keys": []}

    def _verify_id_token(_token, **_kwargs):
        if world.verify_error is not None:
            raise world.verify_error
        return dict(world.payload)

    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: world.users,
        save_users=lambda updated: world.saved.append(dict(updated)),
        hash_password=lambda value: value,
        verify_and_migrate=lambda *_args: True,
        create_session=lambda email: world.sessions.append(email) or f"session:{email}",
        get_session_email=lambda _token: None,
        invalidate_session=lambda _token: None,
        extract_bearer_token=lambda _request: None,
        get_user_role=lambda email, all_users=None: "user",
        require_user=lambda _request: SSO_EMAIL,
        check_ip_rate_limit=lambda *_args, **_kwargs: None,
        client_ip=lambda _request: "203.0.113.10",
        get_sso_settings=lambda: world.settings,
        get_sso_discovery=_discovery,
        public_sso_config=lambda *_args, **_kwargs: {"enabled": True},
        open_registration=False,
        session_ttl=3600,
        require_auth=True,
        ensure_identity=lambda email, _user: world.identities.append(email),
        verify_id_token=_verify_id_token,
        fetch_jwks=_fetch_jwks,
    ))
    return TestClient(app, follow_redirects=False)


def _begin_login(client: TestClient) -> str:
    """Run the real /auth/sso/login and return the state it minted."""
    response = client.get("/auth/sso/login")
    assert response.status_code == 307, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["state"][0]


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "SSO가 설정되지 않았습니다."), ("en", "Single sign-on is not set up.")],
)
def test_sso_login_refuses_when_the_provider_is_off(language, expected, monkeypatch):
    world = _SSOWorld()
    world.settings = {"enabled": False}
    world.discovery = None
    client = _sso_client(world, monkeypatch)

    response = client.get("/auth/sso/login", headers={"X-Lattice-Language": language})

    assert response.status_code == 503
    assert response.json()["detail"] == expected


def test_sso_callback_forwards_a_provider_error_to_the_login_page(monkeypatch):
    world = _SSOWorld()
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get("/auth/sso/callback", params={"state": state, "error": "access_denied"})

    assert response.status_code == 307
    assert response.headers["location"] == "/?sso_error=access_denied"
    assert world.sessions == []


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "SSO 설정 오류입니다."), ("en", "Single sign-on is misconfigured.")],
)
def test_sso_callback_refuses_when_the_provider_is_turned_off_mid_flight(language, expected, monkeypatch):
    world = _SSOWorld()
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)
    world.settings = {**world.settings, "enabled": False}

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "ID 토큰을 받지 못했습니다."), ("en", "The identity provider did not return an ID token.")],
)
def test_sso_callback_refuses_a_token_response_without_an_id_token(language, expected, monkeypatch):
    world = _SSOWorld()
    world.token_response = {"access_token": "opaque"}
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "SSO 토큰 검증에 실패했습니다."), ("en", "The sign-on token could not be verified.")],
)
def test_sso_callback_fails_closed_on_a_rejected_id_token(language, expected, monkeypatch):
    world = _SSOWorld()
    world.verify_error = OIDCValidationError("nonce mismatch")
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == expected
    assert world.users == {}, "a rejected token must never provision an account"


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "SSO 공급자 검증에 실패했습니다."), ("en", "The sign-on provider could not be verified.")],
)
def test_sso_callback_fails_closed_when_the_jwks_fetch_breaks(language, expected, monkeypatch):
    world = _SSOWorld()
    world.jwks_error = RuntimeError("jwks unreachable")
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "이메일을 확인할 수 없습니다."), ("en", "The identity provider did not share an email address.")],
)
def test_sso_callback_refuses_a_payload_without_any_email_claim(language, expected, monkeypatch):
    world = _SSOWorld()
    world.payload = {"name": "Anonymous"}
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected


def test_sso_callback_provisions_a_new_user_and_signs_them_in(monkeypatch):
    world = _SSOWorld()
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get("/auth/sso/callback", params={"state": state, "code": "auth-code"})

    assert response.status_code == 302
    assert response.headers["location"] == "/app"
    assert world.users[SSO_EMAIL]["role"] == "admin", "the first SSO account becomes the admin"
    assert world.users[SSO_EMAIL]["sso"] is True
    assert world.identities == [SSO_EMAIL]
    assert world.sessions == [SSO_EMAIL]
    assert "session_token=" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "비활성화된 계정입니다."), ("en", "This account has been disabled.")],
)
def test_sso_callback_refuses_a_disabled_account(language, expected, monkeypatch):
    world = _SSOWorld(users={SSO_EMAIL: {"password": "", "role": "user", "disabled": True, "sso": True}})
    client = _sso_client(world, monkeypatch)
    state = _begin_login(client)

    response = client.get(
        "/auth/sso/callback",
        params={"state": state, "code": "auth-code"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == expected
    assert world.sessions == []
