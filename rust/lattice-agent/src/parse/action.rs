use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{Map, Value};

use crate::parse::pyjson;

#[path = "action_salvage.rs"]
mod salvage;
use salvage::{normalize_call_keys, salvage_labeled_action};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionError(pub String);

impl std::fmt::Display for ActionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ActionError {}

pub type Parsed = (Map<String, Value>, Vec<String>);

fn compiled(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("ported pattern must compile"))
}

fn think_block() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?is)<(think|thinking|reasoning)>.*?</\1>")
}

fn fenced_block() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?s)```(?:json)?\s*(\{.*?\})\s*```")
}

fn trailing_comma() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r",\s*([}\]])")
}

pub(super) fn escape_unescaped_newlines(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_string = false;
    let mut escaped = false;
    for ch in text.chars() {
        if in_string {
            if escaped {
                escaped = false;
                out.push(ch);
            } else if ch == '\\' {
                escaped = true;
                out.push(ch);
            } else if ch == '"' {
                in_string = false;
                out.push(ch);
            } else {
                match ch {
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    _ => out.push(ch),
                }
            }
        } else {
            if ch == '"' {
                in_string = true;
            }
            out.push(ch);
        }
    }
    out
}

fn tag_wrapper() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        concat!(
            r"(?i)</?(?:tool_call|tool_code|tool_use|function_call)>",
            r"|\[/?(?:TOOL_CALL|TOOL_CODE|TOOL_USE|FUNCTION_CALL)\]",
            r"|<\|(?:tool_call|tool_calls|tool_code)\|>",
        ),
    )
}

pub fn first_balanced_object(text: &str) -> Option<(usize, usize)> {
    let mut depth = 0usize;
    let mut start = None;
    let mut in_string = false;
    let mut escaped = false;
    for (offset, character) in text.char_indices() {
        if in_string {
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                in_string = false;
            }
            continue;
        }
        match character {
            '"' => in_string = true,
            '{' => {
                if depth == 0 {
                    start = Some(offset);
                }
                depth += 1;
            }
            '}' => {
                if depth == 0 {
                    continue;
                }
                depth -= 1;
                if depth == 0 {
                    return start.map(|start| (start, offset + character.len_utf8()));
                }
            }
            _ => {}
        }
    }
    None
}

fn truncation_repairs(text: &str) -> Vec<String> {
    let mut stack: Vec<char> = Vec::new();
    let mut in_string = false;
    let mut escaped = false;
    let mut last_comma: Option<usize> = None;
    for (offset, character) in text.char_indices() {
        if in_string {
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                in_string = false;
            }
            continue;
        }
        match character {
            '"' => in_string = true,
            '{' => stack.push('}'),
            '[' => stack.push(']'),
            '}' | ']' => {
                stack.pop();
            }
            ',' => last_comma = Some(offset),
            _ => {}
        }
    }
    if stack.is_empty() && !in_string {
        return Vec::new();
    }
    let close = |body: &str, in_string: bool, stack: &[char]| {
        let mut closed = body.to_string();
        if in_string {
            closed.push('"');
        }
        closed.extend(stack.iter().rev());
        closed
    };
    let closed = close(text, in_string, &stack);
    let mut candidates = vec![closed.clone()];
    let comma_repaired = trailing_comma().replace_all(&closed, "$1").to_string();
    if comma_repaired != closed {
        candidates.push(comma_repaired);
    }
    if let Some(comma) = last_comma {
        let head = &text[..comma];
        let mut trimmed_stack: Vec<char> = Vec::new();
        let mut trimmed_string = false;
        let mut trimmed_escape = false;
        for character in head.chars() {
            if trimmed_string {
                if trimmed_escape {
                    trimmed_escape = false;
                } else if character == '\\' {
                    trimmed_escape = true;
                } else if character == '"' {
                    trimmed_string = false;
                }
                continue;
            }
            match character {
                '"' => trimmed_string = true,
                '{' => trimmed_stack.push('}'),
                '[' => trimmed_stack.push(']'),
                '}' | ']' => {
                    trimmed_stack.pop();
                }
                _ => {}
            }
        }
        candidates.push(close(head, trimmed_string, &trimmed_stack));
    }
    candidates
}

pub fn extract_json_object(raw: &str) -> Result<(Value, Vec<String>), ActionError> {
    let mut repairs: Vec<String> = Vec::new();
    let stripped = think_block().replace_all(raw, "");
    let mut text: String = stripped.trim().to_string();
    if text != raw.trim() {
        repairs.push("think_strip".into());
    }

    if !text.starts_with('{') {
        let mut untagged = text.clone();
        if let Some(payload) = crate::parse::channel::strip_channel_frames(&untagged) {
            untagged = payload;
        }
        untagged = tag_wrapper().replace_all(&untagged, "").trim().to_string();
        if untagged != text {
            text = untagged;
            repairs.push("tag_strip".into());
        }
    }

    let fenced: Option<String> =
        fenced_block()
            .captures(&text)
            .ok()
            .flatten()
            .and_then(|captures| {
                captures
                    .get(1)
                    .map(|group| group.as_str().trim().to_string())
            });
    if let Some(inner) = fenced {
        text = inner;
        repairs.push("fence".into());
    } else if !text.starts_with('{') {
        if let (Some(start), Some(end)) = (text.find('{'), text.rfind('}')) {
            if end > start {
                text = text[start..end + 1].to_string();
                repairs.push("slice".into());
            }
        }
    }

    if let Some((start, end)) = first_balanced_object(&text) {
        let span = &text[start..end];
        if span != text {
            text = span.to_string();
            repairs.push("balanced".into());
        }
    }

    let action = match pyjson::loads(&text) {
        Ok(value) => value,
        Err(_) => {
            let repaired = trailing_comma().replace_all(&text, "$1");
            match pyjson::loads(&repaired) {
                Ok(value) => {
                    repairs.push("trailing_comma".into());
                    value
                }
                Err(error) => {
                    let refuse =
                        || ActionError(format!("Agent did not return valid JSON: {error}"));
                    let escaped_text = escape_unescaped_newlines(&text);
                    if escaped_text != text {
                        if let Ok(value) = pyjson::loads(&escaped_text) {
                            repairs.push("unescaped_newline".into());
                            return Ok((value, repairs));
                        }
                        let escaped_comma = trailing_comma().replace_all(&escaped_text, "$1");
                        if escaped_comma != escaped_text {
                            if let Ok(value) = pyjson::loads(&escaped_comma) {
                                repairs.push("trailing_comma".into());
                                repairs.push("unescaped_newline".into());
                                return Ok((value, repairs));
                            }
                        }
                    }
                    let tail = match text.find('{') {
                        Some(brace) => &text[brace..],
                        None => text.as_str(),
                    };
                    let escaped_tail = escape_unescaped_newlines(tail);
                    if escaped_tail != tail {
                        if let Some(value) = truncation_repairs(&escaped_tail)
                            .into_iter()
                            .find_map(|candidate| pyjson::loads(&candidate).ok())
                        {
                            repairs.push("unescaped_newline".into());
                            repairs.push("truncated_close".into());
                            return Ok((value, repairs));
                        }
                    }
                    let closed = truncation_repairs(tail)
                        .into_iter()
                        .find_map(|candidate| pyjson::loads(&candidate).ok());
                    match closed {
                        Some(value) => {
                            repairs.push("truncated_close".into());
                            value
                        }
                        None => match crate::parse::pyliteral::literal_eval(&text) {
                            Some(Value::Object(map)) => {
                                repairs.push("python_literal".into());
                                Value::Object(map)
                            }
                            _ => return Err(refuse()),
                        },
                    }
                }
            }
        }
    };
    Ok((action, repairs))
}

pub fn extract_action_details(raw: &str) -> Result<Parsed, ActionError> {
    match extract_json_object(raw) {
        Ok((action, mut repairs)) => match action {
            Value::Object(map) => {
                let map = normalize_call_keys(map, &mut repairs);
                if map.contains_key("action") {
                    Ok((map, repairs))
                } else {
                    match salvage_labeled_action(raw) {
                        Some(parsed) => Ok(parsed),
                        None => Err(ActionError(
                            "Agent JSON must include an action field.".into(),
                        )),
                    }
                }
            }
            _ => match salvage_labeled_action(raw) {
                Some(parsed) => Ok(parsed),
                None => Err(ActionError(
                    "Agent JSON must include an action field.".into(),
                )),
            },
        },
        Err(error) => match salvage_labeled_action(raw) {
            Some(parsed) => Ok(parsed),
            None => Err(error),
        },
    }
}

pub fn extract_verdict_details(raw: &str) -> Result<Parsed, ActionError> {
    let (action, repairs) = extract_json_object(raw)?;
    match action {
        Value::Object(mut map) if map.contains_key("action") || map.contains_key("verdict") => {
            if !map.contains_key("action") {
                map.insert("action".into(), serde_json::json!("verdict"));
            }
            Ok((map, repairs))
        }
        _ => Err(ActionError(
            "Agent JSON must include an action field.".into(),
        )),
    }
}

#[cfg(test)]
#[path = "action_tests.rs"]
mod tests;
