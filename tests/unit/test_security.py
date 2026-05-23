"""Unit tests for security-sensitive helpers in server.py."""
import sys
import time
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server import (
    _bytes_match_extension,
    _rate_buckets,
    enforce_rate_limit,
    hash_password,
    verify_password,
    _agent_risk,
    _host_is_loopback,
    _local_permission_response,
    _require_local_approval,
    _LOCAL_WRITE_BLOCKED_PREFIXES,
)
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_password_hash_not_plaintext():
    h = hash_password("hunter2")
    assert "hunter2" not in h
    assert ":" in h  # salt:hash format


def test_password_hash_unique_per_call():
    """Same input must yield different hashes (salted)."""
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
    assert verify_password("same", h1)
    assert verify_password("same", h2)


# ---------------------------------------------------------------------------
# MIME / magic-number sniffing
# ---------------------------------------------------------------------------

def test_bytes_match_pdf():
    assert _bytes_match_extension(b"%PDF-1.7\n...", ".pdf")


def test_bytes_match_pdf_rejects_zip_bytes():
    assert not _bytes_match_extension(b"PK\x03\x04...", ".pdf")


def test_bytes_match_docx_is_zip():
    assert _bytes_match_extension(b"PK\x03\x04...", ".docx")


def test_bytes_match_png():
    assert _bytes_match_extension(b"\x89PNG\r\n\x1a\nrest", ".png")


def test_bytes_match_txt_skips_check():
    """Text-like formats have no magic — always accepted."""
    assert _bytes_match_extension(b"anything goes", ".txt")
    assert _bytes_match_extension(b"anything goes", ".md")
    assert _bytes_match_extension(b"anything goes", ".csv")


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_allows_within_capacity():
    _rate_buckets.clear()
    for _ in range(10):
        enforce_rate_limit("test_user@example.com", "agent")  # capacity 10


def test_rate_limit_blocks_over_capacity():
    _rate_buckets.clear()
    for _ in range(10):
        enforce_rate_limit("burst_user@example.com", "agent")
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit("burst_user@example.com", "agent")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_rate_limit_skips_unauth():
    """Empty email = no rate-limit (anon health-check style)."""
    _rate_buckets.clear()
    for _ in range(200):
        enforce_rate_limit("", "agent")  # never raises


# ---------------------------------------------------------------------------
# Harness risk classification
# ---------------------------------------------------------------------------

def test_agent_risk_read_only_is_low():
    assert _agent_risk("local_read", {"path": "/tmp/x"}) == "low"
    assert _agent_risk("list_dir", {}) == "low"


def test_agent_risk_write_is_medium():
    assert _agent_risk("write_file", {"path": "out.txt"}) == "medium"
    assert _agent_risk("local_write", {"path": "/tmp/safe.txt"}) == "medium"


def test_agent_risk_run_command_is_high():
    assert _agent_risk("run_command", {"command": "ls"}) == "high"


def test_agent_risk_system_path_write_upgraded_to_high():
    for prefix in _LOCAL_WRITE_BLOCKED_PREFIXES:
        risk = _agent_risk("local_write", {"path": prefix + "evil.txt"})
        assert risk == "high", f"prefix {prefix} should upgrade local_write to high"


def test_agent_risk_unknown_action_defaults_medium():
    assert _agent_risk("nonexistent_tool_xyz", {}) == "medium"


# ---------------------------------------------------------------------------
# Network exposure / local file approvals
# ---------------------------------------------------------------------------

def test_host_loopback_detection():
    assert _host_is_loopback("127.0.0.1")
    assert _host_is_loopback("localhost")
    assert not _host_is_loopback("0.0.0.0")
    assert not _host_is_loopback("192.168.0.2")


def test_local_approval_token_allows_exact_scope(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello")
    user = "alice@example.com"
    approval = _local_permission_response(str(target), "read", user)

    _require_local_approval(
        token=approval["approval_token"],
        path=str(target),
        action="read",
        user_email=user,
    )


def test_local_approval_token_rejects_wrong_path(tmp_path):
    allowed = tmp_path / "allowed.txt"
    denied = tmp_path / "denied.txt"
    allowed.write_text("allowed")
    denied.write_text("denied")
    user = "alice@example.com"
    approval = _local_permission_response(str(allowed), "read", user)

    with pytest.raises(HTTPException) as exc:
        _require_local_approval(
            token=approval["approval_token"],
            path=str(denied),
            action="read",
            user_email=user,
        )
    assert exc.value.status_code == 403


def test_local_write_approval_binds_content(tmp_path):
    target = tmp_path / "out.txt"
    user = "alice@example.com"
    approval = _local_permission_response(str(target), "write", user, "first")

    with pytest.raises(HTTPException) as exc:
        _require_local_approval(
            token=approval["approval_token"],
            path=str(target),
            action="write",
            user_email=user,
            content="changed",
        )
    assert exc.value.status_code == 403
