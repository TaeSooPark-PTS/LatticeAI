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
    for sentence in text.split(['.', '!', '?', '\n']) {
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

/// Synthesize a strictly grounded answer from active (non-superseded) evidence passages.
pub fn synthesize_active_evidence_answer(
    passages: &[crate::kernel::transcript::rag_grounding::EvidencePassage],
) -> Option<String> {
    let active: Vec<_> = passages.iter().filter(|p| !p.superseded).collect();
    if active.is_empty() {
        return None;
    }
    let lines: Vec<String> = active
        .iter()
        .enumerate()
        .map(|(idx, p)| {
            let num = idx + 1;
            match p.source.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                Some(src) => format!("[{num}] ({src}) {}", p.text.trim()),
                None => format!("[{num}] {}", p.text.trim()),
            }
        })
        .collect();
    Some(lines.join("\n"))
}

/// Complete an answer with deterministic findings from executed read, search,
/// web, knowledge, and computer-use tool steps when the model's answer omits them.
pub fn complete_tool_findings(said: &str, request: &str, transcript: &[Value]) -> String {
    if request_asks_for_a_count(request) || request_asks_for_a_summary(request) {
        return said.to_string();
    }
    if crate::kernel::transcript::rag_grounding::transcript_has_evidence(transcript) {
        use crate::kernel::transcript::rag_grounding::{
            extract_evidence_passages, verify_grounded_answer, GroundingDefect,
        };
        let passages = extract_evidence_passages(transcript);
        let active: Vec<_> = passages.iter().filter(|p| !p.superseded).collect();
        if active.is_empty() {
            return said.to_string();
        }
        let report = verify_grounded_answer(said, &passages);
        if report.has(GroundingDefect::SupersededFactLeak)
            || report.has(GroundingDefect::NumericMismatch)
            || report.has(GroundingDefect::PolarityMismatch)
        {
            return said.to_string();
        }
        let said_lower = said.to_lowercase();
        let cites_active_with_overlap = report.is_clean()
            && (1..=active.len()).any(|n| said.contains(&format!("[{n}]")))
            && active.iter().any(|p| {
                p.text
                    .split(|c: char| !c.is_alphanumeric())
                    .filter(|tok| tok.chars().count() >= 3)
                    .any(|tok| said_lower.contains(&tok.to_lowercase()))
            });
        let all_active_covered = cites_active_with_overlap
            || (report.is_clean()
                && !answer_is_generic_ack(said)
                && active.iter().all(|p| {
                    let snippet = char_slice(p.text.trim(), 24).to_lowercase();
                    (!snippet.is_empty() && said_lower.contains(&snippet))
                        || p.source.as_deref().is_some_and(|s| {
                            !s.is_empty() && said_lower.contains(&s.to_lowercase())
                        })
                }));
        if all_active_covered {
            return said.to_string();
        }
        if let Some(grounded) = synthesize_active_evidence_answer(&passages) {
            return if said.trim().is_empty() || answer_is_generic_ack(said) {
                grounded
            } else {
                format!("{}\n\n{grounded}", said.trim())
            };
        }
    }

    let mut findings: Vec<String> = Vec::new();
    let said_lower = said.to_lowercase();
    for step in transcript {
        if step.get("error").is_some() {
            continue;
        }
        let Some(raw_action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        let action = raw_action.strip_prefix("mcp.").unwrap_or(raw_action);
        let Some(result) = step.get("result") else {
            continue;
        };
        match action {
            "read_file" => {
                let content = result
                    .get("content")
                    .or_else(|| result.get("text"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim();
                let path = result
                    .get("path")
                    .or_else(|| step.get("args").and_then(|a| a.get("path")))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                if !content.is_empty() && !said.contains(content) {
                    let body = char_slice(content, 500);
                    if path.is_empty() {
                        findings.push(body.to_string());
                    } else {
                        findings.push(format!("{path}: {body}"));
                    }
                }
            }
            "grep" => {
                if let Some(matches) = result.get("matches").and_then(Value::as_array) {
                    for m in matches.iter().take(5) {
                        let p = m.get("path").and_then(Value::as_str).unwrap_or("");
                        let t = m.get("text").and_then(Value::as_str).unwrap_or("");
                        if !p.is_empty() && !said_lower.contains(&p.to_lowercase()) {
                            findings.push(format!("{p}: {t}"));
                        }
                    }
                }
            }
            "list_dir" => {
                if let Some(items) = result.get("items").and_then(Value::as_array) {
                    let names: Vec<String> = items
                        .iter()
                        .filter_map(|it| {
                            it.as_str()
                                .or_else(|| it.get("name").and_then(Value::as_str))
                                .map(String::from)
                        })
                        .collect();
                    if !names.is_empty()
                        && !names.iter().all(|n| said_lower.contains(&n.to_lowercase()))
                    {
                        findings.push(format!("{} items: {}", names.len(), names.join(", ")));
                    }
                }
            }
            "computer_click" => {
                let x = result
                    .get("x")
                    .or_else(|| step.get("args").and_then(|a| a.get("x")))
                    .and_then(Value::as_i64);
                let y = result
                    .get("y")
                    .or_else(|| step.get("args").and_then(|a| a.get("y")))
                    .and_then(Value::as_i64);
                let label = result
                    .get("target_label")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let tid = result
                    .get("target_id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                if let (Some(cx), Some(cy)) = (x, y) {
                    let coord_str = format!("({cx}, {cy})");
                    if !said.contains(&coord_str) {
                        findings.push(format!("Clicked {label} ({tid}) at ({cx}, {cy})."));
                    }
                }
            }
            "computer_type" => {
                let text = result
                    .get("text")
                    .or_else(|| step.get("args").and_then(|a| a.get("text")))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                if !text.is_empty() && !said_lower.contains(&text.to_lowercase()) {
                    findings.push(format!("Typed '{text}'."));
                }
            }
            "computer_key" => {
                let key = result
                    .get("key")
                    .or_else(|| step.get("args").and_then(|a| a.get("key")))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                if !key.is_empty() && !said_lower.contains(&key.to_lowercase()) {
                    findings.push(format!("Pressed key '{key}'."));
                }
            }
            _ => {}
        }
    }
    if findings.is_empty() {
        return said.to_string();
    }
    let summary = findings.join("\n");
    if said.trim().is_empty() || answer_is_generic_ack(said) {
        summary
    } else {
        format!("{}\n\n{summary}", said.trim())
    }
}

/// Count repair, summary repair, and transcript tool-finding synthesis.
pub fn complete_counted_and_summarized(said: &str, request: &str, transcript: &[Value]) -> String {
    let said = complete_a_count(said, request, transcript);
    let said = complete_a_summary(&said, request, transcript);
    complete_tool_findings(&said, request, transcript)
}
