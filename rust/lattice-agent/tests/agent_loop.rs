//! The native loop against the committed Python trajectories.
//!
//! Every case here is the *same* run twice: once by `latticeai.core.agent` when
//! `scripts/generate_agent_loop_fixtures.py` recorded it, and once by
//! [`lattice_agent::agentloop::Runtime`] here, driven by the identical scripted
//! reasoner and the identical tool results through a fake worker. What is
//! compared is the whole record — final state, state history, transcript,
//! rollback log, trace counters, the tool calls that were actually dispatched,
//! and the audit trail — under the four normalisation rules the manifest names.
//!
//! Comparison is over canonical JSON rather than `==`, so a `bool` that quietly
//! became an integer still fails.

mod common;

use common::{
    audit_of, canonical, file_create_actions, loop_deps, loop_golden, loop_normalize, loop_request,
    start_replay_worker,
};
use lattice_agent::agentloop::Runtime;
use lattice_agent::sandbox::Workspace;
use lattice_agent::state::{AgentRunContext, AgentState};
use lattice_agent::transcript::{
    artifact_checklist, compact_transcript, files_written, requirement_coverage, truncate_strings,
    PhaseBudgets, TranscriptBudget,
};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;

fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("agent_loop")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch dir");
    dir
}

fn cases(name: &str) -> Vec<Value> {
    loop_golden(name)["cases"]
        .as_array()
        .expect("cases array")
        .clone()
}

fn actions() -> BTreeSet<String> {
    file_create_actions()
}

// ── deterministic helpers ────────────────────────────────────────────────────
#[test]
fn every_recorded_action_parses_the_way_python_parsed_it() {
    let helpers = loop_golden("helpers.json");
    let recorded = helpers["extract_action_details"].as_array().expect("rows");
    assert!(recorded.len() >= 30, "the grid must stay wide");
    let mut failures = Vec::new();
    for case in recorded {
        let key = case["key"].as_str().expect("key");
        let raw = case["raw"].as_str().expect("raw");
        let actual = match lattice_agent::action::extract_action_details(raw) {
            Ok((action, repairs)) => json!({
                "key": key, "raw": raw, "ok": true,
                "action": Value::Object(action), "repairs": repairs,
            }),
            Err(error) => json!({
                "key": key, "raw": raw, "ok": false,
                "error": loop_normalize(&json!(error.0), "<none>"),
            }),
        };
        if canonical(&actual) != canonical(case) {
            failures.push(format!("{key}: {actual} != {case}"));
        }
    }
    common::assert_no_failures(recorded.len(), failures, "action parses");
}

#[test]
fn every_recorded_plan_normalizes_the_way_python_normalized_it() {
    let helpers = loop_golden("helpers.json");
    let recorded = helpers["normalize_plan"].as_array().expect("rows");
    assert!(recorded.len() >= 20);
    let mut failures = Vec::new();
    for case in recorded {
        let (plan, fixes) = lattice_agent::plan::normalize_plan(
            &case["plan"],
            case["message"].as_str().unwrap_or(""),
        );
        let actual = json!({"normalized": Value::Object(plan), "fixes": fixes});
        let expected = json!({"normalized": case["normalized"], "fixes": case["fixes"]});
        if canonical(&actual) != canonical(&expected) {
            failures.push(format!("{}: {actual} != {expected}", case["key"]));
        }
    }
    common::assert_no_failures(recorded.len(), failures, "plan normalizations");
}

#[test]
fn the_two_inferences_answer_the_recorded_requests() {
    let helpers = loop_golden("helpers.json");
    let recorded = helpers["inference"].as_array().expect("rows");
    let mut failures = Vec::new();
    for case in recorded {
        let message = case["message"].as_str().expect("message");
        let actual = json!({
            "message": message,
            "file_target": lattice_agent::inference::infer_file_target(message),
            "manifest": lattice_agent::inference::infer_project_manifest(message),
        });
        if canonical(&actual) != canonical(case) {
            failures.push(format!("{message:?}: {actual} != {case}"));
        }
    }
    common::assert_no_failures(recorded.len(), failures, "inferences");
}

#[test]
fn the_transcript_helpers_read_the_recorded_transcripts_the_same_way() {
    let helpers = loop_golden("helpers.json");
    let recorded = helpers["transcript_helpers"].as_array().expect("rows");
    let mut failures = Vec::new();
    for case in recorded {
        let transcript = case["transcript"].as_array().expect("transcript").clone();
        let message = case["message"].as_str().expect("message");
        let actual = json!({
            "files_written": files_written(&transcript, &actions()),
            "artifact_checklist": artifact_checklist(&transcript, &actions()),
            "requirement_coverage": requirement_coverage(message, &transcript, &actions()),
            "compact_window_2": compact_transcript(&transcript, 2, 40),
        });
        let expected = json!({
            "files_written": case["files_written"],
            "artifact_checklist": case["artifact_checklist"],
            "requirement_coverage": case["requirement_coverage"],
            "compact_window_2": case["compact_window_2"],
        });
        if canonical(&actual) != canonical(&expected) {
            failures.push(format!("{}: {actual} != {expected}", case["key"]));
        }
    }
    common::assert_no_failures(recorded.len(), failures, "transcript helpers");
}

#[test]
fn truncation_and_learning_filters_match_their_recorded_answers() {
    let helpers = loop_golden("helpers.json");
    for case in helpers["truncate_strings"].as_array().expect("rows") {
        let limit = case["limit"].as_u64().expect("limit") as usize;
        assert_eq!(
            canonical(&truncate_strings(&case["value"], limit)),
            canonical(&case["truncated"]),
            "{}",
            case["key"]
        );
    }
    for case in helpers["filter_learnings"].as_array().expect("rows") {
        let input = case["input"].as_array().expect("input");
        assert_eq!(
            canonical(&json!(lattice_agent::transcript::filter_learnings(input))),
            canonical(&case["kept"]),
            "{input:?}"
        );
    }
    let budgets = &helpers["budgets"];
    let phase = PhaseBudgets::default();
    assert_eq!(budgets["phase"]["plan_tokens"], phase.plan_tokens);
    assert_eq!(budgets["phase"]["execute_tokens"], phase.execute_tokens);
    assert_eq!(budgets["phase"]["verify_tokens"], phase.verify_tokens);
    assert_eq!(budgets["phase"]["memory_tokens"], phase.memory_tokens);
    let transcript = TranscriptBudget::default();
    assert_eq!(budgets["transcript"]["window"], transcript.window);
    assert_eq!(
        budgets["transcript"]["result_chars"],
        transcript.result_chars
    );
    assert_eq!(
        budgets["transcript"]["verify_chars"],
        transcript.verify_chars
    );
}

// ── run store ────────────────────────────────────────────────────────────────
#[test]
fn the_run_store_contract_round_trips_the_recorded_contexts() {
    let mut failures = Vec::new();
    let recorded = cases("run_store.json");
    for case in &recorded {
        if let Some(serialized) = case.get("serialized") {
            let restored = AgentRunContext::restore(serialized);
            let actual = loop_normalize(&restored.serialize(), "<none>");
            if canonical(&actual) != canonical(&case["round_trip"]) {
                failures.push(format!(
                    "{}: {actual} != {}",
                    case["key"], case["round_trip"]
                ));
            }
        }
        if let Some(payload) = case.get("payload") {
            let actual = loop_normalize(&AgentRunContext::restore(payload).serialize(), "<none>");
            if canonical(&actual) != canonical(&case["restored"]) {
                failures.push(format!("{}: {actual} != {}", case["key"], case["restored"]));
            }
        }
    }
    common::assert_no_failures(recorded.len(), failures, "run store round trips");
}

// ── the verdict mapping ──────────────────────────────────────────────────────
#[tokio::test]
async fn the_verdict_mapping_reaches_the_recorded_states() {
    let dir = scratch("verification");
    let workspace = Workspace::new(dir.join("agent_workspace")).expect("workspace");
    let server = start_replay_worker(workspace.root()).await;
    let root = workspace.root().display().to_string();
    let recorded = cases("verification.json");
    let mut failures = Vec::new();
    let mut checked = 0usize;

    for case in &recorded {
        // The two named rows drive the critic twice; the grid rows drive it once.
        let script: Vec<String> = match case["verdict"].as_str() {
            Some("never_parses") => vec!["prose".into(), "still prose".into()],
            Some("strict_retry_recovers") => vec![
                "prose".into(),
                r#"{"action": "v", "verdict": "PASS", "next_state": "DONE", "reason": "r"}"#.into(),
            ],
            _ => vec![json!({
                "action": "verdict",
                "verdict": case["verdict"],
                "next_state": case["next_state"],
                "reason": "because",
                "corrections": ["be specific"],
                "confidence": 0.5,
            })
            .to_string()],
        };
        server.worker.push_completions(&script);

        let mut runtime = Runtime::new(loop_deps(&server.origin, workspace.clone()));
        let mut ctx = AgentRunContext::new();
        ctx.retry_count = case["retry_count"].as_u64().unwrap_or(0) as u32;
        ctx.transcript = vec![if case["evidence"] == json!(true) {
            json!({"state": "EXECUTING", "action": "write_file", "args": {"path": "index.html"},
                   "result": {"path": "index.html", "bytes": 4}})
        } else {
            json!({"state": "EXECUTING", "action": "final", "thoughts": "t"})
        }];
        let request = loop_request(case["message"].as_str().expect("message"));
        runtime.verify(&mut ctx, &request).await.expect("verify");

        let actual = json!({
            "final_state": ctx.state.as_str(),
            "final_message": ctx.final_message,
            "retry_count_after": ctx.retry_count,
            "transcript": loop_normalize(&Value::Array(ctx.transcript.clone()), &root),
        });
        let expected = json!({
            "final_state": case["final_state"],
            "final_message": case["final_message"],
            "retry_count_after": case["retry_count_after"],
            "transcript": case["transcript"],
        });
        if canonical(&actual) != canonical(&expected) {
            failures.push(format!(
                "{}/{}/evidence={}/retry={}: {actual} != {expected}",
                case["verdict"], case["next_state"], case["evidence"], case["retry_count"]
            ));
        }
        checked += 1;
    }
    common::assert_no_failures(checked, failures, "verdict mappings");
    assert!(checked >= 88, "the mapping grid must stay wide");
}

// ── end-to-end trajectories ──────────────────────────────────────────────────
/// Replay one recorded trajectory through the native loop.
async fn replay(case: &Value) -> (Value, Value) {
    let key = case["key"].as_str().expect("key");
    let dir = scratch(key);
    let workspace = Workspace::new(dir.join("agent_workspace")).expect("workspace");
    let root = workspace.root().display().to_string();
    for (name, body) in case["seed"].as_object().expect("seed") {
        let target = workspace.root().join(name);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).expect("seed dir");
        }
        std::fs::write(&target, body.as_str().unwrap_or("")).expect("seed file");
    }

    let server = start_replay_worker(workspace.root()).await;
    let script: Vec<String> = case["scripted_llm"]
        .as_array()
        .expect("script")
        .iter()
        .map(|text| text.as_str().unwrap_or_default().to_string())
        .collect();
    server.worker.push_completions(&script);
    if case["governor_verdict"].is_object() {
        server.worker.set_proposal(case["governor_verdict"].clone());
    }
    server
        .worker
        .load_tool_calls(&case["tool_calls"].as_array().cloned().unwrap_or_default());

    let mut runtime = Runtime::new(loop_deps(&server.origin, workspace.clone()));
    let request = loop_request(case["message"].as_str().expect("message"));
    let mut ctx = AgentRunContext::new();
    ctx.permission_mode = case["mode"].as_str().map(String::from);
    ctx.state = AgentState::Planning;
    ctx.state_history.push(ctx.state.as_str().to_string());
    runtime.plan(&mut ctx, &request).await.expect("plan");
    let requirements = runtime.approval_requirements(&ctx, &request);
    let paused = requirements["requires_approval"] == json!(true);
    if paused {
        ctx.state_history
            .push(AgentState::WaitingApproval.as_str().to_string());
    } else {
        runtime.approve(&mut ctx, &request, false);
        runtime
            .run_to_completion(&mut ctx, &request)
            .await
            .expect("run");
    }

    let actual = json!({
        "paused": paused,
        "approval_requirements": loop_normalize(&requirements, &root),
        "final_state": ctx.state.as_str(),
        "final_message": loop_normalize(&json!(ctx.final_message), &root),
        "state_history": ctx.state_history,
        "transcript": loop_normalize(&Value::Array(ctx.transcript.clone()), &root),
        "rollback_log": loop_normalize(&Value::Array(ctx.rollback_log.clone()), &root),
        "loop": ctx.trace.summary(),
        "audit": audit_of(&runtime, &root),
        "unused_script": server.worker.remaining_completions(),
    });
    let expected = json!({
        "paused": case["paused"],
        "approval_requirements": case["approval_requirements"],
        "final_state": case["final_state"],
        "final_message": case["final_message"],
        "state_history": case["state_history"],
        "transcript": case["transcript"],
        "rollback_log": case["rollback_log"],
        "loop": case["loop"],
        "audit": case["audit"],
        "unused_script": case["unused_script"],
    });
    // The tools the loop actually dispatched, in order, with the arguments it
    // sent — a run that reached the same state by calling something else would
    // otherwise pass.
    let dispatched: Vec<Value> = server
        .worker
        .observed_calls()
        .iter()
        .map(|call| loop_normalize(call, &root))
        .collect();
    let recorded_calls: Vec<Value> = case["tool_calls"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|call| json!({"tool": call["tool"], "args": call["args"]}))
        .collect();
    assert_eq!(
        canonical(&json!(dispatched)),
        canonical(&json!(recorded_calls)),
        "{key}: the dispatched tool calls differ"
    );
    (actual, expected)
}

#[tokio::test]
async fn every_recorded_trajectory_replays_identically() {
    let recorded = cases("trajectories.json");
    assert!(recorded.len() >= 9, "the scenario set must stay wide");
    let mut failures = Vec::new();
    for case in &recorded {
        let (actual, expected) = replay(case).await;
        if canonical(&actual) != canonical(&expected) {
            failures.push(format!(
                "{}:\n  native: {actual}\n  python: {expected}",
                case["key"]
            ));
        }
    }
    common::assert_no_failures(recorded.len(), failures, "trajectories");
}

#[tokio::test]
async fn the_trajectories_between_them_reach_every_terminal_state() {
    let states: BTreeSet<String> = cases("trajectories.json")
        .iter()
        .map(|case| case["final_state"].as_str().unwrap_or_default().to_string())
        .collect();
    assert!(
        states.is_superset(
            &["DONE", "FAILED", "NEEDS_REVIEW", "WAITING_APPROVAL"]
                .into_iter()
                .map(String::from)
                .collect()
        ),
        "{states:?}"
    );
}
