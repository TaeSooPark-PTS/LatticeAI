//! Tests for [`super`] (`extract_action_details` and friends).
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
    let error = extract_action_details("I should maybe call `invent_tool` for `README.md` next.")
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

#[test]
fn openai_name_arguments_is_an_action() {
    let parsed = parse(r#"{"name": "read_file", "arguments": {"path": "README.md"}}"#);
    assert_eq!(parsed.1, vec!["alias_keys".to_string()]);
    assert_eq!(parsed.0["action"], json!("read_file"));
    assert_eq!(parsed.0["args"]["path"], json!("README.md"));
}

#[test]
fn nested_function_object_and_string_arguments_are_aliased() {
    let parsed =
        parse(r#"{"function": {"name": "mcp.grep", "arguments": "{\"pattern\": \"LatticeAI\"}"}}"#);
    assert!(parsed.1.iter().any(|repair| repair == "alias_keys"));
    assert!(parsed.1.iter().any(|repair| repair == "args_string"));
    assert_eq!(parsed.0["action"], json!("mcp.grep"));
    assert_eq!(parsed.0["args"]["pattern"], json!("LatticeAI"));
}

#[test]
fn a_thoughts_object_without_a_call_still_refuses() {
    let error =
        extract_action_details(r#"{"thoughts": "no action here"}"#).expect_err("still not a call");
    assert_eq!(error.0, "Agent JSON must include an action field.");
}

#[test]
fn labeled_mcp_name_is_a_call() {
    let parsed = parse("Action: `mcp.grep`\nArgs: `pattern: \"LatticeAI\"`");
    assert_eq!(parsed.1, vec!["labeled".to_string()]);
    assert_eq!(parsed.0["action"], json!("mcp.grep"));
    assert_eq!(parsed.0["args"]["pattern"], json!("LatticeAI"));
}

#[test]
fn qwen_function_xml_is_a_call() {
    let parsed = parse("<function=read_file>\n<parameter=path>README.md</parameter>\n</function>");
    assert_eq!(parsed.1, vec!["xml_call".to_string()]);
    assert_eq!(parsed.0["action"], json!("read_file"));
    assert_eq!(parsed.0["args"]["path"], json!("README.md"));
}

#[test]
fn tool_call_name_plus_path_tag_is_a_call() {
    let parsed = parse("<tool_call>\nread_file\n<path>notes/hello.md</path>\n</tool_call>");
    assert!(parsed.1.iter().any(|repair| repair == "xml_call"));
    assert_eq!(parsed.0["action"], json!("read_file"));
    assert_eq!(parsed.0["args"]["path"], json!("notes/hello.md"));
}
