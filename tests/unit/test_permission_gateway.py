from types import SimpleNamespace
import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.permissions import PermissionGateway, create_permissions_router


def _gateway(tmp_path):
    config = SimpleNamespace(
        discord_permission_webhook="",
        discord_bot_token="",
        discord_permission_channel="",
        permission_monitor_secret="",
    )
    return PermissionGateway(
        config=config,
        data_dir=tmp_path,
        require_admin=lambda request: "admin@example.com",
        get_current_user=lambda request: "user@example.com",
    )


def test_permission_queue_hashes_tokens_at_rest(tmp_path):
    gateway = _gateway(tmp_path)
    response = gateway.local_permission_response(
        str(tmp_path / "note.md"),
        "write",
        "user@example.com",
        content="hello",
    )
    token = response["approval_token"]
    raw_queue = (tmp_path / "permission_queue.json").read_text()

    assert token not in raw_queue
    assert gateway.token_hash(token) in raw_queue
    assert gateway.token_hint(token) in raw_queue
    assert token not in gateway.local_approvals

    gateway.local_approvals[gateway.token_hash(token)]["approved"] = True
    gateway.require_local_approval(
        token=token,
        path=str(tmp_path / "note.md"),
        action="write",
        user_email="user@example.com",
        content="hello",
    )


def test_permission_gateway_blocks_system_write_prefix(tmp_path):
    gateway = _gateway(tmp_path)
    with pytest.raises(HTTPException) as exc:
        gateway.ensure_path_allowed("/etc/passwd", action="write")
    assert exc.value.status_code == 403


def test_expired_permission_cleanup_preserves_current_token_lookup(tmp_path):
    gateway = _gateway(tmp_path)
    valid = gateway.local_permission_response(
        str(tmp_path / "note.md"),
        "write",
        "user@example.com",
        content="hello",
    )["approval_token"]
    expired = gateway.local_permission_response(
        str(tmp_path / "old.md"),
        "read",
        "user@example.com",
    )["approval_token"]
    valid_key = gateway.token_hash(valid)
    expired_key = gateway.token_hash(expired)
    gateway.local_approvals[valid_key]["approved"] = True
    gateway.local_approvals[expired_key]["expires_at"] = time.time() - 1

    gateway.require_local_approval(
        token=valid,
        path=str(tmp_path / "note.md"),
        action="write",
        user_email="user@example.com",
        content="hello",
    )

    assert expired_key not in gateway.local_approvals
    assert valid_key in gateway.local_approvals


def _permission_client(tmp_path):
    config = SimpleNamespace(
        discord_permission_webhook="",
        discord_bot_token="",
        discord_permission_channel="",
        permission_monitor_secret="monitor-secret",
    )

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
        config=config,
        data_dir=tmp_path,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), gateway


def test_requester_can_poll_but_cannot_self_approve(tmp_path):
    client, gateway = _permission_client(tmp_path)
    approval = gateway.local_permission_response(
        str(tmp_path / "note.md"), "read", "requester@example.com"
    )
    token = approval["approval_token"]
    requester = {"X-Test-User": "requester@example.com"}

    assert client.get(f"/permissions/status/{token}", headers=requester).json()["status"] == "pending"
    assert client.post(f"/permissions/approve/{token}", headers=requester).status_code == 403
    assert gateway.local_approvals[gateway.token_hash(token)]["approved"] is False

    other = client.get(
        f"/permissions/status/{token}",
        headers={"X-Test-User": "other@example.com"},
    )
    assert other.status_code == 403


def test_monitor_secret_or_admin_can_decide_permission(tmp_path):
    client, gateway = _permission_client(tmp_path)
    approve_token = gateway.local_permission_response(
        str(tmp_path / "read.md"), "read", "requester@example.com"
    )["approval_token"]
    deny_token = gateway.local_permission_response(
        str(tmp_path / "deny.md"), "read", "requester@example.com"
    )["approval_token"]

    monitor = client.post(
        f"/permissions/approve/{approve_token}",
        headers={"Authorization": "Bearer monitor-secret"},
    )
    assert monitor.status_code == 200
    assert gateway.local_approvals[gateway.token_hash(approve_token)]["approved"] is True

    requester = {"X-Test-User": "requester@example.com"}
    assert client.post(f"/permissions/deny/{deny_token}", headers=requester).status_code == 403
    denied = client.post(
        f"/permissions/deny/{deny_token}",
        headers={"X-Test-Admin": "true"},
    )
    assert denied.status_code == 200
    assert gateway.token_hash(deny_token) not in gateway.local_approvals
