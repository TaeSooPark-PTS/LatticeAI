use super::*;
use crate::kernel::agentloop::harness::harness;

const FINAL: &str = r#"{"action": "final", "message": "done"}"#;

#[tokio::test]
async fn the_compact_fallback_writes_the_planned_files_without_json() {
    // Four *different* prose replies: the budget is spent the ordinary way,
    // with no repeat escalation — that path has its own test below.
    let mut harness = harness(&[
        "prose one",
        "prose two",
        "prose three",
        "prose four",
        "# the file body",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "make a note", "steps": [
        {"action": "write_file", "args": {"path": "note.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.state, AgentState::Verifying);
    assert_eq!(
        std::fs::read_to_string(harness.root.join("note.md")).expect("file"),
        "# the file body"
    );
    let written = ctx.transcript.last().expect("step");
    assert_eq!(written["direct_path"], true);
    assert_eq!(written["generation"], json!({"repaired": false}));
    assert!(ctx.final_message.contains("직접 생성했습니다"));
}

#[tokio::test]
async fn a_repeated_reply_buys_exactly_one_extra_attempt() {
    // The same rejected reply four times, then a valid action. Under the
    // ported budget the run stopped at the fourth and never saw the fifth;
    // the repeat escalation buys one more turn, and the run finishes.
    let mut harness = harness(&["prose", "prose", "prose", "prose", FINAL]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(ctx.final_message, "done", "the fifth reply was reached");
    let slips: Vec<&Value> = ctx
        .transcript
        .iter()
        .filter(|step| step["action"] == json!("parse_error"))
        .collect();
    assert_eq!(slips.len(), 4, "budget 4, all spent");
    assert_eq!(
        slips
            .iter()
            .filter(|step| step.get("repeated").is_some())
            .count(),
        3,
        "the first sighting is not a repeat"
    );
    // The correction says so, and only once.
    let repeated_hints = ctx
        .corrections
        .iter()
        .filter(|hint| py_str(hint).contains("You already sent exactly this reply"))
        .count();
    assert_eq!(repeated_hints, 1);
}

#[tokio::test]
async fn the_extra_attempt_is_bought_once_and_never_again() {
    // Nine identical replies against a budget of four. Without a ceiling
    // the escalation would renew itself forever; with one, the run stops
    // after five and falls through to the direct path.
    let mut harness = harness(&["prose"; 9]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript
            .iter()
            .filter(|step| step["action"] == json!("parse_error"))
            .count(),
        5,
        "four plus the one extra, and no more"
    );
}

#[tokio::test]
async fn a_fenced_file_in_a_chat_reply_becomes_the_write_the_plan_asked_for() {
    let reply = "Here is your page:\n\n```html\n<!doctype html>\n<html><body>\
<h1>Hi</h1></body></html>\n```\n\nLet me know if you want changes.";
    let mut harness = harness(&[reply, FINAL]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "make a page", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        std::fs::read_to_string(harness.root.join("index.html")).expect("file"),
        "<!doctype html>\n<html><body><h1>Hi</h1></body></html>"
    );
    assert_eq!(ctx.transcript[0]["action"], "write_file");
    assert!(
        ctx.transcript
            .iter()
            .all(|step| step["action"] != json!("parse_error")),
        "a rescued reply is not a parse failure"
    );
    assert!(ctx.trace.events.iter().any(
        |event| event["kind"] == json!("repair") && event["repairs"] == json!(["fence_rescue"])
    ));
}

#[tokio::test]
async fn the_fence_rescue_refuses_where_it_would_have_to_guess() {
    // No pending plan path: the fence may be an example, a snippet, an
    // apology in disguise. There is nothing to write it to, so nothing is
    // written — the reply is an ordinary parse failure.
    let reply = "Something like:\n\n```html\n<!doctype html>\n<html></html>\n```";
    let mut harness = harness(&[reply, reply, reply, FINAL]).await;
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert!(harness.tool_calls().is_empty(), "nothing was written");
    assert_eq!(ctx.transcript[0]["action"], "parse_error");
}

#[tokio::test]
async fn a_tagged_fence_is_written_to_the_file_of_its_own_kind() {
    // The plan wants a page and a stylesheet, in that order, and the model
    // answers with the stylesheet. Taking "the first pending path" would
    // put CSS into index.html, where the sanitize pass would then repair it
    // into an HTML scaffold and the stylesheet would be gone.
    let reply = "스타일은 이렇습니다:\n\n```css\nbody { color: red; }\n```";
    let mut harness = harness(&[reply, FINAL]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}},
        {"action": "write_file", "args": {"path": "style.css"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        std::fs::read_to_string(harness.root.join("style.css")).expect("file"),
        "body { color: red; }"
    );
    assert!(
        !harness.root.join("index.html").exists(),
        "the page was not overwritten with a stylesheet"
    );
}

#[tokio::test]
async fn a_tagged_fence_with_no_matching_plan_step_is_not_rescued() {
    let reply = "```python\nprint('hi')\n```";
    let mut harness = harness(&[reply, reply, reply, FINAL]).await;
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert!(harness.tool_calls().is_empty(), "nothing was written");
    assert_eq!(ctx.transcript[0]["action"], "parse_error");
}

#[tokio::test]
async fn a_plan_path_already_written_is_not_rescued_over() {
    let reply = "Here you go:\n\n```md\n# second thoughts\n```";
    let mut harness = harness(&[reply, reply, reply, FINAL]).await;
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "note.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "write_file", "args": {"path": "note.md"},
        "result": {"path": "note.md", "bytes": 4},
    }));
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert_eq!(
        ctx.transcript[1]["action"], "parse_error",
        "the only pending path was already written"
    );
}

#[tokio::test]
async fn the_direct_path_regenerates_once_and_writes_the_better_candidate() {
    // Attempt one is an apology (tier 0), attempt two is a real document
    // that is merely unfinished (tier 2). Longest-wins would have written
    // the apology; the salvage score writes the document.
    let apology = "I'm sorry, but I can't create that file for you.";
    let document = "<!doctype html>\n<html><body><h1>Hi</h1></body>";
    let mut harness = harness(&[
        "prose one",
        "prose two",
        "prose three",
        "prose four",
        apology,
        document,
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "make a page", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    let written = std::fs::read_to_string(harness.root.join("index.html")).expect("file");
    assert!(written.contains("<h1>Hi</h1>"), "{written}");
    assert!(!written.contains("I'm sorry"), "{written}");
    // The unfinished document was repaired rather than regenerated again —
    // the retry budget is one, and the transcript says what happened.
    let step = ctx.transcript.last().expect("step");
    assert_eq!(step["direct_path"], true);
    assert_eq!(step["generation"], json!({"repaired": true}));
    assert!(ctx
        .trace
        .events
        .iter()
        .any(|event| event["kind"] == json!("repair")
            && event["repairs"] == json!(["direct_path_regenerate"])));
}

#[tokio::test]
async fn content_the_sanitize_pass_can_extract_costs_no_regeneration() {
    // A fenced document does not validate as-is, but `sanitize_write_content`
    // recovers it without repairing — so there is nothing to regenerate,
    // and asking again would be a second opinion about a settled question.
    let fenced = "```html\n<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n```";
    let mut harness = harness(&[fenced]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .direct_file_path(
            &mut ctx,
            &harness.request,
            None,
            crate::kernel::profile::COMPACT,
        )
        .await
        .expect("direct path");
    assert_eq!(harness.runtime_llm_calls(&ctx), 1, "one call was enough");
    let written = std::fs::read_to_string(harness.root.join("index.html")).expect("file");
    assert!(written.starts_with("<!doctype html>"), "{written}");
    assert!(!written.contains("```"), "the fence came off: {written}");
    let step = ctx.transcript.last().expect("step");
    assert_eq!(step["generation"], json!({"repaired": false}));
}

#[tokio::test]
async fn a_repeated_direct_path_reply_buys_one_more_generation_and_names_it() {
    // Two identical rejected answers, then a real document. Without the
    // escalation the run would write the first apology; with it, the third
    // call — the only one whose prompt says "you already sent this" — is
    // the one that lands.
    let apology = "I'm sorry, I can't help with that.";
    let document = "<!doctype html>\n<html><body><h1>Hi</h1></body></html>\n";
    let mut harness = harness(&[apology, apology, document]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .direct_file_path(
            &mut ctx,
            &harness.request,
            None,
            crate::kernel::profile::COMPACT,
        )
        .await
        .expect("direct path");
    assert_eq!(
        harness.runtime_llm_calls(&ctx),
        3,
        "one call, one retry, one bought by the repeat"
    );
    assert_eq!(
        std::fs::read_to_string(harness.root.join("index.html")).expect("file"),
        document
    );
    let repairs: Vec<Value> = ctx
        .trace
        .events
        .iter()
        .filter(|event| event["kind"] == json!("repair"))
        .map(|event| event["repairs"].clone())
        .collect();
    assert!(repairs.contains(&json!(["direct_path_regenerate"])));
    assert!(
        repairs.contains(&json!(["direct_path_repeated"])),
        "the repetition is named in the trace, not just in the prompt: {repairs:?}"
    );
    // And the prompt that bought the extra call says so.
    let asks = harness.worker.calls.lock().expect("lock").clone();
    let third = asks
        .iter()
        .filter(|call| call["seam"] == json!("llm"))
        .nth(2)
        .expect("a third ask")["body"]["context"]
        .as_str()
        .expect("context")
        .to_string();
    assert!(
        third.contains("You already sent exactly this reply"),
        "{third}"
    );
}

#[tokio::test]
async fn the_standard_profile_never_spends_a_regeneration() {
    // `regeneration_retries` is zero there, so the first answer is written
    // whatever it looks like — no behaviour change for a capable model.
    let mut harness = harness(&["not a valid html document at all"]).await;
    harness.request.permission_mode = Some("trusted".into());
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "write_file", "args": {"path": "index.html"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .direct_file_path(
            &mut ctx,
            &harness.request,
            None,
            crate::kernel::profile::STANDARD,
        )
        .await
        .expect("direct path");
    assert_eq!(
        harness.runtime_llm_calls(&ctx),
        1,
        "one call, no regeneration"
    );
}

#[tokio::test]
async fn the_fallback_writes_nothing_when_the_plan_names_nothing() {
    let mut harness = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert!(
        harness.tool_calls().is_empty(),
        "no path, no fabricated write"
    );
    assert_eq!(ctx.final_message, "");
}

// ── the plan path ────────────────────────────────────────────────────────
/// The live gemma_e2b:S2 shape. The plan says *read the file, then write a
/// summary of it*; the model never lands a parseable turn; the file half
/// alone reached the write with no README in sight and the live run wrote
/// its own reasoning trace ending "README content not provided". The read
/// is a step the plan already declared, so the fallback runs it.
#[tokio::test]
async fn the_direct_path_runs_the_plans_read_before_it_writes() {
    let mut harness = harness(&[
        "prose one",
        "prose two",
        "prose three",
        "prose four",
        "# 요약\n\nLatticeAI는 로컬 우선 에이전트입니다.\n",
    ])
    .await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.permission_mode = Some("trusted".into());
    harness.request.message = "README.md 첫 문단을 요약해 notes/summary.md로 저장해줘".into();
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "read_file".into(),
        json!({"result": {"path": "README.md",
                          "text": "LatticeAI is a local-first agent."}}),
    );
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "summarise the readme", "steps": [
        {"action": "read_file", "args": {"path": "README.md"}},
        {"action": "write_file", "args": {"path": "notes/summary.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");

    let read = ctx
        .transcript
        .iter()
        .find(|step| step["action"] == json!("read_file"))
        .expect("the plan's read really ran");
    assert_eq!(read["direct_plan"], json!(true), "and says who ran it");
    assert!(read.get("result").is_some());
    // The write turn was shown what the read returned, so the summary is of
    // the file rather than of the model's memory of it.
    let asks = harness.worker.calls.lock().expect("lock").clone();
    let content_turn = asks
        .iter()
        .rfind(|call| call["seam"] == json!("llm"))
        .expect("a content turn")["body"]["context"]
        .as_str()
        .expect("context")
        .to_string();
    assert!(
        content_turn.contains("LatticeAI is a local-first agent."),
        "{content_turn}"
    );
    assert!(
        std::fs::read_to_string(harness.root.join("notes/summary.md"))
            .expect("file")
            .contains("로컬 우선"),
    );
}

/// The live gemma_e2b:S3/S5 shape: a plan of one **non-write** step. The
/// file half has nothing to write, so before v12.0.0 these runs ended with
/// `created_files: []` and no tool ever dispatched — the plan named the
/// exact call and nobody made it.
#[tokio::test]
async fn a_plan_that_names_no_file_runs_its_own_step_instead_of_nothing() {
    let mut harness = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    harness.request.message =
        "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘".into();
    harness.runtime.deps.tool_names.push("list_dir".into());
    harness.runtime.deps.policies.tools.insert(
        "list_dir".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    harness.worker.tool_bodies.lock().expect("lock").insert(
        "list_dir".into(),
        json!({"result": {"items": [{"name": "notes"}, {"name": "README.md"}]}}),
    );
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "count the files", "steps": [
        {"action": "list_dir", "args": {"path": "."}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");

    let listed = ctx
        .transcript
        .iter()
        .find(|step| step["action"] == json!("list_dir"))
        .expect("the plan's own step ran");
    assert_eq!(listed["direct_plan"], json!(true));
    assert!(
        Runtime::has_execution_evidence(&ctx),
        "a run that listed a directory has evidence"
    );
    assert!(
        harness
            .tool_calls()
            .iter()
            .all(|call| call["tool"] != json!("write_file")),
        "a plan with no file writes no file"
    );
    assert!(
        ctx.final_message.contains("list_dir"),
        "the answer says what was run: {}",
        ctx.final_message
    );
    assert_eq!(ctx.state, AgentState::Verifying);
}

/// A planner may name anything. Only a row the run actually offers may be
/// dispatched without a model having chosen it — the live 2B's `plan` and
/// `build_project` inventions must stay refusals, not become harness calls.
#[tokio::test]
async fn the_plan_path_never_dispatches_a_name_the_run_has_no_row_for() {
    let mut harness = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    harness.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    let mut ctx = harness.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "plan", "args": {"goal": "do it"}},
        {"action": "deploy_project", "args": {"path": "."}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");
    assert!(
        harness.tool_calls().is_empty(),
        "nothing the run does not govern was run: {:?}",
        harness.tool_calls()
    );
    assert_eq!(ctx.final_message, "", "and nothing was claimed");
}

/// A step that already worked is not re-run, and `standard` never reaches
/// this path at all.
#[tokio::test]
async fn the_plan_path_skips_what_worked_and_never_runs_under_standard() {
    for (profile, expected) in [
        (crate::kernel::profile::COMPACT, 0),
        (crate::kernel::profile::STANDARD, 0),
    ] {
        let mut harness = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
        harness.runtime.deps.agent_profile = Some(profile);
        let mut ctx = harness.context();
        ctx.plan = json!({"goal": "g", "steps": [
            {"action": "read_file", "args": {"path": "README.md"}}
        ]})
        .as_object()
        .expect("plan")
        .clone();
        ctx.transcript.push(json!({
            "state": "EXECUTING", "action": "read_file", "args": {"path": "README.md"},
            "result": {"path": "README.md", "text": "already read"},
        }));
        harness
            .runtime
            .execute(&mut ctx, &harness.request)
            .await
            .expect("execute");
        assert_eq!(
            harness
                .tool_calls()
                .iter()
                .filter(|call| call["tool"] == json!("read_file"))
                .count(),
            expected,
            "{}: a step that already succeeded is not re-run",
            profile.name
        );
    }
}

/// `LOOP_DETECTED` halts a run and then calls straight into the rescue.
/// The rescue must not be the halted step again.
///
/// The `gemma_e2b:S5` cell: `mcp.grep` with the planner's `search_term` and
/// no `pattern`, twelve dispatches an attempt, thirty-six across the cell —
/// fail, halt, replay, fail, halt, replay. Two outcomes here, and both are
/// the rule: a call the catalog can **repair** is a different call and runs;
/// a call it cannot is dropped and the halt stands.
#[tokio::test]
async fn the_rescue_repairs_the_step_that_failed_or_respects_the_halt() {
    // 1. Repairable: the value is in the plan under the planner's own name.
    let mut repaired = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    repaired.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    repaired.runtime.deps.tool_names = vec!["grep".into(), "write_file".into()];
    repaired.runtime.deps.policies.tools.insert(
        "grep".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    let mut ctx = repaired.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "grep", "args": {"path": ".", "search_term": "LatticeAI"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "grep",
        "args": {"path": ".", "search_term": "LatticeAI"},
        "error": "'pattern'",
    }));
    repaired
        .runtime
        .execute(&mut ctx, &repaired.request)
        .await
        .expect("execute");
    let calls: Vec<Value> = repaired
        .tool_calls()
        .into_iter()
        .filter(|call| call["tool"] == json!("grep"))
        .collect();
    assert_eq!(calls.len(), 1, "repaired once, not replayed: {calls:?}");
    assert_eq!(
        calls[0]["args"]["pattern"],
        json!("LatticeAI"),
        "the plan's own value, read under the name the tool takes"
    );

    // 2. Unrepairable: nothing in the plan is a pattern, so the failed
    //    signature is all the rescue would have to send. It sends nothing.
    let mut bare = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    bare.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    bare.runtime.deps.tool_names = vec!["grep".into(), "write_file".into()];
    bare.runtime.deps.policies.tools.insert(
        "grep".into(),
        crate::kernel::policy::ToolPolicy::read_only(),
    );
    let mut ctx = bare.context();
    ctx.plan = json!({"goal": "g", "steps": [{"action": "grep", "args": {"path": "."}}]})
        .as_object()
        .expect("plan")
        .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "grep", "args": {"path": "."},
        "error": "'pattern'",
    }));
    bare.runtime
        .execute(&mut ctx, &bare.request)
        .await
        .expect("execute");
    assert!(
        bare.tool_calls()
            .iter()
            .all(|call| call["tool"] != json!("grep")),
        "the halted step was not replayed: {:?}",
        bare.tool_calls()
    );

    // 3. And a step that failed with the arguments it would be re-sent with
    //    is dropped even when nothing needed repairing.
    let mut again = harness(&["prose one", "prose two", "prose three", "prose four"]).await;
    again.runtime.deps.agent_profile = Some(crate::kernel::profile::COMPACT);
    again.runtime.deps.tool_names = vec!["read_file".into(), "write_file".into()];
    let mut ctx = again.context();
    ctx.plan = json!({"goal": "g", "steps": [
        {"action": "read_file", "args": {"path": "nope.md"}}
    ]})
    .as_object()
    .expect("plan")
    .clone();
    ctx.transcript.push(json!({
        "state": "EXECUTING", "action": "read_file", "args": {"path": "nope.md"},
        "error": "File not found.",
    }));
    again
        .runtime
        .execute(&mut ctx, &again.request)
        .await
        .expect("execute");
    assert!(
        again
            .tool_calls()
            .iter()
            .all(|call| call["tool"] != json!("read_file")),
        "same call, same error, not again: {:?}",
        again.tool_calls()
    );
}

#[tokio::test]
async fn parse_failures_burn_the_budget_and_escalate_the_hint() {
    let mut harness = harness(&["prose one", "prose two", "prose three", FINAL]).await;
    let mut ctx = harness.context();
    harness
        .runtime
        .execute(&mut ctx, &harness.request)
        .await
        .expect("execute");

    // standard profile: budget 3 → three parse_error steps, then the loop
    // breaks without reaching the fourth (valid) completion.
    assert_eq!(ctx.transcript.len(), 3);
    let raws: Vec<&str> = ctx
        .transcript
        .iter()
        .map(|step| {
            assert_eq!(step["action"], "parse_error");
            assert!(step["error"]
                .as_str()
                .expect("error")
                .starts_with("Agent did not return valid JSON: "));
            step["raw"].as_str().expect("raw")
        })
        .collect();
    assert_eq!(raws, vec!["prose one", "prose two", "prose three"]);
    assert_eq!(
        ctx.state,
        AgentState::Verifying,
        "the run still gets verified"
    );
    // Two corrections: the plain hint, then the escalated one naming tools.
    assert_eq!(ctx.corrections.len(), 2);
    assert!(py_str(&ctx.corrections[0]).starts_with("Your last reply was not"));
    assert!(py_str(&ctx.corrections[1]).contains("read_file, write_file, final"));
    let summary = ctx.trace.summary();
    assert_eq!(summary["parse_errors"], 3);
    assert_eq!(
        summary["parse_recovered"], 2,
        "the last one is not recovered"
    );
}
