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


# ── Python validation (ast.parse) ───────────────────────────────────────

def test_validate_accepts_parseable_python():
    ok, reason = validate_file_content("import os\n\n\ndef main():\n    return os.getcwd()\n", "script.py")
    assert ok, reason


def test_validate_rejects_python_syntax_errors():
    ok, reason = validate_file_content("def broken(:\n    pass\n", "script.py")
    assert not ok
    assert "invalid Python syntax" in reason


def test_python_repair_path_always_yields_parseable_module():
    import ast

    from latticeai.core.file_generation import sanitize_write_content

    fixed, meta = sanitize_write_content("script.py", "def broken(:\n    pass\n")
    assert meta["sanitized"] and meta["repaired"]
    ast.parse(fixed)  # must not raise
    assert "broken" in fixed  # the draft is preserved (as comments)


# ── braced code types (.js/.jsx/.ts/.tsx) ───────────────────────────────

VALID_TSX = (
    "// braces in comments { should not count }\n"
    "const label = \"unmatched { in a string\";\n"
    "const tpl = `also { unmatched`;\n"
    "export default function App(): JSX.Element {\n"
    "  return <div onClick={() => console.log('hi')}>ok</div>;\n"
    "}\n"
)


def test_validate_accepts_valid_tsx_with_braces_in_literals():
    for path in ("App.tsx", "App.jsx", "util.ts", "util.js"):
        ok, reason = validate_file_content(VALID_TSX, path)
        assert ok, f"{path}: {reason}"


def test_validate_rejects_truncated_braced_code():
    truncated = "export function run() {\n  if (true) {\n    doIt();\n"
    for path in ("a.ts", "a.tsx", "a.js", "a.jsx"):
        ok, reason = validate_file_content(truncated, path)
        assert not ok
        assert "unbalanced" in reason


def test_validate_rejects_fenced_code_for_new_extensions():
    fenced = "```tsx\nexport const x = 1;\n```"
    assert not validate_file_content(fenced, "x.tsx")[0]


def test_escaped_braces_in_regex_do_not_false_reject():
    code = "const m = s.replace(/\\{/g, '(');\nexport const ok = () => m;\n"
    ok, reason = validate_file_content(code, "re.ts")
    assert ok, reason


# ── single-file components (.vue/.svelte) ───────────────────────────────

VALID_VUE = (
    "<template>\n  <button @click=\"count++\">{{ count }}</button>\n</template>\n"
    "<script setup>\nimport { ref } from 'vue';\nconst count = ref(0);\n</script>\n"
    "<style scoped>\nbutton { padding: 8px; }\n</style>\n"
)

VALID_SVELTE = (
    "<script>\n  let count = 0;\n</script>\n"
    "<button on:click={() => count++}>{count}</button>\n"
    "<style>\n  button { padding: 8px; }\n</style>\n"
)


def test_validate_accepts_valid_vue_and_svelte_components():
    assert validate_file_content(VALID_VUE, "Counter.vue")[0]
    assert validate_file_content(VALID_SVELTE, "Counter.svelte")[0]


def test_validate_rejects_unclosed_component_blocks():
    truncated_vue = "<template>\n  <p>hi</p>\n</template>\n<script setup>\nconst x = 1;\n"
    ok, reason = validate_file_content(truncated_vue, "Broken.vue")
    assert not ok
    assert "not closed" in reason
    truncated_svelte = "<script>\n  let x = 1;\n"
    assert not validate_file_content(truncated_svelte, "Broken.svelte")[0]


def test_fenced_component_reply_is_extracted_then_validates():
    raw = f"Sure! Here is the component:\n```vue\n{VALID_VUE}```\nEnjoy!"
    extracted = extract_file_content(raw, "Counter.vue")
    assert validate_file_content(extracted, "Counter.vue")[0]


# ── weak local models: choosing what to salvage, and not looping ────────


def _scripted(replies):
    """An async ``generate`` that returns each reply in turn, then repeats the last."""
    calls = {"n": 0}

    async def generate(_context):
        index = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return replies[index]

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


def test_repair_prefers_a_truncated_document_over_a_longer_apology():
    """Longest-wins handed repair the apology; the document is the salvageable one.

    A 1-4B model often answers once with a real but unterminated document and
    once with a wordy refusal. The refusal is the longer string, so picking by
    length threw away the only reply repair could actually finish.
    """
    truncated = "<!DOCTYPE html>\n<html>\n<head><title>Budget</title></head>\n<body><h1>Q3</h1>"
    apology = (
        "I'm sorry, but I can't help with generating that file. " * 6
    )
    assert len(apology) > len(truncated)

    content, meta = asyncio.run(
        generate_file_content(
            _scripted([truncated, apology]),
            target_path="page.html",
            user_request="Q3 budget page",
        )
    )
    assert meta["repaired"] is True
    assert "<h1>Q3</h1>" in content
    assert content.rstrip().endswith("</html>")
    assert "I'm sorry" not in content


def test_repeated_identical_reply_is_flagged_and_told_so():
    """The same rejected reply twice is a wasted retry unless the model is told."""
    prose = "Certainly! I will write that file for you now."
    seen_contexts = []

    async def generate(context):
        seen_contexts.append(context)
        return prose

    content, meta = asyncio.run(
        generate_file_content(
            generate, target_path="notes.md", user_request="meeting notes"
        )
    )
    assert meta["attempts"][0]["repeated"] is False
    assert meta["attempts"][1]["repeated"] is True
    # Detecting the duplicate has to buy something: one extra call, carrying a
    # prompt that names the repetition. Without it the detection is a log line.
    assert meta["attempts"][1]["escalated"] is True
    assert len(meta["attempts"]) == 3
    assert "do not repeat" in seen_contexts[2].lower()
    # ...and only one extra call, no matter how long the model stays stuck.
    assert len(seen_contexts) == 3
    assert content  # still ends in a file, never an error


def test_prose_only_reply_is_rejected_for_types_with_no_grammar():
    """.md/.txt had no validation at all, so a chat answer was saved as the file."""
    ok, reason = validate_file_content(
        "Sure! Here is the document you asked for:", "notes.md"
    )
    assert not ok
    assert "instead of being the file" in reason

    ok, _ = validate_file_content("Of course, I'll write that up.", "notes.txt")
    assert not ok


def test_real_markdown_is_not_mistaken_for_commentary():
    """The guard must not eat documents that merely open conversationally."""
    doc = (
        "Here are the notes from the planning meeting.\n\n"
        "## Decisions\n\n- Ship 10.8.0 on Friday\n- Keep the dmg unsigned for now\n"
    )
    assert validate_file_content(doc, "notes.md")[0]

    long_prose = "The following summary covers the quarter. " * 20
    assert validate_file_content(long_prose, "summary.txt")[0]


def test_markdown_still_wearing_fences_is_rejected():
    ok, reason = validate_file_content("```md\n# Title\n```", "notes.md")
    assert not ok
    assert "fences" in reason
