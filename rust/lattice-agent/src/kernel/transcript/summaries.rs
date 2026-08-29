use super::*;
use crate::parse::pystr::char_slice;
use serde_json::Value;

/// A file this run actually read, from which a summary may be grounded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourcedText {
    pub path: String,
    pub content: String,
}

/// Whether the request asked for a summary of something that was read.
///
/// Whole-word `summary` / `summarize` so `summarily` and account-adjacent
/// English do not fire; Korean markers (`요약`, `정리해`, `줄여`) are not
/// fragments of other words in the product's copy.
pub fn request_asks_for_a_summary(request: &str) -> bool {
    let hay = request.to_lowercase();
    hay.contains("요약")
        || hay.contains("정리해")
        || hay.contains("줄여서")
        || hay.contains("줄여 줘")
        || hay.contains("줄여줘")
        || contains_ascii_word(&hay, "summary")
        || contains_ascii_word(&hay, "summarize")
        || contains_ascii_word(&hay, "summarise")
        || hay.contains("tl;dr")
}

fn is_read_action(action: &str) -> bool {
    action == "read_file"
        || action
            .rsplit_once('.')
            .is_some_and(|(_, bare)| bare == "read_file")
}

/// The most recent successful file read this run can ground a summary in.
pub fn attributed_summary(transcript: &[Value]) -> Option<SourcedText> {
    transcript.iter().rev().find_map(|step| {
        if step.get("error").is_some() {
            return None;
        }
        let action = step.get("action").and_then(Value::as_str)?;
        if !is_read_action(action) {
            return None;
        }
        let result = step.get("result")?;
        let content = result
            .get("content")
            .or_else(|| result.get("text"))
            .and_then(Value::as_str)?
            .trim();
        if content.is_empty() {
            return None;
        }
        let path = result
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        Some(SourcedText {
            path,
            content: content.to_string(),
        })
    })
}

fn significant_span(text: &str, min: usize) -> Option<&str> {
    let trimmed = text.trim();
    if trimmed.chars().count() < min {
        return None;
    }
    Some(char_slice(trimmed, min))
}

fn answer_is_generic_ack(answer: &str) -> bool {
    let trimmed = answer.trim();
    if trimmed.is_empty() {
        return true;
    }
    let chars = trimmed.chars().count();
    if chars < 40 {
        return true;
    }
    let lower = trimmed.to_lowercase();
    chars < 80 && (lower.contains("요약") || lower.contains("summar"))
}

/// Whether the answer already carries a grounded slice of the source.
pub fn answer_carries_summary(answer: &str, source: &SourcedText) -> bool {
    if answer_is_generic_ack(answer) {
        return false;
    }
    match significant_span(&source.content, 16) {
        Some(span) if !span.trim().is_empty() => {
            answer.contains(span) || answer.to_lowercase().contains(&span.to_lowercase())
        }
        _ => answer.chars().count() >= 120,
    }
}

fn excerpt(text: &str, max_chars: usize) -> String {
    let mut out = String::new();
    for sentence in text.split(|ch: char| matches!(ch, '.' | '!' | '?' | '\n')) {
        let piece = sentence.trim();
        if piece.is_empty() {
            continue;
        }
        if !out.is_empty() {
            out.push(' ');
        }
        let next_len = out.chars().count() + piece.chars().count();
        if next_len > max_chars {
            if out.is_empty() {
                let cut = char_slice(piece, max_chars).trim_end();
                return format!("{cut}…");
            }
            break;
        }
        out.push_str(piece);
        if !piece.ends_with('.') {
            out.push('.');
        }
        if out.chars().count() >= 80 {
            break;
        }
    }
    out
}

/// Restore a grounded summary when the request asked for one and the answer
/// is still thin. The words come from a `read_file` / `mcp.read_file` result
/// already on the transcript — never invented.
pub fn complete_a_summary(said: &str, request: &str, transcript: &[Value]) -> String {
    if !request_asks_for_a_summary(request) {
        return said.to_string();
    }
    let Some(source) = attributed_summary(transcript) else {
        return said.to_string();
    };
    if answer_carries_summary(said, &source) {
        return said.to_string();
    }
    let body = excerpt(&source.content, 600);
    if body.is_empty() {
        return said.to_string();
    }
    let fact = if source.path.is_empty() {
        body
    } else {
        format!("{} 요약:\n\n{body}", source.path)
    };
    if said.trim().is_empty() || answer_is_generic_ack(said) {
        fact
    } else {
        format!("{}\n\n{fact}", said.trim())
    }
}

/// A summary request whose answer still does not carry the file's own words
/// after [`complete_a_summary`] has had its chance.
pub fn answer_owes_a_summary(answer: &str, request: &str, transcript: &[Value]) -> bool {
    if !request_asks_for_a_summary(request) {
        return false;
    }
    match attributed_summary(transcript) {
        Some(source) => !answer_carries_summary(answer, &source),
        None => false,
    }
}

/// Count repair then summary repair — the two deterministic deliverables.
pub fn complete_counted_and_summarized(said: &str, request: &str, transcript: &[Value]) -> String {
    let said = complete_a_count(said, request, transcript);
    complete_a_summary(&said, request, transcript)
}
