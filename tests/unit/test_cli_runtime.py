"""Tests for pure CLI runtime helpers extracted to latticeai/cli/runtime.py"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from latticeai.cli.runtime import (
    _apply_extra_path,
    _has_module,
    _load_env_file,
)


def test_load_env_file_loads_new_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ=\"quoted\"\n# comment\nEMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    _load_env_file(env_file)
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "quoted"


def test_load_env_file_skips_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("EXISTING", "already")
    _load_env_file(env_file)
    assert os.environ["EXISTING"] == "already"


def test_apply_extra_path_prepends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", str(extra_dir))
    monkeypatch.setenv("PATH", "/usr/bin")
    _apply_extra_path()
    assert os.environ["PATH"].startswith(str(extra_dir))


def test_has_module_detects_stdlib() -> None:
    assert _has_module("os") is True
    assert _has_module("nonexistent_module_xyz") is False
