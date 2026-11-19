use super::*;
use crate::governance::workflow_designer::definition::legacy_steps_from_nodes;

fn store() -> (tempfile::TempDir, WorkflowStore) {
    let dir = tempfile::tempdir().expect("tmp");
    let store = WorkflowStore::open(dir.path().join("workspace_os.json"));
    (dir, store)
}

fn wf(nodes: Vec<Value>) -> Value {
    json!({
        "id": "workflow-test",
        "name": "test",
        "nodes": nodes,
        "steps": legacy_steps_from_nodes(&nodes),
        "workspace_id": "personal",
        "metadata": {},
    })
}

#[test]
fn a_trigger_output_workflow_reaches_ok_with_step_results() {
    let (dir, store) = store();
    let workflow = store.create_workflow(
        "test",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "name": "Manual", "config": {"trigger": "manual"}, "next": "output"}),
            json!({"id": "output", "type": "output", "name": "Out", "config": {"value": "done"}, "next": null}),
        ],
        json!({}),
        None,
        "personal",
        None,
    );
    let workflow = serde_json::to_value(&workflow).unwrap();
    let accepted = start_run(
        &store,
        &workflow,
        "personal",
        None,
        json!({}),
        None,
        Some(dir.path()),
    );
    let run_id = accepted["id"].as_str().unwrap();
    let run = store.get_workflow_run(run_id, "personal").expect("run");
    assert_eq!(run["status"], "ok", "{run}");
    let steps = run["outputs"]["steps"].as_array().expect("steps");
    assert_eq!(steps.len(), 2);
    assert_eq!(steps[0]["node"], "trigger");
    assert_eq!(steps[0]["status"], "ok");
    assert_eq!(steps[1]["node"], "output");
    assert_eq!(steps[1]["status"], "ok");
    let events: Vec<&str> = run["timeline"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|item| item.get("event").and_then(Value::as_str))
        .collect();
    assert!(events.contains(&"step_completed"));
    assert!(events.contains(&"workflow_finished"));
}

#[test]
fn unknown_kinds_complete_as_manual_and_the_run_is_partial() {
    let (dir, store) = store();
    let workflow = store.create_workflow(
        "agent-wf",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "config": {}, "next": "draft"}),
            json!({"id": "draft", "type": "agent", "config": {"prompt": "draft"}, "next": "output"}),
            json!({"id": "output", "type": "output", "config": {}, "next": null}),
        ],
        json!({}),
        None,
        "personal",
        None,
    );
    let workflow = serde_json::to_value(&workflow).unwrap();
    let accepted = start_run(
        &store,
        &workflow,
        "personal",
        None,
        json!({}),
        None,
        Some(dir.path()),
    );
    let run = store
        .get_workflow_run(accepted["id"].as_str().unwrap(), "personal")
        .unwrap();
    assert_eq!(run["status"], "partial", "{run}");
    let draft = run["outputs"]["steps"]
        .as_array()
        .unwrap()
        .iter()
        .find(|step| step["node"] == "draft")
        .unwrap();
    assert_eq!(draft["status"], "manual");
}

#[test]
fn resume_walks_past_an_approval_step_and_409s_only_when_not_awaiting() {
    let (dir, store) = store();
    let workflow = store.create_workflow(
        "gated",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "config": {}, "next": "gate"}),
            json!({"id": "gate", "type": "output", "config": {"requires_approval": true, "value": "hold"}, "next": "done"}),
            json!({"id": "done", "type": "output", "config": {"value": "after"}, "next": null}),
        ],
        json!({}),
        None,
        "personal",
        None,
    );
    let workflow = serde_json::to_value(&workflow).unwrap();
    let accepted = start_run(
        &store,
        &workflow,
        "personal",
        None,
        json!({}),
        None,
        Some(dir.path()),
    );
    let run_id = accepted["id"].as_str().unwrap();
    let paused = store.get_workflow_run(run_id, "personal").unwrap();
    assert_eq!(paused["status"], "awaiting_approval", "{paused}");
    assert!(is_awaiting_approval(&paused));
    let finished = resume_paused(
        &store,
        &workflow,
        &paused,
        "personal",
        true,
        None,
        Some(dir.path()),
    )
    .expect("resume");
    assert_eq!(finished["status"], "ok", "{finished}");
    assert!(!is_awaiting_approval(&finished));
    assert!(resume_paused(
        &store,
        &workflow,
        &finished,
        "personal",
        true,
        None,
        Some(dir.path()),
    )
    .is_err());
}

#[test]
fn a_notification_step_writes_the_workspace_timeline() {
    let (dir, store) = store();
    let workflow = store.create_workflow(
        "note",
        vec![],
        vec![
            json!({"id": "trigger", "type": "trigger", "config": {}, "next": "ping"}),
            json!({"id": "ping", "type": "notification", "config": {"message": "hello"}, "next": null}),
        ],
        json!({}),
        None,
        "personal",
        None,
    );
    let workflow = serde_json::to_value(&workflow).unwrap();
    let accepted = start_run(
        &store,
        &workflow,
        "personal",
        None,
        json!({}),
        None,
        Some(dir.path()),
    );
    let run = store
        .get_workflow_run(accepted["id"].as_str().unwrap(), "personal")
        .unwrap();
    assert_eq!(run["status"], "ok", "{run}");
    let ping = &run["outputs"]["steps"].as_array().unwrap()[1];
    assert_eq!(ping["status"], "ok");
}

#[test]
fn wf_helper_still_describes_a_linear_plan() {
    let planned = plan_steps(&wf(vec![
        json!({"id": "trigger", "type": "trigger", "next": "t"}),
        json!({"id": "t", "type": "tool", "config": {"tool": "list_dir"}, "next": null}),
    ]));
    assert_eq!(planned.len(), 2);
}
