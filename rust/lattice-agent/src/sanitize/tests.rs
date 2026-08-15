//! The parity vectors: Python's own golden pairs, its deleted unit-test
//! vectors, and the idempotence the two write paths depend on.

use super::*;
use crate::pyjson;

/// `tests/fixtures/filegen/` — the golden pairs `test_filegen_golden_files.py`
/// pinned before the pipeline was deleted. Read with `include_str!` so a
/// missing or renamed fixture is a compile error rather than a skipped test.
const GOLDEN: [(&str, &str, &str, bool, bool); 5] = [
    (
        "fenced_html_with_prose",
        "page.html",
        include_str!("../../../../tests/fixtures/filegen/fenced_html_with_prose.dirty.txt"),
        true,
        false,
    ),
    (
        "json_trailing_commentary",
        "data.json",
        include_str!("../../../../tests/fixtures/filegen/json_trailing_commentary.dirty.txt"),
        true,
        false,
    ),
    (
        "css_in_fence",
        "styles.css",
        include_str!("../../../../tests/fixtures/filegen/css_in_fence.dirty.txt"),
        true,
        false,
    ),
    (
        "py_markdown_wrapper",
        "script.py",
        include_str!("../../../../tests/fixtures/filegen/py_markdown_wrapper.dirty.txt"),
        true,
        false,
    ),
    (
        "truncated_html",
        "report.html",
        include_str!("../../../../tests/fixtures/filegen/truncated_html.dirty.txt"),
        true,
        true,
    ),
];

const EXPECTED: [&str; 5] = [
    include_str!("../../../../tests/fixtures/filegen/fenced_html_with_prose.expected.html"),
    include_str!("../../../../tests/fixtures/filegen/json_trailing_commentary.expected.json"),
    include_str!("../../../../tests/fixtures/filegen/css_in_fence.expected.css"),
    include_str!("../../../../tests/fixtures/filegen/py_markdown_wrapper.expected.py"),
    include_str!("../../../../tests/fixtures/filegen/truncated_html.expected.html"),
];

/// `_normalize`: CRLF→LF plus at most one trailing newline.
fn normalize(text: &str) -> String {
    text.replace("\r\n", "\n")
        .trim_end_matches('\n')
        .to_string()
}

#[test]
fn the_python_golden_pairs_sanitize_to_the_same_bytes() {
    for (index, (case, target, dirty, sanitized, repaired)) in GOLDEN.iter().enumerate() {
        let (clean, meta) =
            sanitize_write_content(target, dirty, &format!("golden fixture: {case}"));
        assert_eq!(
            normalize(&clean),
            normalize(EXPECTED[index]),
            "{case}: sanitized output diverged from the golden file"
        );
        assert_eq!(meta.sanitized, *sanitized, "{case}: {meta:?}");
        assert_eq!(meta.repaired, *repaired, "{case}: {meta:?}");
        assert!(!meta.reason.is_empty(), "{case}: the failure is reported");
    }
}

#[test]
fn a_fenced_stylesheet_is_never_written_verbatim() {
    // The 9.9.3 fix: `.css` used to check only for braces, so a fenced
    // stylesheet was written fences and all.
    let (clean, meta) = sanitize_write_content("styles.css", GOLDEN[2].2, "");
    assert!(!clean.contains("```"));
    assert!(meta.sanitized);
}

#[test]
fn everything_the_sanitizer_produces_survives_a_second_pass() {
    // The loop sanitizes and the tool sanitizes again (v11.7.0 closes both
    // doors). That is only safe if the pipeline is idempotent on its own
    // output, so every vector in this file is run twice.
    let mut vectors: Vec<(&str, String)> = GOLDEN
        .iter()
        .map(|(_, target, dirty, _, _)| (*target, (*dirty).to_string()))
        .collect();
    vectors.extend([
        ("note.md", "# Title\n\nBody text.\n".to_string()),
        ("page.html", "Sure! here you go".to_string()),
        ("script.py", "def broken(:\n    pass\n".to_string()),
        ("data.json", "not json at all".to_string()),
        ("a.txt", "물론입니다! 아래 내용입니다.".to_string()),
        ("styles.css", "no rules here".to_string()),
        ("app.js", "function a() {".to_string()),
        ("C.vue", "<template>\n<p>hi</p>\n".to_string()),
    ]);
    for (target, raw) in vectors {
        let (once, _) = sanitize_write_content(target, &raw, "second pass");
        let (twice, meta) = sanitize_write_content(target, &once, "second pass");
        assert_eq!(twice, once, "{target} is not idempotent: {meta:?}");
    }
}

#[test]
fn clean_content_is_returned_byte_for_byte() {
    for (target, content) in [
        ("note.md", "hello"),
        ("data.json", "{\"a\": 1}"),
        ("page.html", "<!DOCTYPE html><html><body>ok</body></html>"),
        ("script.py", "import sys\n\nprint(sys.argv)\n"),
        ("styles.css", "body { margin: 0; }"),
        ("run.sh", "#!/bin/sh\necho hi\n"),
        ("q.sql", "SELECT 1;"),
        ("unknown.bin", "raw bytes as text"),
    ] {
        let (clean, meta) = sanitize_write_content(target, content, "keep it");
        assert_eq!(clean, content, "{target}");
        assert_eq!(meta, SanitizeMeta::untouched("ok"), "{target}");
    }
}

#[test]
fn empty_content_is_never_repaired() {
    // Creating an empty file is an intentional act (`__init__.py`).
    for blank in ["", "   ", "\n\n"] {
        let (clean, meta) = sanitize_write_content("src/__init__.py", blank, "");
        assert_eq!(clean, blank);
        assert_eq!(meta, SanitizeMeta::untouched("empty"));
    }
}

#[test]
fn a_truncated_document_is_closed_and_then_validates() {
    // FG-04.
    let truncated = "<!DOCTYPE html><html><head><title>t</title></head><body><p>hi</p>";
    let (fixed, meta) = sanitize_write_content("page.html", truncated, "");
    assert!(meta.sanitized && meta.repaired);
    assert_eq!(
        meta.reason, "HTML document is truncated (missing </html>)",
        "the original failure is what gets reported"
    );
    assert_eq!(
        validate_file_content(&fixed, "page.html"),
        (true, "ok".into())
    );
    assert!(fixed.ends_with("</body>\n</html>"));
}

#[test]
fn an_already_closed_body_less_document_is_returned_unchanged() {
    let content = "<!DOCTYPE html><html><head><title>표</title></head></html>";
    let repaired = repair_file_content(content, "page.html", "표 페이지 만들어줘");
    assert_eq!(repaired, content);
    assert_eq!(repaired.matches("</html>").count(), 1);
    assert!(!repaired.contains("</body>"));
}

#[test]
fn a_reply_that_was_only_a_think_block_is_repaired_from_the_raw_text() {
    let content = "<think>파이썬 파일을 어떻게 쓸지 고민한다</think>";
    let (result, meta) = sanitize_write_content("script.py", content, "정렬 스크립트");
    assert!(meta.sanitized && meta.repaired);
    assert!(!meta.reason.is_empty());
    assert!(result.starts_with("# TODO: model produced invalid Python for: 정렬 스크립트"));
    // Nothing is thrown away: the raw draft survives as comments.
    assert!(result.contains("# <think>파이썬 파일을 어떻게 쓸지 고민한다</think>"));
}

#[test]
fn the_python_repair_path_always_yields_a_parseable_module() {
    let (fixed, meta) = sanitize_write_content("script.py", "def broken(:\n    pass\n", "");
    assert!(meta.sanitized && meta.repaired);
    assert_eq!(
        meta.reason,
        "invalid Python syntax: '(' was never closed (line 1)"
    );
    assert!(python_parses(&fixed).is_ok(), "{fixed}");
    assert!(
        fixed.contains("broken"),
        "the draft is preserved as comments"
    );
    assert!(fixed.contains("# TODO: model produced invalid Python for: content for script.py"));
}

#[test]
fn nothing_usable_leaves_an_honest_placeholder_in_the_right_format() {
    for (target, expected) in [
        (
            "a.py",
            "# TODO: model produced no usable content for: 요청\n",
        ),
        (
            "a.sh",
            "# TODO: model produced no usable content for: 요청\n",
        ),
        (
            "a.js",
            "// TODO: model produced no usable content for: 요청\n",
        ),
        (
            "a.ts",
            "// TODO: model produced no usable content for: 요청\n",
        ),
        (
            "a.css",
            "/* TODO: model produced no usable content for: 요청 */\n",
        ),
        (
            "a.sql",
            "-- TODO: model produced no usable content for: 요청\n",
        ),
        ("a.md", "요청\n"),
    ] {
        assert_eq!(
            repair_file_content("I'm sorry, I can't", target, "요청"),
            expected
        );
    }
}

#[test]
fn an_unparseable_json_reply_becomes_a_document_that_records_the_request() {
    let repaired = repair_file_content("kein json", "data.json", "설정 파일");
    assert_eq!(
        repaired, "{\n  \"request\": \"설정 파일\",\n  \"content\": \"kein json\"\n}",
        "json.dumps(..., ensure_ascii=False, indent=2) in insertion order"
    );
    assert!(pyjson::loads(&repaired).is_ok());
}

#[test]
fn a_prose_reply_becomes_a_page_rather_than_an_error() {
    let repaired = repair_file_content("첫 줄\n\n둘째 줄 & <b", "page.html", "안내 페이지");
    assert!(repaired.starts_with("<!DOCTYPE html>\n<html lang=\"ko\">"));
    assert!(repaired.contains("<title>안내 페이지</title>"));
    assert!(
        repaired.contains("  <p>첫 줄</p>\n  <p>둘째 줄 &amp; &lt;b</p>"),
        "prose is escaped, one paragraph per non-blank line: {repaired}"
    );
    assert!(repaired.ends_with("</body>\n</html>"));
    // An HTML fragment is embedded as-is rather than escaped.
    assert!(repair_file_content("<p>hi</p>", "page.html", "x").contains("<p>hi</p>\n</body>"));
    // No salvage at all still produces a page, titled by the request.
    assert!(repair_file_content("", "page.html", "").contains("<title>Generated page</title>"));
}

#[test]
fn the_validation_reasons_are_pythons_strings() {
    for (content, target, reason) in [
        ("   ", "a.md", "empty output"),
        (
            "I'm sorry, I cannot assist with that request.",
            "a.md",
            "the reply was a refusal/chat message, not file content",
        ),
        (
            "no document here",
            "a.html",
            "not a complete HTML document (missing <!DOCTYPE html>/<html>)",
        ),
        (
            "<html><body>hi</body>",
            "a.html",
            "HTML document is truncated (missing </html>)",
        ),
        (
            "intro\n<html><body>hi</body></html>",
            "a.html",
            "HTML document is wrapped in prose or fences",
        ),
        (
            "```\nx\n```",
            "a.md",
            "output still contains Markdown fences",
        ),
        ("no rules", "a.css", "no CSS rule blocks found"),
        (
            "Sure! Here is the file you asked for.",
            "a.md",
            "the reply talks about the file instead of being the file",
        ),
        (
            "function a() {",
            "a.ts",
            "unbalanced braces ({}) — the file looks truncated",
        ),
        (
            "<template>\n<p>hi</p>\n",
            "a.vue",
            "<template> block is not closed — the component looks truncated",
        ),
    ] {
        let (ok, actual) = validate_file_content(content, target);
        assert!(!ok, "{target}: {content:?} should not validate");
        assert_eq!(actual, reason, "{target}");
    }
    assert_eq!(
        validate_file_content("{\"a\": 1} trailing", "a.json").1,
        "invalid JSON: Extra data: line 1 column 10 (char 9)",
        "CPython's decoder text, through `pyjson`"
    );
}

#[test]
fn braced_code_counts_delimiters_outside_literals_only() {
    const VALID_TSX: &str = "// braces in comments { should not count }\n\
const label = \"unmatched { in a string\";\n\
const tpl = `also { unmatched`;\n\
export default function App(): JSX.Element {\n\
  return <div onClick={() => console.log('hi')}>ok</div>;\n\
}\n";
    for target in ["App.tsx", "App.jsx", "util.ts", "util.js"] {
        assert_eq!(
            validate_file_content(VALID_TSX, target),
            (true, "ok".into())
        );
    }
    // An escaped brace inside a regex literal must not false-reject.
    let code = "const m = s.replace(/\\{/g, '(');\nexport const ok = () => m;\n";
    assert!(validate_file_content(code, "re.ts").0, "{code}");
    // …and a truncated file still fails.
    let truncated = "export function run() {\n  if (true) {\n    doIt();\n";
    for target in ["a.ts", "a.tsx", "a.js", "a.jsx"] {
        let (ok, reason) = validate_file_content(truncated, target);
        assert!(!ok && reason.contains("unbalanced"), "{target}: {reason}");
    }
    assert!(!validate_file_content("```tsx\nexport const x = 1;\n```", "x.tsx").0);
}

#[test]
fn single_file_components_are_judged_by_their_blocks() {
    const VALID_VUE: &str =
        "<template>\n  <button @click=\"count++\">{{ count }}</button>\n</template>\n\
<script setup>\nimport { ref } from 'vue';\nconst count = ref(0);\n</script>\n\
<style scoped>\nbutton { padding: 8px; }\n</style>\n";
    const VALID_SVELTE: &str = "<script>\n  let count = 0;\n</script>\n\
<button on:click={() => count++}>{count}</button>\n\
<style>\n  button { padding: 8px; }\n</style>\n";
    assert!(validate_file_content(VALID_VUE, "Counter.vue").0);
    assert!(validate_file_content(VALID_SVELTE, "Counter.svelte").0);
    assert!(!validate_file_content("<script>\n  let x = 1;\n", "Broken.svelte").0);
    // A fenced component is recovered by extraction and then validates.
    let raw = format!("Sure! Here is the component:\n```vue\n{VALID_VUE}```\nEnjoy!");
    let extracted = extract_file_content(&raw, "Counter.vue");
    assert!(
        validate_file_content(&extracted, "Counter.vue").0,
        "{extracted}"
    );
}

#[test]
fn extraction_prefers_the_matching_language_then_the_longest_block() {
    let raw = "```bash\nnpm run dev\n```\n\n```python\nimport os\nprint(os.getcwd())\n```\n";
    assert_eq!(
        extract_file_content(raw, "a.py"),
        "import os\nprint(os.getcwd())",
        "the language tag wins over the position"
    );
    // With no tag to match, the longest block wins…
    let untagged = "```\nshort\n```\n```\na much longer block of content\n```\n";
    assert_eq!(
        extract_file_content(untagged, "a.md"),
        "a much longer block of content"
    );
    // …and the first of two equally long ones (`max` keeps the first).
    let tied = "```\naaaaa\n```\n```\nbbbbb\n```\n";
    assert_eq!(extract_file_content(tied, "a.md"), "aaaaa");
    // An unclosed final fence is still recovered.
    assert_eq!(extract_file_content("```py\nx = 1\n", "a.py"), "x = 1");
}

#[test]
fn extraction_strips_reasoning_blocks_and_chat_framing() {
    let raw = "<think>plan it</think>\nSure! Here is the file:\nthe body\nLet me know if you need changes!";
    assert_eq!(extract_file_content(raw, "a.md"), "the body");
    // An unclosed think block (the model hit the token limit) goes too.
    assert_eq!(
        extract_file_content("keep\n<thinking>and then", "a.md"),
        "keep"
    );
    // Nothing but chat lines leaves the text alone rather than emptying it.
    assert_eq!(extract_file_content("Sure!", "a.md"), "Sure!");
    assert_eq!(extract_file_content("   ", "a.md"), "");
}

#[test]
fn json_extraction_takes_the_largest_parseable_value() {
    let raw = "Here is the config:\n{\"a\": 1, \"b\": [2, 3]}\nHope this helps!";
    assert_eq!(
        extract_file_content(raw, "a.json"),
        "{\"a\": 1, \"b\": [2, 3]}"
    );
    // Nothing parseable leaves the content for the repair pass.
    assert_eq!(
        extract_file_content("no json here", "a.json"),
        "no json here"
    );
}

#[test]
fn the_structural_python_check_rejects_only_definite_breakage() {
    for valid in [
        "x = 1\n",
        "print(f\"args: {len('ab') - 1}\")\n",
        "# a comment with ! and $ and `backticks`\n",
        "s = '# not a comment'\n",
        "doc = \"\"\"\nmulti\nline ! $\n\"\"\"\n",
        "if a != b:\n    pass\n",
        "path = 'C:\\\\tmp'\n",
        "match command:\n    case _:\n        pass\n",
        "total = (1 +\n         2)\n",
        "value = 1 \\\n    + 2\n",
        "@decorator\ndef f():\n    return -1\n",
    ] {
        assert_eq!(python_parses(valid), Ok(()), "{valid:?}");
    }
    for (broken, msg, line) in [
        ("def f(:\n    pass\n", "'(' was never closed", 1),
        ("x = [1, 2\ny = 3\n", "'[' was never closed", 1),
        ("x = 1)\n", "unmatched ')'", 1),
        (
            "x = (1]\n",
            "closing parenthesis ']' does not match opening parenthesis '('",
            1,
        ),
        ("Sure! Here is the script.\n", "invalid syntax", 1),
        ("<think>plan</think>\n", "invalid syntax", 1),
        (
            "x = 'open\n",
            "unterminated string literal (detected at line 1)",
            1,
        ),
        (
            "x = \"\"\"open\nmore\n",
            "unterminated triple-quoted string literal (detected at line 2)",
            1,
        ),
        ("x = 1\ny = $2\n", "invalid syntax", 2),
    ] {
        assert_eq!(
            python_parses(broken),
            Err(SyntaxFault {
                msg: msg.into(),
                line
            }),
            "{broken:?}"
        );
    }
}

#[test]
fn the_meta_is_pythons_dict_in_pythons_order() {
    let meta = SanitizeMeta {
        sanitized: true,
        repaired: false,
        reason: "why".into(),
    };
    assert_eq!(
        meta.to_value(),
        serde_json::json!({"sanitized": true, "repaired": false, "reason": "why"})
    );
    assert_eq!(ext_of("a/b/c.TXT"), ".txt");
    assert_eq!(ext_of("noext"), "");
}
