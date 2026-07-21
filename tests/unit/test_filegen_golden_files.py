"""Regression golden files for the write-side sanitize pipeline.

Each fixture pair in ``tests/fixtures/filegen/`` is one realistic dirty model
reply and the exact file that must land on disk after
``sanitize_write_content``. A prompt or sanitizer change that alters any
output fails here — golden updates must be reviewed deliberately (see the
fixtures README), never regenerated blindly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.core.file_generation import sanitize_write_content

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "filegen"

# (case, target filename, expect sanitized, expect repaired)
GOLDEN_CASES = [
    ("fenced_html_with_prose", "page.html", True, False),
    ("json_trailing_commentary", "data.json", True, False),
    ("css_in_fence", "styles.css", True, False),
    ("py_markdown_wrapper", "script.py", True, False),
    ("truncated_html", "report.html", True, True),
]


def _normalize(text: str) -> str:
    """Newline normalization only: CRLF→LF plus at most one trailing newline."""
    return text.replace("\r\n", "\n").rstrip("\n")


def _run_case(case: str, target: str):
    dirty = (FIXTURES / f"{case}.dirty.txt").read_text(encoding="utf-8")
    ext = target.rsplit(".", 1)[-1]
    expected = (FIXTURES / f"{case}.expected.{ext}").read_text(encoding="utf-8")
    clean, meta = sanitize_write_content(target, dirty, user_request=f"golden fixture: {case}")
    return clean, meta, expected


@pytest.mark.parametrize("case,target,sanitized,repaired", GOLDEN_CASES)
def test_dirty_output_sanitizes_to_golden_file(case, target, sanitized, repaired):
    clean, meta, expected = _run_case(case, target)
    assert _normalize(clean) == _normalize(expected), (
        f"{case}: sanitized output diverged from the golden file — if this is "
        "an intentional prompt/sanitizer change, review the diff deliberately "
        "and update the golden in the same change (see fixtures README)"
    )
    assert meta["sanitized"] is sanitized, meta
    assert meta["repaired"] is repaired, meta


def test_css_fenced_stylesheet_never_written_verbatim():
    # Regression guard for the 9.9.3 fix: .css validation used to check only
    # for braces, so a fenced/chatty stylesheet was written verbatim. Fences
    # must never survive a CSS write.
    clean, meta, _expected = _run_case("css_in_fence", "styles.css")
    assert "```" not in clean
    assert meta["sanitized"] is True


def test_every_dirty_fixture_has_an_expected_golden():
    dirty_files = sorted(FIXTURES.glob("*.dirty.txt"))
    assert dirty_files, "no golden fixtures found"
    for dirty in dirty_files:
        case = dirty.name.replace(".dirty.txt", "")
        matches = list(FIXTURES.glob(f"{case}.expected.*"))
        assert matches, f"fixture {case} has no expected golden file"


def test_fixtures_readme_requires_deliberate_review():
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert "deliberately" in readme.lower()
