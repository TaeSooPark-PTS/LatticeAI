use serde_json::{json, Value};

use super::tests_menu::FakeCatalog;
use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::profile::GUIDED;
use crate::kernel::state::AgentState;
use crate::tools::catalog::{ArgSpec, CatalogEntry, EntryKind};

#[tokio::test]
async fn a_write_with_no_body_is_never_dispatched() {
    // The live 2B, twice in one run: the body turn produced nothing, the loop
    // sent `write_file` with a path and no content, and collected the tool's
    // own `needs args.content` refusal — then did it again.
    let mut harness = harness(&["1", "notes/hello.md", "", "", "", "", "", "", ""]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.request.message = "인사말을 notes/hello.md 파일로 저장해줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    // The rule is about every path that can reach a write, not only the menu's:
    // the direct fallback below it asks the same silent model for the same file
    // and would otherwise leave a 0-byte artifact behind.
    for call in harness.tool_calls() {
        if call["tool"] != json!("write_file") {
            continue;
        }
        panic!("a write with no body was dispatched: {call}");
    }
    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .contains("needs args.content")),
        "and the reason is recorded: {:?}",
        ctx.transcript
    );
    assert!(!harness.root.join("notes/hello.md").exists());
    assert!(
        std::fs::read_dir(harness.root.join("notes")).is_err(),
        "no file at all, rather than an empty one"
    );
}

#[tokio::test]
async fn a_re_asked_line_turn_drops_the_stop_that_cut_the_answer_off() {
    // The live gemma-4-e2b, thirteen dispatches across one cell: asked for
    // `mcp.grep`'s pattern it answered `<|channel>thought\nLatticeAI`, the
    // newline stop fired inside the frame header, and the harness was handed a
    // label where a search term had actually been generated.
    let catalog = FakeCatalog::new();
    let mut harness = harness(&["1", "", "LatticeAI", "2", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "mcp.remote_search로 LatticeAI를 찾아줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let asks: Vec<Value> = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .filter(|call| {
            call["seam"] == json!("llm")
                && call["body"]["message"]
                    .as_str()
                    .unwrap_or_default()
                    .contains("Answer with the value on one line")
        })
        .map(|call| call["body"].clone())
        .collect();
    assert!(asks.len() >= 2, "the turn was re-asked: {asks:?}");
    assert_eq!(
        asks[0]["stop"],
        json!(["\n"]),
        "the first ask keeps the stop"
    );
    assert_eq!(
        asks[1]["stop"],
        Value::Null,
        "the re-ask drops it, or it is the first ask replayed"
    );
    let calls = catalog.calls.lock().expect("lock").clone();
    assert_eq!(calls[0].1["query"], json!("LatticeAI"), "{:?}", calls[0].1);
}

#[tokio::test]
async fn a_call_missing_a_required_argument_is_never_dispatched() {
    // Same rule as the contentless write, one tool over: a live gemma-4-e2b
    // sent `mcp.grep` with no pattern thirteen times across a cell's retries,
    // collecting the tool's own `'pattern'` refusal every time.
    let catalog = FakeCatalog::new();
    let mut harness = harness(&["1", "", "", "", "", "", "", "", ""]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "mcp.remote_search로 찾아줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(
        catalog.calls.lock().expect("lock").is_empty(),
        "nothing was sent: {:?}",
        catalog.calls.lock().expect("lock")
    );
    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .contains("needs args.query")),
        "and the reason is recorded: {:?}",
        ctx.transcript
    );
}

#[tokio::test]
async fn an_argument_turn_that_answered_with_a_bare_frame_takes_the_plans_value() {
    // The live gemma-4-e2b: asked for `mcp.grep`'s pattern with the plan
    // sitting on `LatticeAI`, it answered `<|channel>thought` — a frame header
    // and no body — and the run searched the workspace for the word "thought".
    let catalog = FakeCatalog::new();
    let mut harness = harness(&["1", "<|channel>thought", "2", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "mcp.remote_search로 LatticeAI를 찾아줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    ctx.plan = json!({"steps": [
        {"action": "mcp.remote_search", "args": {"query": "LatticeAI"}}]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let calls = catalog.calls.lock().expect("lock").clone();
    assert_eq!(calls.len(), 1, "{calls:?}");
    assert_eq!(calls[0].0, "mcp.remote_search");
    assert_eq!(
        calls[0].1["query"],
        json!("LatticeAI"),
        "the plan's own value, not the frame's label: {:?}",
        calls[0].1
    );
}

#[tokio::test]
async fn a_reason_that_is_our_own_question_read_back_is_not_a_reason() {
    // The live 2B answered the reason turn with our own question, one word
    // inserted — and the run showed that to the user as the critic's reason and
    // fed it back to the executor as the next attempt's correction.
    let mut harness = harness(&[
        "PASS",
        "이유를 다시 한 줄로 쓰세요. / Give the reason in one short line.",
    ])
    .await;
    let mut ctx = harness.context();
    let verdict = harness
        .runtime
        .guided_verdict(&mut ctx, &harness.request, None)
        .await
        .expect("worker")
        .expect("a verdict word came back");
    assert_eq!(verdict["verdict"], json!("PASS"));
    assert_eq!(
        verdict["reason"],
        json!("guided verdict: the transcript shows the work was done"),
        "our own sentence is never recorded as the critic's"
    );
}

#[test]
fn a_one_line_value_is_refused_only_for_a_shape_this_crate_put_on_the_screen() {
    let request = "워크스페이스에서 LatticeAI라는 단어를 mcp.grep으로 찾아주고, \
찾은 개수를 알려줘";
    // 1. our own `[고른 행동 / CHOSEN ACTION]` label, carried on — the live 0.5B.
    assert!(!usable_line(
        "mcp.grep --pattern \"^LatticeAI",
        "mcp.grep",
        "pattern",
        request
    ));
    assert!(!usable_line("mcp.grep", "mcp.grep", "pattern", request));
    // 2. a command line rather than the value inside it.
    assert!(!usable_line(
        "--pattern LatticeAI",
        "mcp.grep",
        "pattern",
        request
    ));
    assert!(!usable_line("\"LatticeAI", "mcp.grep", "pattern", request));
    // 3. the request restated — the live 2B searched for a whole sentence.
    assert!(!usable_line(
        "워크스페이스에서 LatticeAI라는 단어를 찾았습니다. 3개의 개수가 존재했습니다.",
        "mcp.grep",
        "pattern",
        request
    ));
    // And the answers that are answers, none of which may be refused.
    assert!(usable_line("LatticeAI", "mcp.grep", "pattern", request));
    assert!(usable_line("notes/hello.md", "write_file", "path", request));
    assert!(usable_line("^Lattice.*AI$", "mcp.grep", "pattern", request));
    // A value that is wholly a fragment of the request is copied, not restated,
    // trailing punctuation and all.
    assert!(usable_line(
        "워크스페이스에서 LatticeAI라는 단어를",
        "mcp.grep",
        "pattern",
        request
    ));
    assert!(usable_line(
        "워크스페이스에서 LatticeAI라는 단어를.",
        "mcp.grep",
        "pattern",
        request
    ));
    // A long path the request opened with is the answer to a path question.
    assert!(usable_line(
        "documents/reports/lattice-2026.md",
        "write_file",
        "path",
        "documents/reports/lattice-2026.md 파일로 저장해줘"
    ));
    // And the one argument whose value *is* a command line keeps its flags.
    assert!(usable_line(
        "npm run build -- --watch",
        "run_command",
        "command",
        request
    ));
}

#[tokio::test]
async fn a_line_turn_that_answered_with_a_command_line_is_re_asked_for_the_value() {
    // The live qwen05b:S5: asked for the search pattern it continued our own
    // action label into a command line, and the workspace was searched for
    // `mcp.grep --pattern "^LatticeAI`.
    let catalog = FakeCatalog::new();
    let mut harness = harness(&[
        "1",
        "mcp.remote_search --query \"LatticeAI",
        "LatticeAI",
        "5",
        "찾았습니다",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "mcp.remote_search로 LatticeAI를 찾아줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let calls = catalog.calls.lock().expect("lock").clone();
    assert_eq!(calls[0].0, "mcp.remote_search");
    assert_eq!(
        calls[0].1["query"],
        json!("LatticeAI"),
        "the value, not the command line: {:?}",
        calls[0].1
    );
    assert!(
        ctx.trace
            .events
            .iter()
            .any(|event| event["decision"] == json!("guided_line_rejected")),
        "the refusal is on the record: {:?}",
        ctx.trace.events
    );
    // And the re-ask was a different question, or it was the first ask replayed.
    let asks: Vec<String> = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .filter(|call| {
            call["seam"] == json!("llm")
                && call["body"]["message"]
                    .as_str()
                    .unwrap_or_default()
                    .contains("Answer with the value on one line")
        })
        .map(|call| {
            call["body"]["context"]
                .as_str()
                .unwrap_or_default()
                .to_string()
        })
        .collect();
    assert!(asks.len() >= 2, "{asks:?}");
    assert!(
        !asks[0].contains(crate::prompts::guided::LINE_RETRY_NOTE),
        "the first ask carries no correction: {:?}",
        asks[0]
    );
    assert!(
        asks[1].contains(crate::prompts::guided::LINE_RETRY_NOTE),
        "the re-ask names what went wrong: {:?}",
        asks[1]
    );
    assert!(
        !asks[1].contains("[고른 행동 / CHOSEN ACTION]"),
        "and drops the label the rejected answer was made of: {:?}",
        asks[1]
    );
}

#[test]
fn a_plan_that_wrote_the_call_as_a_command_still_names_its_argument() {
    // The live qwen05b planner: the tool in a field and its arguments nested
    // one level under it, with the planner's own name for the argument.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let runtime = crate::kernel::agentloop::harness::runtime(workspace);
    let grep = CatalogEntry {
        name: "mcp.grep".into(),
        kind: EntryKind::Mcp,
        summary: "search".into(),
        required: vec![ArgSpec::line("pattern", "the text to look for")],
    };
    let req = crate::kernel::agentloop::RunRequest {
        message: "LatticeAI를 mcp.grep으로 찾아줘".into(),
        ..crate::kernel::agentloop::RunRequest::default()
    };
    let plan = |steps: Value| {
        let mut ctx = crate::kernel::state::AgentRunContext::new();
        ctx.plan = json!({ "steps": steps }).as_object().expect("plan").clone();
        ctx
    };

    let nested = plan(json!([{"action": "command",
        "args": {"command": "mcp.grep", "arguments": {"text": "LatticeAI"}}}]));
    assert_eq!(
        runtime.suggested_arg(&nested, &req, &grep, "pattern"),
        Some("LatticeAI".to_string()),
        "one string, one line argument: it can only be that argument"
    );

    let by_name = plan(json!([{"action": "command",
        "args": {"command": "mcp.grep", "arguments": {"pattern": "^Lattice", "flags": "i"}}}]));
    assert_eq!(
        runtime.suggested_arg(&by_name, &req, &grep, "pattern"),
        Some("^Lattice".to_string()),
        "the argument's own name is read first"
    );

    // Two strings, one of which the keyword rule names: `text` is a spelling of
    // `pattern` (v12.0.0), so this is read rather than guessed at.
    let keyworded = plan(json!([{"action": "command",
        "args": {"command": "mcp.grep", "arguments": {"text": "LatticeAI", "in": "README.md"}}}]));
    assert_eq!(
        runtime.suggested_arg(&keyworded, &req, &grep, "pattern"),
        Some("LatticeAI".to_string()),
        "the planner's own word for this argument, read under the name it used"
    );

    // Two strings and no name to match at all: the harness does not choose
    // between them, it asks.
    let ambiguous = plan(json!([{"action": "command",
        "args": {"command": "mcp.grep", "arguments": {"needle": "LatticeAI", "in": "README.md"}}}]));
    assert_eq!(
        runtime.suggested_arg(&ambiguous, &req, &grep, "pattern"),
        None
    );
    // A command naming a different tool is a different call.
    let elsewhere = plan(json!([{"action": "command",
        "args": {"command": "run_command", "arguments": {"text": "LatticeAI"}}}]));
    assert_eq!(
        runtime.suggested_arg(&elsewhere, &req, &grep, "pattern"),
        None
    );
}

#[tokio::test]
async fn a_rejected_line_yields_to_the_value_the_plan_computed() {
    // The floor under the re-ask must not put a command line into a tool when
    // the run itself already worked out the value.
    let catalog = FakeCatalog::new();
    let mut harness = harness(&[
        "1",
        "mcp.remote_search --query \"LatticeAI",
        "5",
        "찾았습니다",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.external = Some(catalog.clone());
    harness.request.message = "mcp.remote_search로 LatticeAI를 찾아줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    ctx.plan = json!({"steps": [{"action": "command",
        "args": {"command": "mcp.remote_search", "arguments": {"text": "LatticeAI"}}}]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let calls = catalog.calls.lock().expect("lock").clone();
    assert_eq!(calls[0].0, "mcp.remote_search");
    assert_eq!(
        calls[0].1["query"],
        json!("LatticeAI"),
        "the plan's own value, not the rejected command line: {:?}",
        calls[0].1
    );
}

#[tokio::test]
async fn the_runs_own_answer_is_never_refused_for_restating_the_question() {
    // `final` is the one action whose one-line argument is prose for the user
    // rather than a value for a tool, and an answer that opens with the
    // question is an answer.
    let mut harness = harness(&[
        "3",
        "이 폴더에 어떤 파일이 몇 개나 있는지 확인했습니다. 3개입니다.",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.request.message = "이 폴더에 어떤 파일이 몇 개나 있는지 알려줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(
        ctx.final_message, "이 폴더에 어떤 파일이 몇 개나 있는지 확인했습니다. 3개입니다.",
        "the run's own words reach the user whole"
    );
}

// ── v12.0.0 fix round: the two arguments a user states themselves ──────────

#[tokio::test]
async fn a_listing_the_tool_would_have_returned_is_never_refused() {
    // The `gemma2b:S3` cell, end to end: nine `list_dir args{}` dispatches,
    // every one refused by our own gate, three `LOOP_DETECTED` halts and no
    // count — over a tool whose signature is `list_dir(path: str = ".")`.
    // The model answers every path turn with nothing. Since v12.0.0 a
    // defaulting argument is *offered* before it is asked for, so the turns
    // are: menu 1 → list_dir, two silent choice turns (the ask and its one
    // re-ask), three silent line turns (the cap), then the menu again — where
    // `final` is row one now that the count is in hand. The floor under all of
    // them is unchanged: an absent argument the tool documents is filled
    // rather than refused.
    let mut harness = harness(&["1", "", "", "", "", "", "1", "파일을 확인했습니다"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.tool_names = vec!["list_dir".into(), "write_file".into()];
    harness.runtime.deps.policies.tools.insert(
        "list_dir".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    harness.request.message =
        "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘".into();
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "list_dir".into(),
        json!({"result": {"path": ".", "items": [{"name": "README.md"}, {"name": "notes"}]}}),
    );
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let listings: Vec<Value> = harness
        .tool_calls()
        .into_iter()
        .filter(|call| call["tool"] == json!("list_dir"))
        .collect();
    assert!(!listings.is_empty(), "it ran at all");
    assert_eq!(
        listings[0]["args"]["path"],
        json!("."),
        "the tool's own default, filled by the harness: {listings:?}"
    );
    assert!(
        !ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .contains("needs args.path")),
        "and nothing was refused for the argument the tool defaults: {:?}",
        ctx.transcript
    );
    // And the count the listing returned reaches the user, attributed to the
    // tool the request named.
    assert!(
        ctx.final_message.contains('2'),
        "the deliverable is the number: {:?}",
        ctx.final_message
    );
}

#[tokio::test]
async fn the_term_the_user_named_is_what_gets_searched_for() {
    // Two live cells, one rule. A 0.5B answered `mcp.grep`'s pattern turn with
    // `LatticeAI mcp.grep` and a 2B answered it with the whole request
    // sentence; both searched for that string, found nothing over a workspace
    // containing the word, and reported `0개` and `DONE`.
    let request =
        "워크스페이스에서 LatticeAI라는 단어를 mcp.remote_search로 찾아주고, 찾은 개수를 알려줘";
    for answer in [
        "LatticeAI mcp.remote_search",
        "워크스페이스에서 LatticeAI라는 단어를 mcp.remote_search로 찾아주고, 찾은 개수를 알려줘",
        "LatticeAI",
    ] {
        let catalog = FakeCatalog::new();
        let mut harness = harness(&["1", answer, "2", "찾았습니다"]).await;
        harness.runtime.deps.agent_profile = Some(GUIDED);
        harness.runtime.deps.external = Some(catalog.clone());
        harness.request.message = request.into();
        let mut ctx = harness.context();
        ctx.state = AgentState::Executing;
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("worker");
        let calls = catalog.calls.lock().expect("lock").clone();
        assert_eq!(
            calls.first().map(|call| call.1["query"].clone()),
            Some(json!("LatticeAI")),
            "answered {answer:?}: the user's own word is what is searched for"
        );
    }
}

#[test]
fn a_term_is_only_read_from_a_request_that_names_exactly_one() {
    let catalog =
        crate::tools::catalog::native_entries(&["grep".to_string(), "read_file".to_string()]);
    let term = |request: &str| term_named_in_request(request, &catalog);
    // The live shape: one ASCII literal, the tool name and the particles around
    // it are not candidates.
    assert_eq!(
        term("워크스페이스에서 LatticeAI라는 단어를 grep으로 찾아주고, 찾은 개수를 알려줘")
            .as_deref(),
        Some("LatticeAI")
    );
    // A quoted literal is unambiguous in any language.
    assert_eq!(term("\"TODO\" 를 찾아줘").as_deref(), Some("TODO"));
    assert_eq!(term("find '안녕' please").as_deref(), Some("안녕"));
    // A path is a place, not a term…
    assert_eq!(
        term("README.md 안에서 LatticeAI를 찾아줘").as_deref(),
        Some("LatticeAI")
    );
    // …and two candidates is a guess the harness does not make.
    assert_eq!(term("find TODO or FIXME"), None);
    assert_eq!(term("search the workspace for something"), None);
    // Nothing ASCII at all: there is no literal to read.
    assert_eq!(term("안녕이라는 말을 찾아줘"), None);
    assert_eq!(term(""), None);
}

// ── v12.0.0 round 4: the argument a 0.5B cannot type ──────────────────────

/// The S3 request, on the dial three live cells ran it on.
const COUNT_REQUEST: &str = "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘";

/// A run whose only tools are the listing and a write, with no plan.
async fn listing_harness(script: &[&str]) -> crate::kernel::agentloop::harness::Harness {
    let mut harness = harness(script).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.runtime.deps.tool_names = vec!["list_dir".into(), "write_file".into()];
    harness.runtime.deps.policies.tools.insert(
        "list_dir".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    harness.request.message = COUNT_REQUEST.into();
    harness
}

fn listing(items: usize) -> Value {
    let rows: Vec<Value> = (0..items)
        .map(|index| json!({"name": format!("file{index}.md"), "type": "file"}))
        .collect();
    json!({"result": {"root": "/ws", "path": ".", "items": rows}})
}

#[tokio::test]
async fn a_documented_default_is_offered_as_a_number_never_asked_for_as_a_path() {
    // The live qwen05b:S3 turn. Asked `path — workspace-relative path`, the
    // 0.5B answered `path/scratchpad/matrix/home_qwen05b/agent_workspace` — the
    // workspace line in its own context, continued — and looped on
    // `Directory does not exist.` until it halted. `list_dir(path: str = ".")`
    // documents the answer, so the turn is a two-row menu and the answer is a
    // number.
    // menu 1 → list_dir, choice 1 → the documented default, then `final` — row
    // one now that the count is in hand — and the answer.
    let mut harness = listing_harness(&["1", "1", "1", "확인했습니다"]).await;
    harness
        .worker
        .tool_bodies
        .lock()
        .expect("lock")
        .insert("list_dir".into(), listing(2));
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let listings: Vec<Value> = harness
        .tool_calls()
        .into_iter()
        .filter(|call| call["tool"] == json!("list_dir"))
        .collect();
    assert_eq!(listings.len(), 1, "one dispatch, not a loop: {listings:?}");
    assert_eq!(listings[0]["args"]["path"], json!("."));
    // The turn that produced it offered the tool's own default as row one and
    // an escape as row two — no path was ever asked for.
    let choice = harness
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
        .find(|context| context.contains("[선택지 / CHOICES]"))
        .expect("a numbered choice turn");
    assert!(choice.contains("1. 현재 폴더 (.)"), "{choice}");
    assert!(choice.contains("2. 다른 값을 직접"), "{choice}");
    assert!(
        !choice.contains("workspace-relative path\n\n[기본값"),
        "the open value slot is what the 0.5B answered wrongly: {choice}"
    );
    // And the count the listing returned is the run's answer.
    assert!(ctx.final_message.contains('2'), "{}", ctx.final_message);
}

#[tokio::test]
async fn a_path_the_tool_says_is_not_there_is_repaired_once_with_the_tools_own_default() {
    // Both live shapes, one rule. A 0.5B typed our own workspace line back
    // (`path/scratchpad/…`) and a 2B typed a filename its planner had invented
    // (`file_list.txt`); both dispatched, both were told the directory was not
    // there, both sent the identical call again, and both halted with no count.
    for answered in [
        "path/scratchpad/matrix/home_qwen05b/agent_workspace",
        "file_list.txt",
    ] {
        // menu → list_dir, choice → "2" (type it myself), then the live answer.
        let mut harness = listing_harness(&["1", "2", answered, "1", "확인했습니다"]).await;
        {
            let mut bodies = harness.worker.tool_bodies.lock().expect("lock");
            bodies.insert(
                format!("list_dir:{answered}"),
                json!({"error": "Directory does not exist."}),
            );
            bodies.insert("list_dir:.".into(), listing(2));
        }
        let mut ctx = harness.context();
        ctx.state = AgentState::Executing;
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("worker");

        let paths: Vec<Value> = harness
            .tool_calls()
            .into_iter()
            .filter(|call| call["tool"] == json!("list_dir"))
            .map(|call| call["args"]["path"].clone())
            .collect();
        assert_eq!(
            paths,
            vec![json!(answered), json!(".")],
            "the repaired call is a different call, and it runs: {paths:?}"
        );
        // The repair is on the record, not silent.
        let repaired = ctx
            .transcript
            .iter()
            .find(|step| step.get("arg_repair").is_some())
            .expect("the repair is recorded on the step it produced");
        assert_eq!(repaired["arg_repair"]["arg"], json!("path"));
        assert_eq!(repaired["arg_repair"]["default"], json!("."));
        assert_eq!(repaired["arg_repair"]["stated"], json!(answered));
        assert_eq!(
            repaired["result"]["items"].as_array().map(Vec::len),
            Some(2)
        );
        assert!(
            ctx.corrections
                .iter()
                .any(|hint| hint.as_str().unwrap_or_default().contains("Retried once")),
            "the next turn is told what was changed: {:?}",
            ctx.corrections
        );
        assert!(
            !ctx.transcript.iter().any(|step| step["error"]
                .as_str()
                .unwrap_or_default()
                .contains("LOOP_DETECTED")),
            "nothing looped: {:?}",
            ctx.transcript
        );
        assert!(ctx.final_message.contains('2'), "{}", ctx.final_message);
    }
}

#[tokio::test]
async fn a_call_that_already_used_the_default_is_never_repaired_into_a_replay() {
    // The halt is respected: a listing that failed *with* the default has
    // nothing left to try, and sending it again would be the identical replay
    // this rule exists to prevent.
    // Three steps of menu-1 / choice-1: the same call, the same argument, the
    // same error — the shape the repeat floor is for.
    let mut harness = listing_harness(&["1", "1", "1", "1", "1", "1"]).await;
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "list_dir:.".into(),
        json!({"error": "Directory does not exist."}),
    );
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    assert!(
        !ctx.transcript
            .iter()
            .any(|step| step.get("arg_repair").is_some()),
        "nothing to repair: {:?}",
        ctx.transcript
    );
    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .contains("LOOP_DETECTED")),
        "the ordinary floor still halts the run: {:?}",
        ctx.transcript
    );
}

#[tokio::test]
async fn guidance_read_means_the_declared_file_is_what_comes_next() {
    // The live qwen05b:S4 shape. The run consulted `skill.code_review`, and its
    // planner had named `read_file notes/review_note.md` — the file the request
    // asks it to *write*. The menu offered that read as row one and the run
    // spent itself on `File does not exist.`
    let catalog = FakeCatalog::new();
    let mut harness = harness(&[]).await;
    harness.runtime.deps.external = Some(catalog.clone());
    let request = "file_edit 스킬을 참고해서 notes/review_note.md에 리뷰 체크리스트를 써줘";
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "review", "steps": [
        {"action": "read_file", "args": {"path": "notes/review_note.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();

    // Before the consult the skill the request named is row one — the guidance
    // is what was asked for first, and nothing here changes that.
    assert_eq!(
        harness.runtime.rank_catalog(&ctx, request)[0].name,
        "skill.file_edit"
    );
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "skill.file_edit",
        "result": {"kind": "skill", "text": "Always read a file before editing it."},
    }));
    assert_eq!(
        harness.runtime.rank_catalog(&ctx, request)[0].name,
        "write_file",
        "guidance is in force and the declared file is still missing: {:?}",
        harness
            .runtime
            .rank_catalog(&ctx, request)
            .iter()
            .map(|entry| entry.name.clone())
            .collect::<Vec<_>>()
    );
    // A request that declared no file owes none, so nothing is boosted for it.
    let counting = harness.runtime.rank_catalog(&ctx, COUNT_REQUEST);
    assert_ne!(counting[0].name, "write_file", "{counting:?}");
}

#[tokio::test]
async fn a_counted_question_is_offered_the_exit_once_the_count_exists() {
    // The deliverable of a count question is the number. Before the listing
    // there is work to do and `final` is last; after it there is nothing to do
    // but say the figure, and a 0.5B that answers "1" must land on `final`
    // rather than on a `write_file` nobody asked for.
    let harness = listing_harness(&[]).await;
    let mut ctx = harness.context();
    let rows = |ctx: &AgentRunContext| -> Vec<String> {
        harness
            .runtime
            .rank_catalog(ctx, COUNT_REQUEST)
            .into_iter()
            .map(|entry| entry.name)
            .collect()
    };
    let before = rows(&ctx);
    assert_eq!(before.first().map(String::as_str), Some("list_dir"));
    assert_eq!(
        before.last().map(String::as_str),
        Some("final"),
        "{before:?}"
    );
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "list_dir",
        "args": {"path": "."}, "result": {"items": [{"name": "a"}, {"name": "b"}]}}));
    let after = rows(&ctx);
    assert_eq!(
        after.first().map(String::as_str),
        Some("final"),
        "{after:?}"
    );
    // And a request that never asked how many is untouched by this.
    let other: Vec<String> = harness
        .runtime
        .rank_catalog(&ctx, "notes/hello.md에 인사말을 써줘")
        .into_iter()
        .map(|entry| entry.name)
        .collect();
    assert_ne!(
        other.first().map(String::as_str),
        Some("final"),
        "{other:?}"
    );
}
