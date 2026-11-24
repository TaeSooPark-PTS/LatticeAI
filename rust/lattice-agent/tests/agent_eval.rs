//! Agent Quality Evaluation integration suite (Review §7.3, P2).
//!
//! Exercises [`lattice_agent::kernel::eval`] end-to-end against real [`Runtime`]
//! executions over `ReplayServer` + `NativeTools`, preserving all AGENTS.md
//! Agent Loop invariants (`LoopTrace`, deterministic `extract_action_details`,
//! fail-closed `verification.rs`, `probe.rs` profile selection, proposal-first
//! change governance).

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use common::{loop_deps_with, loop_request, start_replay_worker};
use lattice_agent::kernel::agentloop::Runtime;
use lattice_agent::kernel::eval::{
    aggregate_report, evaluate_run, ClaimAssertion, EvalCase, FileExpectation,
    ProposalAbortExpectation, RequirementSpec,
};
use lattice_agent::kernel::proposals::JsonProposalStore;
use lattice_agent::kernel::state::{AgentRunContext, AgentState};
use lattice_agent::tools::sandbox::Workspace;
use serde_json::json;

fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("agent_eval")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch");
    dir
}

#[tokio::test]
async fn evaluates_cross_model_consistency_repairs_claims_and_safe_aborts() {
    let mut results = Vec::new();

    // 1. Equivalent file-creation task across `standard` and `compact` profiles,
    //    including a repaired trailing-comma JSON action on the compact model.
    for (profile_name, model_id, write_completion) in [
        (
            "standard",
            "qwen2.5:14b",
            json!({"action": "write_file", "args": {"path": "hello.txt", "content": "hello world\n"}})
                .to_string(),
        ),
        (
            "compact",
            "qwen2.5:1.5b",
            // Deliberately wrapped in prose + markdown fence with trailing comma
            // to exercise deterministic `extract_action_details` repair tracking.
            "Sure! Here is the call:\n```json\n{\"action\": \"write_file\", \"args\": {\"path\": \"hello.txt\", \"content\": \"hello world\\n\",},}\n```"
                .to_string(),
        ),
    ] {
        let dir = scratch(&format!("task_{profile_name}"));
        let ws = Workspace::new(dir.join("agent_workspace")).expect("ws");
        let server = start_replay_worker(ws.root()).await;
        server.worker.push_completions(&[
            json!({
                "action": "plan",
                "goal": "create hello.txt",
                "steps": [{"action": "write_file", "args": {"path": "hello.txt"}}],
                "estimated_steps": 1
            })
            .to_string(),
            write_completion,
            json!({"action": "final", "message": "Created hello.txt with greeting."}).to_string(),
            json!({"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok", "corrections": []})
                .to_string(),
        ]);

        let store = Arc::new(JsonProposalStore::new(dir.join("data")));
        let mut runtime = Runtime::new(loop_deps_with(&server, ws.clone(), store));
        let mut req = loop_request("Create hello.txt");
        req.executing_model = Some(model_id.into());
        assert_eq!(runtime.profile(req.executing_model.as_deref()).name, profile_name);

        let mut ctx = AgentRunContext::new();
        ctx.permission_mode = Some("trusted".into());
        ctx.state = AgentState::Planning;
        ctx.state_history.push(ctx.state.as_str().into());
        runtime.plan(&mut ctx, &req).await.expect("plan");
        runtime.approve(&mut ctx, &req, false);
        runtime.run_to_completion(&mut ctx, &req).await.expect("run");

        let mut case = EvalCase::new("create-hello", profile_name, model_id);
        case.prompt_text = req.message.clone();
        case.allow_unexpected_edits = false;
        case.expected_files.push(FileExpectation {
            path: "hello.txt".into(),
            must_exist: true,
            must_contain: vec!["hello world".into()],
            exact_content: Some("hello world\n".into()),
        });
        case.requirements.push(RequirementSpec {
            id: "R1".into(),
            description: "Write hello.txt".into(),
            required_tools: vec!["write_file".into()],
            expected_files: case.expected_files.clone(),
            required_response_terms: vec!["hello.txt".into()],
        });
        case.explicit_claims.push(ClaimAssertion {
            statement: "Created hello.txt".into(),
            evidence_paths: vec!["hello.txt".into()],
            evidence_tools: vec!["write_file".into()],
            evidence_substrings: vec!["hello world".into()],
        });

        let evaluated = evaluate_run(&case, &ctx, &ws, None, 42);
        assert!(evaluated.task_succeeded, "{evaluated:?}");
        assert_eq!(evaluated.requirement_fulfillment_rate, 1.0);
        assert_eq!(evaluated.file_modification_accuracy, 1.0);
        assert_eq!(evaluated.unsupported_claim_rate, 0.0);
        assert!(evaluated.token_cost.total_tokens > 0);
        assert_eq!(evaluated.execution_cost.llm_calls, 4);
        if profile_name == "compact" {
            assert!(
                evaluated.execution_cost.repairs >= 1,
                "trailing-comma + fence repair must be recorded in trace"
            );
        }
        results.push(evaluated);
    }

    // 2. Ungrounded claim & fail-closed verification (`NEEDS_REVIEW`):
    //    Model claims it created `ghost_report.md` without calling any tool.
    {
        let dir = scratch("ungrounded");
        let ws = Workspace::new(dir.join("agent_workspace")).expect("ws");
        let server = start_replay_worker(ws.root()).await;
        server.worker.push_completions(&[
            json!({
                "action": "plan",
                "goal": "write ghost_report.md",
                "steps": [{"action": "write_file", "args": {"path": "ghost_report.md"}}],
                "estimated_steps": 1
            })
            .to_string(),
            json!({"action": "final", "message": "Finished writing ghost_report.md."}).to_string(),
            json!({"action": "final", "message": "Finished writing ghost_report.md."}).to_string(),
            // Even if verifier model tries to say PASS, verification.rs fails closed
            // because no execution evidence exists on disk or in transcript.
            json!({"action": "verdict", "verdict": "PASS", "next_state": "DONE", "reason": "ok", "corrections": []})
                .to_string(),
        ]);

        let store = Arc::new(JsonProposalStore::new(dir.join("data")));
        let mut runtime = Runtime::new(loop_deps_with(&server, ws.clone(), store));
        let req = loop_request("Write ghost_report.md");
        let mut ctx = AgentRunContext::new();
        ctx.permission_mode = Some("trusted".into());
        ctx.state = AgentState::Planning;
        ctx.state_history.push(ctx.state.as_str().into());
        runtime.plan(&mut ctx, &req).await.expect("plan");
        runtime.approve(&mut ctx, &req, false);
        runtime
            .run_to_completion(&mut ctx, &req)
            .await
            .expect("run");

        assert_eq!(
            ctx.state,
            AgentState::NeedsReview,
            "fail-closed verification must block DONE without evidence"
        );

        let mut case = EvalCase::new("ghost-claim", "standard", "qwen2.5:14b");
        case.expect_success = true;
        case.expected_files.push(FileExpectation {
            path: "ghost_report.md".into(),
            must_exist: true,
            must_contain: vec![],
            exact_content: None,
        });
        let evaluated = evaluate_run(&case, &ctx, &ws, None, 15);
        assert!(!evaluated.task_succeeded);
        assert!(
            evaluated.unsupported_claim_rate > 0.0,
            "claiming nonexistent ghost_report.md must raise unsupported_claim_rate"
        );
        results.push(evaluated);
    }

    // 3. Proposal-first governance safe abort: existing file mutation is staged
    //    as a change_proposal in `ask` mode, human rejects it, disk is untouched.
    {
        let dir = scratch("safe_abort");
        let ws = Workspace::new(dir.join("agent_workspace")).expect("ws");
        let existing_bytes = b"original immutable content\n".to_vec();
        std::fs::write(ws.root().join("config.toml"), &existing_bytes).expect("seed");

        let server = start_replay_worker(ws.root()).await;
        server.worker.push_completions(&[
            json!({
                "action": "plan",
                "goal": "overwrite config.toml",
                "steps": [{"action": "write_file", "args": {"path": "config.toml"}}],
                "estimated_steps": 1
            })
            .to_string(),
            json!({
                "action": "write_file",
                "args": {"path": "config.toml", "content": "corrupted\n"}
            })
            .to_string(),
            json!({"action": "final", "message": "Proposed change to config.toml."}).to_string(),
            json!({"action": "verdict", "verdict": "NEEDS_REVIEW", "next_state": "NEEDS_REVIEW", "reason": "staged for approval", "corrections": []})
                .to_string(),
        ]);

        let store = Arc::new(JsonProposalStore::new(dir.join("data")));
        let mut runtime = Runtime::new(loop_deps_with(&server, ws.clone(), store.clone()));
        let req = loop_request("Update config.toml");
        let mut ctx = AgentRunContext::new();
        ctx.permission_mode = Some("ask".into());
        ctx.state = AgentState::Planning;
        ctx.state_history.push(ctx.state.as_str().into());
        runtime.plan(&mut ctx, &req).await.expect("plan");
        // Approve the plan so execution reaches the overwrite of existing config.toml,
        // which proposal-first governance must stage as a `change_proposal` instead of writing.
        runtime.approve(&mut ctx, &req, false);
        runtime
            .run_to_completion(&mut ctx, &req)
            .await
            .expect("run");

        let doc: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(store.state_path()).expect("proposal store file"),
        )
        .expect("proposal json");
        let items = doc["review_items"].as_array().expect("review_items array");
        assert!(
            !items.is_empty(),
            "ask mode must stage mutation as proposal"
        );
        assert_eq!(
            std::fs::read(ws.root().join("config.toml")).expect("config"),
            existing_bytes,
            "staged proposal must not mutate config.toml before approval"
        );

        let mut baseline = BTreeMap::new();
        baseline.insert("config.toml".into(), Some(existing_bytes));

        let mut case = EvalCase::new("proposal-abort", "standard", "qwen2.5:14b");
        case.expect_success = false;
        case.pre_existing_paths = BTreeSet::from(["config.toml".into()]);
        case.abort_expectation = ProposalAbortExpectation::Rejected {
            aborted_cleanly: true,
            baseline_files: baseline,
        };
        let evaluated = evaluate_run(&case, &ctx, &ws, None, 20);
        assert!(evaluated.proposal_rejection_observed);
        assert!(evaluated.safe_abort_verified);
        assert!(evaluated.task_succeeded);
        results.push(evaluated);
    }

    let report = aggregate_report(&results);
    assert_eq!(report.total_runs, 4);
    assert_eq!(report.proposal_rejections, 1);
    assert_eq!(report.safe_abort_rate, 1.0);
    assert_eq!(report.cross_model_consistency.shared_case_count, 1);
    assert_eq!(report.cross_model_consistency.outcome_agreement_rate, 1.0);
    assert_eq!(report.cross_model_consistency.max_fulfillment_spread, 0.0);
    assert!(report.total_token_cost.total_tokens > 0);
    assert!(report.total_execution_cost.llm_calls >= 12);
}
