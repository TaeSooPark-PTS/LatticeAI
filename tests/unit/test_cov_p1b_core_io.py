"""Coverage for keep-set core helpers: io, origin, quiet, policy, messages."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.core.http_origin import (
    effective_host,
    effective_origin,
    peer_may_forward,
    request_external_origin,
)
from latticeai.core.io_utils import atomic_write_json, sha256_file
from latticeai.core.messages import bilingual, http_error, translate
from latticeai.core.module_probe import module_available
from latticeai.core.policy import (
    capabilities_for_role,
    normalize_role,
    policy_matrix,
    require_capability,
    role_has_capability,
)
from latticeai.core.quiet import quiet


def test_atomic_write_json_and_sha256(tmp_path: Path, monkeypatch):
    target = tmp_path / "nested" / "data.json"
    atomic_write_json(target, {"ok": True})
    assert target.read_text(encoding="utf-8")
    digest = sha256_file(target)
    assert len(digest) == 64

    def boom(*_args, **_kwargs):
        raise OSError("no chmod")

    monkeypatch.setattr(Path, "chmod", boom)
    atomic_write_json(tmp_path / "other.json", {"x": 1})


def test_http_origin_loopback_and_untrusted():
    assert peer_may_forward(None) is False
    assert peer_may_forward("not-an-ip") is False
    assert peer_may_forward("127.0.0.1") is True
    assert peer_may_forward("8.8.8.8") is False

    assert effective_host(host="worker:1", forwarded_host="front:9", peer="127.0.0.1") == "front:9"
    assert effective_host(host="worker:1", forwarded_host="", peer="127.0.0.1") == "worker:1"
    assert effective_host(host="", peer="8.8.8.8") is None

    origin = effective_origin(
        host="worker:1",
        scheme="http",
        forwarded_host="front:9",
        forwarded_proto="https",
        peer="127.0.0.1",
    )
    assert origin == "https://front:9"
    assert effective_origin(host="", peer="8.8.8.8") is None

    request = SimpleNamespace(
        headers={"host": "worker:1", "x-forwarded-host": "front:9", "x-forwarded-proto": "https"},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(scheme="http"),
    )
    assert request_external_origin(request) == "https://front:9"
    empty = SimpleNamespace(headers={}, client=None, url=None)
    assert request_external_origin(empty, fallback="http://fallback") == "http://fallback"


def test_quiet_and_module_probe():
    quiet()
    assert module_available("json") is True
    assert module_available("json", strict=True) is True
    assert module_available("latticeai_no_such_module_xyz") is False
    assert module_available("latticeai_no_such_module_xyz", strict=True) is False


def test_policy_roles():
    assert normalize_role("ADMIN") == "admin"
    assert normalize_role("") == "user"
    assert role_has_capability("owner", "admin:users") is True
    assert role_has_capability("user", "admin:users") is False
    require_capability("admin", "admin:users")
    with pytest.raises(PermissionError):
        require_capability("user", "admin:users")
    assert "chat" in capabilities_for_role("member")
    matrix = policy_matrix(["owner", "viewer"])
    assert matrix[0]["role"] == "owner"


def test_messages_bilingual_and_unknown_key():
    pair = bilingual("안녕", "hello")
    assert pair["ko"] == "안녕"
    assert pair["en"] == "hello"
    assert translate("nope.missing") == "nope.missing"
    err = http_error(400, "nope.missing", "en")
    assert err.status_code == 400
    assert err.detail == "nope.missing"
