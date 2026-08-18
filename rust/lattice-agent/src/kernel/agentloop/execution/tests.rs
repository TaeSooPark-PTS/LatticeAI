use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::policy::ToolPolicy;

fn write_action(path: &str, content: &str) -> String {
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

#[tokio::test]
async fn a_destructive_policy_is_blocked_in_every_mode() {
    for mode in ["strict", "trusted", "bypass"] {
        let mut harness = harness(&[
            &json!({"action": "delete_file", "args": {"path": "a.md"}}).to_string(),
            FINAL,
        ])
        .await;
        harness.runtime.deps.policies.tools.insert(
            "delete_file".into(),
            ToolPolicy {
                risk: "destructive".into(),
                destructive: true,
                ..ToolPolicy::default()
            },
        );
        harness.request.permission_mode = Some(mode.into());
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            ctx.transcript[0]["error"], "BLOCKED: destructive action is always blocked",
            "mode {mode}"
        );
        assert_eq!(ctx.transcript[0]["permission_mode"], mode);
        assert!(
            harness.tool_calls().is_empty(),
            "mode {mode} must not dispatch"
        );
    }
}

#[tokio::test]
async fn strict_blocks_an_ungoverned_write_at_the_approval_gate() {
    let mut harness = harness(&[
        &json!({"action": "run_command", "args": {"command": "ls"}}).to_string(),
        FINAL,
    ])
    .await;
    harness.runtime.deps.policies.tools.insert(
        "run_command".into(),
        ToolPolicy {
            risk: "exec".into(),
            ..ToolPolicy::default()
        },
    );
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript[0]["error"],
        "BLOCKED: action 'run_command' requires explicit approval (mode=strict)."
    );
    assert!(harness.tool_calls().is_empty());
    assert_eq!(harness.runtime.audit[0]["event"], "agent_exec");
}

#[tokio::test]
async fn an_unstageable_overwrite_fails_closed_even_under_bypass() {
    let mut harness = harness(&[
        &json!({"action": "create_docx", "args": {"filename": "report", "body": "x"}}).to_string(),
        FINAL,
    ])
    .await;
    std::fs::create_dir_all(harness.root.join("generated_documents")).expect("dir");
    std::fs::write(harness.root.join("generated_documents/report.docx"), b"old").expect("file");
    harness.runtime.deps.policies.tools.insert(
        "create_docx".into(),
        ToolPolicy {
            risk: "write".into(),
            ..ToolPolicy::default()
        },
    );
    harness.request.permission_mode = Some("bypass".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    let error = ctx.transcript[0]["error"].as_str().expect("error");
    assert!(error.starts_with("NEEDS_REVIEW: 'create_docx'"), "{error}");
    assert_eq!(ctx.transcript[0]["change_class"], "mutation");
    assert!(harness.tool_calls().is_empty(), "nothing was overwritten");
}

#[tokio::test]
async fn strict_stages_a_governed_mutation_as_a_proposal() {
    let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
    std::fs::write(harness.root.join("a.md"), b"old").expect("file");
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    // The proposal is staged **here** now (v11.6.0 §P1c), so the id on the
    // transcript is a real review item's and the item is on disk.
    let items = harness.staged_items();
    assert_eq!(items.len(), 1, "one staged proposal");
    let proposal_id = items[0]["id"].as_str().expect("id").to_string();
    assert_eq!(items[0]["source"], "change_proposal");
    assert_eq!(items[0]["status"], "pending");
    assert_eq!(items[0]["payload"]["path"], "a.md");
    assert_eq!(items[0]["payload"]["new_content"], "new");
    assert_eq!(
        items[0]["payload"]["base_sha256"],
        json!(crate::kernel::proposals::sha256_text("old"))
    );

    let staged = &ctx.transcript[0];
    assert_eq!(staged["result"]["proposed"], true);
    assert_eq!(staged["result"]["proposal_id"], json!(proposal_id));
    assert!(
        staged["args"].get("content").is_none(),
        "the payload is stripped"
    );
    assert_eq!(
        std::fs::read_to_string(harness.root.join("a.md")).expect("read"),
        "old"
    );
    assert_eq!(harness.runtime.audit[0]["event"], "agent_change_proposed");
    assert_eq!(harness.runtime.audit[0]["change_class"], "mutation");
    assert_eq!(harness.runtime.audit[0]["proposal_id"], json!(proposal_id));
    // Nothing crossed the seam: the retired `/agent/change-proposal` hop is
    // gone, and the write never ran.
    assert!(harness.tool_calls().is_empty());
}

#[tokio::test]
async fn a_staging_failure_is_an_error_step_not_a_silent_fallthrough() {
    let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
    std::fs::write(harness.root.join("a.md"), b"old").expect("file");
    // A data directory that cannot hold a file: the store's write fails.
    std::fs::write(&harness.data_dir, b"not a directory").expect("block");
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    let error = ctx.transcript[0]["error"].as_str().expect("error");
    assert!(error.starts_with("PROPOSAL_FAILED: "), "{error}");
    assert!(ctx.transcript[0].get("result").is_none());
    assert_eq!(
        harness.runtime.audit[0]["event"],
        "agent_change_proposal_failed"
    );
    assert_eq!(harness.runtime.audit[0]["path"], "a.md");
    // The file is untouched: a change that could not be staged is not a
    // change that gets applied.
    assert_eq!(
        std::fs::read_to_string(harness.root.join("a.md")).expect("read"),
        "old"
    );
    assert!(harness.tool_calls().is_empty());
}

#[tokio::test]
async fn trusted_applies_a_governed_mutation_and_audits_it_instead() {
    let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
    std::fs::write(harness.root.join("a.md"), b"old").expect("file");
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        std::fs::read_to_string(harness.root.join("a.md")).expect("read"),
        "new"
    );
    assert_eq!(
        harness.runtime.audit[0]["event"],
        "agent_change_auto_applied"
    );
    // The governor was never consulted, so no orphan proposal was left.
    assert!(
        harness.staged_items().is_empty(),
        "reviewing first would persist an orphan"
    );
    // The snapshot captured the pre-run bytes, so rollback has something.
    assert_eq!(ctx.rollback_log[0]["content"], "old");
    assert_eq!(ctx.rollback_log[0]["existed"], true);
}

// ── the executor prompt and what surrounds it (v11.9.0) ─────────────────
/// A run five steps in: a plan, five results, a written file, a
/// conversation. This is the shape the compact profile was drowning in.
fn five_steps_in(harness: &crate::kernel::agentloop::harness::Harness) -> AgentRunContext {
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "build the page", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}, "description": "the page"},
        {"action": "write_file", "args": {"path": "style.css"}, "description": "the styles"}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    for index in 0..5 {
        ctx.transcript.push(json!({
            "state": "EXECUTING", "action": "read_file",
            "args": {"path": format!("src/module_{index}.js")},
            "result": {"path": format!("src/module_{index}.js"),
                       "content": "x".repeat(4_000)},
        }));
    }
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file", "args": {"path": "index.html"},
        "result": {"path": "index.html", "bytes": 40},
    }));
    ctx
}

#[tokio::test]
async fn execute_uses_the_profile_temperature_not_the_request_default() {
    // Before: EXECUTE forwarded req.temperature (serde default 0.1) for
    // every profile. After: compact 0.1 / standard 0.2, even if the
    // request carries something else. PLAN/VERIFY temps are unchanged.
    for (profile, expected) in [
        (crate::kernel::profile::COMPACT, 0.1),
        (crate::kernel::profile::STANDARD, 0.2),
    ] {
        let mut harness = harness(&[r#"{"action": "final", "message": "done"}"#]).await;
        harness.runtime.deps.agent_profile = Some(profile);
        harness.request.temperature = 0.9;
        let mut ctx = harness.context();
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        let asks = harness.worker.calls.lock().expect("lock").clone();
        let execute = asks
            .iter()
            .find(|call| call["seam"] == json!("llm"))
            .expect("one execute call");
        assert_eq!(
            execute["body"]["temperature"],
            json!(expected),
            "{}",
            profile.name
        );
    }
}

#[tokio::test]
async fn a_run_with_no_prompt_library_still_gets_the_action_contract() {
    // The v11.9.0 bug in one assertion: this used to open with a blank line
    // and the model was never told what a reply looked like.
    let harness = harness(&[]).await;
    let ctx = harness.context();
    let context =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::STANDARD);
    assert!(context.starts_with("You are the executor"), "{context}");
    assert!(context.contains("EXACTLY ONE JSON object"));
    assert!(context.contains("- write_file{path, content}"), "{context}");
    assert!(context.contains("<!doctype html>"));
}

#[tokio::test]
async fn a_caller_supplied_prompt_wins_outright() {
    let mut harness = harness(&[]).await;
    harness.runtime.deps.prompts.executor = "HOST PROMPT".into();
    let ctx = harness.context();
    let context =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::COMPACT);
    assert!(context.starts_with("HOST PROMPT"));
    assert!(!context.contains("EXACTLY ONE JSON object"));
}

#[tokio::test]
async fn declared_skills_reach_the_executor_prompt() {
    let mut harness = harness(&[]).await;
    harness.request.skills = vec![crate::prompts::SkillBrief {
        name: "release-manager".into(),
        brief: "prepare a release".into(),
        when: "the user asks to ship".into(),
    }];
    let ctx = harness.context();
    let context =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::STANDARD);
    assert!(context.contains("Available skills:\n- release-manager: prepare a release"));
}

#[tokio::test]
async fn the_compact_context_drops_the_framing_the_transcript_already_carries() {
    let mut harness = harness(&[]).await;
    harness.request.recent_conversation = Some("USER: hi\nASSISTANT: hello".into());
    let ctx = five_steps_in(&harness);

    let standard =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::STANDARD);
    assert!(standard.contains("Recent conversation:"));
    assert!(standard.contains("Files written by this run so far"));

    let compact =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::COMPACT);
    assert!(!compact.contains("Recent conversation:"));
    assert!(!compact.contains("Files written by this run so far"));
    assert!(!compact.contains("USER: hi"));
    // The plan and the transcript still carry every fact that was dropped.
    assert!(compact.contains("index.html"), "the plan is still there");
    assert!(compact.contains("Execution transcript:"));
}

#[tokio::test]
async fn the_compact_context_stays_small_enough_for_a_2b_model_at_step_five() {
    let mut harness = harness(&[]).await;
    harness.request.recent_conversation = Some("USER: ".to_string() + &"chat ".repeat(400));
    let ctx = five_steps_in(&harness);
    let standard =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::STANDARD);
    let compact =
        harness
            .runtime
            .executor_context(&ctx, &harness.request, crate::kernel::profile::COMPACT);
    // ~4 characters per token for this mix of English prose and JSON, so
    // 8,000 characters is the ~2k-token target. The standard profile is
    // over it on the same run — which is the measurement this dial exists
    // because of, and why it is asserted from both sides.
    assert!(
        compact.len() < 8_000,
        "compact context is {} chars",
        compact.len()
    );
    assert!(
        standard.len() > 8_000,
        "standard context is {} chars — the fixture stopped being the \
problem this compares against",
        standard.len()
    );
    assert!(
        compact.len() * 2 < standard.len(),
        "compact {} vs standard {}",
        compact.len(),
        standard.len()
    );
}

#[tokio::test]
async fn a_write_to_a_blocked_prefix_is_rewritten_into_a_destructive_denial() {
    let mut harness = harness(&[
        &json!({"action": "write_file", "args": {"path": "/etc/hosts", "content": "x"}})
            .to_string(),
        FINAL,
    ])
    .await;
    harness.request.permission_mode = Some("bypass".into());
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.transcript[0]["governance"]["risk"], "destructive");
    assert!(ctx.transcript[0]["error"]
        .as_str()
        .expect("error")
        .starts_with("BLOCKED: "));
    assert!(harness.tool_calls().is_empty());
}

#[tokio::test]
async fn a_file_that_is_just_our_instruction_is_never_written() {
    let leaked = "이미지·머리말·코드블록 금지不予提供。";
    let mut harness = harness(&[&write_action("notes/hello.md", leaked)]).await;
    harness.request.permission_mode = Some("bypass".into());
    let mut ctx = harness.context();
    ctx.state = AgentState::Executing;
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert!(
        !harness.root.join("notes/hello.md").exists(),
        "our own instruction must never reach the workspace"
    );
    let step = ctx
        .transcript
        .iter()
        .find(|step| step.get("error").is_some())
        .expect("a refusal");
    assert!(
        step["error"]
            .as_str()
            .expect("error")
            .starts_with("COPIED_"),
        "{step:?}"
    );
}
