use serde_json::{json, Value};

use super::tests_menu::FakeCatalog;
use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::profile::GUIDED;
use crate::kernel::state::AgentState;
use crate::kernel::transcript::result_digest;

#[test]
fn an_absolute_plan_path_is_offered_the_short_way() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let absolute = workspace.root().join("notes/hello.md");
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "write_file", "args": {"path": absolute}}]})
        .as_object()
        .expect("plan")
        .clone();
    let entry = crate::tools::catalog::native_entries(&["write_file".to_string()])
        .into_iter()
        .next()
        .expect("write_file");
    let req = crate::kernel::agentloop::RunRequest::default();
    assert_eq!(
        runtime.suggested_arg(&ctx, &req, &entry, "path"),
        Some("notes/hello.md".to_string()),
        "the same file, said in four tokens instead of a hundred characters"
    );
    // A path outside the workspace is left exactly as the plan wrote it.
    ctx.plan = json!({"steps": [{"action": "write_file", "args": {"path": "/etc/hosts"}}]})
        .as_object()
        .expect("plan")
        .clone();
    assert_eq!(
        runtime.suggested_arg(&ctx, &req, &entry, "path"),
        Some("/etc/hosts".to_string())
    );
}

#[tokio::test]
async fn a_model_that_only_copies_the_example_is_demoted_into_guided() {
    // The live 2B: probed as `compact`, then spent its whole format budget
    // replying with the prompt's own worked example. The probe asked a toy
    // question; the run is better evidence, and the loop is allowed to act on
    // it — downward only.
    let example = format!(
        r#"{{"thoughts": "t", "action": "write_file", "args": {{"path": "notes/hello.md", "content": {}}}}}"#,
        serde_json::to_string(crate::prompts::WRITE_EXAMPLE_CONTENT).expect("json")
    );
    let mut script: Vec<&str> = Vec::new();
    for _ in 0..6 {
        script.push(&example);
    }
    // …and then the guided micro-turns it is demoted into.
    script.push("1");
    script.push("notes/hello.md");
    script.push("진짜 인사말입니다.");
    script.push("3");
    script.push("메모를 만들었습니다");
    let mut harness = harness(&script).await;
    // Measured, not injected: demotion corrects a *measurement*, so a caller
    // that stated the dial keeps it.
    harness.runtime.deps.agent_profile = None;
    harness.runtime.deps.probe = Some(crate::kernel::probe::ProbeConfig::new(
        harness.data_dir.join("probe"),
    ));
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let written = std::fs::read_to_string(harness.root.join("notes/hello.md"))
        .expect("the demoted run still finished the work");
    assert!(written.contains("진짜 인사말"), "{written:?}");
    assert!(
        ctx.trace.events.iter().any(|event| {
            event["kind"] == json!("decision") && event["decision"] == json!("profile_demoted")
        }),
        "the demotion is on the record: {:?}",
        ctx.trace.events
    );
    // And verification now runs under the dial the executor finished in.
    assert!(harness.runtime.profile(None).decomposed);
}

#[tokio::test]
async fn an_injected_profile_is_never_demoted() {
    let example = format!(
        r#"{{"thoughts": "t", "action": "write_file", "args": {{"path": "a.md", "content": {}}}}}"#,
        serde_json::to_string(crate::prompts::WRITE_EXAMPLE_CONTENT).expect("json")
    );
    let script: Vec<&str> = (0..8).map(|_| example.as_str()).collect();
    let mut harness = harness(&script).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.runtime.deps.probe = Some(crate::kernel::probe::ProbeConfig::new(
        harness.data_dir.join("probe"),
    ));
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(
        !ctx.trace
            .events
            .iter()
            .any(|event| event["decision"] == json!("profile_demoted")),
        "a caller that stated the dial keeps it"
    );
}

#[test]
fn a_finished_plan_puts_finishing_first_and_stops_re_offering_the_write() {
    // The live 0.5B wrote its file, was offered `write_file` as row one again,
    // answered "1" again, and spent the rest of the run being stopped by the
    // loop guard instead of finishing.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["read_file".into(), "write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "write_file", "args": {"path": "notes/hello.md"}}]})
        .as_object()
        .expect("plan")
        .clone();

    // Before the write: the plan leads, `final` is last.
    let names: Vec<String> = runtime
        .step_catalog(&ctx)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names.first().map(String::as_str), Some("write_file"));
    assert_eq!(names.last().map(String::as_str), Some("final"));

    // After it: nothing is pending, so finishing leads.
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "notes/hello.md"},
        "result": {"path": "notes/hello.md", "bytes": 12},
    }));
    let names: Vec<String> = runtime
        .step_catalog(&ctx)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(
        names.first().map(String::as_str),
        Some("final"),
        "{names:?}"
    );
    assert_eq!(
        names.iter().filter(|name| *name == "final").count(),
        1,
        "on the menu exactly once, wherever it sits"
    );
    assert!(names.contains(&"write_file".to_string()), "still reachable");
}

#[test]
fn the_plans_own_action_is_row_one_and_the_alphabet_decides_nothing() {
    // The live 0.5B defect this exists to stop: with the policy table in
    // alphabetical order `build_project` was row one, and a model that answers
    // "1" when unsure ran it fifteen times against a plan that said
    // `write_file`.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec![
        "build_project".into(),
        "computer_click".into(),
        "deploy_project".into(),
        "read_file".into(),
        "run_command".into(),
        "write_file".into(),
    ];
    let mut ctx = crate::kernel::state::AgentRunContext::new();

    // With no plan, the file-work core leads and `build_project` does not.
    let names: Vec<String> = runtime
        .step_catalog(&ctx)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "write_file", "{names:?}");
    assert_eq!(names[1], "read_file", "{names:?}");
    assert_eq!(names.last().map(String::as_str), Some("final"));

    // With a plan, the plan leads.
    ctx.plan = json!({"steps": [{"action": "run_command", "args": {}}]})
        .as_object()
        .expect("plan")
        .clone();
    let names: Vec<String> = runtime
        .step_catalog(&ctx)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "run_command", "{names:?}");
    assert_eq!(names[1], "write_file", "{names:?}");
}

#[tokio::test]
async fn the_same_call_failing_twice_stops_the_run_instead_of_spinning() {
    // Fifteen identical failing dispatches is what the first live 0.5B run
    // produced. Two is the ceiling now.
    let script: Vec<&str> = ["1", "../escape.md", "x"]
        .iter()
        .cycle()
        .take(30)
        .copied()
        .collect();
    let mut harness = harness(&script).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let errors = ctx
        .transcript
        .iter()
        .filter(|step| step.get("error").is_some())
        .count();
    assert!(
        errors <= 3,
        "{errors} identical failures: {:?}",
        ctx.transcript
    );
    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .unwrap_or_default()
            .contains("LOOP_DETECTED")),
        "{:?}",
        ctx.transcript
    );
}

#[test]
fn the_menu_is_capped_and_still_carries_the_plan_and_final() {
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = (0..30).map(|index| format!("tool_{index:02}")).collect();
    runtime.deps.tool_names.push("write_file".into());
    runtime.deps.external = Some(FakeCatalog::new());
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "tool_17", "args": {}}]})
        .as_object()
        .expect("plan")
        .clone();

    let menu = runtime.step_catalog(&ctx);
    assert!(menu.len() <= MENU_LIMIT, "{}", menu.len());
    let names: Vec<&str> = menu.iter().map(|entry| entry.name.as_str()).collect();
    assert_eq!(names.last(), Some(&"final"), "final is always reachable");
    assert_eq!(names[0], "tool_17", "the plan's own tool leads");
    assert!(names.contains(&"write_file"), "the writer survives");
    assert!(
        names.iter().any(|name| name.starts_with("skill.")),
        "one row per kind survives so skills never vanish: {names:?}"
    );
}

#[tokio::test]
async fn the_guided_verdict_is_a_closed_question_and_still_needs_evidence() {
    // PASS over a run that did nothing is not a completion, however cheaply the
    // verdict was obtained.
    let mut harness = harness(&["PASS", "the file was written"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(
        ctx.state,
        AgentState::NeedsReview,
        "no execution evidence, so no DONE"
    );
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("verdict").is_some())
        .expect("a verdict step");
    assert_eq!(step["verdict"], json!("PASS"));
    assert_eq!(step["verdict_source"], json!("guided"));
    assert_eq!(step["confidence"], json!(0.6));
}

#[tokio::test]
async fn a_guided_fail_asks_for_another_attempt_with_the_reason_as_the_correction() {
    let mut harness = harness(&["FAIL", "the file is empty"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "a.md"}, "result": {"path": "a.md", "bytes": 0},
    }));
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(ctx.state, AgentState::Executing);
    assert_eq!(ctx.corrections, vec![json!("the file is empty")]);
}

#[tokio::test]
async fn the_verdict_examples_placeholder_reason_is_never_shown_as_a_reason() {
    // A live 2B returned the example's every field, placeholder included.
    let copied = format!(
        r#"{{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "{}", "corrections": []}}"#,
        crate::prompts::VERDICT_REASON_PLACEHOLDER
    );
    let mut harness = harness(&[&copied]).await;
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "a.md"}, "result": {"path": "a.md", "bytes": 5},
    }));
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("verdict").is_some())
        .expect("a verdict step");
    assert_eq!(step["verdict"], json!("PASS"));
    assert_eq!(step["reason"], json!(""), "a placeholder is not a reason");
}

#[tokio::test]
async fn an_unreadable_verdict_word_falls_through_to_the_json_critic() {
    let mut harness = harness(&[
        "hmm, hard to say",   // no verdict word
        "still hard to say",  // and none on the re-ask either
        r#"{"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok", "corrections": []}"#,
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "a.md"}, "result": {"path": "a.md", "bytes": 5},
    }));
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("verdict").is_some())
        .expect("a verdict step");
    assert_eq!(step["verdict"], json!("PASS"));
    assert_eq!(
        step.get("verdict_source"),
        None,
        "the JSON chain answered, so no guided stamp"
    );
}

/// The menu turn's fix, on its twin. A model whose first eight tokens are a
/// reasoning preamble produced no verdict word in four live runs and the work
/// it had really done ended `UNAVAILABLE`. The re-ask buys room and drops the
/// line stop, and the answer's position is forced by a prefix carrying neither
/// word.
#[tokio::test]
async fn a_verdict_turn_that_produced_no_word_is_re_asked_with_room() {
    let mut harness = harness(&[
        "Thinking Process: the user asked", // the preamble, and the budget gone
        "PASS",
        "파일을 만들었습니다",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "a.md"}, "result": {"path": "a.md", "bytes": 5},
    }));
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert_eq!(ctx.state, AgentState::Done);
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("verdict").is_some())
        .expect("a verdict step");
    assert_eq!(step["verdict_source"], json!("guided"));
    let asks = harness.worker.calls.lock().expect("lock").clone();
    let verdict_turns: Vec<&Value> = asks
        .iter()
        .filter(|call| call["body"]["message"] == json!(crate::prompts::guided::VERDICT_QUESTION))
        .collect();
    assert_eq!(verdict_turns.len(), 2, "asked, then re-asked");
    assert_eq!(
        verdict_turns[0]["body"]["prefix"],
        json!(crate::prompts::guided::VERDICT_ANSWER_PREFIX),
        "the answer's position is forced on the first turn too"
    );
    assert_eq!(verdict_turns[0]["body"]["max_tokens"], json!(MENU_TOKENS));
    assert_eq!(
        verdict_turns[1]["body"]["max_tokens"],
        json!(MENU_RETRY_TOKENS),
        "the re-ask buys room"
    );
    assert_eq!(
        verdict_turns[0]["body"]["stop"],
        json!(["\n"]),
        "the first turn keeps the line stop"
    );
    assert_eq!(
        verdict_turns[1]["body"].get("stop"),
        None,
        "and the re-ask drops the line stop that cut the preamble short"
    );
}

#[tokio::test]
async fn an_empty_path_answer_takes_the_plans_own_file() {
    let mut harness = harness(&["1", "", "본문", "3", "done"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    let mut ctx = harness.context();
    ctx.plan = json!({
        "goal": "메모 만들기",
        "steps": [{"action": "write_file", "args": {"path": "notes/from_plan.md"}}],
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
        harness.root.join("notes/from_plan.md").exists(),
        "an unanswered path falls back to the plan's, never to an invented one"
    );
}

// ── v12.0.0 fix round: the menu turn, the evidence, and the ranking ─────────

#[tokio::test]
async fn the_menu_turn_is_prefilled_and_a_re_ask_is_a_different_question() {
    // The gemma-4-e2b defect, twelve times out of twelve: eight tokens, a
    // newline stop, no prefill — the model spent the whole budget on
    // `Thinking Process:` and the parser never saw a digit. Three identical
    // turns followed, because at temperature zero an identical question has an
    // identical answer.
    let mut harness = harness(&[
        "Thinking Process:",
        "Thinking Process:",
        "Thinking Process:",
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

    let calls = harness.worker.calls.lock().expect("lock").clone();
    let menu: Vec<&Value> = calls
        .iter()
        .filter(|call| call["body"]["message"] == json!(crate::prompts::guided::MENU_QUESTION))
        .collect();
    assert!(
        menu.len() >= 3,
        "the profile's micro-turn cap: {}",
        menu.len()
    );
    assert_eq!(menu.len() % 3, 0, "whole steps, {}", menu.len());
    for (index, turn) in menu.iter().enumerate() {
        assert_eq!(
            turn["body"]["prefix"],
            json!(crate::prompts::guided::MENU_ANSWER_PREFIX),
            "every menu turn puts the answer's opening in the model's mouth"
        );
        // Each step restarts the cap, so the first turn of every three is the
        // cheap ask and the two after it are the re-asks.
        if index % 3 == 0 {
            assert_eq!(
                turn["body"]["max_tokens"],
                json!(MENU_TOKENS),
                "turn {index}"
            );
            assert_eq!(turn["body"]["stop"], json!(["\n"]), "turn {index}");
        } else {
            assert_eq!(
                turn["body"]["max_tokens"],
                json!(MENU_RETRY_TOKENS),
                "turn {index}: a re-ask buys room, or it is the first ask replayed"
            );
            assert_eq!(
                turn["body"]["stop"],
                Value::Null,
                "turn {index}: and drops the line stop"
            );
        }
    }
}

#[test]
fn a_result_digest_only_ever_reads_a_field_the_tool_wrote() {
    assert_eq!(
        result_digest(&json!({"items": [1, 2]})).as_deref(),
        Some("항목 2개 / 2 items")
    );
    assert_eq!(
        result_digest(&json!({"matches": 4})).as_deref(),
        Some("matches 4")
    );
    assert_eq!(
        result_digest(&json!({"files_with_matches": ["a", "b", "c"]})).as_deref(),
        Some("files_with_matches 3개 / 3 files_with_matches")
    );
    assert_eq!(
        result_digest(&json!({"path": "notes/hello.md", "bytes": 12})).as_deref(),
        Some("notes/hello.md, 12 bytes")
    );
    assert_eq!(
        result_digest(&json!({"text": "  Always read a file first.  "})).as_deref(),
        Some("\"Always read a file first.\"")
    );
    assert_eq!(
        result_digest(&json!({"path": "a.md"})).as_deref(),
        Some("a.md")
    );
    // Nothing countable: the row stays the bare `ok` it always was.
    assert_eq!(result_digest(&json!({"ok": true})), None);
    assert_eq!(result_digest(&json!({})), None);
}

#[tokio::test]
async fn the_closed_question_is_shown_the_count_and_the_answer() {
    // The S3 defect: `- list_dir: ok` is not enough to answer "was the count
    // reported?", so three models answered FAIL over a list_dir that had
    // returned two real entries.
    let mut harness = harness(&["PASS", "the folder was listed"]).await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "list_dir", "args": {"path": "."},
        "result": {"items": [{"name": "notes"}, {"name": "README.md"}]},
    }));
    ctx.final_message = "이 폴더에는 파일이 2개 있습니다.".into();
    ctx.state = AgentState::Verifying;
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let context = harness.worker.calls.lock().expect("lock")[0]["body"]["context"]
        .as_str()
        .expect("context")
        .to_string();
    assert!(
        context.contains("- list_dir .: ok — 항목 2개 / 2 items"),
        "{context}"
    );
    assert!(
        context.contains("THE ANSWER THIS RUN WILL GIVE"),
        "{context}"
    );
    assert!(context.contains("이 폴더에는 파일이 2개 있습니다."));
}

#[test]
fn a_planned_read_that_already_succeeded_is_not_offered_again() {
    // The live 2B read README.md, was offered the read as row one again, and
    // never reached the write it was asked for.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["read_file".into(), "write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [
        {"action": "read_file", "args": {"path": "README.md"}},
        {"action": "write_file", "args": {"path": "notes/summary.md"}},
    ]})
    .as_object()
    .expect("plan")
    .clone();
    let request = "README.md 첫 문단을 요약해 notes/summary.md로 저장해줘";

    let before: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(before[0], "read_file", "{before:?}");

    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "read_file",
        "args": {"path": "README.md"}, "result": {"path": "README.md", "bytes": 40},
    }));
    let after: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(
        after[0], "write_file",
        "the pending write is what comes next: {after:?}"
    );
    assert_ne!(
        after[0], "final",
        "a declared output that is not on disk is not a finished plan"
    );
}

#[test]
fn a_request_that_declared_no_file_may_finish_once_its_search_ran() {
    // The other direction, and the live 0.5B that lost a whole run to it: the
    // planner invented a `write_file` step for "find the word and tell me how
    // many", so `final` stayed off row one and the model answered "1" into the
    // invented write until the budget ran out.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["grep".into(), "write_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [
        {"action": "grep", "args": {"pattern": "LatticeAI"}},
        {"action": "write_file", "args": {"path": "invented.md"}},
    ]})
    .as_object()
    .expect("plan")
    .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "grep",
        "args": {"pattern": "LatticeAI"}, "result": {"matches": 0},
    }));
    let names: Vec<String> = runtime
        .rank_catalog(
            &ctx,
            "워크스페이스에서 LatticeAI라는 단어를 찾아주고, 찾은 개수를 알려줘",
        )
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(
        names[0], "final",
        "the request named no output file, so the search was the work: {names:?}"
    );
}

// ── round 1: the exit, the answer, and the body ────────────────────────────

#[test]
fn a_row_name_inside_a_paragraph_is_not_a_choice() {
    // The regression this exists to stop, and the whole reason three live 0.5B
    // cells ended on turn one: the re-asked menu turn buys sixty-four tokens
    // and drops the line stop, so a model that will not commit answers with
    // prose — and `final` is an ordinary English word that happens to be a row.
    let catalog =
        crate::tools::catalog::native_entries(&["write_file".to_string(), "read_file".to_string()]);
    assert_eq!(
        named_choice(
            "To decide, I would first consider what the user wants, and the final \
answer depends on whether the file already exists in the workspace.",
            &catalog
        ),
        None,
        "a paragraph is not an answer, whatever words it contains"
    );
    // A reply that really names a row still is one, however it is dressed.
    let write = catalog
        .iter()
        .position(|entry| entry.name == "write_file")
        .expect("write_file")
        + 1;
    assert_eq!(named_choice("write_file", &catalog), Some(write));
    assert_eq!(named_choice("I'll use write_file", &catalog), Some(write));
    assert_eq!(named_choice("`write_file`", &catalog), Some(write));
    let finish = catalog
        .iter()
        .position(|entry| entry.name == "final")
        .expect("final")
        + 1;
    assert_eq!(named_choice("final", &catalog), Some(finish));
    // Whole tokens only: a longer word that contains a row name is not it.
    assert_eq!(named_choice("finalize the document", &catalog), None);
    // Two rows named is no choice, as it always was.
    assert_eq!(named_choice("write_file or read_file", &catalog), None);
}

#[test]
fn a_run_that_owes_a_declared_file_is_not_offered_the_exit() {
    // Three live 0.5B cells, and every one of them a request that named a
    // destination file: the first menu turn resolved to `final`, nothing was
    // written, and the run reported itself finished. `final` was already ranked
    // last; last is still a row.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into(), "list_dir".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "write_file", "args": {"path": "notes/hello.md"}}]})
        .as_object()
        .expect("plan")
        .clone();
    let request = "인사말을 notes/hello.md 파일로 저장해줘";

    let before: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(before[0], "write_file", "{before:?}");
    assert!(
        !before.iter().any(|name| name == "final"),
        "the file the request named is not on disk: {before:?}"
    );

    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "notes/hello.md"},
        "result": {"path": "notes/hello.md", "bytes": 12},
    }));
    let after: Vec<String> = runtime
        .rank_catalog(&ctx, request)
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(
        after[0], "final",
        "delivered means finishing is what comes next: {after:?}"
    );
}

#[test]
fn a_request_that_named_no_file_keeps_its_exit_from_the_first_turn() {
    // The other direction, stated so the guard can never widen into one: a
    // question is finished when it is answered, and holding `final` back for a
    // run that owes no file would strand every count and every search.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "list_dir".into()];
    let ctx = crate::kernel::state::AgentRunContext::new();
    let names: Vec<String> = runtime
        .rank_catalog(
            &ctx,
            "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘",
        )
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert!(names.iter().any(|name| name == "final"), "{names:?}");
    assert_eq!(names.last().map(String::as_str), Some("final"), "{names:?}");
}

#[test]
fn a_planners_invented_path_does_not_outlive_the_file_the_user_asked_for() {
    // The live 2B: handed `인사말을 notes/hello.md 파일로 저장해줘` its planner
    // wrote `write_file notes.md`. The run wrote the file the *request* named,
    // the plan's invented path stayed pending for ever, `write_file` stayed row
    // one, the model answered "1" again and the loop guard ended a run whose
    // deliverable had been on disk since step one.
    let (_dir, workspace) = crate::kernel::agentloop::harness::workspace();
    let mut runtime = crate::kernel::agentloop::harness::runtime(workspace);
    runtime.deps.tool_names = vec!["write_file".into(), "read_file".into()];
    let mut ctx = crate::kernel::state::AgentRunContext::new();
    ctx.plan = json!({"steps": [{"action": "write_file", "args": {"path": "notes.md"}}]})
        .as_object()
        .expect("plan")
        .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file",
        "args": {"path": "notes/hello.md"},
        "result": {"path": "notes/hello.md", "bytes": 59},
    }));
    let names: Vec<String> = runtime
        .rank_catalog(&ctx, "인사말을 notes/hello.md 파일로 저장해줘")
        .iter()
        .map(|entry| entry.name.clone())
        .collect();
    assert_eq!(names[0], "final", "{names:?}");
    assert_ne!(names[0], "write_file", "{names:?}");
}

#[tokio::test]
async fn a_body_turn_that_answers_with_its_own_reasoning_is_re_asked_differently() {
    // The live gemma-4-e2b: three body turns, three enumerated
    // `Thinking Process:` monologues, 2,371 bytes of the model talking to
    // itself written into the user's greeting file.
    let mut harness = harness(&[
        "1", // menu → write_file
        "notes/hello.md",
        "Thinking Process:\n\n1.  **Analyze the Request:** the user wants a greeting.\n\
2.  **Determine the Action:** I cannot actually create files.",
        "안녕하세요, 반갑습니다.",
        "1", // menu → final (the declared file now exists)
        "저장했습니다.",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.request.message = "인사말을 notes/hello.md 파일로 저장해줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    let written = std::fs::read_to_string(harness.root.join("notes/hello.md")).expect("file");
    assert_eq!(written.trim(), "안녕하세요, 반갑습니다.", "{written:?}");

    // And the re-ask was a different question, or it was the first ask replayed.
    let bodies: Vec<String> = harness
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
        .filter(|context| context.contains("요청 / REQUEST:"))
        .collect();
    assert!(bodies.len() >= 2, "{bodies:?}");
    assert!(
        !bodies[0].contains(crate::prompts::guided::BODY_RETRY_NOTE),
        "the first ask carries no correction: {:?}",
        bodies[0]
    );
    assert!(
        bodies[1].contains(crate::prompts::guided::BODY_RETRY_NOTE),
        "the re-ask names what went wrong: {:?}",
        bodies[1]
    );
}

#[tokio::test]
async fn a_body_that_is_only_ever_reasoning_is_still_written_rather_than_lost() {
    // The floor under the re-ask. A model that cannot do better on any turn has
    // still produced the only text there is, and turning a poor file into a
    // missing one is worse for the user and for the run's own coverage.
    let preamble = "Thinking Process:\n\n1.  **Analyze:** the user wants a greeting.";
    let mut harness = harness(&[
        "1",
        "notes/hello.md",
        preamble,
        preamble,
        preamble,
        "1",
        "저장했습니다.",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(GUIDED);
    harness.request.message = "인사말을 notes/hello.md 파일로 저장해줘".into();
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    let written = std::fs::read_to_string(harness.root.join("notes/hello.md")).expect("file");
    assert!(written.starts_with("Thinking Process:"), "{written:?}");
    assert!(
        ctx.trace
            .events
            .iter()
            .any(|event| event["decision"] == json!("guided_body_fallback")),
        "the fallback is on the record, not silent: {:?}",
        ctx.trace.events
    );
}
