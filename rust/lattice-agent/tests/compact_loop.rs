//! A whole run driven by a ~2B local model, end to end.
//!
//! Every trajectory in `trajectories.json` runs `standard`: Python's generator
//! never passed an `executing_model`, so `profile_for_model(None)` answered
//! `standard` for all ten and the `compact` dial — the shorter window, the
//! earlier escalation, the direct-path fallback — was pinned only by unit
//! tests of its parts. Nothing proved the parts composed.
//!
//! This is that proof, for the model the product actually recommends at the
//! 8GB tier. The reasoner is scripted with the two replies a 2B model really
//! sends — prose wrapped around a fenced action, then a tool-call tag around
//! an object the token limit cut off — and the run has to reach `DONE` with
//! the file on disk **without** the direct-path fallback firing. The fallback
//! is the honest last resort; a release where it is the *usual* path is a
//! release where tool calling does not work.
//!
//! The case lives in `rust/fixtures/agent_loop/golden/trajectories_compact.json`
//! and is a **native** record, not a Python one — there is no Python loop left
//! to record one. `FROZEN.md` says so beside it.

mod common;

use common::{loop_deps, loop_golden, loop_request, start_replay_worker};
use lattice_agent::agentloop::Runtime;
use lattice_agent::sandbox::Workspace;
use lattice_agent::state::{AgentRunContext, AgentState};
use serde_json::{json, Value};
use std::path::PathBuf;

fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("compact_loop")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch dir");
    dir
}

fn compact_case() -> Value {
    let document = loop_golden("trajectories_compact.json");
    assert_eq!(document["schema"], json!("agent-loop-native/v1"));
    document["cases"]
        .as_array()
        .expect("cases")
        .first()
        .cloned()
        .expect("one case")
}

#[tokio::test]
async fn a_two_billion_parameter_model_drives_the_loop_to_done() {
    let case = compact_case();
    let key = case["key"].as_str().expect("key");
    let dir = scratch(key);
    let workspace = Workspace::new(dir.join("agent_workspace")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    let script: Vec<String> = case["scripted_llm"]
        .as_array()
        .expect("script")
        .iter()
        .map(|text| text.as_str().unwrap_or_default().to_string())
        .collect();
    server.worker.push_completions(&script);

    let mut runtime = Runtime::new(loop_deps(&server, workspace.clone()));
    let mut request = loop_request(case["message"].as_str().expect("message"));
    request.executing_model = case["executing_model"].as_str().map(String::from);
    // The dial is derived, never injected: this asserts the *model id* reaches
    // the compact profile, which is the whole of the v11.9.0 §1 fix.
    assert_eq!(
        runtime.profile(request.executing_model.as_deref()).name,
        "compact",
        "{:?} must select the compact loop",
        request.executing_model
    );

    let mut ctx = AgentRunContext::new();
    ctx.permission_mode = case["mode"].as_str().map(String::from);
    ctx.state = AgentState::Planning;
    ctx.state_history.push(ctx.state.as_str().to_string());
    runtime.plan(&mut ctx, &request).await.expect("plan");
    runtime.approve(&mut ctx, &request, false);
    runtime
        .run_to_completion(&mut ctx, &request)
        .await
        .expect("run");

    assert_eq!(
        ctx.state.as_str(),
        case["final_state"].as_str().expect("final_state"),
        "transcript: {}",
        serde_json::to_string_pretty(&ctx.transcript).unwrap_or_default()
    );
    // The file, byte for byte. A run that reached DONE without it would be the
    // exact dishonesty the verify gate exists to prevent.
    let path = case["file"]["path"].as_str().expect("path");
    assert_eq!(
        std::fs::read_to_string(workspace.root().join(path)).expect("the file must exist"),
        case["file"]["content"].as_str().expect("content"),
    );

    // Each reply needed the rung the case says it needed, in order.
    let repairs: Vec<Value> = ctx
        .trace
        .events
        .iter()
        .filter(|event| event["phase"] == json!("execute") && event["kind"] == json!("repair"))
        .map(|event| event["repairs"].clone())
        .collect();
    assert_eq!(
        repairs,
        case["execute_repairs"]
            .as_array()
            .expect("execute_repairs")
            .clone(),
        "the parse rungs that fired differ"
    );

    // The fallback did not fire, and neither did a parse failure: the model
    // stayed on contract because the prompt finally told it what the contract
    // was, and the parse chain read what it sent back.
    assert!(
        ctx.transcript
            .iter()
            .all(|step| step["action"] != json!("parse_error")),
        "no reply was refused"
    );
    assert!(
        ctx.transcript
            .iter()
            .all(|step| step.get("direct_path").is_none()),
        "the direct-path fallback is the last resort, not the usual path"
    );
    assert_eq!(
        server.worker.remaining_completions(),
        case["unused_script"].as_u64().unwrap_or(0) as usize,
        "the script was consumed exactly once through"
    );
}

#[tokio::test]
async fn the_compact_run_is_told_the_contract_it_is_being_held_to() {
    // The other half of the same fix: what the model was actually sent. A run
    // whose executor prompt is blank cannot be said to have "failed to follow
    // the format".
    let case = compact_case();
    let dir = scratch("prompt");
    let workspace = Workspace::new(dir.join("agent_workspace")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    server.worker.push_completions(
        &case["scripted_llm"]
            .as_array()
            .expect("script")
            .iter()
            .map(|text| text.as_str().unwrap_or_default().to_string())
            .collect::<Vec<String>>(),
    );

    let mut runtime = Runtime::new(loop_deps(&server, workspace.clone()));
    let mut request = loop_request(case["message"].as_str().expect("message"));
    request.executing_model = case["executing_model"].as_str().map(String::from);
    let mut ctx = AgentRunContext::new();
    ctx.permission_mode = case["mode"].as_str().map(String::from);
    ctx.state = AgentState::Planning;
    runtime.plan(&mut ctx, &request).await.expect("plan");
    runtime.approve(&mut ctx, &request, false);
    runtime
        .run_to_completion(&mut ctx, &request)
        .await
        .expect("run");

    let asks = server.worker.observed_prompts();
    let planner = asks.first().expect("the planner was asked");
    assert!(planner.contains("\"action\": \"plan\""), "{planner}");
    let executor = asks.get(1).expect("the executor was asked");
    assert!(executor.contains("EXACTLY ONE JSON object"), "{executor}");
    assert!(
        executor.contains("- write_file{path, content}"),
        "{executor}"
    );
    assert!(executor.contains("<!doctype html>"), "{executor}");
    // Compact: no prose preamble, no conversation block, no written-files hint.
    assert!(!executor.contains("You are the executor"), "{executor}");
    assert!(!executor.contains("Recent conversation:"), "{executor}");
    let second = asks.get(2).expect("a second executor turn");
    assert!(
        !second.contains("Files written by this run so far"),
        "{second}"
    );
    let critic = asks.last().expect("the critic was asked");
    assert!(critic.contains("\"action\": \"verdict\""), "{critic}");
}
