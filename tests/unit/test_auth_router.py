from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.auth import create_auth_router


def _client(*, require_auth: bool) -> TestClient:
    def require_user(_request: Request) -> str:
        if require_auth:
            raise HTTPException(status_code=401, detail="auth required")
        return ""

    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: {},
        save_users=lambda _users: None,
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
        require_auth=require_auth,
    ))
    return TestClient(app)


def test_profile_returns_local_identity_when_auth_disabled():
    response = _client(require_auth=False).get("/account/profile")

    assert response.status_code == 200
    assert response.json() == {
        "email": "",
        "name": "Local User",
        "nickname": "You",
        "role": "admin",
        "is_admin": True,
    }


def test_profile_stays_protected_when_auth_required():
    response = _client(require_auth=True).get("/account/profile")

    assert response.status_code == 401
