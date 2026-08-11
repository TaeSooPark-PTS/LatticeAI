"""wp32 coverage — file-content extraction, validation, repair and salvage.

These are the deterministic guarantees behind "the user asked for a file, so a
file must come out": what counts as valid content per extension, what the
delimiter scanner is allowed to ignore (strings, line comments, block
comments), what repair produces when the model gave nothing usable, and how
the salvage scorer ranks partial candidates.
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from latticeai.core import file_generation
from latticeai.core.file_generation import (
    _python_package_manifest,
    _salvage_score,
    build_file_generation_context,
    extract_file_content,
    repair_bundle_references,
    repair_file_content,
    validate_file_content,
    validate_project_bundle,
)

# ── extraction ──────────────────────────────────────────────────────────────


def test_extracting_from_an_empty_reply_yields_nothing():
    assert extract_file_content("", "page.html") == ""
    assert extract_file_content("   \n  ", "page.html") == ""


# ── delimiter scanning ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("escaped quote inside a string", r'const s = "a\"}b"; const t = 1;'),
        ("brace inside a line comment", "const a = 1; // a stray } here\nconst b = 2;"),
        ("brace inside a block comment", "const a = 1; /* a stray } here */ const b = 2;"),
        ("unterminated block comment", "const a = 1; /* a stray } and no close"),
    ],
)
def test_braced_validation_ignores_braces_inside_literals_and_comments(label, source):
    assert validate_file_content(source, "app.js") == (True, "ok"), label


def test_braced_validation_still_catches_a_truncated_file():
    ok, reason = validate_file_content("function f() { return 1;", "app.js")

    assert ok is False
    assert "unbalanced braces" in reason


# ── per-extension validation ────────────────────────────────────────────────


def test_blank_content_is_never_a_valid_file():
    assert validate_file_content("   \n\t ", "notes.md") == (False, "empty output")


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("page.html", "<!doctype html><html><body>```js\nx\n```</body></html>"),
        ("Card.vue", "<template><b>hi</b></template>\n```\n"),
        ("run.sh", "#!/bin/sh\n```\necho hi\n```\n"),
        ("query.sql", "```sql\nSELECT 1;\n```"),
    ],
)
def test_markdown_fences_disqualify_every_structured_type(path, content):
    ok, reason = validate_file_content(content, path)

    assert ok is False
    assert reason == "output still contains Markdown fences"


@pytest.mark.parametrize(
    ("path", "content"),
    [("run.sh", "#!/bin/sh\necho hi\n"), ("query.sql", "SELECT 1;\n")],
)
def test_clean_shell_and_sql_are_accepted(path, content):
    assert validate_file_content(content, path) == (True, "ok")


# ── generation context ──────────────────────────────────────────────────────


def test_a_jsx_bundle_asks_for_the_vite_module_entry_html():
    context = build_file_generation_context(
        "index.html", "make a react app", bundle_files=["index.html", "src/App.jsx"],
    )

    assert 'id="root"' in context
    assert "/src/main.jsx" in context
    assert "Project files in this bundle: index.html, src/App.jsx" in context


def test_a_plain_html_bundle_keeps_the_single_document_rule():
    context = build_file_generation_context(
        "index.html", "make a page", bundle_files=["index.html", "styles.css"],
    )

    assert "/src/main.jsx" not in context


# ── repair ──────────────────────────────────────────────────────────────────


def test_json_repair_slices_a_real_document_out_of_chatty_output():
    repaired = repair_file_content(
        'Sure! Here it is:\n{"name": "demo", "ok": true}\nHope that helps!',
        "data.json",
        "a demo config",
    )

    assert json.loads(repaired) == {"name": "demo", "ok": True}


def test_json_repair_wraps_unrecoverable_output_in_a_valid_document():
    repaired = repair_file_content("no json here at all", "data.json", "a demo config")

    assert json.loads(repaired) == {"request": "a demo config", "content": "no json here at all"}


def test_python_repair_keeps_parseable_source_untouched():
    source = "def add(a, b):\n    return a + b\n"

    repaired = repair_file_content(source, "calc.py", "an adder")

    assert repaired == source.strip()
    ast.parse(repaired)


def test_python_repair_preserves_broken_output_as_comments():
    repaired = repair_file_content("def add(a, b)\n    return a + b", "calc.py", "an adder")

    ast.parse(repaired)  # the file it writes is always importable
    assert "# TODO: model produced invalid Python for: an adder" in repaired


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("styles.css", "/* TODO: model produced no usable content for: a theme */\n"),
        ("script.py", "# TODO: model produced no usable content for: a theme\n"),
        ("script.js", "// TODO: model produced no usable content for: a theme\n"),
        ("query.sql", "-- TODO: model produced no usable content for: a theme\n"),
        ("notes.md", "a theme\n"),
    ],
)
def test_empty_output_becomes_an_honest_typed_stub(path, expected):
    assert repair_file_content("", path, "a theme") == expected


def test_a_refusal_is_discarded_rather_than_written_into_the_file():
    repaired = repair_file_content(
        "I'm sorry, but I can't help with that request.", "notes.txt", "a theme",
    )

    assert "sorry" not in repaired.lower()


# ── bundle references ───────────────────────────────────────────────────────


def test_bundle_reference_repair_ignores_anchors_and_routes():
    files = {
        "index.html": (
            '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
            '</head><body><a href="#top">top</a><a href="/about">about</a>'
            '</body></html>'
        ),
        "styles.css": "body { color: red; }",
    }

    repaired, fixes = repair_bundle_references(files)

    assert fixes == ["index.html: 'style.css' -> 'styles.css'"]
    assert 'href="styles.css"' in repaired["index.html"]
    assert '#top' in repaired["index.html"]
    assert validate_project_bundle(repaired)["ok"] is True


def test_bundle_validation_reports_a_reference_with_no_matching_file():
    files = {
        "index.html": (
            '<!doctype html><html><body>'
            '<script src="missing.js"></script></body></html>'
        ),
    }

    report = validate_project_bundle(files)

    assert report["ok"] is False
    assert report["issues"] == ["index.html: references missing file 'missing.js'"]


# ── python package manifest ─────────────────────────────────────────────────


def test_a_package_name_that_cannot_start_an_identifier_is_prefixed(monkeypatch):
    # The shipped name pattern can only capture ASCII-letter-initial names, so
    # the normalisation guard is exercised through the pattern seam.
    # ``_python_package_manifest`` reads the pattern from its own module
    # globals, so after the v11.3.0 split the seam is ``.inference`` — a name
    # rebound on the package ``__init__`` would leave that read untouched.
    monkeypatch.setattr(
        file_generation.inference, "_PKG_NAME_RE",
        re.compile(r"([0-9A-Za-z][0-9A-Za-z_-]{1,30})\s*(?:패키지|package\b)", re.IGNORECASE),
    )

    manifest = _python_package_manifest("2fast package 만들어줘")

    assert manifest["name"] == "pkg_2fast"
    assert manifest["kind"] == "python"
    assert manifest["files"][0]["path"] == "pkg_2fast/__init__.py"


def test_an_unnamed_package_request_uses_the_default_module_name():
    manifest = _python_package_manifest("파이썬 패키지 만들어줘")

    assert manifest["name"] == "my_package"
    assert manifest["files"][0]["path"] == "my_package/__init__.py"


# ── salvage scoring ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "candidate", "tier"),
    [
        ("page.html", "", 0),
        ("page.html", "I'm sorry, I cannot do that.", 0),
        ("page.html", "<!doctype html><html><body>hi", 2),
        ("data.json", 'prose {"a": 1} more prose', 2),
        ("calc.py", "def add(a, b):\n    return a + b\n", 2),
        ("calc.py", "def add(a, b)\n    return", 1),
        ("app.js", "function f() { return 1; }", 2),
        ("app.js", "function f() { return 1;", 1),
        ("Card.vue", "<template><b>hi</b></template>", 2),
        ("Card.vue", "<template><b>hi</b>", 1),
        ("styles.css", "body { color: red; }", 2),
        ("styles.css", "body color red", 1),
        ("notes.md", "just words", 1),
    ],
)
def test_salvage_score_tiers_candidates_by_how_finishable_they_are(path, candidate, tier):
    score = _salvage_score(candidate, path)

    assert score[0] == tier
    assert score[1] == len(candidate.strip())


def test_a_short_real_document_outranks_a_long_apology():
    document = _salvage_score("<!doctype html><html><body>hi", "page.html")
    apology = _salvage_score("I'm sorry, " + ("but I cannot help. " * 40), "page.html")

    assert document > apology
