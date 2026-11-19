//! Envelope key aliasing, labeled-prose action salvage, and XML tool-call
//! salvage for [`super`] (`extract_action_details`).

use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{Map, Value};

use super::{compiled, tag_wrapper, think_block, trailing_comma, truncation_repairs, Parsed};
use crate::parse::pyjson;

fn fenced_content_block() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compiled(&RE, r"(?s)```[A-Za-z0-9_-]*\r?\n(.*?)```")
}

/// Tools this crate will actually dispatch. A backtick around an unknown
/// name is chatter, not a call.
const KNOWN_ACTIONS: &[&str] = &[
    "create_web_project",
    "edit_file",
    "final",
    "grep",
    "inspect_html",
    "knowledge_save",
    "knowledge_search",
    "list_dir",
    "local_write",
    "preview_url",
    "read_file",
    "run_command",
    "search_files",
    "todo_write",
    "workspace_tree",
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
        Value::String(text) => {
            if let Ok(Value::Object(inner)) = pyjson::loads(text) {
                return Some((inner, "args_string"));
            }
            let comma_repaired = trailing_comma().replace_all(text, "$1");
            if let Ok(Value::Object(inner)) = pyjson::loads(&comma_repaired) {
                return Some((inner, "args_string"));
            }
            for candidate in truncation_repairs(text) {
                if let Ok(Value::Object(inner)) = pyjson::loads(&candidate) {
                    return Some((inner, "args_string"));
                }
            }
            if let Some(Value::Object(inner)) = crate::parse::pyliteral::literal_eval(text) {
                return Some((inner, "args_string"));
            }
            None
        }
        _ => None,
    }
}

/// Map the function-calling envelope onto `action` / `args`.
///
/// Qwen, Llama and Gemma 2B instruct models emit `name`+`arguments` (or a
/// nested `function` object or `tool_calls` array) more often than this
/// crate's `action`+`args`. The object already parsed; this only copies keys.
/// An object that already has `action` is untouched, so a genuine
/// `{"action": "final"}` cannot become something else.
pub(super) fn normalize_call_keys(
    mut map: Map<String, Value>,
    repairs: &mut Vec<String>,
) -> Map<String, Value> {
    let first_tool_call = map
        .get("tool_calls")
        .and_then(Value::as_array)
        .and_then(|arr| arr.first())
        .and_then(Value::as_object)
        .cloned();
    if nonempty_string(&map, "action").is_none() {
        let aliased = nonempty_string(&map, "name")
            .or_else(|| nonempty_string(&map, "tool"))
            .or_else(|| nonempty_string(&map, "tool_name"))
            .map(str::to_string)
            .or_else(|| {
                map.get("function")
                    .and_then(Value::as_object)
                    .and_then(|function| nonempty_string(function, "name").map(str::to_string))
            })
            .or_else(|| {
                first_tool_call.as_ref().and_then(|tc| {
                    nonempty_string(tc, "name")
                        .or_else(|| nonempty_string(tc, "tool"))
                        .map(str::to_string)
                        .or_else(|| {
                            tc.get("function")
                                .and_then(Value::as_object)
                                .and_then(|f| nonempty_string(f, "name").map(str::to_string))
                        })
                })
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
            })
            .or_else(|| {
                first_tool_call.as_ref().and_then(|tc| {
                    tc.get("arguments")
                        .or_else(|| tc.get("parameters"))
                        .cloned()
                        .or_else(|| {
                            tc.get("function").and_then(Value::as_object).and_then(|f| {
                                f.get("arguments").or_else(|| f.get("parameters")).cloned()
                            })
                        })
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
        "read_file" | "write_file" | "edit_file" | "list_dir" | "local_write" | "inspect_html"
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
/// Execute-only — [`super::extract_verdict_details`] must not invent a tool call
/// from a critic that happened to mention one.
pub(super) fn salvage_labeled_action(raw: &str) -> Option<Parsed> {
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
            let mut args = labeled_args_line()
                .captures(&text)
                .ok()
                .flatten()
                .map(|found| parse_kv_pairs(found.get(1).map(|group| group.as_str()).unwrap_or("")))
                .unwrap_or_default();
            if matches!(action.as_str(), "write_file" | "local_write")
                && !args.contains_key("content")
            {
                if let Some(fenced) = fenced_content_block()
                    .captures(&text)
                    .ok()
                    .flatten()
                    .and_then(|c| c.get(1).map(|g| g.as_str().trim().to_string()))
                    .filter(|body| !body.is_empty())
                {
                    args.insert("content".into(), Value::String(fenced));
                }
            }
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
