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
    compiled(&RE, r"(?im)^\s*Action:\s*`?([A-Za-z_][A-Za-z0-9_.]*)`?")
}

fn labeled_args_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?im)^\s*Args:\s*`?(.+?)`?\s*$")
}

fn backtick_tool_for_path() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"`([A-Za-z_][A-Za-z0-9_.]*)`\s+for\s+`([^`]+)`")
}

fn first_step_tool() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?i)first step\s*[:(]\s*`([A-Za-z_][A-Za-z0-9_.]*)`")
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

/// A name this crate will dispatch, including `mcp.*` / `skill.*` rows.
///
/// Native tools stay on the frozen [`KNOWN_ACTIONS`] list. Qualified host
/// names are accepted by prefix so a 2B that writes `Action: mcp.grep` is
/// not declined as chatter — until v12.2.0 the labeled regex and the known
/// list both refused the dot, which is the whole of an MCP call's spelling.
fn accepted_action(name: &str) -> Option<String> {
    let name = name.trim();
    if name.is_empty() {
        return None;
    }
    if let Some(known) = known_action(name) {
        return Some(known.to_string());
    }
    let lower = name.to_ascii_lowercase();
    let qualified = lower.starts_with("mcp.") || lower.starts_with("skill.");
    if qualified
        && lower.len() > 4
        && lower.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '.' || character == '_'
        })
    {
        return Some(lower);
    }
    None
}

fn nonempty_string<'a>(map: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    map.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
}

fn object_from_args_value(value: &Value) -> Option<(Map<String, Value>, &'static str)> {
    match value {
        Value::Object(inner) => Some((inner.clone(), "alias_keys")),
        Value::String(text) => match pyjson::loads(text) {
            Ok(Value::Object(inner)) => Some((inner, "args_string")),
            _ => None,
        },
        _ => None,
    }
}

/// Map the function-calling envelope onto `action` / `args`.
///
/// Qwen, Llama and Gemma 2B instruct models emit `name`+`arguments` (or a
/// nested `function` object) more often than this crate's `action`+`args`.
/// The object already parsed; this only copies keys. An object that already
/// has `action` is untouched, so a genuine `{"action": "final"}` cannot
/// become something else.
fn normalize_call_keys(
    mut map: Map<String, Value>,
    repairs: &mut Vec<String>,
) -> Map<String, Value> {
    if nonempty_string(&map, "action").is_none() {
        let aliased = nonempty_string(&map, "name")
            .or_else(|| nonempty_string(&map, "tool"))
            .or_else(|| nonempty_string(&map, "tool_name"))
            .map(str::to_string)
            .or_else(|| {
                map.get("function")
                    .and_then(Value::as_object)
                    .and_then(|function| nonempty_string(function, "name").map(str::to_string))
            });
        if let Some(name) = aliased {
            map.insert("action".into(), Value::String(name));
            repairs.push("alias_keys".into());
        }
    }
    let args_ok = matches!(map.get("args"), Some(Value::Object(_)));
    if !args_ok {
        let candidate = map
            .get("arguments")
            .or_else(|| map.get("parameters"))
            .or_else(|| map.get("input"))
            .or_else(|| map.get("params"))
            .cloned()
            .or_else(|| {
                map.get("function")
                    .and_then(Value::as_object)
                    .and_then(|function| {
                        function
                            .get("arguments")
                            .or_else(|| function.get("parameters"))
                            .cloned()
                    })
            });
        if let Some(value) = candidate {
            if let Some((inner, repair)) = object_from_args_value(&value) {
                map.insert("args".into(), Value::Object(inner));
                if !repairs.iter().any(|known| known == repair) {
                    repairs.push(repair.into());
                }
            }
        }
    }
    map
}

fn qwen_function_open() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r#"(?is)<function\s*(?:=\s*["']?([A-Za-z_][A-Za-z0-9_.]*)["']?|name\s*=\s*["']([A-Za-z_][A-Za-z0-9_.]*)["'])"#,
    )
}

fn qwen_parameter() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r#"(?is)<parameter\s*=\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?>(.*?)</parameter>"#,
    )
}

fn xml_arg_tag() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(
        &RE,
        r"(?is)<(path|pattern|query|content|command|url|title|message)>(.*?)</\1>",
    )
}

fn collect_xml_args(pattern: &Regex, text: &str) -> Map<String, Value> {
    let mut args = Map::new();
    for captures in pattern.captures_iter(text).flatten() {
        let Some(key) = captures.get(1).map(|found| found.as_str()) else {
            continue;
        };
        let value = captures
            .get(2)
            .map(|found| found.as_str().trim().to_string())
            .unwrap_or_default();
        if !key.is_empty() && !value.is_empty() {
            args.insert(key.to_string(), Value::String(value));
        }
    }
    args
}

/// A tool call written as XML tags, with no JSON object.
fn salvage_xml_call(text: &str) -> Option<Map<String, Value>> {
    if let Some(captures) = qwen_function_open().captures(text).ok().flatten() {
        let name = captures
            .get(1)
            .or_else(|| captures.get(2))
            .map(|found| found.as_str())
            .unwrap_or("");
        if let Some(action) = accepted_action(name) {
            let args = collect_xml_args(qwen_parameter(), text);
            return usable_labeled(&action, args);
        }
    }
    let first = text.lines().next()?.trim();
    let action = accepted_action(first)?;
    let args = collect_xml_args(xml_arg_tag(), text);
    if args.is_empty() && !action.starts_with("skill.") {
        return None;
    }
    usable_labeled(&action, args)
}

fn first_file_like_path(text: &str) -> Option<String> {
    for captures in backtick_path().captures_iter(text).flatten() {
        let Some(body) = captures.get(1).map(|found| found.as_str().trim()) else {
            continue;
        };
        if body.contains('.') && !body.contains(' ') && body.len() < 200 {
            if known_action(body).is_some() || accepted_action(body).is_some() {
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
        if let Some(action) = accepted_action(name) {
            let args = labeled_args_line()
                .captures(&text)
                .ok()
                .flatten()
                .map(|found| parse_kv_pairs(found.get(1).map(|group| group.as_str()).unwrap_or("")))
                .unwrap_or_default();
            if let Some(map) = usable_labeled(&action, args) {
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
        if accepted_action(name).is_some() && !path.is_empty() {
            last_for = Some((name.to_string(), path.to_string()));
        }
    }
    if let Some((name, path)) = last_for {
        let action = accepted_action(&name)?;
        let mut args = Map::new();
        args.insert("path".into(), Value::String(path));
        if let Some(map) = usable_labeled(&action, args) {
            repairs.push("labeled".into());
            return Some((map, repairs));
        }
    }

    if let Some(captures) = first_step_tool().captures(&text).ok().flatten() {
        let name = captures.get(1).map(|found| found.as_str()).unwrap_or("");
        if let Some(action) = accepted_action(name) {
            let mut args = Map::new();
            if let Some(path) = first_file_like_path(&text) {
                args.insert("path".into(), Value::String(path));
            }
            if let Some(map) = usable_labeled(&action, args) {
                repairs.push("labeled".into());
                return Some((map, repairs));
            }
        }
    }

    if let Some(map) = salvage_xml_call(&text) {
        repairs.push("xml_call".into());
        return Some((map, repairs));
    }

    None
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
