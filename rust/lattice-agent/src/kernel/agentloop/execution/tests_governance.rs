use super::tests::{write_action, FINAL};
use super::*;
use crate::kernel::agentloop::harness::harness;
use crate::kernel::policy::ToolPolicy;

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
    assert!(harness.tool_calls().is_empty());
}

#[tokio::test]
async fn a_staging_failure_is_an_error_step_not_a_silent_fallthrough() {
    let mut harness = harness(&[&write_action("a.md", "new"), FINAL]).await;
    std::fs::write(harness.root.join("a.md"), b"old").expect("file");
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
    assert!(
        harness.staged_items().is_empty(),
        "reviewing first would persist an orphan"
    );
    assert_eq!(ctx.rollback_log[0]["content"], "old");
    assert_eq!(ctx.rollback_log[0]["existed"], true);
}

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
