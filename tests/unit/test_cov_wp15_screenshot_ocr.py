"""Screenshot ingestion: image probing plus every OCR outcome.

``extract_screenshot_context`` turns an attached screenshot into prompt text
without ever raising into the chat request.  These tests drive each honest
report it can produce: no attachment, an undecodable payload, tesseract absent,
tesseract succeeding on the first or the fallback language, tesseract finding
nothing, tesseract blowing up, and a temp file that refuses to be removed.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from PIL import Image

from latticeai.api import chat_documents

TESSERACT = "/opt/fake/bin/tesseract"


@pytest.fixture()
def screenshot_b64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (7, 4), (12, 34, 56)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.fixture()
def isolated_tmpdir(monkeypatch, tmp_path: Path) -> Path:
    """Keep the module's NamedTemporaryFile inside the test's own directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _fields(context: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in context.splitlines():
        if line.startswith("- ") and ": " in line:
            key, _, value = line[2:].partition(": ")
            parsed[key] = value
    return parsed


def _install_tesseract(monkeypatch, runner) -> List[List[str]]:
    commands: List[List[str]] = []

    def which(name: str):
        assert name == "tesseract"
        return TESSERACT

    def run(command, **kwargs):
        commands.append(list(command))
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        return runner(list(command))

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    return commands


@pytest.mark.parametrize("payload", [None, ""])
def test_missing_attachment_contributes_no_prompt_text(payload):
    assert chat_documents.extract_screenshot_context(payload) == ""


def test_undecodable_attachment_reports_the_decode_error_and_stops(monkeypatch):
    def unexpected(_name):
        raise AssertionError("OCR must not be attempted on an unreadable image")

    monkeypatch.setattr(shutil, "which", unexpected)

    context = chat_documents.extract_screenshot_context("not-a-real-image")

    assert context.startswith("[SCREENSHOT INGESTION]")
    assert "image_decode_error" in _fields(context)
    assert "image_size" not in context


def test_missing_tesseract_is_reported_as_an_actionable_gap(monkeypatch, screenshot_b64):
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    context = chat_documents.extract_screenshot_context(screenshot_b64)

    fields = _fields(context)
    assert fields["image_size"] == "7x4"
    assert fields["image_mode"] == "RGB"
    assert "install `tesseract`" in fields["ocr"]


def test_first_language_that_returns_text_wins_and_ends_the_scan(
    monkeypatch, isolated_tmpdir, screenshot_b64
):
    commands = _install_tesseract(
        monkeypatch, lambda _cmd: SimpleNamespace(returncode=0, stdout=" 회의록 초안 \n")
    )

    context = chat_documents.extract_screenshot_context(screenshot_b64)

    assert [command[-3] for command in commands] == ["kor+eng"]
    assert _fields(context)["ocr_language"] == "kor+eng"
    assert context.endswith("- ocr_text:\n회의록 초안")
    assert not list(isolated_tmpdir.iterdir())


def test_scan_falls_back_to_the_second_language(
    monkeypatch, isolated_tmpdir, screenshot_b64
):
    def runner(command):
        if command[-3] == "kor+eng":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="quarterly plan")

    commands = _install_tesseract(monkeypatch, runner)

    context = chat_documents.extract_screenshot_context(screenshot_b64)

    assert [command[-3] for command in commands] == ["kor+eng", "eng"]
    assert _fields(context)["ocr_language"] == "eng"
    assert "quarterly plan" in context


def test_blank_ocr_output_says_so_instead_of_inventing_text(
    monkeypatch, isolated_tmpdir, screenshot_b64
):
    _install_tesseract(monkeypatch, lambda _cmd: SimpleNamespace(returncode=0, stdout="   \n"))

    context = chat_documents.extract_screenshot_context(screenshot_b64)

    assert _fields(context)["ocr"] == "no text extracted."
    assert "ocr_language" not in context
    assert "ocr_text" not in context


def test_ocr_crash_is_recorded_on_the_prompt_rather_than_raised(
    monkeypatch, isolated_tmpdir, screenshot_b64
):
    def boom(_command):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=20)

    _install_tesseract(monkeypatch, boom)

    context = chat_documents.extract_screenshot_context(screenshot_b64)

    fields = _fields(context)
    assert fields["image_size"] == "7x4"
    assert "timed out" in fields["ocr_error"]


def test_an_unremovable_temp_file_does_not_break_ingestion(
    monkeypatch, isolated_tmpdir, screenshot_b64, caplog
):
    _install_tesseract(monkeypatch, lambda _cmd: SimpleNamespace(returncode=0, stdout="text"))

    def refuse(self: Path, *args: Any, **kwargs: Any) -> None:
        raise OSError(str(self.name) + " is locked")

    monkeypatch.setattr(Path, "unlink", refuse)

    with caplog.at_level("DEBUG", logger="latticeai.suppressed"):
        context = chat_documents.extract_screenshot_context(screenshot_b64)

    assert "text" in context
    assert "suppressed OSError" in caplog.text
    # The screenshot still produced prompt text; only the cleanup was refused.
    assert [path.suffix for path in isolated_tmpdir.iterdir()] == [".png"]
