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

/// Tools this crate will actually dispatch. A backtick around an unknown
/// name is chatter, not a call.
const KNOWN_ACTIONS: &[&str] = &[
    "create_web_project",
    "edit_file",
    "final",
    "list_dir",
    "local_write",
    "read_file",
    "run_command",
    "todo_write",
    "write_file",
];

fn known_action(name: &str) -> Option<&'static str> {
    let lower = name.trim().to_ascii_lowercase();
    KNOWN_ACTIONS
        .binary_search(&lower.as_str())
        .ok()
        .map(|index| KNOWN_ACTIONS[index])
}

fn labeled_action_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Action:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
}

fn labeled_args_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Args:\s*`?(.+?)`?\s*$")
}

fn backtick_tool_for_path() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"`([A-Za-z_][A-Za-z0-9_]*)`\s+for\s+`([^`]+)`")
}

fn first_step_tool() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?i)first step\s*[:(]\s*`([A-Za-z_][A-Za-z0-9_]*)`")
}

fn backtick_path() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"`([^`]+)`")
}

fn kv_pairs() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r#"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:"([^"]*)"|`([^`]+)`|([^\s,`]+))"#,
    )
}

fn parse_kv_pairs(text: &str) -> Map<String, Value> {
    let mut args = Map::new();
    for captures in kv_pairs().captures_iter(text).flatten() {
        let Some(key) = captures.get(1).map(|found| found.as_str()) else {
            continue;
        };
        let value = captures
            .get(2)
            .or_else(|| captures.get(3))
            .or_else(|| captures.get(4))
            .map(|found| found.as_str().trim_matches('"').trim().to_string())
            .unwrap_or_default();
        if !key.is_empty() {
            args.insert(key.to_string(), Value::String(value));
        }
    }
    args
}

fn first_file_like_path(text: &str) -> Option<String> {
    for captures in backtick_path().captures_iter(text).flatten() {
        let Some(body) = captures.get(1).map(|found| found.as_str().trim()) else {
            continue;
        };
        if body.contains('.') && !body.contains(' ') && body.len() < 200 {
            if known_action(body).is_some() {
                continue;
            }
            return Some(body.to_string());
        }
    }
    None
}

fn action_object(action: &str, args: Map<String, Value>) -> Map<String, Value> {
    let mut map = Map::new();
    map.insert("action".into(), Value::String(action.to_string()));
    if !args.is_empty() {
        map.insert("args".into(), Value::Object(args));
    }
    map
}

/// A path-taking tool with no path is not a call — it is a KeyError at
/// dispatch. Decline so the next salvage pattern (or a parse_error) can fire.
fn usable_labeled(action: &str, args: Map<String, Value>) -> Option<Map<String, Value>> {
    if matches!(
        action,
        "read_file" | "write_file" | "edit_file" | "list_dir" | "local_write"
    ) {
        let path = args
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if path.is_empty() {
            return None;
        }
    }
    Some(action_object(action, args))
}

/// Gemma-4-e2b often spends the whole reply inside `<|channel>thought` and
/// never opens a brace. The live verify5 tapes named the call anyway:
///
/// * `Action: \`read_file\`\nArgs: \`path: "README.md"\``
/// * `the first step: \`read_file\` for \`README.md\``
/// * `the first step (\`read_file\`)` plus an earlier `` `README.md` ``
///
/// Execute-only — [`extract_verdict_details`] must not invent a tool call
/// from a critic that happened to mention one.
fn salvage_labeled_action(raw: &str) -> Option<Parsed> {
    let mut repairs: Vec<String> = Vec::new();
    let stripped = think_block().replace_all(raw, "");
    if stripped.trim() != raw.trim() {
        repairs.push("think_strip".into());
    }
    let text = match crate::parse::channel::strip_channel_frames(stripped.trim()) {
        Some(payload) => {
            repairs.push("tag_strip".into());
            payload
        }
        None => stripped.trim().to_string(),
    };
    let untagged = tag_wrapper().replace_all(&text, "").trim().to_string();
    if untagged != text {
        repairs.push("tag_strip".into());
    }
    let text = untagged;
    if text.is_empty() {
        return None;
    }

    if let Some(captures) = labeled_action_line().captures(&text).ok().flatten() {
        let name = captures.get(1).map(|found| found.as_str()).unwrap_or("");
        if let Some(action) = known_action(name) {
            let args = labeled_args_line()
                .captures(&text)
                .ok()
                .flatten()
                .map(|found| parse_kv_pairs(found.get(1).map(|group| group.as_str()).unwrap_or("")))
                .unwrap_or_default();
            if let Some(map) = usable_labeled(action, args) {
                repairs.push("labeled".into());
                return Some((map, repairs));
            }
        }
    }

    let mut last_for: Option<(String, String)> = None;
    for captures in backtick_tool_for_path().captures_iter(&text).flatten() {
        let Some(name) = captures.get(1).map(|found| found.as_str()) else {
            continue;
        };
        let Some(path) = captures.get(2).map(|found| found.as_str().trim()) else {
            continue;
        };
        if known_action(name).is_some() && !path.is_empty() {
            last_for = Some((name.to_string(), path.to_string()));
        }
    }
    if let Some((name, path)) = last_for {
        let action = known_action(&name)?;
        let mut args = Map::new();
        args.insert("path".into(), Value::String(path));
        if let Some(map) = usable_labeled(action, args) {
            repairs.push("labeled".into());
            return Some((map, repairs));
        }
    }

    if let Some(captures) = first_step_tool().captures(&text).ok().flatten() {
        let name = captures.get(1).map(|found| found.as_str()).unwrap_or("");
        if let Some(action) = known_action(name) {
            let mut args = Map::new();
            if let Some(path) = first_file_like_path(&text) {
                args.insert("path".into(), Value::String(path));
            }
            if let Some(map) = usable_labeled(action, args) {
                repairs.push("labeled".into());
                return Some((map, repairs));
            }
        }
    }

    None
}

/// Parse one JSON action object out of an LLM response.
pub fn extract_action_details(raw: &str) -> Result<Parsed, ActionError> {
    match extract_json_object(raw) {
        Ok((action, repairs)) => match action {
            Value::Object(map) if map.contains_key("action") => Ok((map, repairs)),
            _ => Err(ActionError(
                "Agent JSON must include an action field.".into(),
            )),
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
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(raw: &str) -> Parsed {
        extract_action_details(raw).expect("must parse")
    }

    fn action_of(parsed: &Parsed) -> Value {
        Value::Object(parsed.0.clone())
    }

    #[test]
    fn clean_json_needs_no_repair_at_all() {
        let parsed = parse(r#"{"action": "final", "message": "done"}"#);
        assert_eq!(parsed.1, Vec::<String>::new());
        assert_eq!(
            action_of(&parsed),
            json!({"action": "final", "message": "done"})
        );
    }

    #[test]
    fn a_fence_is_unwrapped_and_named() {
        let parsed = parse("Sure!\n```json\n{\"action\": \"read_file\"}\n```\nHope that helps.");
        assert_eq!(parsed.1, vec!["fence".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "read_file"}));
        // A bare fence with no language tag is the same rung.
        assert_eq!(parse("```\n{\"action\": \"x\"}\n```").1, vec!["fence"]);
    }

    #[test]
    fn thinking_blocks_are_stripped_before_the_braces_are_looked_for() {
        // The reasoning contains braces of its own; a port that sliced first
        // would take them and fail.
        let raw = "<think>maybe {\"action\": \"wrong\"}?</think>\n{\"action\": \"right\"}";
        let parsed = parse(raw);
        assert_eq!(parsed.1, vec!["think_strip".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "right"}));
        // The backreference matters: `<think>…</reasoning>` is not a block, so
        // nothing is stripped and the object is reached by slicing instead.
        // Cross-checked against the Python original, which answers `['slice']`.
        assert_eq!(
            parse("<think>{\"action\": \"a\"}</reasoning>").1,
            vec!["slice".to_string()]
        );
    }

    #[test]
    fn think_and_fence_can_both_fire() {
        let parsed = parse("<thinking>hmm</thinking>\n```json\n{\"action\": \"final\"}\n```");
        assert_eq!(
            parsed.1,
            vec!["think_strip".to_string(), "fence".to_string()]
        );
    }

    #[test]
    fn prose_around_an_object_is_sliced_away() {
        let parsed = parse("I will now call: {\"action\": \"write_file\"} — done.");
        assert_eq!(parsed.1, vec!["slice".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "write_file"}));
    }

    #[test]
    fn a_trailing_comma_is_repaired_on_the_second_pass() {
        let parsed = parse("{\"action\": \"final\", \"args\": {\"a\": 1,},}");
        assert_eq!(parsed.1, vec!["trailing_comma".to_string()]);
        assert_eq!(
            action_of(&parsed),
            json!({"action": "final", "args": {"a": 1}})
        );
    }

    #[test]
    fn a_python_dict_literal_is_the_last_rung() {
        let parsed = parse("{'action': 'write_file', 'args': {'path': 'a.md'}, 'ok': True}");
        assert_eq!(parsed.1, vec!["python_literal".to_string()]);
        assert_eq!(
            action_of(&parsed),
            json!({"action": "write_file", "args": {"path": "a.md"}, "ok": true})
        );
    }

    #[test]
    fn the_literal_rung_reads_the_unrepaired_text() {
        // Trailing comma *and* single quotes: the comma repair does not make it
        // JSON, and `literal_eval` sees the original — which it accepts.
        let parsed = parse("{'action': 'final', 'message': 'hi',}");
        assert_eq!(parsed.1, vec!["python_literal".to_string()]);
    }

    #[test]
    fn unparseable_output_reports_the_repaired_passs_decoder_message() {
        let error = extract_action_details("the model just talked").expect_err("no json");
        assert_eq!(
            error.0,
            "Agent did not return valid JSON: Expecting value: line 1 column 1 (char 0)"
        );
    }

    #[test]
    fn a_non_dict_or_action_less_object_is_the_other_refusal() {
        for raw in [
            "[1, 2, 3]",
            "42",
            r#"{"thoughts": "no action key"}"#,
            "\"text\"",
        ] {
            let error = extract_action_details(raw).expect_err(raw);
            assert_eq!(error.0, "Agent JSON must include an action field.", "{raw}");
        }
        // A literal that parses but is not a dict falls to the JSON message.
        let error = extract_action_details("('a', 'b')").expect_err("tuple");
        assert!(error.0.starts_with("Agent did not return valid JSON: "));
    }

    #[test]
    fn an_action_key_holding_anything_at_all_is_still_an_action() {
        // The presence of the key is the whole predicate; `str(… or "")` is what
        // turns a null into "" later, in the executor.
        assert!(extract_action_details(r#"{"action": null}"#).is_ok());
        assert!(extract_action_details(r#"{"action": 5}"#).is_ok());
    }

    // ── the v11.9.0 rungs ───────────────────────────────────────────────────
    #[test]
    fn a_tool_call_wrapper_is_unwrapped_without_losing_its_contents() {
        for raw in [
            "<tool_call>{\"action\": \"final\"}</tool_call>",
            "[TOOL_CALL]{\"action\": \"final\"}[/TOOL_CALL]",
            "<|tool_call|>{\"action\": \"final\"}",
            // The reply that ran out of tokens has an opening tag and no close.
            "<tool_call>\n{\"action\": \"final\"}",
        ] {
            let parsed = parse(raw);
            assert_eq!(parsed.1, vec!["tag_strip".to_string()], "{raw}");
            assert_eq!(action_of(&parsed), json!({"action": "final"}), "{raw}");
        }
    }

    #[test]
    fn a_channel_frame_unwraps_to_the_final_payload() {
        for raw in [
            "<|channel|>thought\nI should finish.\n<|message|>{\"action\": \"final\"}<|end|>",
            "<|channel>thought\nnot this\n<|channel>commentary\n{\"action\": \"final\"}",
            "<|start|>assistant<|channel|>final<|message|>{\"action\": \"final\"}<|end|>",
        ] {
            let parsed = parse(raw);
            assert_eq!(parsed.1, vec!["tag_strip".to_string()], "{raw}");
            assert_eq!(action_of(&parsed), json!({"action": "final"}), "{raw}");
        }
    }

    #[test]
    fn a_tag_inside_a_string_is_content_and_is_left_alone() {
        // A file that documents tool-call syntax is a file, not a wrapper.
        let raw = r#"{"action": "write_file", "args": {"path": "a.md", "content": "Use <tool_call> like this."}}"#;
        let parsed = parse(raw);
        assert_eq!(parsed.1, Vec::<String>::new());
        assert_eq!(
            parsed.0["args"]["content"],
            json!("Use <tool_call> like this.")
        );
        // A stray closing tag *after* the object is still recovered — by the
        // balanced scan, which never reaches inside a string.
        let parsed = parse(r#"{"action": "final"}</tool_call>"#);
        assert_eq!(parsed.1, vec!["balanced".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "final"}));
    }

    #[test]
    fn two_objects_in_one_reply_take_the_first_complete_one() {
        // The ported chain spliced `{"action": "a"` onto `"b"}` and refused.
        let parsed = parse(r#"{"action": "a"} {"action": "b"}"#);
        assert_eq!(parsed.1, vec!["balanced".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "a"}));

        // Prose on both sides: slice first, then the balanced scan trims the
        // second object off what slicing kept.
        let parsed = parse(r#"Here: {"action": "a"} and also {"action": "b"} ok"#);
        assert_eq!(parsed.1, vec!["slice".to_string(), "balanced".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "a"}));
    }

    #[test]
    fn a_trailing_sentence_after_the_object_is_no_longer_fatal() {
        let parsed = parse(r#"{"action": "write_file"} — that is the call."#);
        assert_eq!(parsed.1, vec!["balanced".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "write_file"}));
    }

    #[test]
    fn a_brace_inside_a_string_does_not_close_the_object_early() {
        let raw = r#"{"action": "write_file", "args": {"path": "a.css", "content": "body { color: red; }"}} trailing"#;
        let parsed = parse(raw);
        assert_eq!(parsed.1, vec!["balanced".to_string()]);
        assert_eq!(parsed.0["args"]["content"], json!("body { color: red; }"));
        // And an escaped quote inside that string is not a terminator.
        let raw = r#"{"action": "a", "args": {"content": "say \"hi\" }"}} tail"#;
        assert_eq!(
            parse(raw).0["args"]["content"],
            json!("say \"hi\" }"),
            "an escaped quote must not end the string"
        );
    }

    #[test]
    fn a_reply_cut_off_by_the_token_limit_is_closed_rather_than_refused() {
        // Cut mid-object.
        let parsed = parse(r#"{"action": "final""#);
        assert_eq!(parsed.1, vec!["truncated_close".to_string()]);
        assert_eq!(action_of(&parsed), json!({"action": "final"}));

        // Cut mid-string, deep inside a write: the partial content survives.
        let parsed =
            parse(r#"{"action": "write_file", "args": {"path": "i.html", "content": "<!doctype"#);
        assert_eq!(parsed.1, vec!["truncated_close".to_string()]);
        assert_eq!(parsed.0["args"]["content"], json!("<!doctype"));
        assert_eq!(parsed.0["args"]["path"], json!("i.html"));

        // Cut on a key with no value: fall back to the last complete pair.
        let parsed = parse(r#"{"action": "write_file", "args": {"path": "a.md"}, "thoughts""#);
        assert_eq!(parsed.1, vec!["truncated_close".to_string()]);
        assert_eq!(
            action_of(&parsed),
            json!({"action": "write_file", "args": {"path": "a.md"}})
        );
    }

    #[test]
    fn the_truncation_rung_declines_on_text_that_is_wrong_rather_than_short() {
        // Nothing is open in either of these, so closing them is not the
        // repair — and inventing one would turn a refusal into a wrong action.
        for raw in [r#"{"action": }"#, "{action: 1}"] {
            let error = extract_action_details(raw).expect_err(raw);
            assert!(
                error.0.starts_with("Agent did not return valid JSON: "),
                "{raw}: {}",
                error.0
            );
        }
        assert!(truncation_repairs(r#"{"a": 1}"#).is_empty());
    }

    #[test]
    fn the_whole_chain_can_fire_at_once() {
        // What a 2B model at its token ceiling actually sends: reasoning, a
        // tool-call tag, a fence, and a cut mid-string.
        let raw = "<think>which tool?</think>\n<tool_call>\n```json\n\
{\"action\": \"write_file\", \"args\": {\"path\": \"a.md\", \"content\": \"# hi";
        let parsed = extract_action_details(raw).expect("must parse");
        assert_eq!(
            parsed.1,
            vec![
                "think_strip".to_string(),
                "tag_strip".to_string(),
                "truncated_close".to_string()
            ],
            "the fence never closed, so that rung does not fire"
        );
        assert_eq!(parsed.0["args"]["content"], json!("# hi"));
    }

    #[test]
    fn korean_prose_does_not_split_a_character() {
        let parsed = parse("작업 계획입니다: {\"action\": \"final\", \"message\": \"완료\"} 끝.");
        assert_eq!(parsed.1, vec!["slice".to_string()]);
        assert_eq!(parsed.0["message"], json!("완료"));
    }

    #[test]
    fn a_verdict_object_without_action_is_accepted_only_by_the_critic_parser() {
        let raw = r#"{"verdict": "PASS", "next_state": "DONE", "reason": "written"}"#;
        assert_eq!(
            extract_action_details(raw)
                .expect_err("execute still requires action")
                .0,
            "Agent JSON must include an action field."
        );
        let (map, repairs) = extract_verdict_details(raw).expect("verify accepts verdict");
        assert!(repairs.is_empty());
        assert_eq!(map["action"], json!("verdict"));
        assert_eq!(map["verdict"], json!("PASS"));
        assert_eq!(map["next_state"], json!("DONE"));
    }

    #[test]
    fn a_channel_framed_verdict_without_action_unwraps() {
        let raw = "<|channel>thought\nlooks done\n<|channel>commentary\n\
{\"verdict\": \"FAIL\", \"next_state\": \"FAILED\", \"reason\": \"missing\"}";
        let (map, repairs) = extract_verdict_details(raw).expect("framed verdict");
        assert_eq!(repairs, vec!["tag_strip".to_string()]);
        assert_eq!(map["action"], json!("verdict"));
        assert_eq!(map["verdict"], json!("FAIL"));
    }

    // ── verify5 live tapes (gemma-4-e2b, 400-char raw slices) ───────────────
    const VERIFY5_STEP1: &str = "<|channel>thought\nThe user wants me to execute the next step of the plan.\nThe plan is:\n1. Read the content of `README.md`.\n2. Save the summary of the first paragraph to `notes/summary.md`.\n\nThe previous step was planning, and the execution transcript shows that the plan was auto-approved.\nThe next step in the execution transcript is the first step: `read_file` for `README.md`.\n\nI need to perform t";

    const VERIFY5_STEP2: &str = "<|channel>thought\nThe user is asking me to execute the next step of the previously defined plan.\nThe plan was:\n1. Read the content of `README.md`.\n2. Save the summarized content to `notes/summary.md`.\n\nThe execution transcript showed an attempt to execute the first step (`read_file`) which resulted in a parsing error. I need to re-execute the first step, which is to read the file, assuming the env";

    const VERIFY5_STEP3: &str = "<|channel>thought\nThe user is asking me to execute the next step of the plan.\nThe plan is:\n1. Read the content of `README.md`.\n2. Save the summarized content to `notes/summary.md`.\n\nThe execution transcript showed an error on the first step. I need to re-execute the first step: `read_file` for `README.md`.\n\nAction: `read_file`\nArgs: `path: \"README.md\"`\nDescription: Read the content of README.md to";

    #[test]
    fn verify5_labeled_action_args_recovers_the_tool_call() {
        // The third execute slip on every verify5 tape: thought channel, then
        // a labeled Action:/Args: pair, never a brace.
        let parsed = parse(VERIFY5_STEP3);
        assert_eq!(
            parsed.1,
            vec!["tag_strip".to_string(), "labeled".to_string()]
        );
        assert_eq!(parsed.0["action"], json!("read_file"));
        assert_eq!(parsed.0["args"]["path"], json!("README.md"));
    }

    #[test]
    fn verify5_backtick_tool_for_path_recovers_the_tool_call() {
        // The first execute slip: the model names `read_file` for `README.md`
        // inside the thought and then gets cut off.
        let parsed = parse(VERIFY5_STEP1);
        assert_eq!(
            parsed.1,
            vec!["tag_strip".to_string(), "labeled".to_string()]
        );
        assert_eq!(parsed.0["action"], json!("read_file"));
        assert_eq!(parsed.0["args"]["path"], json!("README.md"));
    }

    #[test]
    fn verify5_first_step_parenthetical_recovers_the_tool_call() {
        // The second execute slip: `the first step (`read_file`)` and the
        // plan's first file-like backtick is README.md, not notes/summary.md.
        let parsed = parse(VERIFY5_STEP2);
        assert_eq!(
            parsed.1,
            vec!["tag_strip".to_string(), "labeled".to_string()]
        );
        assert_eq!(parsed.0["action"], json!("read_file"));
        assert_eq!(parsed.0["args"]["path"], json!("README.md"));
    }

    #[test]
    fn a_labeled_call_without_a_channel_frame_still_recovers() {
        let parsed = parse("Action: `write_file`\nArgs: `path: \"notes/summary.md\"`");
        assert_eq!(parsed.1, vec!["labeled".to_string()]);
        assert_eq!(parsed.0["action"], json!("write_file"));
        assert_eq!(parsed.0["args"]["path"], json!("notes/summary.md"));
    }

    #[test]
    fn an_unknown_backtick_name_is_not_a_tool_call() {
        let error =
            extract_action_details("I should maybe call `invent_tool` for `README.md` next.")
                .expect_err("unknown tool");
        assert!(error.0.starts_with("Agent did not return valid JSON: "));
    }

    #[test]
    fn a_labeled_path_tool_without_a_path_is_declined() {
        // Live verify6 dispatched these and the tool raised KeyError 'path'.
        let error =
            extract_action_details("Action: `read_file`\nDescription: go").expect_err("no path");
        assert!(error.0.starts_with("Agent did not return valid JSON: "));
        let error = extract_action_details("Action: `write_file`").expect_err("no path");
        assert!(error.0.starts_with("Agent did not return valid JSON: "));
    }

    #[test]
    fn the_critic_parser_does_not_invent_a_tool_from_a_thought() {
        // extract_verdict_details must not take the execute-only labeled rung.
        let error = extract_verdict_details(VERIFY5_STEP3).expect_err("not a verdict");
        assert!(error.0.starts_with("Agent did not return valid JSON: "));
    }
}
