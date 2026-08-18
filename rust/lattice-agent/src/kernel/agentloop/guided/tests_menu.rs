//! What the guided dial must do, and what it must never do.

use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::profile::GUIDED;
use crate::kernel::state::AgentState;
use crate::surface::worker::ToolOutcome;
use crate::tools::catalog::{ArgSpec, CatalogEntry, EntryKind, ToolCatalog};
use crate::tools::{CallScope, ToolFuture};

#[test]
fn a_menu_answer_is_read_from_anywhere_a_digit_appears() {
    for (reply, expected) in [
        ("1", Some(1)),
        ("2", Some(2)),
        (" 3 ", Some(3)),
        ("2.", Some(2)),
        ("2)", Some(2)),
        ("I choose 2", Some(2)),
        ("2 — write a file", Some(2)),
        ("**1**", Some(1)),
        ("번호: 3", Some(3)),
    ] {
        assert_eq!(parse_choice(reply, 3), expected, "{reply:?}");
    }
    // Out of range is not silently clamped: a 12 against a three-row menu is a
    // model that did not read the menu, and pretending it said 1 or 3 would be
    // the harness inventing a choice.
    assert_eq!(parse_choice("12", 3), None);
    assert_eq!(parse_choice("0", 3), None);
    assert_eq!(parse_choice("", 3), None);
    assert_eq!(parse_choice("write the file", 3), None);
    assert_eq!(parse_choice("1", 0), None, "an empty menu has no answer");
}

#[test]
fn a_one_line_answer_is_read_without_its_decoration() {
    for (reply, expected) in [
        ("notes/hello.md", "notes/hello.md"),
        ("  notes/hello.md  ", "notes/hello.md"),
        ("`notes/hello.md`", "notes/hello.md"),
        ("\"notes/hello.md\"", "notes/hello.md"),
        ("'notes/hello.md'", "notes/hello.md"),
        ("path: notes/hello.md", "notes/hello.md"),
        ("\n\nnotes/hello.md\nand then...", "notes/hello.md"),
        ("```\nnotes/hello.md\n```", "notes/hello.md"),
        ("", ""),
        ("   \n  \n", ""),
    ] {
        assert_eq!(parse_line(reply), expected, "{reply:?}");
    }
    // A sentence containing a colon is not a `key: value` echo.
    assert_eq!(
        parse_line("The file is: notes/hello.md"),
        "The file is: notes/hello.md"
    );
}

#[test]
fn a_verdict_word_is_read_only_when_it_is_unambiguous() {
    assert_eq!(parse_verdict_word("PASS"), Some(true));
    assert_eq!(parse_verdict_word("pass"), Some(true));
    assert_eq!(parse_verdict_word("  FAIL  "), Some(false));
    assert_eq!(parse_verdict_word("The answer is PASS."), Some(true));
    // Both words, or neither, is not an answer — and "not an answer" sends the
    // run to the ordinary critic chain, which is the fail-closed direction.
    assert_eq!(parse_verdict_word("not FAIL, so PASS"), None);
    assert_eq!(parse_verdict_word("maybe"), None);
    assert_eq!(parse_verdict_word(""), None);
    // A longer word that merely contains the letters is not the word.
    assert_eq!(parse_verdict_word("PASSPORT"), None);
    assert_eq!(parse_verdict_word("FAILURE"), None);
}

/// A catalog offering one MCP tool the run does not govern and one skill.
#[derive(Debug)]
pub(super) struct FakeCatalog {
    pub(super) calls: std::sync::Mutex<Vec<(String, Value)>>,
}

impl FakeCatalog {
    pub(super) fn new() -> Arc<Self> {
        Arc::new(Self {
            calls: std::sync::Mutex::new(Vec::new()),
        })
    }
}

impl ToolCatalog for FakeCatalog {
    fn entries(&self) -> Vec<CatalogEntry> {
        vec![
            CatalogEntry {
                name: "mcp.remote_search".into(),
                kind: EntryKind::Mcp,
                summary: "search a remote index".into(),
                required: vec![ArgSpec::line("query", "what to look for")],
            },
            CatalogEntry {
                name: "skill.file_edit".into(),
                kind: EntryKind::Skill,
                summary: "how to edit files carefully".into(),
                required: Vec::new(),
            },
            // Already native under its bare name: must not double the menu.
            CatalogEntry {
                name: "mcp.read_file".into(),
                kind: EntryKind::Mcp,
                summary: "read".into(),
                required: vec![ArgSpec::line("path", "path")],
            },
        ]
    }

    fn execute<'a>(
        &'a self,
        name: &'a str,
        args: &'a Map<String, Value>,
        _scope: &'a CallScope,
    ) -> ToolFuture<'a> {
        Box::pin(async move {
            self.calls
                .lock()
                .expect("lock")
                .push((name.to_string(), Value::Object(args.clone())));
            if name.starts_with("skill.") {
                return ToolOutcome::Result(json!({
                    "kind": "skill",
                    "name": name,
                    "text": "Always read a file before editing it.",
                }));
            }
            ToolOutcome::Result(json!({"hits": 0}))
        })
    }
}

#[tokio::test]
async fn a_menu_choice_and_two_argument_turns_write_a_real_file() {
    // The whole point, end to end: not one character of JSON is asked for, and
    // a file lands on disk through the ordinary gates.
    // The harness offers `read_file` and `write_file`; the menu ranks the
    // file-work core first, so `write_file` is row one whatever order the
    // policy table happened to be in.
    let mut harness = harness(&[
        "1",                          // menu: write_file
        "notes/hello.md",             // path
        "안녕하세요!\n반갑습니다.\n", // content, free-form
        "3",                          // menu: final
        "메모를 만들었습니다",        // final message
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("the fake worker answers");

    assert_eq!(ctx.state, AgentState::Verifying);
    let written = std::fs::read_to_string(harness.root.join("notes/hello.md")).expect("the file");
    assert!(written.contains("안녕하세요"), "{written}");
    assert_eq!(ctx.final_message, "메모를 만들었습니다");

    let actions: Vec<&str> = ctx
        .transcript
        .iter()
        .filter_map(|step| step.get("action").and_then(Value::as_str))
        .collect();
    assert_eq!(actions, vec!["write_file", "final"]);
    // The dispatch went through the ordinary tool path, args and all.
    let step = &ctx.transcript[0];
    assert_eq!(step["args"]["path"], json!("notes/hello.md"));
    assert_eq!(step["result"]["path"], json!("notes/hello.md"));
    // And no completion asked the model for an object.
    for call in harness.worker.calls.lock().expect("lock").iter() {
        if call["seam"] != json!("llm") {
            continue;
        }
        let context = call["body"]["context"].as_str().unwrap_or_default();
        assert!(
            !context.contains("JSON object"),
            "a guided turn must not ask for JSON: {context}"
        );
    }
}

#[tokio::test]
async fn a_fenced_content_answer_is_unwrapped_before_it_is_written() {
    let mut harness = harness(&[
        "1",
        "notes/hello.md",
        "```markdown\n# 안녕\n\n반갑습니다.\n```",
        "3",
        "done",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let written = std::fs::read_to_string(harness.root.join("notes/hello.md")).expect("file");
    assert!(written.starts_with("# 안녕"), "{written:?}");
    assert!(!written.contains("```"), "the fence never reaches disk");
}

#[tokio::test]
async fn a_named_answer_is_accepted_and_a_nonsense_one_is_not() {
    let mut harness = harness(&[
        "I'll use write_file", // named, not numbered
        "notes/a.md",
        "body",
        "let me think about this", // nonsense: no number, no name
        "still thinking",
        "hmm",
        "and again", // budget spent
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(
        harness.root.join("notes/a.md").exists(),
        "the named choice still wrote"
    );
    // The nonsense turns are recorded as parse slips, not as silent no-ops.
    assert!(
        ctx.transcript
            .iter()
            .any(|step| step.get("action") == Some(&json!("parse_error"))),
        "{:?}",
        ctx.transcript
    );
    assert_eq!(ctx.state, AgentState::Verifying);
}

#[tokio::test]
async fn choosing_a_skill_returns_its_instructions_and_changes_nothing() {
    let catalog = FakeCatalog::new();
    // write_file, read_file, mcp.remote_search, skill.file_edit, final.
    let mut harness = harness(&[
        "4", // menu: skill.file_edit
        "1", // menu: write_file
        "notes/a.md",
        "body",
        "5", // final
        "done",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let skill_step = ctx
        .transcript
        .iter()
        .find(|step| step.get("action") == Some(&json!("skill.file_edit")))
        .expect("the skill step");
    assert_eq!(skill_step["result"]["kind"], json!("skill"));
    assert!(skill_step["result"]["text"]
        .as_str()
        .expect("text")
        .contains("read a file before editing"));
    // A skill is guidance: nothing was created by choosing it.
    assert_eq!(
        catalog.calls.lock().expect("lock").len(),
        1,
        "one external call, and it was the skill"
    );
    // And its instructions are in front of the model on the next turn.
    let contexts: Vec<String> = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .filter(|call| call["seam"] == json!("llm"))
        .map(|call| {
            call["body"]["context"]
                .as_str()
                .unwrap_or_default()
                .to_string()
        })
        .collect();
    assert!(
        contexts
            .iter()
            .any(|context| context.contains("read a file before editing")),
        "the skill body must reach a later turn"
    );
}

/// The live gemma_e2b:S4 shape. Three menu turns, no digit in any of them, and
/// the file half rescued the *artefact* while silently skipping the one thing
/// the request named: the skill. A skill is guidance — choosing it writes
/// nothing and runs nothing — so a run whose model stopped answering still gets
/// the instructions the user asked for, and the write that follows is written
/// with them in front of it.
///
/// Since v12.0.0 the skill is loaded *earlier* than this test used to prove:
/// a request that names an installed skill has it consulted as the run's first
/// step ([`Runtime::consult_named_skill`]), so the fallback's rescue is a floor
/// under a contract rather than the only path. Both ends of the property are
/// asserted below — the instructions are in force before the write either way.
#[tokio::test]
async fn a_skill_the_request_names_is_loaded_before_the_direct_write() {
    let catalog = FakeCatalog::new();
    // Nine menu turns (three steps × `micro_turn_cap`) with no number in any of
    // them, then the one content turn the direct write spends.
    let mut harness = harness(&[
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "hmm",
        "# 체크리스트\n\n- 읽고 나서 고친다\n",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.permission_mode = Some("trusted".into());
    harness.request.message = "file_edit 스킬을 참고해서 notes/review_note.md에 \
체크리스트를 써줘"
        .into();
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "write a checklist", "steps": [
        {"action": "write_file", "args": {"path": "notes/review_note.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let skill_step = ctx
        .transcript
        .iter()
        .find(|step| step.get("action") == Some(&json!("skill.file_edit")))
        .expect("the skill the request named was loaded");
    assert_eq!(skill_step["result"]["kind"], json!("skill"));
    assert_eq!(
        ctx.transcript
            .first()
            .and_then(|step| step.get("action"))
            .cloned(),
        Some(json!("skill.file_edit")),
        "the named skill is the run's first step, not a rescue: {:?}",
        ctx.transcript
    );
    // And its instructions were in front of the write turn.
    let content_turn = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .rfind(|call| call["seam"] == json!("llm"))
        .expect("a content turn")["body"]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(
        content_turn.contains("read a file before editing"),
        "{content_turn}"
    );
    assert!(
        std::fs::read_to_string(harness.root.join("notes/review_note.md"))
            .expect("the file")
            .contains("체크리스트")
    );
    // One external call: the skill. Guidance is not a shopping list.
    assert_eq!(catalog.calls.lock().expect("lock").len(), 1);
}

/// The live qwen05b:S5 defect, in one function. `mcp.grep` satisfies
/// `looks_like_a_path` — stem, dot, alphanumerics — so the harness offered the
/// *tool the request named* as the file to write, and the run wrote it, read it
/// and wrote it again until the loop guard fired.
#[test]
fn a_qualified_tool_name_in_the_request_is_never_read_as_a_path() {
    assert_eq!(
        paths_named_in("워크스페이스에서 LatticeAI를 mcp.grep으로 검색해줘"),
        Vec::<String>::new(),
        "a tool the request names is a tool"
    );
    assert_eq!(
        paths_named_in("skill.code_review 스킬을 참고해서 써줘"),
        Vec::<String>::new()
    );
    // And a real path in the same sentence still reads as one.
    assert_eq!(
        paths_named_in("mcp.grep으로 찾아서 notes/summary.md로 저장해줘"),
        vec!["notes/summary.md".to_string()]
    );
}

#[tokio::test]
async fn an_mcp_tool_the_run_does_not_govern_goes_through_the_host_catalog() {
    let catalog = FakeCatalog::new();
    let mut harness = harness(&["3", "무엇이든", "5", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let calls = catalog.calls.lock().expect("lock").clone();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "mcp.remote_search");
    assert_eq!(calls[0].1["query"], json!("무엇이든"));
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("action") == Some(&json!("mcp.remote_search")))
        .expect("the mcp step");
    assert_eq!(step["result"]["hits"], json!(0));
}

#[tokio::test]
async fn an_external_name_with_no_catalog_refuses_instead_of_pretending() {
    let mut harness = harness(&["1", "x", "y"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    let flow = harness
        .runtime
        .perform_action(
            &mut ctx,
            &harness.request,
            crate::kernel::agentloop::execution::Chosen {
                name: "skill.nothing",
                thoughts: "t",
                args: Map::new(),
                final_message: None,
            },
        )
        .await;
    assert!(matches!(
        flow,
        crate::kernel::agentloop::execution::StepFlow::Continue
    ));
    let step = ctx.transcript.last().expect("a step");
    assert_eq!(step["action"], json!("skill.nothing"));
    assert!(step["error"]
        .as_str()
        .expect("error")
        .contains("no external tool catalog"));
}

#[tokio::test]
async fn an_answer_that_is_just_the_question_is_never_written() {
    // The first live 0.5B run put the literal string "내용을 그대로 쓰세요."
    // into a file, because that was the last instruction it had read.
    let mut harness = harness(&[
        "1",
        "notes/a.md",
        // a line of the body turn's own instruction block, read back
        "/ Write only the resulting file body for the request below — no explanation, \
no preamble, no code fence.",
        "진짜 내용입니다.",
        "3",
        "done",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let written = std::fs::read_to_string(harness.root.join("notes/a.md")).expect("file");
    assert_eq!(written.trim(), "진짜 내용입니다.", "{written:?}");
}

#[test]
fn a_chat_template_control_token_never_reaches_a_file() {
    // The live 2B wrote `안녕하세요!<|im_end|>`: the tokenizer's punctuation, not
    // the model's words.
    assert_eq!(strip_control_tokens("안녕하세요!<|im_end|>"), "안녕하세요!");
    assert_eq!(
        strip_control_tokens("<|channel|>thought<|message|>real body"),
        "thoughtreal body"
    );
    assert_eq!(strip_control_tokens("plain text"), "plain text");
    // An unterminated marker is text, not a token — a document may say `<|`.
    assert_eq!(strip_control_tokens("a <| b"), "a <| b");
    assert_eq!(strip_control_tokens(""), "");
}

#[test]
fn a_control_frame_closed_with_one_pipe_is_still_a_control_frame() {
    // gemma-4-e2b emits `<|channel>thought` — one pipe, not two — and the
    // cleaner's `|>` requirement let it through whole. It reached `mcp.grep` as
    // the pattern to search for, the guided critic as its `reason`, and the
    // user as the entire answer, in twelve of that model's sixteen cells.
    assert_eq!(strip_control_tokens("<|channel>thought"), "thought");
    assert_eq!(
        strip_control_tokens("<|channel>thought<|message|>real body"),
        "thoughtreal body"
    );
    assert_eq!(strip_control_tokens("2개<|end>"), "2개");
    // A name may be empty, and the two spellings of the closer agree.
    assert_eq!(strip_control_tokens("a<|>b"), "ab");
    // And prose is still prose. A `<|` that opens nothing short, wordlike and
    // closed is kept, and a later real frame is still found.
    assert_eq!(strip_control_tokens("a <| b"), "a <| b");
    assert_eq!(strip_control_tokens("x <| y <|im_end|>"), "x <| y ");
    assert_eq!(
        strip_control_tokens("if a <| b then stop"),
        "if a <| b then stop"
    );
    // Long enough to be a sentence rather than a token name: kept.
    let long = format!("<|{}>", "z".repeat(64));
    assert_eq!(strip_control_tokens(&long), long);
}

#[test]
fn a_garbled_echo_of_our_instruction_is_dropped() {
    // The live 0.5B wrote this into notes/hello.md — not a prefix of any
    // one instruction line, but it still carries a phrase we own.
    assert!(strip_echoed_lines("이미지·머리말·코드블록 금지不予提供。", &[]).is_empty());
    assert_eq!(
        strip_echoed_lines("안녕하세요.\n머리말·코드블록 금지\n반갑습니다.", &[]).trim(),
        "안녕하세요.\n반갑습니다."
    );
}

#[test]
fn a_request_that_names_a_skill_puts_that_row_first() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into()];
    runtime.deps.external = Some(FakeCatalog::new());
    let ctx = crate::kernel::state::AgentRunContext::new();
    let names: Vec<String> = runtime
        .rank_catalog(&ctx, "skill.file_edit 를 참고해서 메모를 써줘")
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "skill.file_edit", "{names:?}");
}

#[test]
fn a_path_the_request_named_beats_the_planners_invention() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan =
        json!({"steps": [{"action": "write_file", "args": {"path": "인사말/notes/hello.md"}}]})
            .as_object()
            .expect("plan")
            .clone();
    let entry = crate::tools::catalog::native_entries(&["write_file".to_string()])
        .into_iter()
        .next()
        .expect("write_file");
    let req = crate::kernel::agentloop::RunRequest {
        message: "인사말을 notes/hello.md 파일로 저장해줘".into(),
        ..crate::kernel::agentloop::RunRequest::default()
    };
    assert_eq!(
        runtime.suggested_arg(&ctx, &req, &entry, "path"),
        Some("notes/hello.md".to_string()),
        "the user's path, not the planner's prefixed invention"
    );
}

/// The other half of the qwen05b:S5 defect. The plan held
/// `"pattern": "LatticeAI"`; the 0.5B, asked for `pattern` with no default in
/// front of it, answered `mcp.grep --pattern-literal "LatticeAI` and that is
/// what the search ran for.
#[test]
fn every_one_line_argument_the_plan_computed_is_offered_as_the_default() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["grep".into(), "write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [
        {"action": "grep", "args": {"pattern": "LatticeAI"}},
        {"action": "write_file", "args": {"path": "notes/a.md", "content": "TODO"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    let entries = crate::tools::catalog::native_entries(&["grep".to_string()]);
    let grep = entries
        .iter()
        .find(|entry| entry.name == "grep")
        .expect("grep");
    let req = crate::kernel::agentloop::RunRequest {
        message: "LatticeAI를 grep으로 찾아줘".into(),
        ..crate::kernel::agentloop::RunRequest::default()
    };
    assert_eq!(
        runtime.suggested_arg(&ctx, &req, grep, "pattern"),
        Some("LatticeAI".to_string()),
    );
    // A free-form body is still never defaulted: a planner's `content` is a
    // placeholder often enough that offering it would write "TODO" to disk.
    let writes = crate::tools::catalog::native_entries(&["write_file".to_string()]);
    let write = writes
        .iter()
        .find(|entry| entry.name == "write_file")
        .expect("write_file");
    assert_eq!(runtime.suggested_arg(&ctx, &req, write, "content"), None);
    // And an argument the plan says nothing about is still asked for.
    assert_eq!(runtime.suggested_arg(&ctx, &req, grep, "query"), None);
}

#[test]
fn a_loaded_skill_is_not_row_one_again() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into()];
    runtime.deps.external = Some(FakeCatalog::new());
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.transcript.push(json!({
        "state": "EXECUTING",
        "action": "skill.file_edit",
        "result": {"kind": "skill", "text": "read first"},
    }));
    let names: Vec<String> = runtime
        .rank_catalog(&ctx, "skill.file_edit 를 참고해서 notes/a.md 에 써줘")
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_ne!(names[0], "skill.file_edit", "{names:?}");
    assert_eq!(names[0], "write_file", "{names:?}");
}

#[test]
fn a_finished_list_puts_final_first() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "list_dir".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "list_dir", "args": {"path": "."}}]})
        .as_object()
        .expect("plan")
        .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING",
        "action": "list_dir",
        "result": {"items": [{"name": "a"}, {"name": "b"}]},
    }));
    let names: Vec<String> = runtime
        .rank_catalog(&ctx, "list_dir로 확인하고 파일 개수를 알려줘")
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "final", "{names:?}");
}

#[test]
fn a_request_that_names_list_dir_puts_it_ahead_of_write_file() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into(), "list_dir".into()];
    let ctx = crate::kernel::state::AgentRunContext::new();
    let names: Vec<String> = runtime
        .rank_catalog(
            &ctx,
            "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘",
        )
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "list_dir", "{names:?}");
}

#[test]
fn a_request_that_names_an_mcp_tool_reinserts_the_prefixed_row() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into(), "grep".into()];
    runtime.deps.external = Some(FakeCatalog::new());
    let ctx = crate::kernel::state::AgentRunContext::new();
    // FakeCatalog offers mcp.read_file (dropped as a duplicate) and
    // mcp.remote_search. Ask for the prefixed read.
    let names: Vec<String> = runtime
        .rank_catalog(&ctx, "mcp.read_file 로 README.md 를 읽어줘")
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "mcp.read_file", "{names:?}");
    assert!(
        names.iter().filter(|name| *name == "mcp.read_file").count() == 1,
        "prefixed row once: {names:?}"
    );
}

#[test]
fn a_path_answer_that_is_not_a_path_yields_to_the_plans_own() {
    for path in ["notes/hello.md", "hello.md", "a\\b", "x.TXT"] {
        assert!(looks_like_a_path(path), "{path}");
    }
    for other in ["안녕하세요!", "the file", "", "  ", "write_file"] {
        assert!(!looks_like_a_path(other), "{other}");
    }
    // A trailing dot with nothing after it is not an extension.
    assert!(!looks_like_a_path("hello."));
}

#[tokio::test]
async fn a_greeting_answered_to_a_path_question_does_not_become_a_filename() {
    let mut harness = harness(&["1", "안녕하세요!", "본문입니다.", "3", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.plan = json!({
        "goal": "메모 만들기",
        "steps": [{"action": "write_file", "args": {"path": "notes/hello.md"}}],
    })
    .as_object()
    .expect("plan")
    .clone();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(
        harness.root.join("notes/hello.md").exists(),
        "{:?}",
        ctx.transcript
    );
    assert!(!harness.root.join("안녕하세요!").exists());
}

#[test]
fn our_own_sentences_are_stripped_off_the_front_of_an_answer() {
    let question = "notes/a.md 의 본문: / The body of notes/a.md:";
    let context = "아래 요청을 그대로 수행한 결과물(파일 본문)만 쓰세요.\n\
/ Write only the resulting file body for the request below.\n\n요청 / REQUEST: 인사말";
    // Nothing but echo is no answer at all.
    assert_eq!(
        strip_echoed_lines(question, &[question, context]).trim(),
        ""
    );
    assert_eq!(
        strip_echoed_lines(
            "/ Write only the resulting file body for the request below.",
            &[question, context]
        )
        .trim(),
        ""
    );
    // A preamble in front of a real answer loses the preamble, not the answer.
    assert_eq!(
        strip_echoed_lines(
            "notes/a.md 의 본문: / The body of notes/a.md:\n\n# 안녕하세요\n반갑습니다.",
            &[question, context]
        ),
        "# 안녕하세요\n반갑습니다."
    );
    // A real document is never touched, even one that quotes the request.
    let document = "# 인사말\n\n안녕하세요. 요청 / REQUEST: 인사말 을 반영했습니다.";
    assert_eq!(strip_echoed_lines(document, &[question, context]), document);
    // Short lines are not compared at all: a `#` or a `---` on its own is a
    // document, not an echo.
    assert_eq!(strip_echoed_lines("---\nbody", &["---"]), "---\nbody");
    // A model that copied an instruction and ran out of budget half way is
    // still echoing — the live 0.5B stopped in the middle of the English half
    // of a bilingual sentence.
    assert_eq!(
        strip_echoed_lines(
            "/ Write only the resulting file body for the request\n진짜",
            &[context]
        ),
        "진짜"
    );
    // …including one that closed the sentence we had carried on.
    assert_eq!(
        strip_echoed_lines(
            "/ Write only the resulting file body for the request.\n진짜",
            &["/ Write only the resulting file body for the request below — no fence."]
        ),
        "진짜"
    );
    // But a short opening phrase is a document, not an echo.
    assert_eq!(
        strip_echoed_lines("/ Write only\n진짜", &[context]),
        "/ Write only\n진짜"
    );
    // A model that numbers the instruction before echoing it is still echoing.
    assert_eq!(
        strip_echoed_lines(
            "[1] / Write only the resulting file body for the request below.\n진짜",
            &[context]
        ),
        "진짜"
    );
    assert_eq!(
        strip_echoed_lines(
            "- / Write only the resulting file body for the request below.\n진짜",
            &[context]
        ),
        "진짜"
    );
}

#[tokio::test]
async fn a_default_that_came_back_cut_off_is_taken_whole() {
    // The first live 0.5B run copied a long absolute default into a token
    // budget that ran out, losing `/hello.md` and writing a directory.
    let mut harness = harness(&["1", "notes/hel", "본문", "3", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.plan = json!({
        "goal": "메모 만들기",
        "steps": [{"action": "write_file", "args": {"path": "notes/hello.md"}}],
    })
    .as_object()
    .expect("plan")
    .clone();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(
        harness.root.join("notes/hello.md").exists(),
        "a truncated copy of the default is the default, not a new path: {:?}",
        ctx.transcript
    );
    assert!(!harness.root.join("notes/hel").exists());
}
