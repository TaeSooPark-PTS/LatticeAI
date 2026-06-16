from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.runtime.access_runtime import build_access_runtime


class RequestStub:
    def __init__(self, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.cookies = cookies or {}


def _runtime(*, users: dict | None = None, require_auth: bool = True, sessions: dict[str, str] | None = None):
    users = users or {}
    sessions = sessions or {}

    return build_access_runtime(
        config=SimpleNamespace(admin_emails=["owner@example.com"]),
        require_auth=require_auth,
        http_exception=HTTPException,
        request_type=RequestStub,
        load_users=lambda: users,
        get_session_email=lambda token: sessions.get(token),
        user_id_for_email=lambda _users, email: f"id:{email}",
    )


def test_get_user_role_preserves_legacy_lookup_order():
    users = {
        "first@example.com": {"id": "first-id", "role": "user"},
        "member@example.com": {"id": "member-id", "role": "admin"},
    }
    runtime = _runtime(users=users)

    assert runtime["get_user_role"]("member@example.com", users) == "admin"
    assert runtime["get_user_role"]("member-id", users) == "admin"
    assert runtime["get_user_role"]("owner@example.com", users) == "admin"
    assert runtime["get_user_role"]("first@example.com", users) == "user"
    assert runtime["get_user_role"]("unknown@example.com", users) == "user"


def test_extract_current_user_supports_bearer_and_cookie_tokens():
    runtime = _runtime(sessions={"bearer-token": "bearer@example.com", "cookie-token": "cookie@example.com"})

    assert runtime["_extract_bearer_token"](RequestStub(headers={"Authorization": "Bearer bearer-token"})) == "bearer-token"
    assert runtime["get_current_user"](RequestStub(headers={"Authorization": "Bearer bearer-token"})) == "bearer@example.com"
    assert runtime["get_current_user"](RequestStub(cookies={"session_token": "cookie-token"})) == "cookie@example.com"


def test_require_user_and_admin_enforce_auth_contract():
    users = {
        "admin@example.com": {"id": "admin-id", "role": "admin"},
        "member@example.com": {"id": "member-id", "role": "user"},
    }
    runtime = _runtime(users=users, sessions={"admin-token": "admin@example.com", "member-token": "member@example.com"})

    assert runtime["require_user"](RequestStub(headers={"Authorization": "Bearer member-token"})) == "member@example.com"
    assert runtime["require_admin"](RequestStub(headers={"Authorization": "Bearer admin-token"})) == ("admin@example.com", users)

    with pytest.raises(HTTPException) as unauthenticated:
        runtime["require_user"](RequestStub())
    assert unauthenticated.value.status_code == 401

    with pytest.raises(HTTPException) as forbidden:
        runtime["require_admin"](RequestStub(headers={"Authorization": "Bearer member-token"}))
    assert forbidden.value.status_code == 403


def test_public_user_keeps_identity_projection():
    users = {"member@example.com": {"name": "Member", "nickname": "Mem", "role": "user", "disabled": True}}
    runtime = _runtime(users=users)

    assert runtime["public_user"]("member@example.com", users["member@example.com"], users) == {
        "id": "id:member@example.com",
        "email": "member@example.com",
        "identity": "id:member@example.com",
        "name": "Member",
        "nickname": "Mem",
        "role": "user",
        "disabled": True,
    }
