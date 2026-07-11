"""SSO just-in-time provisioning must honor the signed invite gate."""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.auth import create_auth_router
from latticeai.api.static_routes import (
    INVITE_COOKIE_NAME,
    _sign_invite_cookie,
    _verify_invite_cookie,
)


def _invite_cookie(kind: str, secret: str) -> str | None:
    if kind == "missing":
        return None
    signed = _sign_invite_cookie(secret)
    if kind == "valid":
        return signed
    return f"{signed[:-1]}{'0' if signed[-1] != '0' else '1'}"


def _sso_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    users: dict,
    invite_secret: str,
) -> tuple[TestClient, list[str], list[dict], list[dict]]:
    sessions: list[str] = []
    token_requests: list[dict] = []
    token_verifications: list[dict] = []

    class TokenResponse:
        def json(self):
            return {"id_token": "signed-id-token"}

    class AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, data, **_kwargs):
            token_requests.append(dict(data))
            return TokenResponse()

    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)

    async def discovery():
        return {
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
            "jwks_uri": "https://identity.example/jwks",
            "issuer": "https://identity.example",
        }

    async def fetch_jwks(_url):
        return {"keys": []}

    def verify_id_token(_token, **kwargs):
        token_verifications.append(dict(kwargs))
        return {
            "email": "sso-user@example.com",
            "name": "SSO User",
            "given_name": "SSO",
        }

    app = FastAPI()
    app.include_router(
        create_auth_router(
            load_users=lambda: users,
            save_users=lambda _users: None,
            hash_password=lambda value: value,
            verify_and_migrate=lambda *_args: True,
            create_session=lambda email: sessions.append(email) or f"session:{email}",
            get_session_email=lambda _token: None,
            invalidate_session=lambda _token: None,
            extract_bearer_token=lambda _request: None,
            get_user_role=lambda email, current=None: (current or users)[email]["role"],
            require_user=lambda _request: "sso-user@example.com",
            check_ip_rate_limit=lambda *_args, **_kwargs: None,
            client_ip=lambda _request: "203.0.113.10",
            get_sso_settings=lambda: {
                "enabled": True,
                "client_id": "lattice-client",
                "client_secret": "provider-secret",
                "redirect_uri": "https://lattice.example/auth/sso/callback",
                "scopes": "openid email profile",
            },
            get_sso_discovery=discovery,
            public_sso_config=lambda: {"enabled": True},
            open_registration=False,
            session_ttl=3600,
            require_auth=True,
            secure_cookies=True,
            invite_gate_enabled=True,
            invite_authorized=lambda request: _verify_invite_cookie(
                request.cookies.get(INVITE_COOKIE_NAME),
                invite_secret,
            ),
            verify_id_token=verify_id_token,
            fetch_jwks=fetch_jwks,
        )
    )
    return TestClient(app, follow_redirects=False), sessions, token_requests, token_verifications


@pytest.mark.parametrize("existing_user", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("cookie_kind", ["valid", "invalid", "missing"])
def test_sso_invite_claim_is_bound_to_state_for_new_users_only(
    monkeypatch: pytest.MonkeyPatch,
    existing_user: bool,
    cookie_kind: str,
) -> None:
    users = {}
    if existing_user:
        users["sso-user@example.com"] = {
            "password": "",
            "name": "Existing SSO User",
            "nickname": "Existing",
            "role": "user",
            "disabled": False,
            "sso": True,
        }
    secret = "s" * 64
    client, sessions, token_requests, token_verifications = _sso_client(
        monkeypatch,
        users=users,
        invite_secret=secret,
    )
    cookie = _invite_cookie(cookie_kind, secret)
    headers = (
        {"Cookie": f"{INVITE_COOKIE_NAME}={cookie}"}
        if cookie is not None
        else {}
    )

    login = client.get("/auth/sso/login", headers=headers)

    assert login.status_code in {302, 307}
    authorization_query = parse_qs(urlparse(login.headers["location"]).query)
    state = authorization_query["state"][0]
    nonce = authorization_query["nonce"][0]
    assert authorization_query["code_challenge_method"] == ["S256"]

    callback = client.get(
        f"/auth/sso/callback?code=provider-code&state={quote(state)}",
    )
    should_succeed = existing_user or cookie_kind == "valid"

    assert token_requests[0]["code_verifier"]
    assert token_verifications[0]["nonce"] == nonce
    if should_succeed:
        assert callback.status_code == 302
        assert callback.headers["location"] == "/app"
        assert sessions == ["sso-user@example.com"]
        assert users["sso-user@example.com"]["disabled"] is False
        if not existing_user:
            assert users["sso-user@example.com"]["role"] == "admin"
    else:
        assert callback.status_code == 403
        assert "sso-user@example.com" not in users
        assert sessions == []

    # State remains single-use regardless of provisioning outcome.
    replay = client.get(
        f"/auth/sso/callback?code=provider-code&state={quote(state)}",
    )
    assert replay.status_code == 400
