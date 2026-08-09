"""wp11: the refusal paths of the approval-gated local filesystem tools.

``latticeai/tools/local_files.py`` is the last hop before real user files, so
every refusal it can produce is a promise: an unreadable directory, a file that
is a directory, an oversized read, a decode failure, an oversized write and a
denied write all have to surface as a :class:`ToolError` the API can turn into
a 400 — never as a raw ``OSError`` escaping into a 500.

The size caps are re-pointed at a few bytes rather than writing a 2 MB fixture,
and the OS refusals are injected at the ``pathlib`` seam instead of relying on
``chmod`` (which does nothing when the CI leg runs as root).
"""

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
    assert "10" in message  # the real size is reported back to the caller


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


def test_local_write_refuses_content_over_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "LOCAL_MAX_FILE_BYTES", 4)
    target = tmp_path / "out" / "note.txt"

    with pytest.raises(ToolError) as excinfo:
        local_tools.local_write(str(target), "이 내용은 캡보다 큽니다")
    assert "내용이 너무 큽니다" in str(excinfo.value)
    # Refused before any directory was created — nothing touched the disk.
    assert not target.parent.exists()


def test_local_write_surfaces_permission_error_as_tool_error(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    real_write_text = Path.write_text

    def denied(self, *args, **kwargs):
        if self.name == "note.txt":
            raise PermissionError("read-only filesystem")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", denied)

    with pytest.raises(ToolError) as excinfo:
        local_tools.local_write(str(target), "내용")
    assert "쓰기 권한 없음" in str(excinfo.value)
    assert not target.exists()


def test_local_write_round_trips_within_the_cap(tmp_path):
    target = tmp_path / "nested" / "note.txt"
    result = local_tools.local_write(str(target), "안녕")

    assert result["path"] == str(target)
    assert result["bytes"] == len("안녕".encode())
    assert local_tools.local_read(str(target))["content"] == "안녕"


def test_desktop_bridge_status_declares_the_missing_bridge():
    status = local_tools.desktop_bridge_status()

    assert status["status"] == "requires_desktop_bridge"
    assert status["available_in_codex"] is True
    assert "Chrome" in status["note"]
