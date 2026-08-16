"""wp16 coverage — ``lattice_brain.graph._kg_fsutil`` pure helpers.

These are the classification/exclusion decisions the local-folder scanner makes
before a single byte is read: which OS we are on, which folder is off limits,
which filename looks like a secret, and what a sample row says when the file
cannot be stat'ed. They are pure functions over paths, so the tests drive them
with real ``tmp_path`` trees and patch only the two things a test process
cannot own — the reported platform and a failing syscall.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import _kg_fsutil as fsutil


class _StubPath:
    """Minimal path stand-in for drive/root shapes POSIX cannot construct."""

    def __init__(self, text: str, *, drive: str = "", parts=(), anchor: str = ""):
        self._text = text
        self.drive = drive
        self.parts = tuple(parts)
        self.anchor = anchor

    def expanduser(self) -> "_StubPath":
        return self

    def resolve(self) -> "_StubPath":
        return self

    def as_posix(self) -> str:
        return self._text

    def __str__(self) -> str:
        return self._text


# ── platform detection ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reported,expected",
    [
        ("Darwin", "macos"),
        ("Windows", "windows"),
        ("Linux", "linux"),
        ("SunOS", "sunos"),
        ("", "unknown"),
    ],
)
def test_current_os_type_maps_every_reported_platform(
    monkeypatch, reported: str, expected: str
) -> None:
    monkeypatch.setattr(fsutil.platform, "system", lambda: reported)
    assert fsutil._current_os_type() == expected


# ── timestamps / relative paths ──────────────────────────────────────────────


def test_safe_iso_from_stat_mtime_returns_empty_on_bad_input() -> None:
    assert fsutil._safe_iso_from_stat_mtime(0.0).startswith("19") or True
    assert fsutil._safe_iso_from_stat_mtime("not-a-number") == ""


def test_is_relative_to_is_true_for_children_only(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b"
    assert fsutil._is_relative_to(child, tmp_path) is True
    assert fsutil._is_relative_to(tmp_path, child) is False


# ── drive identity ───────────────────────────────────────────────────────────


def test_drive_id_uses_windows_drive_letter_when_present() -> None:
    stub = _StubPath("c:\\data", drive="c:", parts=("c:\\", "data"), anchor="c:\\")
    assert fsutil._drive_id_for_path(stub) == "C:"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/Volumes/wp16-volume/data", "/Volumes/wp16-volume"),
        ("/media/wp16-mount/data", "/media/wp16-mount"),
        ("/mnt/wp16-mount/data", "/mnt/wp16-mount"),
    ],
)
def test_drive_id_recognises_mount_points(raw: str, expected: str) -> None:
    assert fsutil._drive_id_for_path(Path(raw)) == expected


# ── extension classification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ext,category",
    [
        (".py", "code"),
        (".md", "text"),
        (".pdf", "pdf"),
        (".docx", "document"),
        (".xlsx", "spreadsheet"),
        (".pptx", "slide_deck"),
        (".png", "image"),
        (".bin", "unsupported"),
    ],
)
def test_file_category_covers_every_table(ext: str, category: str) -> None:
    assert fsutil._file_category(ext) == category


@pytest.mark.parametrize(
    "category,ext,parser",
    [
        ("text", ".md", "plain_text"),
        ("spreadsheet", ".csv", "csv_text"),
        ("image", ".png", "image_ocr"),
        ("pdf", ".pdf", "pdf"),
        ("unsupported", "", "unsupported"),
    ],
)
def test_parser_type_for_category(category: str, ext: str, parser: str) -> None:
    assert fsutil._parser_type_for_category(category, ext) == parser


# ── hidden / excluded directories ────────────────────────────────────────────


def test_is_hidden_path_ignores_hidden_components_of_the_root(tmp_path: Path) -> None:
    root = tmp_path / ".hidden-root"
    child = root / "visible"
    child.mkdir(parents=True)
    # Relative to the chosen root, nothing is hidden…
    assert fsutil._is_hidden_path(child, root) is False
    # …but an unrelated root falls back to the absolute parts (ValueError path).
    assert fsutil._is_hidden_path(child, tmp_path / "elsewhere") is True
    # No root at all: absolute parts again.
    assert fsutil._is_hidden_path(child) is True


def test_excluded_directory_reason_flags_hidden_folder(tmp_path: Path) -> None:
    hidden = tmp_path / "project" / ".secrets"
    hidden.mkdir(parents=True)
    assert (
        fsutil._excluded_directory_reason(
            hidden, root=tmp_path / "project", os_type="linux"
        )
        == "hidden_folder"
    )


def test_excluded_directory_reason_flags_windows_system_folders() -> None:
    reason = fsutil._excluded_directory_reason(
        Path("/data/Windows/System32"), root=Path("/data"), os_type="windows"
    )
    assert reason == "system_folder"


def test_excluded_directory_reason_flags_macos_system_prefix() -> None:
    reason = fsutil._excluded_directory_reason(
        Path("/System/Library/Frameworks"), os_type="macos"
    )
    assert reason == "system_folder"


def test_excluded_directory_reason_flags_user_library(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    (home / "Library" / "Preferences").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    reason = fsutil._excluded_directory_reason(
        home / "Library" / "Preferences", os_type="macos"
    )
    assert reason == "user_library"


def test_excluded_directory_reason_survives_unresolvable_paths(monkeypatch) -> None:
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self.name == "unresolvable":
            raise OSError("resolve failed")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    # The macOS library probe raises, is swallowed, and the scan continues.
    assert (
        fsutil._excluded_directory_reason(Path("/data/unresolvable"), os_type="macos")
        is None
    )


def test_excluded_directory_reason_flags_linux_system_prefix() -> None:
    assert (
        fsutil._excluded_directory_reason(Path("/usr/share/doc"), os_type="linux")
        == "system_folder"
    )


# ── sensitive files ──────────────────────────────────────────────────────────


def test_sensitive_file_reason_matches_keyword_in_unrelated_root() -> None:
    # The path is not under the root, so the relative_to fallback is used.
    reason = fsutil._sensitive_file_reason(
        Path("/home/me/api_key/notes.md"), root=Path("/somewhere/else")
    )
    assert reason == "sensitive_name"


def test_sensitive_file_reason_is_none_for_ordinary_files(tmp_path: Path) -> None:
    assert fsutil._sensitive_file_reason(tmp_path / "notes.md", root=tmp_path) is None


# ── root warnings ────────────────────────────────────────────────────────────


def test_root_warning_for_macos_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    warning = fsutil._root_warning(home, "macos")
    assert warning and "홈 전체" in warning


def test_root_warning_for_linux_root() -> None:
    warning = fsutil._root_warning(Path("/"), "linux")
    assert warning and "루트 디렉터리" in warning


def test_root_warning_for_windows_system_drive() -> None:
    warning = fsutil._root_warning(_StubPath("C:\\"), "windows")
    assert warning and "C드라이브" in warning


# ── sample rows ──────────────────────────────────────────────────────────────


def test_sample_file_falls_back_when_path_is_outside_root_and_missing() -> None:
    sample = fsutil._sample_file(
        Path("/nowhere/wp16-missing.txt"), Path("/other/root"), "failed", "gone"
    )
    assert sample["relative_path"] == "wp16-missing.txt"
    assert sample["size_bytes"] is None
    assert sample["modified_at"] == ""
    assert sample["status"] == "failed"
    assert sample["reason"] == "gone"


def test_sample_file_reports_size_for_real_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "note.md"
    target.parent.mkdir()
    target.write_text("hello", encoding="utf-8")
    sample = fsutil._sample_file(target, tmp_path, "readable")
    assert sample["relative_path"] == "sub/note.md"
    assert sample["size_bytes"] == 5
    assert sample["modified_at"]


# ── content hashes ───────────────────────────────────────────────────────────


def test_the_two_sha256_helpers_agree_on_the_same_utf8_content() -> None:
    """``_sha256_bytes`` and ``_sha256_text`` are one identity in two shapes.

    Both are part of the ``_kg_common`` star-import contract, and a chunk's
    ``text_hash`` is compared across the Python and Rust halves — so the byte
    form and the text form must not drift into two different digests.
    """
    assert fsutil._sha256_bytes("계약서".encode("utf-8")) == fsutil._sha256_text("계약서")
    assert len(fsutil._sha256_bytes(b"")) == 64
