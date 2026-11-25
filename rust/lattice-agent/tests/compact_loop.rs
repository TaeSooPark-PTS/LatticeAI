mod common;

use common::{loop_deps, loop_golden, loop_request, start_replay_worker};
use lattice_agent::kernel::agentloop::Runtime;
use lattice_agent::kernel::state::{AgentRunContext, AgentState};
use lattice_agent::tools::sandbox::Workspace;
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
    let path = case["file"]["path"].as_str().expect("path");
    assert_eq!(
        std::fs::read_to_string(workspace.root().join(path)).expect("the file must exist"),
        case["file"]["content"].as_str().expect("content"),
    );

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
