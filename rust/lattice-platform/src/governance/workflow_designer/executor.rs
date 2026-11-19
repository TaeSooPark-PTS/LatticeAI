//! Minimal real workflow executor.
//!
//! Walks the definition's nodes (or the legacy `steps` list). Step kinds that
//! map to a native action run for real; everything else completes as
//! `{status: "manual", detail}` rather than pretending. The run always reaches
//! a terminal status (`ok` / `failed` / `partial`) unless it pauses for an
//! explicit approval gate.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use axum::http::HeaderMap;
use lattice_auth::Identity;
use serde_json::{json, Value};

use crate::toolsurface::tools::ToolsState;

use super::store::WorkflowStore;
use super::time::now_iso;

#[path = "executor_steps.rs"]
mod steps;
pub(crate) use steps::store_data_dir;
use steps::{execute_graph, execute_notification, execute_output, execute_tool};

/// Optional governed tool dispatch. Absent → tool steps complete as manual.
pub(crate) struct ToolContext<'a> {
    pub tools: &'a ToolsState,
    pub identity: &'a Identity,
    pub headers: &'a HeaderMap,
}

/// How far a (re)start should walk.
#[derive(Debug, Clone, Default)]
pub(crate) struct ResumeFrom {
    /// Skip nodes until this id has been seen, then continue at its successor.
    pub after_node: Option<String>,
    /// The approval step itself is recorded as approved rather than re-gated.
    pub approved_node: Option<String>,
}

/// Record a queued run, walk every step, persist a terminal (or paused) row.
pub(crate) fn start_run(
    store: &WorkflowStore,
    workflow: &Value,
    scope: &str,
    user_email: Option<&str>,
    inputs: Value,
    tools: Option<&ToolContext<'_>>,
    data_dir: Option<&Path>,
) -> Value {
    let workflow_id = workflow
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let name = workflow
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("workflow")
        .to_string();
    let run = store.record_workflow_run(
        &workflow_id,
        &name,
        "queued",
        vec![json!({"event": "workflow_started", "status": "queued", "timestamp": now_iso()})],
        json!({}),
        user_email,
        scope,
        None,
    );
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let accepted = store
        .update_workflow_run(
            &run_id,
            scope,
            &[
                ("execution_mode", json!("async")),
                ("inputs", inputs.clone()),
            ],
        )
        .unwrap_or(run);
    drive_run(
        store,
        workflow,
        &run_id,
        scope,
        user_email,
        &inputs,
        tools,
        data_dir,
        ResumeFrom::default(),
        accepted
            .get("timeline")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
    );
    serde_json::to_value(&accepted).unwrap_or(json!({}))
}

/// Continue a run that is genuinely `awaiting_approval`.
pub(crate) fn resume_paused(
    store: &WorkflowStore,
    workflow: &Value,
    run: &Value,
    scope: &str,
    approved: bool,
    tools: Option<&ToolContext<'_>>,
    data_dir: Option<&Path>,
) -> Result<Value, String> {
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "run is missing an id".to_string())?
        .to_string();
    let pause = run.get("pause").cloned().unwrap_or(json!({}));
    let node = pause
        .get("node")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "run is not awaiting approval".to_string())?
        .to_string();
    if !approved {
        let mut timeline = run
            .get("timeline")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        timeline.push(json!({
            "event": "approval_rejected",
            "node": node,
            "status": "rejected",
            "timestamp": now_iso(),
        }));
        let updated = store
            .update_workflow_run(
                &run_id,
                scope,
                &[
                    ("status", json!("rejected")),
                    ("timeline", json!(timeline)),
                    ("pause", Value::Null),
                    ("completed_at", json!(now_iso())),
                ],
            )
            .map_err(|()| "run disappeared while rejecting".to_string())?;
        return Ok(serde_json::to_value(&updated).unwrap_or(json!({})));
    }
    let mut timeline = run
        .get("timeline")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    timeline.push(json!({
        "event": "approval_granted",
        "node": node,
        "status": "ok",
        "timestamp": now_iso(),
    }));
    let inputs = run.get("inputs").cloned().unwrap_or_else(|| json!({}));
    let user_email = run.get("user_email").and_then(Value::as_str);
    Ok(drive_run(
        store,
        workflow,
        &run_id,
        scope,
        user_email,
        &inputs,
        tools,
        data_dir,
        ResumeFrom {
            after_node: Some(node.clone()),
            approved_node: Some(node),
        },
        timeline,
    ))
}

/// Whether this run is parked on an approval step.
pub(crate) fn is_awaiting_approval(run: &Value) -> bool {
    let pause = run.get("pause").cloned().unwrap_or(json!({}));
    run.get("status").and_then(Value::as_str) == Some("awaiting_approval")
        && pause
            .get("node")
            .and_then(Value::as_str)
            .is_some_and(|node| !node.is_empty())
}

/// Execute an already-persisted workflow and return the terminal run row.
pub fn execute_workflow_now(
    data_dir: impl AsRef<Path>,
    workflow: &Value,
    scope: &str,
    user_email: Option<&str>,
    inputs: Value,
    tools: Option<&ToolContext<'_>>,
) -> Value {
    let store = WorkflowStore::open(data_dir.as_ref().join("workspace_os.json"));
    let accepted = start_run(
        &store,
        workflow,
        scope,
        user_email,
        inputs,
        tools,
        Some(data_dir.as_ref()),
    );
    let run_id = accepted
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    store.get_workflow_run(run_id, scope).unwrap_or(accepted)
}

#[allow(clippy::too_many_arguments)]
fn drive_run(
    store: &WorkflowStore,
    workflow: &Value,
    run_id: &str,
    scope: &str,
    user_email: Option<&str>,
    inputs: &Value,
    tools: Option<&ToolContext<'_>>,
    data_dir: Option<&Path>,
    resume: ResumeFrom,
    mut timeline: Vec<Value>,
) -> Value {
    let _ = store.update_workflow_run(
        run_id,
        scope,
        &[
            ("status", json!("running")),
            ("started_at", json!(now_iso())),
            ("pause", Value::Null),
        ],
    );
    let steps = plan_steps(workflow);
    let mut results: Vec<Value> = Vec::new();
    let mut skipping = resume.after_node.is_some();
    let mut paused_on: Option<String> = None;
    let mut saw_failure = false;
    let mut saw_success = false;
    let mut saw_manual = false;

    for step in &steps {
        let id = step_id(step);
        if skipping {
            if resume.after_node.as_deref() == Some(id.as_str()) {
                skipping = false;
            }
            continue;
        }
        let kind = step_kind(step);
        if requires_approval(step) && resume.approved_node.as_deref() != Some(id.as_str()) {
            paused_on = Some(id.clone());
            timeline.push(json!({
                "event": "awaiting_approval",
                "node": id,
                "type": kind,
                "status": "awaiting_approval",
                "timestamp": now_iso(),
            }));
            break;
        }
        let outcome = execute_step(
            step, &kind, inputs, tools, data_dir, scope, user_email, store,
        );
        let status = outcome
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("manual")
            .to_string();
        match status.as_str() {
            "failed" => saw_failure = true,
            "manual" => saw_manual = true,
            _ => saw_success = true,
        }
        timeline.push(json!({
            "event": "step_completed",
            "node": id,
            "type": kind,
            "status": status,
            "detail": outcome.get("detail").cloned().unwrap_or(Value::Null),
            "result": outcome.get("result").cloned().unwrap_or(Value::Null),
            "timestamp": now_iso(),
        }));
        results.push(json!({
            "node": id,
            "type": kind,
            "status": outcome.get("status").cloned().unwrap_or(json!("manual")),
            "detail": outcome.get("detail").cloned().unwrap_or(Value::Null),
            "result": outcome.get("result").cloned().unwrap_or(Value::Null),
        }));
        if status == "failed" && kind != "manual" {
            // A native action failed: stop walking so later steps do not run
            // against a half-applied world.
            break;
        }
    }

    if let Some(node) = paused_on {
        if let Some(dir) = data_dir {
            persist_durable_checkpoint(
                dir,
                workflow,
                run_id,
                scope,
                user_email,
                &steps,
                &results,
                Some(node.as_str()),
                "awaiting_approval",
            );
        }
        let pause = json!({"node": node, "reason": "step requires approval"});
        return serde_json::to_value(
            store
                .update_workflow_run(
                    run_id,
                    scope,
                    &[
                        ("status", json!("awaiting_approval")),
                        ("timeline", json!(timeline)),
                        ("outputs", json!({"steps": results})),
                        ("pause", pause),
                    ],
                )
                .unwrap_or_default(),
        )
        .unwrap_or(json!({}));
    }

    let status = terminal_status(saw_failure, saw_success, saw_manual, results.is_empty());
    if let Some(dir) = data_dir {
        persist_durable_checkpoint(
            dir, workflow, run_id, scope, user_email, &steps, &results, None, status,
        );
    }
    timeline.push(json!({
        "event": "workflow_finished",
        "status": status,
        "timestamp": now_iso(),
    }));
    serde_json::to_value(
        store
            .update_workflow_run(
                run_id,
                scope,
                &[
                    ("status", json!(status)),
                    ("timeline", json!(timeline)),
                    ("outputs", json!({"steps": results})),
                    ("pause", Value::Null),
                    ("completed_at", json!(now_iso())),
                ],
            )
            .unwrap_or_default(),
    )
    .unwrap_or(json!({}))
}

#[allow(clippy::too_many_arguments)]
fn persist_durable_checkpoint(
    data_dir: &Path,
    workflow: &Value,
    run_id: &str,
    scope: &str,
    user_email: Option<&str>,
    steps: &[Value],
    results: &[Value],
    paused_node: Option<&str>,
    status: &str,
) {
    use lattice_agent::kernel::workflow::{
        DurableWorkflowStore, WorkflowRunRecord, WorkflowSpec, WorkflowStatus, WorkflowStepSpec,
        WorkflowStepStatus,
    };

    if steps.is_empty() || run_id.trim().is_empty() {
        return;
    }
    let safe_id: String = run_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if safe_id.is_empty() {
        return;
    }
    let goal = workflow
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("workflow")
        .to_string();
    let mut step_specs = Vec::with_capacity(steps.len());
    let mut prev_id: Option<String> = None;
    let mut seen_ids = HashSet::new();
    for (idx, step) in steps.iter().enumerate() {
        let mut sid = step_id(step);
        if sid.is_empty() || !seen_ids.insert(sid.clone()) {
            sid = format!("step_{idx}");
            seen_ids.insert(sid.clone());
        }
        let kind = step_kind(step);
        let depends_on = prev_id.iter().cloned().collect();
        step_specs.push(WorkflowStepSpec {
            step_id: sid.clone(),
            title: step
                .get("label")
                .or_else(|| step.get("name"))
                .and_then(Value::as_str)
                .unwrap_or(&kind)
                .to_string(),
            action: kind,
            args: step.get("config").cloned().unwrap_or_else(|| json!({})),
            depends_on,
            mutating: matches!(step_kind(step).as_str(), "tool" | "graph" | "notification"),
            requires_approval: requires_approval(step),
            max_retries: 0,
            rollback_path: None,
            consulted_node_ids: Vec::new(),
        });
        prev_id = Some(sid);
    }
    let spec = WorkflowSpec {
        workflow_id: safe_id,
        goal,
        user_email: user_email.map(str::to_string),
        workspace_id: Some(scope.to_string()),
        rollback_on_failure: false,
        steps: step_specs,
    };
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let Ok(mut record) = WorkflowRunRecord::from_spec(spec, now_secs) else {
        return;
    };
    for result in results {
        let node = result.get("node").and_then(Value::as_str).unwrap_or("");
        if let Some(step_state) = record.steps.get_mut(node) {
            let st = result.get("status").and_then(Value::as_str).unwrap_or("ok");
            step_state.attempts = 1;
            step_state.status = if st == "failed" {
                step_state.last_error = result
                    .get("detail")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                WorkflowStepStatus::Failed
            } else {
                WorkflowStepStatus::Completed
            };
        }
    }
    if let Some(node) = paused_node {
        if let Some(step_state) = record.steps.get_mut(node) {
            step_state.status = WorkflowStepStatus::WaitingApproval;
        }
        record.status = WorkflowStatus::WaitingApproval;
    } else {
        record.status = match status {
            "failed" => WorkflowStatus::Failed,
            _ => WorkflowStatus::Completed,
        };
    }
    record.updated_at = now_secs;
    let durable_store = DurableWorkflowStore::new(data_dir.join("durable_workflows"));
    let _ = durable_store.save(&record);
}

fn terminal_status(failed: bool, success: bool, manual: bool, empty: bool) -> &'static str {
    if empty {
        return "ok";
    }
    if failed && success {
        return "partial";
    }
    if failed {
        return "failed";
    }
    if success && manual {
        return "partial";
    }
    "ok"
}

fn plan_steps(workflow: &Value) -> Vec<Value> {
    let nodes = workflow
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if !nodes.is_empty() {
        return walk_nodes(&nodes);
    }
    workflow
        .get("steps")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn walk_nodes(nodes: &[Value]) -> Vec<Value> {
    let by_id: HashMap<String, Value> = nodes
        .iter()
        .filter_map(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .map(|id| (id.to_string(), node.clone()))
        })
        .collect();
    let start = nodes
        .iter()
        .find(|node| node.get("type").and_then(Value::as_str) == Some("trigger"))
        .cloned()
        .or_else(|| nodes.first().cloned());
    let mut ordered = Vec::new();
    let mut seen = HashSet::new();
    let mut current = start;
    while let Some(node) = current {
        let id = node
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !id.is_empty() && !seen.insert(id.clone()) {
            break;
        }
        let ntype = node.get("type").and_then(Value::as_str).unwrap_or("");
        let next = if ntype == "condition" {
            node.get("branches")
                .and_then(|branches| branches.get("true"))
                .and_then(Value::as_str)
                .map(str::to_string)
        } else {
            node.get("next").and_then(Value::as_str).map(str::to_string)
        };
        ordered.push(node);
        current = next.and_then(|id| by_id.get(&id).cloned());
    }
    ordered
}

fn step_id(step: &Value) -> String {
    step.get("id")
        .or_else(|| step.get("node"))
        .and_then(Value::as_str)
        .unwrap_or("step")
        .to_string()
}

fn step_kind(step: &Value) -> String {
    step.get("type")
        .or_else(|| step.get("action"))
        .or_else(|| step.get("kind"))
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string()
}

fn requires_approval(step: &Value) -> bool {
    let kind = step_kind(step);
    if kind == "approval" {
        return true;
    }
    let config = step.get("config").cloned().unwrap_or(json!({}));
    config.get("requires_approval").and_then(Value::as_bool) == Some(true)
        || config.get("await_approval").and_then(Value::as_bool) == Some(true)
        || step.get("requires_approval").and_then(Value::as_bool) == Some(true)
}

#[allow(clippy::too_many_arguments)]
fn execute_step(
    step: &Value,
    kind: &str,
    inputs: &Value,
    tools: Option<&ToolContext<'_>>,
    data_dir: Option<&Path>,
    scope: &str,
    user_email: Option<&str>,
    store: &WorkflowStore,
) -> Value {
    let config = step.get("config").cloned().unwrap_or(json!({}));
    match kind {
        "trigger" => json!({
            "status": "ok",
            "detail": "trigger already fired for this run",
        }),
        "tool" => execute_tool(step, &config, inputs, tools),
        "notification" => execute_notification(step, &config, scope, store),
        "graph" => execute_graph(step, &config, data_dir, scope, user_email),
        "output" => execute_output(step, &config, scope, store),
        "condition" => json!({
            "status": "manual",
            "detail": "no native condition evaluator; defaulted to the recorded true branch when one exists",
        }),
        other => json!({
            "status": "manual",
            "detail": format!("no native executor for step kind '{other}'"),
        }),
    }
}

#[cfg(test)]
#[path = "executor_tests.rs"]
mod tests;
