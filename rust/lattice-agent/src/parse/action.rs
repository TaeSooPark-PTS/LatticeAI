//! `extract_action_details` — one JSON action object out of whatever a model said.
//!
//! Started as a 1:1 port of `latticeai.core.agent_helpers.extract_action_details`,
//! which is the single place the loop turns model prose into a decision. Ten
//! rungs now, in order, each one named in the returned `repairs` list so the
//! trace can say how much help a given model needed:
//!
//! 1. strip `<think>`/`<thinking>`/`<reasoning>` blocks (they contain braces);
//! 2. **`tag_strip`** — drop `<tool_call>` / `[TOOL_CALL]` wrappers and
//!    `<|channel|>…<|message|>` / `<|start|>…<|end|>` frames (v11.9.0);
//! 3. take the first fenced ```json block;
//! 4. otherwise slice from the first `{` to the last `}`;
//! 5. **`balanced`** — take the first *complete* object (v11.9.0);
//! 6. `json.loads`;
//! 7. on failure, drop trailing commas and retry;
//! 8. **`truncated_close`** — close what a token limit cut off (v11.9.0);
//! 9. on failure, `ast.literal_eval` the **unrepaired** text;
//! 10. **`labeled`** — a thought that never opened a brace but named the
//!     call in prose (`Action: \`read_file\`\` / `\`read_file\` for \`README.md\``).
//! 11. **`alias_keys`** — a parsed object that named the call as `name` /
//!     `tool` / `function` and the arguments as `arguments` / `parameters`
//!     (the function-calling envelope 2B instruct models emit).
//! 12. **`args_string`** — `arguments` arrived as a JSON string, not an object.
//! 13. **`xml_call`** — Qwen/Hermes `<function=name><parameter=…>` or a
//!     tool name plus `<path>…</path>` tags, with no JSON object at all.
//!
//! Rung 9 reads `text`, not `repaired` — a detail worth stating because it is
//! easy to "clean up" into a behaviour change: a Python dict literal with a
//! trailing comma reaches `literal_eval` in its original form, and the error
//! finally raised is the one from rung 7, never rung 9's. Rung 10 is execute
//! only: a critic thought that mentions a tool must not become a verdict.
//!
//! ## The three v11.9.0 rungs, and what they cost
//!
//! Rungs 2, 5 and 8 are **extensions past parity**, added because the 2B local
//! models the compact profile exists for fail in exactly three ways the ported
//! chain could not read: they wrap the object in a tool-call tag, they emit two
//! objects in one reply, and they run out of tokens mid-string. Each fires only
//! where the ported chain would have refused *or* where it changes nothing —
//! a rung that would alter an already-parseable reply is not taken. Three rows
//! of the frozen Python grid do flip from refusal to recovery as a result;
//! `rust/fixtures/agent_loop/golden/helpers.json` records them under
//! `extract_action_details_extended` rather than by rewriting the record of
//! what Python answered.

use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{Map, Value};

use crate::parse::pyjson;

#[path = "action_salvage.rs"]
mod salvage;
use salvage::{normalize_call_keys, salvage_labeled_action};

/// The `ValueError` the loop catches. Its text lands in the transcript.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionError(pub String);

impl std::fmt::Display for ActionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ActionError {}

/// One parsed action object plus the tolerances that were needed.
pub type Parsed = (Map<String, Value>, Vec<String>);

fn compiled(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("ported pattern must compile"))
}

fn think_block() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // `re.DOTALL | re.IGNORECASE`, with the backreference Python's `\1` is.
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

/// The tool-call wrappers small instruct models put around their JSON.
///
/// Only the **delimiters** are removed, never their contents — that is the
/// difference from [`think_block`], which drops a whole reasoning block. Each
/// half is matched on its own, because the reply that ran out of tokens has an
/// opening tag and no closing one.
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

/// The span of the first **complete** top-level `{…}` object, string-aware.
///
/// The ported rung sliced first-`{`-to-last-`}`, which is the right answer for
/// prose around one object and the wrong one for two objects in one reply: it
/// splices the head of the first onto the tail of the second and produces text
/// no decoder can read. Braces inside string literals are skipped, so a
/// `content` field holding `{}` (CSS, JSON, a code sample) does not close the
/// object early.
///
/// Returns byte offsets into `text`, or `None` when no object closes.
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

/// Candidate repairs for a reply the token limit cut off, best first.
///
/// A truncated tool call is not malformed JSON — it is *unfinished* JSON, and
/// the difference matters: everything before the cut is exactly what the model
/// meant. So the string it was in the middle of is closed, then the containers
/// it had open, and the partial `content` survives into the write where a
/// refusal would have thrown the whole step away.
///
/// Empty when nothing was open, which is how the rung declines to fire on text
/// that is merely wrong rather than short.
fn truncation_repairs(text: &str) -> Vec<String> {
    let mut stack: Vec<char> = Vec::new();
    let mut in_string = false;
    let mut escaped = false;
    // The last comma outside a string: where a dangling `"key":` fragment can
    // be cut back to a pair boundary that was complete.
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
    // `{"a": 1, "b"` — the cut landed on a key with no value, so the closers
    // alone cannot help. Drop back to the last complete pair.
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

/// The shared repair rungs, before the caller decides which keys are required.
///
/// Returns the decoded JSON value (object or otherwise) plus the named
/// repairs. EXECUTE then demands `action`; VERIFY accepts `verdict` alone.
pub fn extract_json_object(raw: &str) -> Result<(Value, Vec<String>), ActionError> {
    let mut repairs: Vec<String> = Vec::new();
    let stripped = think_block().replace_all(raw, "");
    let mut text: String = stripped.trim().to_string();
    if text != raw.trim() {
        repairs.push("think_strip".into());
    }

    // Only a reply that is *wrapped* is unwrapped. Text already starting with
    // `{` is object-shaped, so any tag inside it is inside a string literal —
    // a file that documents tool-call syntax, most plausibly — and stripping
    // there would corrupt the content the model meant to write.
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
                    // The message the loop records is this one — rung 9's own
                    // failure is discarded, exactly as Python discards it.
                    let refuse =
                        || ActionError(format!("Agent did not return valid JSON: {error}"));
                    // A cut reply may still be wearing the fence it never
                    // closed, or the prose it opened with — neither rung above
                    // could strip those, because both look for a terminator
                    // the model never reached. The object starts at the first
                    // brace, and that is the whole of what is repairable.
                    let tail = match text.find('{') {
                        Some(brace) => &text[brace..],
                        None => text.as_str(),
                    };
                    let closed = truncation_repairs(tail)
                        .into_iter()
                        .find_map(|candidate| pyjson::loads(&candidate).ok());
                    match closed {
                        Some(value) => {
                            repairs.push("truncated_close".into());
                            value
                        }
                        // `literal_eval` reads `text`: not the comma repair,
                        // and not the truncation repair either.
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

/// Parse one JSON action object out of an LLM response.
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

/// The VERIFY-phase twin of [`extract_action_details`].
///
/// Same repair rungs, but a critic that names `verdict` and forgets
/// `"action": "verdict"` is still a verdict — execute must not accept that
/// shape, because an action-less object there is a missed tool call.
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
