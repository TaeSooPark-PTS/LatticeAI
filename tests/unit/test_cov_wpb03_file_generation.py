"""wpb03: file generation for the shapes that need no extra help.

The generation prompt, the deterministic HTML repair, the sanitizer and the
project-manifest inference all branch on "is there something more to add?".
The suite covers the *yes* side of each; this file covers the *no* side — a
target extension with no type rule, an HTML document that is already closed,
a model reply whose only content was a think-block, and a two-file web bundle
(page + behavior, no stylesheet).
"""

from __future__ import annotations

from latticeai.core.file_generation import (
    build_file_generation_context,
    infer_project_manifest,
    repair_file_content,
    sanitize_write_content,
)

# ── prompt assembly ─────────────────────────────────────────────────────────


def test_an_extension_with_no_type_rule_still_gets_the_verbatim_contract():
    prompt = build_file_generation_context("meeting-notes.txt", "회의록 정리해줘")

    assert "meeting-notes.txt" in prompt
    assert "Output ONLY the raw file content." in prompt
    assert prompt.endswith("User request: 회의록 정리해줘")
    # No type rule, no first-line anchor, no bundle listing for a plain .txt.
    assert "- Produce" not in prompt
    assert "The very first line" not in prompt
    assert "Project files in this bundle" not in prompt


# ── deterministic HTML repair ───────────────────────────────────────────────


def test_an_already_closed_body_less_document_is_returned_unchanged():
    content = "<!DOCTYPE html><html><head><title>표</title></head></html>"

    repaired = repair_file_content(content, "page.html", "표 페이지 만들어줘")

    assert repaired == content
    assert repaired.count("</html>") == 1
    assert "</body>" not in repaired


# ── sanitizer ───────────────────────────────────────────────────────────────


def test_a_reply_that_was_only_a_think_block_is_repaired_from_the_raw_text():
    content = "<think>파이썬 파일을 어떻게 쓸지 고민한다</think>"

    result, meta = sanitize_write_content("script.py", content, "정렬 스크립트")

    assert meta == {"sanitized": True, "repaired": True, "reason": meta["reason"]}
    assert meta["reason"], "the original validation failure is reported"
    assert result.startswith("# TODO: model produced invalid Python for: 정렬 스크립트")
    # Nothing is thrown away: the raw draft survives as comments.
    assert "# <think>파이썬 파일을 어떻게 쓸지 고민한다</think>" in result


# ── project manifest inference ──────────────────────────────────────────────


def test_a_page_plus_behavior_request_yields_two_files_and_no_stylesheet():
    manifest = infer_project_manifest("todo 앱을 html 과 javascript 로 만들어줘")

    assert manifest is not None
    assert manifest["kind"] == "web"
    assert manifest["name"] == "todo-app"
    assert [f["path"] for f in manifest["files"]] == ["index.html", "app.js"]
    html_brief = manifest["files"][0]["brief"]
    assert "app.js" in html_brief
    assert "style.css" not in html_brief
