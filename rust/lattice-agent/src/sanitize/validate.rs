//! `validation.py` — per-extension structural checks.

use crate::pyjson;
use crate::pystr::{char_len, char_slice};

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
