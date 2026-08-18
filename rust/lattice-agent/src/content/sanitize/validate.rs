//! `validation.py` — per-extension structural checks.

use crate::parse::pyjson;
use crate::parse::pystr::{char_len, char_slice};

use super::python::{python_parses, SyntaxFault};
use super::text::{
    block_tag, body_structure, commentary, contains_ci, ext_of, matched, refusal, starts_with_ci,
};

// ── validation (`validation.py`) ────────────────────────────────────────────

/// `looks_like_refusal(content)`.
pub fn looks_like_refusal(content: &str) -> bool {
    matched(refusal(), char_slice(content, 300)) && char_len(content) < 600
}

/// `validate_file_content(content, target_path)` — `(ok, reason)`.
pub fn validate_file_content(content: &str, target_path: &str) -> (bool, String) {
    let refuse = |reason: &str| (false, reason.to_string());
    if content.trim().is_empty() {
        return refuse("empty output");
    }
    if looks_like_refusal(content) {
        return refuse("the reply was a refusal/chat message, not file content");
    }
    let fenced = || content.contains("```");
    let ext = ext_of(target_path);
    match ext.as_str() {
        ".html" | ".htm" => {
            if !contains_ci(content, "<html") && !contains_ci(content, "<!doctype") {
                return refuse("not a complete HTML document (missing <!DOCTYPE html>/<html>)");
            }
            if !contains_ci(content, "</html>") {
                return refuse("HTML document is truncated (missing </html>)");
            }
            // A document that merely *contains* html somewhere is not a valid
            // payload: a fenced or chat-wrapped reply must fail here so the
            // extraction pass gets its chance to slice the real document out.
            let stripped = content.trim_start();
            if !(starts_with_ci(stripped, "<!doctype") || starts_with_ci(stripped, "<html")) {
                return refuse("HTML document is wrapped in prose or fences");
            }
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            (true, "ok".into())
        }
        ".json" => match pyjson::loads(content) {
            Ok(_) => (true, "ok".into()),
            Err(error) => (false, format!("invalid JSON: {error}")),
        },
        ".css" => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            if !content.contains('{') || !content.contains('}') {
                return refuse("no CSS rule blocks found");
            }
            (true, "ok".into())
        }
        ".py" => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            match python_parses(content) {
                Ok(()) => (true, "ok".into()),
                Err(SyntaxFault { msg, line }) => {
                    (false, format!("invalid Python syntax: {msg} (line {line})"))
                }
            }
        }
        ".js" | ".jsx" | ".ts" | ".tsx" => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            check_balanced_delimiters(content)
        }
        ".vue" | ".svelte" => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            check_component_blocks(content)
        }
        ".sh" | ".sql" => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            (true, "ok".into())
        }
        // Prose types have no grammar to check, which used to mean *nothing*
        // was checked. The two checks that generalise without inventing one:
        // it must not still be wearing fences, and it must not be only an
        // answer *about* the file.
        _ => {
            if fenced() {
                return refuse("output still contains Markdown fences");
            }
            if looks_like_commentary(content) {
                return refuse("the reply talks about the file instead of being the file");
            }
            (true, "ok".into())
        }
    }
}

/// Headings a model puts on its own reasoning, lower-cased.
///
/// Every one of them is a *whole line* a reply opens with — never a phrase
/// inside one — so a document legitimately titled "Analysis of Q3" is untouched
/// and a reply that opens `Thinking Process:` is not. Kept short and literal
/// for the same reason [`super::text::refusal`] is: a list of things models
/// actually write beats a clever pattern that also matches prose.
const REASONING_HEADINGS: &[&str] = &[
    "thinking process",
    "thought process",
    "chain of thought",
    "internal monologue",
    "reasoning process",
    "thinking",
    "thoughts",
    "사고 과정",
    "생각 과정",
    "사고과정",
];

/// Whether `content` is the model reasoning **about** the task rather than the
/// artefact the task asked for.
///
/// The plain-text twin of the `<think>…</think>` block
/// [`super::text::think_block`] already strips: a model whose chat template
/// carries no reasoning tag writes the same thing under a heading, and a live
/// gemma-4-e2b wrote 2,371 bytes of `Thinking Process:` — an enumerated
/// internal monologue that ends by wondering what the user meant — into
/// `notes/hello.md` where a greeting was wanted, three attempts out of three.
///
/// Deliberately narrow: it reads the **first non-empty line only**, requires
/// that line to be one of [`REASONING_HEADINGS`] and nothing else once markdown
/// decoration and a trailing colon come off, and says nothing about the rest of
/// the document. A file whose opening line is a bare `Thinking Process:` is a
/// model talking to itself in every language and format there is; a file that
/// merely mentions thinking is a file.
pub fn looks_like_reasoning_preamble(content: &str) -> bool {
    let Some(first) = content.lines().map(str::trim).find(|line| !line.is_empty()) else {
        return false;
    };
    let head = first
        .trim_start_matches(['#', '*', '-', '>', ' '])
        .trim_end_matches(['*', ':', '：', '.', ' '])
        .trim()
        .to_lowercase();
    REASONING_HEADINGS.iter().any(|heading| head == *heading)
}

/// `_looks_like_commentary(content)`.
fn looks_like_commentary(content: &str) -> bool {
    let stripped = content.trim();
    if char_len(stripped) > 400 {
        return false;
    }
    if !matched(commentary(), stripped) {
        return false;
    }
    let body = match stripped.split_once('\n') {
        Some((_, rest)) => rest.trim(),
        None => "",
    };
    if matched(body_structure(), body) {
        return false;
    }
    char_len(body) < 120
}

/// `_strip_code_literals(text)` — quotes and comments removed so a delimiter
/// count stays honest.
fn strip_code_literals(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let (mut index, count) = (0usize, chars.len());
    while index < count {
        let ch = chars[index];
        let next = if index + 1 < count {
            chars[index + 1]
        } else {
            '\0'
        };
        if ch == '\\' {
            index += 2;
            continue;
        }
        if ch == '\'' || ch == '"' || ch == '`' {
            let quote = ch;
            index += 1;
            while index < count {
                if chars[index] == '\\' {
                    index += 2;
                    continue;
                }
                if chars[index] == quote {
                    index += 1;
                    break;
                }
                index += 1;
            }
            continue;
        }
        if ch == '/' && next == '/' {
            while index < count && chars[index] != '\n' {
                index += 1;
            }
            continue;
        }
        if ch == '/' && next == '*' {
            match chars[index + 2..]
                .windows(2)
                .position(|pair| pair == ['*', '/'])
            {
                Some(at) => index = index + 2 + at + 2,
                None => index = count,
            }
            continue;
        }
        out.push(ch);
        index += 1;
    }
    out
}

/// `_check_balanced_delimiters(content)`.
pub(super) fn check_balanced_delimiters(content: &str) -> (bool, String) {
    let stripped = strip_code_literals(content);
    for (opener, closer, label) in [
        ('{', '}', "braces"),
        ('(', ')', "parentheses"),
        ('[', ']', "brackets"),
    ] {
        if stripped.matches(opener).count() != stripped.matches(closer).count() {
            return (
                false,
                format!("unbalanced {label} ({opener}{closer}) — the file looks truncated"),
            );
        }
    }
    (true, "ok".into())
}

/// `_check_component_blocks(content)`.
pub(super) fn check_component_blocks(content: &str) -> (bool, String) {
    let lower = content.to_lowercase();
    for tag in ["template", "script", "style"] {
        let opened = block_tag(tag)
            .find_iter(&lower)
            .filter(|found| found.is_ok())
            .count();
        let closed = lower.matches(&format!("</{tag}>")).count();
        if opened != closed {
            return (
                false,
                format!("<{tag}> block is not closed — the component looks truncated"),
            );
        }
    }
    (true, "ok".into())
}
