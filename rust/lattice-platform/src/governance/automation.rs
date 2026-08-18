//! Automation intelligence (`latticeai/api/automation_intelligence.py`).
//!
//! Suggestion / install surface over the workspace-OS workflow store (and,
//! when present, `triggers_state.json`). Question mining reads
//! `conversation_messages` (empty in the seeded sandbox, so happy-path
//! fixtures still pin `questions_scanned: 0`). Live runs go through the
//! workflow executor.

use crate::governance::review_queue;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

mod mining;

use crate::governance::review_queue::{
    create_workflow, daily_memory_digest_definition, gate_read, gate_write, get_workflow,
    http_detail, json_ok, list_agent_runs, list_workflow_runs, list_workflows, now_iso,
    parse_object, require_field, require_user, string_field, update_workflow_metadata,
    GovernanceState, ReviewError,
};
use crate::governance::workflow_designer::{self, ToolContext};

/// Mounted (method, axum-path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/activity/runs"),
    ("POST", "/api/automation/install"),
    ("GET", "/api/automation/overview"),
    ("GET", "/api/automation/patterns"),
    ("POST", "/api/automation/run-now"),
    ("GET", "/api/automation/suggestions"),
    ("GET", "/automations/runs/combined"),
];

/// The automation intelligence router.
pub fn router(state: GovernanceState) -> Router {
    Router::new()
        .route("/api/automation/patterns", get(automation_patterns))
        .route("/api/automation/suggestions", get(automation_suggestions))
        .route("/api/automation/overview", get(automation_overview))
        .route("/api/automation/install", post(automation_install))
        .route("/api/automation/run-now", post(automation_run_now))
        .route("/api/activity/runs", get(activity_runs))
        .route("/automations/runs/combined", get(automations_runs_combined))
        .with_state(state)
}

async fn automation_patterns(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let mined = mining::mine(&state.data_dir, scope.as_deref());
    Ok(json_ok(&mined.patterns_body()))
}

async fn automation_suggestions(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let mined = mining::mine(&state.data_dir, scope.as_deref());
    Ok(json_ok(&mined.suggestions_body()))
}

async fn automation_overview(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let mined = mining::mine(&state.data_dir, scope.as_deref());
    let suggestions = mined.suggestions_body();
    let installed = installed_automations(&state, scope.as_deref());
    let mut body = OrderedMap::new();
    body.insert(
        "suggestions",
        suggestions.get("suggestions").cloned().unwrap_or(json!([])),
    );
    body.insert("questions_scanned", json!(mined.questions_scanned));
    body.insert("installed", Value::Array(installed));
    body.insert(
        "quality",
        suggestions.get("quality").cloned().unwrap_or(json!({})),
    );
    body.insert(
        "consent",
        suggestions.get("consent").cloned().unwrap_or(json!({})),
    );
    body.insert("generated_at", json!(now_iso()));
    Ok(json_ok(&body))
}

async fn automation_install(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let parsed = parse_object(&body)?;
    require_field(&parsed, "suggestion_id")?;
    let suggestion_id = string_field(&parsed, "suggestion_id");
    let mined = mining::mine(&state.data_dir, scope.as_deref());
    if mined.find_suggestion(&suggestion_id).is_none() {
        return Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Automation suggestion not found: {suggestion_id}"),
        ));
    }
    let enabled = parsed
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut definition = daily_memory_digest_definition(enabled);
    if let Some(metadata) = definition
        .get_mut("metadata")
        .and_then(Value::as_object_mut)
    {
        metadata.insert("created_from".into(), json!("automation_suggestion"));
        metadata.insert("suggestion_id".into(), json!(suggestion_id.clone()));
    }
    for workflow in list_workflows(&state, scope.as_deref()) {
        let metadata = workflow
            .get("metadata")
            .cloned()
            .unwrap_or_else(|| json!({}));
        if metadata.get("created_from").and_then(Value::as_str) == Some("automation_suggestion")
            && metadata.get("suggestion_id").and_then(Value::as_str) == Some(suggestion_id.as_str())
        {
            return Ok(already_installed(workflow, json!({"id": suggestion_id})));
        }
    }
    let workflow = create_workflow(
        &state,
        definition
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("automation"),
        json!([{"action": "agent"}]),
        definition
            .get("nodes")
            .cloned()
            .unwrap_or_else(|| json!([])),
        definition
            .get("metadata")
            .cloned()
            .unwrap_or_else(|| json!({})),
        Some(&user.email),
        scope.as_deref(),
    );
    let mut body = OrderedMap::new();
    body.insert("workflow", workflow);
    body.insert("suggestion", json!({"id": suggestion_id}));
    body.insert("enabled", json!(enabled));
    body.insert("already_installed", json!(false));
    Ok(json_ok(&body))
}

async fn automation_run_now(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let parsed = parse_object(&body)?;
    require_field(&parsed, "workflow_id")?;
    let workflow_id = string_field(&parsed, "workflow_id");
    let dry_run = parsed
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let workflow = match get_workflow(&state, &workflow_id, scope.as_deref()) {
        Ok(wf) => wf,
        Err(ReviewError::NotFound) => {
            return Err(http_detail(
                StatusCode::NOT_FOUND,
                &format!("Automation not found: {workflow_id}"),
            ));
        }
        Err(_) => {
            return Err(http_detail(
                StatusCode::NOT_FOUND,
                &format!("Automation not found: {workflow_id}"),
            ));
        }
    };
    if !is_automation_workflow(&workflow) {
        return Err(http_detail(
            StatusCode::NOT_FOUND,
            &format!("Workflow is not an installed automation: {workflow_id}"),
        ));
    }
    if dry_run {
        let report = dry_run_report(&workflow);
        let last_execution = build_last_execution(
            "dry_run",
            report.get("status").and_then(Value::as_str).unwrap_or("ok"),
            report.get("summary").and_then(Value::as_str).unwrap_or(""),
            None,
        );
        let _ = update_workflow_metadata(
            &state,
            &workflow_id,
            json!({"last_execution": last_execution}),
            scope.as_deref(),
        );
        let mut body = OrderedMap::new();
        body.insert("workflow_id", json!(workflow_id));
        body.insert("dry_run", json!(true));
        body.insert(
            "status",
            report.get("status").cloned().unwrap_or(json!("ok")),
        );
        body.insert("report", report);
        body.insert("last_execution", last_execution);
        return Ok(json_ok(&body));
    }
    let run = run_workflow_now(&state, &headers, &user, &workflow, scope.as_deref());
    let status = run
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("failed")
        .to_string();
    let run_id = run.get("id").and_then(Value::as_str).map(str::to_string);
    let summary = run
        .get("outputs")
        .and_then(|outputs| outputs.get("steps"))
        .and_then(Value::as_array)
        .map(|steps| format!("{} step(s) executed", steps.len()))
        .unwrap_or_else(|| "workflow run finished".to_string());
    let last_execution = build_last_execution("live", &status, &summary, run_id.as_deref());
    let _ = update_workflow_metadata(
        &state,
        &workflow_id,
        json!({"last_execution": last_execution}),
        scope.as_deref(),
    );
    let mut body = OrderedMap::new();
    body.insert("workflow_id", json!(workflow_id));
    body.insert("dry_run", json!(false));
    body.insert("status", json!(status));
    body.insert("run_id", json!(run_id));
    body.insert("run", run);
    body.insert("last_execution", last_execution);
    Ok(json_ok(&body))
}

fn run_workflow_now(
    state: &GovernanceState,
    headers: &HeaderMap,
    user: &lattice_auth::Identity,
    workflow: &Value,
    scope: Option<&str>,
) -> Value {
    let resolved = scope.unwrap_or("personal");
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let workspace = lattice_agent::sandbox::Workspace::new(&state.agent_root).ok();
    let tools = workspace.map(|ws| {
        crate::toolsurface::tools::ToolsState::new(
            std::sync::Arc::clone(&state.auth),
            ws,
            &state.data_dir,
        )
    });
    let ctx = tools.as_ref().map(|tools| ToolContext {
        tools,
        identity: user,
        headers,
    });
    workflow_designer::execute_workflow_now(
        &state.data_dir,
        workflow,
        resolved,
        user_email,
        json!({}),
        ctx.as_ref(),
    )
}

async fn activity_runs(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    Ok(json_ok(&combined_runs(
        &state,
        scope.as_deref(),
        query.limit,
    )))
}

async fn automations_runs_combined(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    Ok(json_ok(&combined_runs(
        &state,
        scope.as_deref(),
        query.limit,
    )))
}

#[derive(Debug, Default, serde::Deserialize)]
struct LimitQuery {
    limit: Option<i64>,
}

fn combined_runs(state: &GovernanceState, scope: Option<&str>, limit: Option<i64>) -> OrderedMap {
    let raw = limit.unwrap_or(20);
    let effective = if raw == 0 { 20 } else { raw };
    let capped = effective.clamp(1, 100) as usize;
    let mut rows = Vec::new();
    for run in list_agent_runs(state, scope) {
        rows.push(activity_run_row(run, "agent"));
    }
    for run in list_workflow_runs(state, scope, capped.max(100)) {
        rows.push(activity_run_row(run, "workflow"));
    }
    rows.sort_by(|a, b| {
        let sa = a.get("started_at").and_then(Value::as_str).unwrap_or("");
        let sb = b.get("started_at").and_then(Value::as_str).unwrap_or("");
        sb.cmp(sa)
    });
    let total = rows.len();
    rows.truncate(capped);
    let mut body = OrderedMap::new();
    body.insert("runs", Value::Array(rows));
    body.insert("total", json!(total as i64));
    body.insert("truncated", json!(total > capped));
    body
}

fn activity_run_row(mut run: Value, source: &str) -> Value {
    if let Some(obj) = run.as_object_mut() {
        let existing = obj
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        if existing == "workflow" || existing == "agent" {
            obj.insert("source".into(), json!(existing));
        } else if obj.get("workflow_id").is_some() && !obj.get("workflow_id").unwrap().is_null() {
            obj.insert("source".into(), json!("workflow"));
        } else if obj.get("agent_id").is_some() && !obj.get("agent_id").unwrap().is_null() {
            obj.insert("source".into(), json!("agent"));
        } else if !existing.is_empty() {
            obj.insert("source".into(), json!(existing));
        } else {
            obj.insert("source".into(), json!(source));
        }
    }
    run
}

fn installed_automations(state: &GovernanceState, scope: Option<&str>) -> Vec<Value> {
    let mut installed = Vec::new();
    for workflow in list_workflows(state, scope) {
        let metadata = workflow
            .get("metadata")
            .cloned()
            .unwrap_or_else(|| json!({}));
        let created = metadata
            .get("created_from")
            .and_then(Value::as_str)
            .unwrap_or("");
        if created != "automation_suggestion" && created != "brain_automation_recipe" {
            continue;
        }
        let mut row = OrderedMap::new();
        row.insert("id", workflow.get("id").cloned().unwrap_or(Value::Null));
        row.insert("name", workflow.get("name").cloned().unwrap_or(Value::Null));
        row.insert("created_from", json!(created));
        row.insert(
            "suggestion_id",
            metadata
                .get("suggestion_id")
                .cloned()
                .unwrap_or(Value::Null),
        );
        row.insert(
            "recipe_id",
            metadata.get("recipe_id").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "enabled",
            json!(metadata.get("automation_state").and_then(Value::as_str) == Some("enabled")),
        );
        row.insert(
            "requires_user_enable",
            json!(metadata
                .get("requires_user_enable")
                .and_then(Value::as_bool)
                .unwrap_or(true)),
        );
        row.insert(
            "creates",
            metadata
                .get("creates")
                .cloned()
                .unwrap_or_else(|| json!([])),
        );
        row.insert(
            "last_execution",
            metadata
                .get("last_execution")
                .cloned()
                .unwrap_or(Value::Null),
        );
        installed.push(review_queue::into_value(row));
    }
    installed
}

fn is_automation_workflow(workflow: &Value) -> bool {
    let metadata = workflow
        .get("metadata")
        .cloned()
        .unwrap_or_else(|| json!({}));
    matches!(
        metadata.get("created_from").and_then(Value::as_str),
        Some("automation_suggestion") | Some("brain_automation_recipe")
    )
}

fn dry_run_report(workflow: &Value) -> Value {
    let nodes = workflow
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let by_id: std::collections::HashMap<String, Value> = nodes
        .iter()
        .filter_map(|n| {
            n.get("id")
                .and_then(Value::as_str)
                .map(|id| (id.to_string(), n.clone()))
        })
        .collect();
    let mut current = nodes
        .iter()
        .find(|n| n.get("type").and_then(Value::as_str) == Some("trigger"))
        .cloned();
    let mut visited = std::collections::HashSet::new();
    let mut steps = Vec::new();
    while let Some(node) = current {
        let id = node
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !visited.insert(id.clone()) {
            break;
        }
        let ntype = node.get("type").and_then(Value::as_str).unwrap_or("");
        let config = node.get("config").cloned().unwrap_or_else(|| json!({}));
        let would = match ntype {
            "trigger" => {
                let trigger = config
                    .get("trigger")
                    .and_then(Value::as_str)
                    .unwrap_or("manual");
                let label = match trigger {
                    "interval" => "wait for the user-enabled schedule",
                    "brain_event" => "wait for new Brain knowledge",
                    "manual" => "start when the user asks",
                    _ => "start when triggered",
                };
                format!("skipped in a manual run ({label})")
            }
            "agent" => {
                let prompt = config
                    .get("prompt")
                    .or_else(|| config.get("goal"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let clipped: String = prompt.chars().take(160).collect();
                if clipped.is_empty() {
                    "ask the local agent to draft a review item".into()
                } else {
                    format!("ask the local agent to draft: {clipped}")
                }
            }
            "output" => "deliver the draft to the review inbox".into(),
            other => format!("run node '{other}'"),
        };
        let mut step = OrderedMap::new();
        step.insert("node", json!(id));
        step.insert("type", json!(ntype));
        step.insert(
            "name",
            node.get("name")
                .cloned()
                .unwrap_or_else(|| node.get("id").cloned().unwrap_or(Value::Null)),
        );
        step.insert("would", json!(would));
        steps.push(review_queue::into_value(step));
        current = node
            .get("next")
            .and_then(Value::as_str)
            .and_then(|next| by_id.get(next).cloned());
    }
    let executable = steps
        .iter()
        .filter(|s| {
            !matches!(
                s.get("type").and_then(Value::as_str),
                Some("trigger") | Some("output") | Some("condition")
            )
        })
        .count();
    let metadata = workflow
        .get("metadata")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let creates = metadata
        .get("creates")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut summary =
        format!("{executable} step(s) would run locally and produce a reviewable draft");
    if !creates.is_empty() {
        let names: Vec<String> = creates
            .iter()
            .take(3)
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
        summary.push_str(" (");
        summary.push_str(&names.join(", "));
        summary.push(')');
    }
    summary.push_str("; no external actions, nothing is written until you approve.");
    json!({
        "mode": "dry_run",
        "status": "ok",
        "steps": steps,
        "summary": summary,
        "side_effects": false,
        "validation_errors": []
    })
}

fn build_last_execution(mode: &str, status: &str, summary: &str, run_id: Option<&str>) -> Value {
    let clipped: String = summary.chars().take(300).collect();
    json!({
        "mode": mode,
        "status": status,
        "summary": clipped,
        "run_id": run_id,
        "finished_at": now_iso()
    })
}

fn already_installed(workflow: Value, suggestion: Value) -> Response {
    let enabled = workflow
        .get("metadata")
        .and_then(|m| m.get("automation_state"))
        .and_then(Value::as_str)
        == Some("enabled");
    let mut body = OrderedMap::new();
    body.insert("workflow", workflow);
    body.insert("suggestion", suggestion);
    body.insert("enabled", json!(enabled));
    body.insert("already_installed", json!(true));
    json_ok(&body)
}
