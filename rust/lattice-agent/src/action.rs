//! `extract_action_details` — one JSON action object out of whatever a model said.
//!
//! A 1:1 port of `latticeai.core.agent_helpers.extract_action_details`, which is
//! the single place the loop turns model prose into a decision. Six rungs, in
//! order, each one named in the returned `repairs` list so the trace can say how
//! much help a given model needed:
//!
//! 1. strip `<think>`/`<thinking>`/`<reasoning>` blocks (they contain braces);
//! 2. take the first fenced ```json block;
//! 3. otherwise slice from the first `{` to the last `}`;
//! 4. `json.loads`;
//! 5. on failure, drop trailing commas and retry;
//! 6. on failure, `ast.literal_eval` the **unrepaired** text.
//!
//! Rung 6 reads `text`, not `repaired` — a detail worth stating because it is
//! easy to "clean up" into a behaviour change: a Python dict literal with a
//! trailing comma reaches `literal_eval` in its original form, and the error
//! finally raised is the one from rung 5, never rung 6's.

use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{Map, Value};

use crate::pyjson;

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

/// Parse one JSON action object out of an LLM response.
pub fn extract_action_details(raw: &str) -> Result<Parsed, ActionError> {
    let mut repairs: Vec<String> = Vec::new();
    let stripped = think_block().replace_all(raw, "");
    let mut text: String = stripped.trim().to_string();
    if text != raw.trim() {
        repairs.push("think_strip".into());
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
                    // The message the loop records is this one — rung 6's own
                    // failure is discarded, exactly as Python discards it.
                    let refuse =
                        || ActionError(format!("Agent did not return valid JSON: {error}"));
                    match crate::pyliteral::literal_eval(&text) {
                        Some(Value::Object(map)) => {
                            repairs.push("python_literal".into());
                            Value::Object(map)
                        }
                        _ => return Err(refuse()),
                    }
                }
            }
        }
    };

    match action {
        Value::Object(map) if map.contains_key("action") => Ok((map, repairs)),
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

    #[test]
    fn korean_prose_does_not_split_a_character() {
        let parsed = parse("작업 계획입니다: {\"action\": \"final\", \"message\": \"완료\"} 끝.");
        assert_eq!(parsed.1, vec!["slice".to_string()]);
        assert_eq!(parsed.0["message"], json!("완료"));
    }
}
