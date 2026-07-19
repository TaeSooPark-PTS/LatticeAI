"""Model-agnostic file generation pipeline tests.

Reproduces the failure modes small local models (gemma/qwen class) exhibit
when asked to "generate an HTML file": chat wrappers, Markdown fences,
<think> blocks, truncated documents, refusals — and asserts the pipeline
still yields a structurally valid file every time.
"""

import asyncio
import json

from latticeai.core.file_generation import (
    build_file_generation_context,
    extract_file_content,
    generate_file_content,
    infer_file_target,
    repair_file_content,
    validate_file_content,
)

FULL_HTML = (
    "<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\">"
    "<title>Hi</title></head>\n<body><h1>Hello</h1></body>\n</html>"
)


# ── extraction ──────────────────────────────────────────────────────────

def test_extracts_fenced_html_with_commentary():
    raw = f"Sure! Here is your page:\n\n```html\n{FULL_HTML}\n```\n\nLet me know if you need changes!"
    assert extract_file_content(raw, "page.html") == FULL_HTML


def test_strips_think_blocks():
    raw = f"<think>\nThe user wants {{braces}} in HTML...\n</think>\n{FULL_HTML}"
    assert extract_file_content(raw, "page.html") == FULL_HTML


def test_picks_matching_fence_among_many():
    raw = (
        "First install it:\n```sh\npip install x\n```\n"
        f"Then the page:\n```html\n{FULL_HTML}\n```"
    )
    assert extract_file_content(raw, "page.html") == FULL_HTML


def test_unfenced_reply_drops_leading_and_trailing_chat_lines():
    raw = f"물론입니다! 요청하신 HTML 파일입니다.\n{FULL_HTML}\n도움이 필요하면 말씀해 주세요."
    assert extract_file_content(raw, "page.html") == FULL_HTML


def test_html_sliced_out_of_surrounding_prose():
    raw = f"The document below implements the request. {FULL_HTML} That's all."
    assert extract_file_content(raw, "page.html") == FULL_HTML


def test_json_extracts_largest_parseable_object():
    raw = 'Here you go:\n{"name": "lattice", "items": [1, 2, 3]}\nEnjoy!'
    extracted = extract_file_content(raw, "data.json")
    assert json.loads(extracted) == {"name": "lattice", "items": [1, 2, 3]}


def test_unclosed_fence_still_recovers_content():
    raw = f"```html\n{FULL_HTML}"
    assert extract_file_content(raw, "page.html") == FULL_HTML


# ── validation ──────────────────────────────────────────────────────────

def test_validate_rejects_truncated_html():
    ok, reason = validate_file_content("<!DOCTYPE html>\n<html><body><p>hi", "a.html")
    assert not ok
    assert "truncated" in reason


def test_validate_rejects_fragment_html():
    ok, _ = validate_file_content("<h1>Hello</h1>", "a.html")
    assert not ok


def test_validate_rejects_refusal():
    ok, reason = validate_file_content("죄송하지만 그 요청은 도와드릴 수 없습니다.", "a.html")
    assert not ok
    assert "refusal" in reason


def test_validate_rejects_bad_json_and_accepts_good():
    assert not validate_file_content("{'single': 'quotes'}", "d.json")[0]
    assert validate_file_content('{"a": 1}', "d.json")[0]


def test_validate_accepts_full_html():
    assert validate_file_content(FULL_HTML, "a.html")[0]


# ── repair ──────────────────────────────────────────────────────────────

def test_repair_closes_truncated_html_document():
    repaired = repair_file_content("<!DOCTYPE html>\n<html><body><p>hi</p>", "a.html", "make a page")
    assert validate_file_content(repaired, "a.html")[0]


def test_repair_wraps_html_fragment_into_full_document():
    repaired = repair_file_content("<h1>Hello</h1>", "a.html", "인사 페이지 만들어줘")
    ok, _ = validate_file_content(repaired, "a.html")
    assert ok
    assert "<h1>Hello</h1>" in repaired
    assert repaired.lstrip().lower().startswith("<!doctype html>")


def test_repair_always_yields_valid_json():
    repaired = repair_file_content("not json at all", "d.json", "config json 만들어줘")
    json.loads(repaired)


def test_repair_of_refusal_yields_valid_scaffold():
    repaired = repair_file_content("I'm sorry, I can't do that.", "a.html", "make a page")
    assert validate_file_content(repaired, "a.html")[0]
    assert "sorry" not in repaired.lower()


# ── filename inference ──────────────────────────────────────────────────

def test_infers_html_target_without_explicit_filename():
    assert infer_file_target("간단한 html 파일 만들어줘") == "generated_page.html"
    assert infer_file_target("자기소개 웹페이지 만들어줘") == "generated_page.html"


def test_inference_requires_creation_verb_and_type_keyword():
    assert infer_file_target("html이 뭐야?") is None
    assert infer_file_target("보고서 작성해줘") is None  # document-generator flow
    assert infer_file_target("") is None


# ── prompt ──────────────────────────────────────────────────────────────

def test_prompt_pins_first_line_and_carries_feedback():
    ctx = build_file_generation_context("page.html", "make a page")
    assert "<!DOCTYPE html>" in ctx
    assert "```" in ctx  # the "no fences" rule names the token explicitly
    ctx2 = build_file_generation_context("page.html", "make a page", feedback="missing </html>")
    assert "missing </html>" in ctx2


# ── orchestration ───────────────────────────────────────────────────────

def test_generation_retries_with_feedback_then_succeeds():
    calls = []

    async def fake_generate(context):
        calls.append(context)
        if len(calls) == 1:
            return "<h1>fragment only</h1>"
        return FULL_HTML

    content, meta = asyncio.run(
        generate_file_content(fake_generate, target_path="page.html", user_request="make a page")
    )
    assert content == FULL_HTML
    assert meta["repaired"] is False
    assert len(meta["attempts"]) == 2
    assert "rejected" in calls[1]  # second attempt carried corrective feedback


def test_generation_falls_back_to_repair_when_model_never_complies():
    async def fake_generate(context):
        return "Sorry, as an AI I cannot create files."

    content, meta = asyncio.run(
        generate_file_content(fake_generate, target_path="page.html", user_request="make a page")
    )
    assert meta["repaired"] is True
    assert validate_file_content(content, "page.html")[0]


def test_generation_survives_backend_errors():
    async def fake_generate(context):
        raise RuntimeError("model crashed")

    content, meta = asyncio.run(
        generate_file_content(fake_generate, target_path="notes.md", user_request="메모 파일 만들어줘")
    )
    assert meta["repaired"] is True
    assert content.strip()
