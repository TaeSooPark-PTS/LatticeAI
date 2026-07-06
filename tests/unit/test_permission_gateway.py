from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.api.permissions import PermissionGateway


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
