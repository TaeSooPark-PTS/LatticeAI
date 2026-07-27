"""PermissionModeService persistence (v9.9.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.core.permission_mode import PermissionMode
from latticeai.services.permission_mode_service import PermissionModeService


def test_default_is_strict(tmp_path: Path):
    svc = PermissionModeService(data_dir=tmp_path)
    assert svc.resolve() is PermissionMode.STRICT


def test_set_trusted_and_resolve(tmp_path: Path):
    events = []
    svc = PermissionModeService(
        data_dir=tmp_path,
        audit=lambda event, **kw: events.append((event, kw)),
    )
    out = svc.set_mode("trusted", user_email="a@b.c")
    assert out["mode"] == "trusted"
    assert svc.resolve(user_email="a@b.c") is PermissionMode.TRUSTED
    assert events and events[0][0] == "permission_mode_changed"


def test_bypass_requires_ack(tmp_path: Path):
    svc = PermissionModeService(data_dir=tmp_path)
    with pytest.raises(PermissionError):
        svc.set_mode("bypass", user_email="a@b.c", acknowledge_risk=False)
    out = svc.set_mode("bypass", user_email="a@b.c", acknowledge_risk=True)
    assert out["mode"] == "bypass"


def test_workspace_overrides_user(tmp_path: Path):
    svc = PermissionModeService(data_dir=tmp_path)
    svc.set_mode("trusted", user_email="a@b.c")
    svc.set_mode("strict", user_email="a@b.c", workspace_id="ws1")
    assert svc.resolve(user_email="a@b.c") is PermissionMode.TRUSTED
    assert svc.resolve(user_email="a@b.c", workspace_id="ws1") is PermissionMode.STRICT
