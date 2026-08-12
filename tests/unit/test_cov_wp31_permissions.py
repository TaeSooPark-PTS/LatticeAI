"""wp31: the permission gateway's failure paths and the approval route states.

``tests/unit/test_permission_gateway.py`` covers the happy paths and the
self-approval refusal. What never ran: the queue's corruption/IO tolerance, the
duplicate-hint 409, both Discord notify failures, the sign-in guard, each
``require_local_approval`` refusal, and the expired / unknown / approved states
of the pending, approve, deny and status routes.

Every "expired" case here sets ``expires_at`` to a past timestamp explicitly —
no sleeping, so the outcome does not depend on how fast the suite runs.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api import permissions as permissions_module
from latticeai.api.permissions import PermissionGateway, create_permissions_router

REQUESTER = "requester@example.com"
ADMIN_HEADERS = {"X-Test-Admin": "true"}


def _config(**overrides: Any) -> SimpleNamespace:
    base = {
        "discord_permission_webhook": "",
        "discord_bot_token": "",
        "discord_permission_channel": "",
        "permission_monitor_secret": "",
        "port": 4825,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _gateway(tmp_path, **overrides: Any) -> PermissionGateway:
    return PermissionGateway(
        config=_config(**overrides),
        data_dir=tmp_path,
        require_admin=lambda request: "admin@example.com",
        get_current_user=lambda request: request.headers.get("X-Test-User"),
    )


def _client(tmp_path):
    def get_current_user(request: Request):
        return request.headers.get("X-Test-User")

    def require_user(request: Request):
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    def require_admin(request: Request):
        if request.headers.get("X-Test-Admin") != "true":
            raise HTTPException(status_code=403, detail="admin required")
        return "admin@example.com", {}

    router, gateway = create_permissions_router(
        config=_config(),
        data_dir=tmp_path,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), gateway


def _request(user: str = REQUESTER) -> Request:
    headers = [(b"x-test-user", user.encode())] if user else []
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})


# ── queue durability ─────────────────────────────────────────────────────────


def test_a_corrupt_queue_file_is_replaced_rather_than_crashing(tmp_path):
    gateway = _gateway(tmp_path)
    (tmp_path / "permission_queue.json").write_text("{not json", encoding="utf-8")

    approval = gateway.local_permission_response(
        str(tmp_path / "note.md"), "read", REQUESTER
    )

    queue = json.loads((tmp_path / "permission_queue.json").read_text(encoding="utf-8"))
    assert list(queue) == [gateway.token_hash(approval["approval_token"])]


def test_a_queue_write_failure_never_blocks_the_request(tmp_path, monkeypatch, caplog):
    gateway = _gateway(tmp_path)

    def explode(path, payload):
        raise OSError("disk is read-only")

    monkeypatch.setattr(permissions_module, "atomic_write_json", explode)

    with caplog.at_level("WARNING"):
        approval = gateway.local_permission_response(
            str(tmp_path / "note.md"), "read", REQUESTER
        )
        gateway._perm_queue_remove_key(
            gateway.token_hash(approval["approval_token"])
        )

    assert approval["approval_token"]
    assert gateway.token_hash(approval["approval_token"]) in gateway.local_approvals
    assert not (tmp_path / "permission_queue.json").exists()
    messages = [record.getMessage() for record in caplog.records]
    assert any("perm_queue_write failed" in message for message in messages)
    assert any("perm_queue_remove failed" in message for message in messages)


def test_removing_by_token_clears_the_queue_entry(tmp_path):
    gateway = _gateway(tmp_path)
    token = gateway.local_permission_response(
        str(tmp_path / "note.md"), "read", REQUESTER
    )["approval_token"]

    before = json.loads((tmp_path / "permission_queue.json").read_text(encoding="utf-8"))
    gateway._perm_queue_remove_key(gateway.token_hash(token))
    after = json.loads((tmp_path / "permission_queue.json").read_text(encoding="utf-8"))

    assert list(before) == [gateway.token_hash(token)]
    assert after == {}


def test_an_ambiguous_request_hint_is_refused_instead_of_guessed(tmp_path):
    gateway = _gateway(tmp_path)
    for suffix in ("one", "two"):
        gateway.local_approvals["key-" + suffix] = {
            "path": "/tmp/" + suffix,
            "action": "read",
            "user_email": REQUESTER,
            "expires_at": time.time() + 60,
            "approved": False,
            "token_hint": "abcdefgh",
        }

    with pytest.raises(HTTPException) as raised:
        gateway.resolve_approval_key("abcdefgh")

    assert raised.value.status_code == 409


# ── Discord notification failures ────────────────────────────────────────────


def test_a_failing_discord_bot_call_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    gateway = _gateway(
        tmp_path, discord_bot_token="bot-token", discord_permission_channel="chan-1"
    )

    def explode(request, timeout):
        raise OSError("discord unreachable")

    monkeypatch.setattr("urllib.request.urlopen", explode)

    with caplog.at_level("WARNING"):
        gateway._notify_discord_permission_sync(
            "abcdefgh-token", str(tmp_path / "note.md"), "read", REQUESTER
        )

    assert any(
        "Discord bot permission notify failed" in record.getMessage()
        for record in caplog.records
    )


def test_a_failing_discord_webhook_call_is_logged_not_raised(
    tmp_path, monkeypatch, caplog
):
    gateway = _gateway(tmp_path, discord_permission_webhook="https://discord/webhook")

    def explode(request, timeout):
        raise OSError("webhook unreachable")

    monkeypatch.setattr("urllib.request.urlopen", explode)

    with caplog.at_level("WARNING"):
        gateway._notify_discord_permission_sync(
            "abcdefgh-token", str(tmp_path / "note.md"), "write", REQUESTER
        )

    assert any(
        "Discord permission webhook failed" in record.getMessage()
        for record in caplog.records
    )


# ── local file access guards ─────────────────────────────────────────────────


def test_local_file_access_requires_a_signed_in_session(tmp_path):
    gateway = _gateway(tmp_path)

    assert gateway.require_local_user(_request(REQUESTER)) == REQUESTER
    with pytest.raises(HTTPException) as raised:
        gateway.require_local_user(_request(""))
    assert raised.value.status_code == 401


def _approval(gateway, tmp_path, *, action="read", content=""):
    return gateway.local_permission_response(
        str(tmp_path / "note.md"), action, REQUESTER, content=content
    )["approval_token"]


def test_an_unknown_or_unapproved_token_is_refused(tmp_path):
    gateway = _gateway(tmp_path)
    token = _approval(gateway, tmp_path)
    kwargs: Dict[str, Any] = {
        "path": str(tmp_path / "note.md"),
        "action": "read",
        "user_email": REQUESTER,
    }

    with pytest.raises(HTTPException) as unknown:
        gateway.require_local_approval(token="not-a-real-token", **kwargs)
    with pytest.raises(HTTPException) as pending:
        gateway.require_local_approval(token=token, **kwargs)

    assert unknown.value.status_code == 403
    assert "만료" in unknown.value.detail
    assert pending.value.status_code == 403
    assert "아직 승인되지" in pending.value.detail


def test_another_users_approval_cannot_be_borrowed(tmp_path):
    gateway = _gateway(tmp_path)
    token = _approval(gateway, tmp_path)
    gateway.local_approvals[gateway.token_hash(token)]["approved"] = True

    with pytest.raises(HTTPException) as raised:
        gateway.require_local_approval(
            token=token,
            path=str(tmp_path / "note.md"),
            action="read",
            user_email="someone-else@example.com",
        )

    assert raised.value.status_code == 403
    assert "다른 사용자" in raised.value.detail


# ── routes ───────────────────────────────────────────────────────────────────


def test_pending_listing_hides_expired_requests(tmp_path):
    client, gateway = _client(tmp_path)
    live = _approval(gateway, tmp_path)
    stale = gateway.local_permission_response(
        str(tmp_path / "old.md"), "read", REQUESTER
    )["approval_token"]
    gateway.local_approvals[gateway.token_hash(stale)]["expires_at"] = time.time() - 1

    body = client.get("/permissions/pending", headers=ADMIN_HEADERS).json()

    assert body["count"] == 1
    assert list(body["pending"]) == [gateway.token_hint(live)]
    entry = body["pending"][gateway.token_hint(live)]
    assert entry["action_label"] == "파일 읽기"
    assert entry["approved"] is False
    assert entry["expires_in"] > 0


def test_approving_an_unknown_or_expired_token(tmp_path):
    client, gateway = _client(tmp_path)
    stale = _approval(gateway, tmp_path)
    gateway.local_approvals[gateway.token_hash(stale)]["expires_at"] = time.time() - 1

    unknown = client.post("/permissions/approve/ghost", headers=ADMIN_HEADERS)
    expired = client.post("/permissions/approve/" + stale, headers=ADMIN_HEADERS)

    assert unknown.status_code == 404
    assert expired.status_code == 410
    assert gateway.token_hash(stale) not in gateway.local_approvals


def test_denying_an_unknown_token_is_404(tmp_path):
    client, _gateway = _client(tmp_path)

    response = client.post("/permissions/deny/ghost", headers=ADMIN_HEADERS)

    assert response.status_code == 404


def test_status_reports_each_lifecycle_state(tmp_path):
    client, gateway = _client(tmp_path)
    requester = {"X-Test-User": REQUESTER}
    token = _approval(gateway, tmp_path)
    key = gateway.token_hash(token)

    unknown = client.get("/permissions/status/ghost-token", headers=requester).json()
    pending = client.get("/permissions/status/" + token, headers=requester).json()
    gateway.local_approvals[key]["approved"] = True
    approved = client.get("/permissions/status/" + token, headers=requester).json()
    gateway.local_approvals[key]["expires_at"] = time.time() - 1
    expired = client.get("/permissions/status/" + token, headers=requester).json()

    assert unknown["status"] == "denied_or_expired"
    assert pending["status"] == "pending"
    assert pending["expires_in"] > 0
    assert approved["status"] == "approved"
    assert approved["token_hint"] == gateway.token_hint(token)
    assert expired["status"] == "expired"
