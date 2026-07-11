from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.runtime.access_runtime import build_access_runtime
from latticeai.services.tool_dispatch import ToolDispatchService
from latticeai.tools import DEFAULT_TOOL_REGISTRY


class RequestStub:
    def __init__(self, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.cookies = cookies or {}


def _runtime(
    *,
    users: dict | None = None,
    require_auth: bool = True,
    sessions: dict[str, str] | None = None,
    is_public: bool = False,
    network_exposed: bool = False,
):
    users = users or {}
    sessions = sessions or {}

    return build_access_runtime(
        config=SimpleNamespace(
            admin_emails=["owner@example.com"],
            is_public=is_public,
            network_exposed=network_exposed,
        ),
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
    users = {
        "bearer@example.com": {"id": "bearer-id", "role": "user"},
        "cookie@example.com": {"id": "cookie-id", "role": "user"},
    }
    runtime = _runtime(
        users=users,
        sessions={"bearer-token": "bearer@example.com", "cookie-token": "cookie@example.com"},
    )

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


@pytest.mark.parametrize(
    "users",
    [
        {},
        {"owner@example.com": {"id": "owner-id", "role": "admin", "disabled": True}},
    ],
    ids=["deleted", "disabled"],
)
def test_deleted_or_disabled_account_invalidates_existing_session(users):
    runtime = _runtime(users=users, sessions={"stale-token": "owner@example.com"})
    request = RequestStub(headers={"Authorization": "Bearer stale-token"})

    assert runtime["get_current_user"](request) is None
    with pytest.raises(HTTPException) as user_error:
        runtime["require_user"](request)
    assert user_error.value.status_code == 401

    with pytest.raises(HTTPException) as admin_error:
        runtime["require_admin"](request)
    assert admin_error.value.status_code == 403


def test_account_store_failure_fails_session_closed():
    runtime = build_access_runtime(
        config=SimpleNamespace(admin_emails=["owner@example.com"]),
        require_auth=True,
        http_exception=HTTPException,
        request_type=RequestStub,
        load_users=lambda: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
        get_session_email=lambda _token: "owner@example.com",
        user_id_for_email=lambda _users, email: f"id:{email}",
    )
    request = RequestStub(headers={"Authorization": "Bearer token"})

    assert runtime["get_current_user"](request) is None
    with pytest.raises(HTTPException) as exc:
        runtime["require_user"](request)
    assert exc.value.status_code == 401


def test_loopback_no_auth_identity_is_authorized_as_trusted_local_owner():
    runtime = _runtime(require_auth=False)
    request = RequestStub()

    # Preserve the long-standing anonymous Local User identity while making
    # its authorization role explicit and consistent across policy callers.
    assert runtime["require_user"](request) == ""
    assert runtime["get_user_role"]("", {}) == "owner"
    assert runtime["require_admin"](request) == ("", {})

    service = ToolDispatchService(registry=DEFAULT_TOOL_REGISTRY)
    service.configure(
        load_users=lambda: {},
        get_user_role=runtime["get_user_role"],
    )
    for tool_name in (
        "computer_status",
        "computer_screenshot",
        "network_status",
        "knowledge_search",
    ):
        policy = service.enforce_policy(
            tool_name,
            {"query": "local"} if tool_name == "knowledge_search" else {},
            current_user=runtime["require_user"](request),
            source="http",
        )
        assert policy["destructive"] is False


def test_loopback_no_auth_preserves_a_valid_optional_session_identity():
    users = {
        "member@example.com": {
            "id": "member-id",
            "role": "user",
            "disabled": False,
        }
    }
    runtime = _runtime(
        users=users,
        require_auth=False,
        sessions={"member-token": "member@example.com"},
    )
    request = RequestStub(headers={"Authorization": "Bearer member-token"})

    assert runtime["get_current_user"](request) == "member@example.com"
    assert runtime["require_user"](request) == "member@example.com"


@pytest.mark.parametrize(
    "exposure",
    [
        {"is_public": True},
        {"network_exposed": True},
    ],
)
def test_exposed_runtime_never_inherits_no_auth_local_owner_trust(exposure):
    runtime = _runtime(require_auth=False, **exposure)
    request = RequestStub()

    assert runtime["get_user_role"]("", {}) == "user"
    with pytest.raises(HTTPException) as user_error:
        runtime["require_user"](request)
    assert user_error.value.status_code == 401
    with pytest.raises(HTTPException) as admin_error:
        runtime["require_admin"](request)
    assert admin_error.value.status_code == 403
