//! Minimal real workflow executor.
//!
//! Walks the definition's nodes (or the legacy `steps` list). Step kinds that
//! map to a native action run for real; everything else completes as
//! `{status: "manual", detail}` rather than pretending. The run always reaches
//! a terminal status (`ok` / `failed` / `partial`) unless it pauses for an
//! explicit approval gate.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use axum::http::HeaderMap;
use lattice_auth::Identity;
use lattice_core::db::tables::GRAPH_DB;
use lattice_core::graph_write::types::IngestEventRequest;
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};

use crate::toolsurface::tools::ToolsState;

use super::store::WorkflowStore;
use super::time::now_iso;
use super::DEFAULT_WORKSPACE_ID;
use crate::toolsurface::mcp;

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

    for step in steps {
        let id = step_id(&step);
        if skipping {
            if resume.after_node.as_deref() == Some(id.as_str()) {
                skipping = false;
            }
            continue;
        }
        let kind = step_kind(&step);
        if requires_approval(&step) && resume.approved_node.as_deref() != Some(id.as_str()) {
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
            &step, &kind, inputs, tools, data_dir, scope, user_email, store,
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

fn execute_tool(
    step: &Value,
    config: &Value,
    inputs: &Value,
    tools: Option<&ToolContext<'_>>,
) -> Value {
    let name = config
        .get("tool")
        .or_else(|| config.get("name"))
        .or_else(|| step.get("tool"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if name.is_empty() {
        return json!({
            "status": "manual",
            "detail": "tool step has no tool name",
        });
    }
    let Some(ctx) = tools else {
        return json!({
            "status": "manual",
            "detail": format!("tool '{name}' has no governed dispatch attached to this process"),
        });
    };
    if !mcp::is_native_tool(&name) {
        return json!({
            "status": "manual",
            "detail": format!("tool '{name}' has no native executor"),
        });
    }
    let mut args = config
        .get("args")
        .cloned()
        .or_else(|| config.get("arguments").cloned())
        .unwrap_or(json!({}));
    if let (Some(dst), Some(src)) = (args.as_object_mut(), inputs.as_object()) {
        for (key, value) in src {
            dst.entry(key.clone()).or_insert(value.clone());
        }
    }
    match mcp::dispatch(
        Some(ctx.tools),
        Path::new(""),
        ctx.identity,
        ctx.headers,
        &name,
        &args,
    ) {
        Ok(result) => json!({
            "status": "ok",
            "detail": format!("ran governed tool '{name}'"),
            "result": result,
        }),
        Err(error) => json!({
            "status": "failed",
            "detail": format!("tool '{name}': {error:?}"),
        }),
    }
}

fn execute_notification(step: &Value, config: &Value, scope: &str, store: &WorkflowStore) -> Value {
    let message = config
        .get("message")
        .or_else(|| config.get("value"))
        .or_else(|| step.get("message"))
        .and_then(Value::as_str)
        .unwrap_or("workflow notification");
    let event = store.record_notification(
        scope,
        json!({
            "node": step_id(step),
            "message": message,
        }),
    );
    json!({
        "status": "ok",
        "detail": "recorded a workspace timeline notification",
        "result": event,
    })
}

fn execute_graph(
    step: &Value,
    config: &Value,
    data_dir: Option<&Path>,
    scope: &str,
    user_email: Option<&str>,
) -> Value {
    let Some(data_dir) = data_dir else {
        return json!({
            "status": "manual",
            "detail": "graph writer is not attached to this process",
        });
    };
    let db = data_dir.join(GRAPH_DB);
    if !db.exists() {
        return json!({
            "status": "manual",
            "detail": "knowledge graph store is not present",
        });
    }
    let Ok(store) = lattice_core::db::Store::open(&db) else {
        return json!({
            "status": "failed",
            "detail": "could not open the knowledge graph store",
        });
    };
    let blobs = data_dir.join("knowledge_graph_blobs");
    let Ok(writer) = GraphWriter::open(std::sync::Arc::new(store), blobs) else {
        return json!({
            "status": "failed",
            "detail": "could not open the graph writer",
        });
    };
    let title = config
        .get("title")
        .or_else(|| step.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("workflow graph step");
    let event_type = config
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or("workflow_step");
    let mut metadata = Map::new();
    metadata.insert("node".into(), json!(step_id(step)));
    let request = IngestEventRequest {
        event_type: event_type.to_string(),
        title: title.to_string(),
        user_email: user_email.map(str::to_string),
        source: Some("workflow".into()),
        workspace_id: Some(if scope.is_empty() {
            DEFAULT_WORKSPACE_ID.to_string()
        } else {
            scope.to_string()
        }),
        metadata,
        ..IngestEventRequest::default()
    };
    match writer.ingest_event(&request) {
        Ok(outcome) => json!({
            "status": "ok",
            "detail": "ingested a graph event",
            "result": outcome.to_json_brief(),
        }),
        Err(error) => json!({
            "status": "failed",
            "detail": format!("graph ingest failed: {error}"),
        }),
    }
}

fn execute_output(step: &Value, config: &Value, scope: &str, store: &WorkflowStore) -> Value {
    let value = config
        .get("value")
        .or_else(|| step.get("value"))
        .cloned()
        .unwrap_or(json!("output recorded"));
    // An output that names the review inbox is a notification.
    let review_inbox = config.get("review_queue").and_then(Value::as_bool) == Some(true)
        || config
            .get("value")
            .and_then(Value::as_str)
            .is_some_and(|text| text.to_ascii_lowercase().contains("review"));
    if review_inbox {
        let event = store.record_notification(
            scope,
            json!({
                "node": step_id(step),
                "message": value,
            }),
        );
        return json!({
            "status": "ok",
            "detail": "delivered output to the workspace timeline (review inbox)",
            "result": event,
        });
    }
    json!({
        "status": "ok",
        "detail": "output recorded",
        "result": value,
    })
}

/// Resolve the data dir that owns this store (parent of `workspace_os.json`).
pub(crate) fn store_data_dir(store: &WorkflowStore) -> PathBuf {
    store
        .path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(test)]
mod tests {
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
}
