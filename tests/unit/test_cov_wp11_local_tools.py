from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.tools import ToolError
from latticeai.tools import local_files as local_tools


def test_local_list_surfaces_permission_error_as_tool_error(tmp_path, monkeypatch):
    root = tmp_path / "locked"
    root.mkdir()
    real_iterdir = Path.iterdir

    def denied(self):
        if self.name == "locked":
            raise PermissionError("Operation not permitted")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", denied)

    with pytest.raises(ToolError) as excinfo:
        local_tools.local_list(str(root))
    assert "접근 권한 없음" in str(excinfo.value)


def test_local_read_refuses_a_directory(tmp_path):
    with pytest.raises(ToolError) as excinfo:
        local_tools.local_read(str(tmp_path))
    assert "파일이 아닙니다" in str(excinfo.value)


def test_local_read_refuses_a_file_over_the_cap(tmp_path, monkeypatch):
    target = tmp_path / "big.txt"
    target.write_text("0123456789", encoding="utf-8")
    monkeypatch.setattr(local_tools, "LOCAL_MAX_FILE_BYTES", 4)

    with pytest.raises(ToolError) as excinfo:
        local_tools.local_read(str(target))
    message = str(excinfo.value)
    assert "파일이 너무 큽니다" in message
    assert "10" in message


def test_local_read_wraps_a_failing_read(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    real_read_text = Path.read_text

    def broken(self, *args, **kwargs):
        if self.name == "note.txt":
            raise OSError("device is busy")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken)

    with pytest.raises(ToolError) as excinfo:
        local_tools.local_read(str(target))
    assert "파일 읽기 실패" in str(excinfo.value)
    assert "device is busy" in str(excinfo.value)


def test_desktop_bridge_status_declares_the_missing_bridge():
    status = local_tools.desktop_bridge_status()

    assert status["status"] == "requires_desktop_bridge"
    assert status["available_in_codex"] is True
    assert "Chrome" in status["note"]
