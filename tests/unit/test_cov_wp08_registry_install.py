"""``create_mcp_install_state`` — persistence, recommendation and installation.

The package-installer seam (``_run_installer``) is replaced with a recorder, so
no pip/npm process is ever started; ``_get_combined_registry`` is replaced with a
small deterministic catalog.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.core import mcp_registry
from latticeai.core.mcp_registry import create_mcp_install_state

_CATALOG = [
    {
        "id": "presentations",
        "name": "Presentations MCP",
        "category": "PPT",
        "install_mode": "bundled",
        "description": "slide decks",
        "keywords": ["ppt", "발표"],
        "capabilities": ["PPTX"],
    },
    {
        "id": "filesystem",
        "name": "Filesystem MCP",
        "install_mode": "builtin",
        "description": "local files",
        "keywords": [],
    },
    {
        "id": "browser",
        "name": "Browser MCP",
        "install_mode": "bundled",
        "description": "renders dashboard pages",
        "keywords": [],
    },
    {
        "id": "documents",
        "name": "Documents MCP",
        "install_mode": "bundled",
        "description": "",
        "keywords": [],
    },
    {"id": "gmail", "name": "Gmail", "install_mode": "connector", "description": "mail"},
    {
        "id": "pip-bundle",
        "name": "Pip Bundle",
        "install_mode": "pip",
        "pip_packages": ["pkg-a", "pkg-b"],
    },
    {
        "id": "pypi-pinned",
        "name": "Pypi Pinned",
        "install_mode": "pypi",
        "package": "demo-mcp",
        "package_version": "1.2.3",
    },
    {"id": "pypi-floating", "name": "Pypi Floating", "install_mode": "pypi", "package": "demo-mcp"},
    {
        "id": "npm-pinned",
        "name": "Npm Pinned",
        "install_mode": "npm",
        "package": "@scope/demo",
        "package_version": "2.0.0",
    },
    {"id": "npm-floating", "name": "Npm Floating", "install_mode": "npm", "package": "@scope/demo"},
]


class _Installer:
    """Stands in for ``_run_installer``; records commands, returns canned results."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls: list = []

    def __call__(self, command, timeout):
        self.calls.append((list(command), timeout))
        return self._results.pop(0)


def _ok():
    return SimpleNamespace(returncode=0, stdout="done", stderr="")


def _failed(stderr):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def _state(monkeypatch, tmp_path, installer=None):
    async def fake_registry():
        return _CATALOG

    monkeypatch.setattr(mcp_registry, "_get_combined_registry", fake_registry)
    if installer is not None:
        monkeypatch.setattr(mcp_registry, "_run_installer", installer)
    return create_mcp_install_state(tmp_path)


def test_run_installer_shells_out_with_capture_and_no_raise(monkeypatch):
    captured = {}

    class FakeSubprocess:
        def run(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    monkeypatch.setattr(mcp_registry, "subprocess", FakeSubprocess())

    completed = mcp_registry._run_installer(["npm", "install", "-g", "demo"], 42)

    assert completed.stdout == "installed"
    assert captured["command"] == ["npm", "install", "-g", "demo"]
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 42,
        "check": False,
    }


def test_load_mcp_installs_repairs_a_file_without_an_installed_key(tmp_path):
    (tmp_path / "mcp_installs.json").write_text('{"updated_at": "2026-01-01"}', encoding="utf-8")
    state = create_mcp_install_state(tmp_path)

    assert state["load_mcp_installs"]() == {"updated_at": "2026-01-01", "installed": {}}


def test_load_mcp_installs_falls_back_when_the_file_is_corrupt(tmp_path):
    (tmp_path / "mcp_installs.json").write_text("{not json", encoding="utf-8")

    loaded = create_mcp_install_state(tmp_path)["load_mcp_installs"]()

    assert loaded == {"installed": {}, "updated_at": None}


def test_save_mcp_installs_stamps_updated_at_on_disk(tmp_path):
    state = create_mcp_install_state(tmp_path)

    state["save_mcp_installs"]({"installed": {"browser": {"installed": True}}})

    on_disk = json.loads((tmp_path / "mcp_installs.json").read_text(encoding="utf-8"))
    assert on_disk["installed"] == {"browser": {"installed": True}}
    assert on_disk["updated_at"]


def test_mcp_public_item_reports_connector_auth_and_plain_availability(tmp_path):
    public_item = create_mcp_install_state(tmp_path)["mcp_public_item"]

    pending = public_item({"id": "gmail", "install_mode": "connector"}, {"gmail": {"installed": True}})
    authed = public_item(
        {"id": "gmail", "install_mode": "connector"},
        {"gmail": {"installed": True, "authenticated": True, "updated_at": "2026-01-01"}},
    )
    available = public_item({"id": "npm-pinned", "install_mode": "npm", "package": "x"}, {})

    assert (pending["status"], pending["authenticated"]) == ("needs_auth", False)
    assert (authed["status"], authed["authenticated"]) == ("active", True)
    assert authed["updated_at"] == "2026-01-01"
    assert (available["installed"], available["status"]) == (False, "available")
    assert available["package"] == "x"


def test_recommend_scores_keywords_descriptions_and_the_filesystem_boost(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path)

    ranked = asyncio.run(state["recommend_mcps"]("ppt 발표 자료 build dashboard"))

    assert [(r["id"], r["score"]) for r in ranked] == [
        ("presentations", 4),  # "ppt" (long) + "발표" (short)
        ("filesystem", 2),     # build-intent boost
        ("browser", 1),        # description word match
    ]
    assert ranked[0]["matched_keywords"] == ["ppt", "발표"]
    assert ranked[2]["matched_keywords"] == ["dashboard"]


def test_recommend_falls_back_to_starter_mcps_and_clamps_the_limit(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path)

    fallback = asyncio.run(state["recommend_mcps"](""))
    clamped = asyncio.run(state["recommend_mcps"]("", limit=0))

    assert [r["id"] for r in fallback] == ["filesystem", "browser", "documents"]
    assert all(r["score"] == 1 and r["matched_keywords"] == [] for r in fallback)
    assert len(clamped) == 1


def test_install_mcp_rejects_an_unknown_id(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(state["install_mcp"]("nope"))

    assert exc.value.status_code == 404
    assert state["load_mcp_installs"]()["installed"] == {}


def test_install_mcp_marks_a_connector_as_awaiting_authentication(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path)

    result = asyncio.run(state["install_mcp"]("gmail"))

    assert (result["status"], result["authenticated"]) == ("needs_auth", False)
    assert "커넥터 인증이 필요합니다" in result["message"]
    assert state["load_mcp_installs"]()["installed"]["gmail"]["status"] == "needs_auth"


def test_install_mcp_runs_every_pip_package_off_the_event_loop(monkeypatch, tmp_path):
    installer = _Installer(_ok(), _ok())
    state = _state(monkeypatch, tmp_path, installer)

    result = asyncio.run(state["install_mcp"]("pip-bundle"))

    assert [call[0][-3:] for call in installer.calls] == [
        ["install", "--upgrade", "pkg-a"],
        ["install", "--upgrade", "pkg-b"],
    ]
    assert {call[1] for call in installer.calls} == {900}
    assert result["message"] == "필수 패키지 설치 완료: pkg-a, pkg-b"
    assert result["status"] == "active"
    assert state["load_mcp_installs"]()["installed"]["pip-bundle"]["installed"] is True


def test_install_mcp_surfaces_pip_stderr_and_records_nothing(monkeypatch, tmp_path):
    installer = _Installer(_failed("wheel build exploded"))
    state = _state(monkeypatch, tmp_path, installer)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(state["install_mcp"]("pip-bundle"))

    assert exc.value.status_code == 500
    assert exc.value.detail == "wheel build exploded"
    assert state["load_mcp_installs"]()["installed"] == {}


def test_install_mcp_pins_the_pypi_version_when_the_catalog_supplies_one(monkeypatch, tmp_path):
    installer = _Installer(_ok(), _ok())
    state = _state(monkeypatch, tmp_path, installer)

    pinned = asyncio.run(state["install_mcp"]("pypi-pinned"))
    floating = asyncio.run(state["install_mcp"]("pypi-floating"))

    assert [call[0][-2:] for call in installer.calls] == [
        ["install", "demo-mcp==1.2.3"],
        ["install", "demo-mcp"],
    ]
    assert {call[1] for call in installer.calls} == {300}
    assert pinned["message"] == "pip 패키지 설치 완료: demo-mcp==1.2.3"
    assert floating["message"] == "pip 패키지 설치 완료: demo-mcp"


def test_install_mcp_reports_a_pypi_failure_without_stderr(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path, _Installer(_failed("")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(state["install_mcp"]("pypi-pinned"))

    assert exc.value.status_code == 500
    assert exc.value.detail == "demo-mcp 설치 실패"


def test_install_mcp_installs_npm_packages_globally(monkeypatch, tmp_path):
    installer = _Installer(_ok(), _ok())
    state = _state(monkeypatch, tmp_path, installer)

    pinned = asyncio.run(state["install_mcp"]("npm-pinned"))
    floating = asyncio.run(state["install_mcp"]("npm-floating"))

    assert [call[0] for call in installer.calls] == [
        ["npm", "install", "-g", "@scope/demo@2.0.0"],
        ["npm", "install", "-g", "@scope/demo"],
    ]
    assert pinned["message"] == "npm 패키지 설치 완료: @scope/demo@2.0.0"
    assert floating["message"] == "npm 패키지 설치 완료: @scope/demo"
    assert floating["authenticated"] is True


def test_install_mcp_reports_an_npm_failure(monkeypatch, tmp_path):
    state = _state(monkeypatch, tmp_path, _Installer(_failed("EACCES")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(state["install_mcp"]("npm-pinned"))

    assert exc.value.status_code == 500
    assert exc.value.detail == "EACCES"
