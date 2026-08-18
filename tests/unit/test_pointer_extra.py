"""The pointer extra is declared, and is not a core dependency."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pointer_extra_declares_pyautogui_and_is_not_core():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "pointer" in extras
    assert extras["pointer"], "pointer extra must install something"
    assert any(dep.startswith("pyautogui") for dep in extras["pointer"])
    core = data["project"]["dependencies"]
    assert not any("pyautogui" in dep for dep in core)
    # Empty extras such as pdf stay empty aliases; pointer is a real extra.
    assert extras["pointer"] != extras.get("pdf")
