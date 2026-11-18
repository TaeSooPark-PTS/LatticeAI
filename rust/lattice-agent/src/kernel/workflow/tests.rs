use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};

#[test]
fn rejects_cyclic_dags_and_schedules_parallel_read_only_waves() {
    let cyclic = WorkflowSpec {
        workflow_id: "wf_cycle".into(),
        goal: "invalid cycle".into(),
        user_email: None,
        workspace_id: None,
        rollback_on_failure: true,
        steps: vec![
            WorkflowStepSpec {
                step_id: "a".into(),
                title: "A".into(),
                action: "read".into(),
                args: json!({}),
                depends_on: vec!["b".into()],
                mutating: false,
                requires_approval: false,
                max_retries: 0,
                rollback_path: None,
                consulted_node_ids: vec![],
            },
            WorkflowStepSpec {
                step_id: "b".into(),
                title: "B".into(),
                action: "read".into(),
                args: json!({}),
                depends_on: vec!["a".into()],
                mutating: false,
                requires_approval: false,
                max_retries: 0,
                rollback_path: None,
                consulted_node_ids: vec![],
            },
        ],
    };
    assert!(cyclic.validate_dag().unwrap_err().contains("cycle"));
}

#[test]
fn durable_workflow_survives_approval_restart_replays_idempotent_steps_retries_and_rolls_back() {
    let dir = tempfile::tempdir().expect("tempdir");
    let target_file = dir.path().join("config.toml");
    std::fs::write(
        &target_file,
        "port = 4824
",
    )
    .expect("seed file");

    let store = DurableWorkflowStore::new(dir.path().join("workflows"));
    let spec = WorkflowSpec {
        workflow_id: "wf_upgrade_1".into(),
        goal: "Audit Brain context and apply approved config update".into(),
        user_email: Some("owner@local".into()),
        workspace_id: Some("personal".into()),
        rollback_on_failure: true,
        steps: vec![
            WorkflowStepSpec {
                step_id: "gather_a".into(),
                title: "Read architecture".into(),
                action: "kg_read".into(),
                args: json!({"node": "doc:arch"}),
                depends_on: vec![],
                mutating: false,
                requires_approval: false,
                max_retries: 0,
                rollback_path: None,
                consulted_node_ids: vec!["doc:arch".into()],
            },
            WorkflowStepSpec {
                step_id: "gather_b".into(),
                title: "Read security".into(),
                action: "kg_read".into(),
                args: json!({"node": "doc:sec"}),
                depends_on: vec![],
                mutating: false,
                requires_approval: false,
                max_retries: 0,
                rollback_path: None,
                consulted_node_ids: vec!["doc:sec".into()],
            },
            WorkflowStepSpec {
                step_id: "apply_patch".into(),
                title: "Write config".into(),
                action: "write_file".into(),
                args: json!({"port": 4824, "strict": true}),
                depends_on: vec!["gather_a".into(), "gather_b".into()],
                mutating: true,
                requires_approval: true,
                max_retries: 1,
                rollback_path: Some(target_file.clone()),
                consulted_node_ids: vec![],
            },
        ],
    };

    let created = store.create(spec, 1_000.0).expect("create");
    assert_eq!(
        created.next_wave(),
        Some(ExecutionWave::ParallelReadOnly(vec![
            "gather_a".into(),
            "gather_b".into()
        ]))
    );

    let gather_executions = AtomicUsize::new(0);
    let outcome = store
        .run_until_yield("wf_upgrade_1", 1_010.0, |step| {
            if step.action == "kg_read" {
                gather_executions.fetch_add(1, Ordering::SeqCst);
            }
            Ok(StepExecOutput {
                value: json!({"ok": true}),
                generated_artifacts: vec![],
            })
        })
        .expect("first run");

    let token = match outcome {
        WorkflowAdvanceOutcome::PausedForApproval {
            step_id,
            approval_token,
            ..
        } => {
            assert_eq!(step_id, "apply_patch");
            approval_token
        }
        other => panic!("expected PausedForApproval, got {other:?}"),
    };
    assert_eq!(gather_executions.load(Ordering::SeqCst), 2);

    // Simulate FULL APP RESTART: open a brand new store over the same directory!
    let restarted_store = DurableWorkflowStore::new(dir.path().join("workflows"));
    assert!(restarted_store
        .approve_step(
            "wf_upgrade_1",
            "apply_patch",
            "wrong-token",
            Some("owner@local"),
            1_020.0
        )
        .is_err());
    restarted_store
        .approve_step(
            "wf_upgrade_1",
            "apply_patch",
            &token,
            Some("owner@local"),
            1_020.0,
        )
        .expect("valid approval after restart");

    // First attempt at `apply_patch` fails transiently (max_retries = 1), then retry succeeds!
    let fail_first = restarted_store
        .run_until_yield("wf_upgrade_1", 1_025.0, |_step| {
            Err("transient lock busy".into())
        })
        .expect("transient fail");
    assert!(matches!(
        fail_first,
        WorkflowAdvanceOutcome::Failed {
            rolled_back: false,
            ..
        }
    ));

    restarted_store
        .retry_failed_step("wf_upgrade_1", "apply_patch", 1_030.0)
        .expect("retry");
    let done = restarted_store
        .run_until_yield("wf_upgrade_1", 1_035.0, |step| {
            if step.action == "kg_read" {
                gather_executions.fetch_add(1, Ordering::SeqCst);
            }
            std::fs::write(
                &target_file,
                "port = 4824
strict = true
",
            )
            .expect("mutate");
            Ok(StepExecOutput {
                value: json!({"patched": true}),
                generated_artifacts: vec!["artifact:config_toml".into()],
            })
        })
        .expect("complete");
    assert_eq!(
        done,
        WorkflowAdvanceOutcome::Completed {
            workflow_id: "wf_upgrade_1".into()
        }
    );
    // Upstream completed steps NEVER re-executed across pause/restart/retry!
    assert_eq!(gather_executions.load(Ordering::SeqCst), 2);

    let final_rec = restarted_store
        .load("wf_upgrade_1", 1_040.0)
        .expect("load")
        .expect("exists");
    let prov = final_rec.brain_provenance_payload();
    assert_eq!(prov["consulted_node_ids"], json!(["doc:arch", "doc:sec"]));
    assert_eq!(prov["generated_artifacts"], json!(["artifact:config_toml"]));

    // Verify automatic file rollback when a multi-step mutating workflow terminally fails!
    let rb_spec = WorkflowSpec {
        workflow_id: "wf_rollback_test".into(),
        goal: "Mutate then fail verification".into(),
        user_email: None,
        workspace_id: None,
        rollback_on_failure: true,
        steps: vec![
            WorkflowStepSpec {
                step_id: "mutate".into(),
                title: "Corrupt config".into(),
                action: "write_file".into(),
                args: json!({}),
                depends_on: vec![],
                mutating: true,
                requires_approval: false,
                max_retries: 0,
                rollback_path: Some(target_file.clone()),
                consulted_node_ids: vec![],
            },
            WorkflowStepSpec {
                step_id: "verify".into(),
                title: "Verify config".into(),
                action: "verify".into(),
                args: json!({}),
                depends_on: vec!["mutate".into()],
                mutating: false,
                requires_approval: false,
                max_retries: 0,
                rollback_path: None,
                consulted_node_ids: vec![],
            },
        ],
    };
    restarted_store.create(rb_spec, 2_000.0).expect("create rb");
    let rb_out = restarted_store
        .run_until_yield("wf_rollback_test", 2_010.0, |step| {
            if step.step_id == "mutate" {
                std::fs::write(&target_file, "BROKEN_STATE").expect("corrupt");
                Ok(StepExecOutput::default())
            } else {
                Err("verification failed".into())
            }
        })
        .expect("rb run");
    assert!(matches!(
        rb_out,
        WorkflowAdvanceOutcome::Failed {
            rolled_back: true,
            ..
        }
    ));
    assert_eq!(
        std::fs::read_to_string(&target_file).expect("restored"),
        "port = 4824
strict = true
"
    );
}
