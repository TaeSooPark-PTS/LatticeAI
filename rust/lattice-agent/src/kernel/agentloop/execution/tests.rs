use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::policy::ToolPolicy;

pub(super) fn write_action(path: &str, content: &str) -> String {
    json!({"thoughts": "writing", "action": "write_file",
           "args": {"path": path, "content": content}})
    .to_string()
}

pub(super) const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

#[tokio::test]
async fn a_reply_that_copies_the_worked_example_writes_nothing() {
    // A live 2B handed a Korean request answered with the prompt's own example
    // — same path, same body — and the run wrote it, passed verification and
    // reported success over a file nobody asked for.
    let example = format!(
        r#"{{"thoughts": "t", "action": "write_file", "args": {{"path": "notes/hello.md", "content": {}}}}}"#,
        serde_json::to_string(crate::prompts::WRITE_EXAMPLE_CONTENT).expect("json")
    );
    let mut harness = super::super::harness::harness(&[&example]).await;
    let mut ctx = harness.context();
    ctx.state = crate::kernel::state::AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");

    assert!(
        !harness.root.join("notes/hello.md").exists(),
        "our own example must never reach the workspace"
    );
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("error").is_some())
        .expect("a refusal step");
    assert!(step["error"]
        .as_str()
        .expect("error")
        .contains("COPIED_EXAMPLE"));
    assert!(
        ctx.corrections.iter().any(|hint| hint
            .as_str()
            .unwrap_or_default()
            .contains("copied the example")),
        "the next attempt is told what happened: {:?}",
        ctx.corrections
    );

    // And the example's *path* alone is refused too.
    let path_only = r#"{"thoughts": "t", "action": "write_file", "args": {"path": "example.txt", "content": "a real document"}}"#;
    let mut harness = super::super::harness::harness(&[path_only]).await;
    let mut ctx = harness.context();
    ctx.state = crate::kernel::state::AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("worker");
    assert!(!harness.root.join("example.txt").exists());
}

#[tokio::test]
async fn a_write_then_final_is_two_steps_and_a_real_file() {
    let mut harness = harness(&[&write_action("note.md", "hello"), FINAL]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");

    assert_eq!(ctx.state, AgentState::Verifying);
    assert_eq!(ctx.transcript.len(), 2);
    assert_eq!(ctx.transcript[0]["action"], "write_file");
    assert_eq!(ctx.transcript[0]["result"]["path"], "note.md");
    assert_eq!(ctx.transcript[0]["risk"], "medium");
    assert_eq!(ctx.transcript[0]["governance"]["risk"], "write");
    assert_eq!(ctx.transcript[1]["action"], "final");
    assert_eq!(ctx.final_message, "done");
    assert_eq!(
        std::fs::read_to_string(harness.root.join("note.md")).expect("file"),
        "hello"
    );
    // The pre-write snapshot recorded that the file did not exist yet.
    assert_eq!(
        ctx.rollback_log,
        vec![json!({"path": "note.md", "existed": false,
                                             "content": null, "too_large": false})]
    );
}

#[tokio::test]
async fn the_budget_is_what_is_left_of_max_steps_not_max_steps() {
    // One executing step already on the transcript, max_steps 2 → one turn.
    let mut harness = harness(&[&write_action("a.md", "1"), &write_action("b.md", "2")]).await;
    harness.request.permission_mode = Some("trusted".into());
    harness.request.max_steps = 2;
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "read_file",
                               "result": {"ok": true}}));
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(harness.tool_calls().len(), 1, "one turn of budget was left");
    assert_eq!(ctx.state, AgentState::Verifying);
}

#[tokio::test]
async fn the_budget_never_drops_below_one_turn() {
    let mut harness = harness(&[FINAL]).await;
    harness.request.max_steps = 1;
    let mut ctx = harness.context();
    for _ in 0..5 {
        ctx.transcript
            .push(json!({"state": "EXECUTING", "action": "read_file"}));
    }
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.final_message, "done", "one turn still ran");
}

#[tokio::test]
async fn the_repeated_create_guard_halts_on_an_identical_reissue() {
    let action = write_action("a.md", "same");
    let mut harness = harness(&[&action, &action, FINAL]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.transcript.len(), 2);
    assert_eq!(
        ctx.transcript[1]["error"],
        "LOOP_DETECTED: identical action+args repeated — halted."
    );
    assert_eq!(harness.tool_calls().len(), 1, "the second write never ran");
    assert_eq!(ctx.state, AgentState::Verifying);
}

#[tokio::test]
async fn a_different_payload_is_not_a_repeat() {
    let mut harness = harness(&[
        &write_action("a.md", "one"),
        &write_action("a.md", "two"),
        FINAL,
    ])
    .await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(harness.tool_calls().len(), 2);
    assert_eq!(
        ctx.rollback_log.len(),
        1,
        "the first snapshot per path wins"
    );
}

#[tokio::test]
async fn scoped_knowledge_arguments_are_overwritten_by_the_server() {
    let mut harness = harness(&[&json!({"action": "knowledge_save",
         "args": {"content": "x", "workspace_id": "someone-elses",
                  "user_email": "attacker@example.com"}})
    .to_string()])
    .await;
    harness.request.permission_mode = Some("bypass".into());
    harness.request.workspace_id = Some("mine".into());
    harness.request.user_email = Some("owner@example.com".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    let sent = &harness.tool_calls()[0];
    assert_eq!(sent["args"]["workspace_id"], "mine");
    assert_eq!(sent["args"]["user_email"], "owner@example.com");
}

#[tokio::test]
async fn scoped_arguments_fall_back_to_personal_and_local() {
    let mut harness =
        harness(&[&json!({"action": "knowledge_search", "args": {}}).to_string()]).await;
    harness.request.permission_mode = Some("bypass".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    let sent = &harness.tool_calls()[0];
    assert_eq!(sent["args"]["workspace_id"], "personal");
    assert_eq!(sent["args"]["user_email"], "local");
}

#[tokio::test]
async fn clear_history_goes_through_the_seam_with_only_keep_last() {
    let mut harness = harness(&[
        &json!({"action": "clear_history", "args": {"keep_last": 5}}).to_string(),
        FINAL,
    ])
    .await;
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(harness.tool_calls()[0]["args"], json!({"keep_last": 5}));
    assert_eq!(ctx.transcript[0]["action"], "clear_history");
    assert!(ctx.transcript[0]["result"].is_object());
    assert!(ctx.transcript[0].get("governance").is_none(), "no gate ran");
}

#[tokio::test]
async fn a_final_with_no_message_gets_the_default_and_an_explicit_null_does_not() {
    let mut absent = harness(&[r#"{"action": "final"}"#]).await;
    let mut ctx = absent.context();
    absent
        .runtime
        .execute(&mut ctx, &absent.request)
        .await
        .expect("execute");
    assert_eq!(ctx.final_message, "작업을 완료했습니다.");

    let mut null = harness(&[r#"{"action": "final", "message": null}"#]).await;
    let mut ctx = null.context();
    null.runtime
        .execute(&mut ctx, &null.request)
        .await
        .expect("execute");
    assert_eq!(ctx.final_message, "", "a present null is not an absent key");
}

#[tokio::test]
async fn a_native_refusal_is_an_error_step_not_a_crash() {
    // The write runs in-process now, so this is the tool's own refusal —
    // and it lands on the transcript exactly where a seam error did.
    let mut harness = harness(&[&write_action("../escape.md", "x"), FINAL]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript[0]["error"],
        "Path escapes the agent workspace."
    );
    assert!(ctx.transcript[0].get("result").is_none());
    assert_eq!(ctx.trace.summary()["tool_outcomes"], json!({"error": 1}));
    assert!(
        !harness
            .root
            .parent()
            .expect("parent")
            .join("escape.md")
            .exists(),
        "nothing was written outside the workspace"
    );
}

#[tokio::test]
async fn a_seam_refusal_is_an_error_step_not_a_crash() {
    // A compute-only tool is still a worker call, and its refusal is
    // recorded the same way a native one is.
    let mut harness = harness(&[
        &json!({"action": "read_file", "args": {"path": "missing.md"}}).to_string(),
        FINAL,
    ])
    .await;
    harness
        .worker
        .tool_bodies
        .lock()
        .expect("lock")
        .insert("read_file".into(), json!({"error": "File does not exist."}));
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.transcript[0]["error"], "File does not exist.");
    assert!(ctx.transcript[0].get("result").is_none());
    assert_eq!(ctx.trace.summary()["tool_outcomes"], json!({"error": 1}));
}

/// The live gemma2b:S4 spiral: `run_command npm run build` refused thirteen
/// times in a row. A tool error is not a format error, so no budget in the JSON
/// loop was counting it and the run spent every step on the same refusal. The
/// floor the guided dial has always had now stands under both dials — and it
/// hands the run to the same escape hatch a spent format budget takes, which is
/// what still produces the file the plan asked for.
#[tokio::test]
async fn an_identical_failing_dispatch_stops_the_run_and_takes_the_escape_hatch() {
    let refused =
        json!({"action": "run_command", "args": {"command": "npm run build"}}).to_string();
    // Two refusals is the floor; the third completion is the direct write's.
    let mut harness = harness(&[&refused, &refused, "# 체크리스트\n\n- 읽고 나서 고친다\n"]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.permission_mode = Some("bypass".into());
    harness.runtime.deps.tool_names.push("run_command".into());
    harness
        .runtime
        .deps
        .policies
        .tools
        .insert("run_command".into(), ToolPolicy::default());
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "run_command".into(),
        json!({"error": "Command is not allowed: npm"}),
    );
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "write the checklist", "steps": [
        {"action": "write_file", "args": {"path": "notes/review_note.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");

    let refusals = ctx
        .transcript
        .iter()
        .filter(|step| step["error"] == json!("Command is not allowed: npm"))
        .count();
    assert_eq!(refusals, 2, "two identical refusals, then the run stops");
    assert!(
        ctx.transcript.iter().any(|step| step["error"]
            .as_str()
            .is_some_and(|error| error.starts_with("LOOP_DETECTED"))),
        "and it says so: {:?}",
        ctx.transcript
    );
    assert!(
        std::fs::read_to_string(harness.root.join("notes/review_note.md"))
            .expect("the plan's file was still produced")
            .contains("체크리스트")
    );
}

/// A live 2B answered `Unknown action: plan` by sending `plan` three more
/// times: the only feedback it got was buried in a transcript step. The
/// correction names the run's real catalog, once.
#[tokio::test]
async fn an_invented_action_name_is_answered_with_the_catalog_once() {
    let invented = json!({"action": "plan", "args": {"goal": "list the files"}}).to_string();
    let mut harness = harness(&[&invented, &invented]).await;
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    // The live step recorded `auto_approve: false` and dispatched anyway, so
    // the run was in `bypass`. Under the stricter modes the approval gate
    // refuses the unknown name first and the seam never gets to say it does
    // not exist — a different (already-tested) path.
    harness.request.permission_mode = Some("bypass".into());
    harness
        .worker
        .tool_bodies
        .lock()
        .expect("lock")
        .insert("plan".into(), json!({"error": "Unknown action: plan"}));
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.transcript[0]["error"], "Unknown action: plan");
    let hints: Vec<String> = ctx.corrections.iter().map(py_str).collect();
    assert_eq!(
        hints.len(),
        1,
        "the same slip twice is still one correction: {hints:?}"
    );
    assert!(
        hints[0].contains("does not have") && hints[0].contains("write_file"),
        "the correction names what the run actually offers: {}",
        hints[0]
    );
    assert!(
        !hints[0].contains("plan,"),
        "and never offers the invented name back: {}",
        hints[0]
    );
}
