//! HTTP handlers for the workflow designer family.

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::response::json_response;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use super::contract::workflow_run_contract;
use super::definition::{
    export_workflow, import_workflow, legacy_steps_from_nodes, validate_definition,
};
use super::recipes::{build_recipe_workflow, find_installed_recipe, recipe_as_dict, RECIPES};
use super::time::now_iso;
use super::{
    is_awaiting_approval, ok, require_user, resume_paused, scope_from_request, start_run,
    store_data_dir, WorkflowDesignerState, ACTIVE_STATUSES, DEFAULT_WORKSPACE_ID,
};

// ── body parsing ─────────────────────────────────────────────────────────────

pub(crate) fn problem(kind: &str, loc: Value, msg: &str, input: Value) -> OrderedMap {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!(kind));
    entry.insert("loc", loc);
    entry.insert("msg", json!(msg));
    entry.insert("input", input);
    entry
}

pub(crate) fn validation_errors(problems: &[OrderedMap]) -> Response {
    let rendered: Vec<String> = problems
        .iter()
        .filter_map(|entry| serde_json::to_string(entry).ok())
        .collect();
    json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        &format!("{{\"detail\":[{}]}}", rendered.join(",")),
        None,
    )
}

pub(crate) fn parse_object(bytes: &[u8]) -> Result<Map<String, Value>, Response> {
    let parsed: Value = match serde_json::from_slice(bytes) {
        Ok(value) => value,
        Err(error) => {
            return Err(validation_errors(&[problem(
                "json_invalid",
                json!(["body", 0]),
                "JSON decode error",
                json!({"error": error.to_string()}),
            )]))
        }
    };
    parsed.as_object().cloned().ok_or_else(|| {
        validation_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
        )])
    })
}

// ── handlers ─────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub(crate) struct ListQuery {
    q: Option<String>,
    workspace_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct LimitQuery {
    limit: Option<i64>,
    workspace_id: Option<String>,
}

pub(crate) async fn list_definitions(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    Query(query): Query<ListQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let workflows = state
        .store
        .list_workflows(query.q.as_deref().unwrap_or(""), &scope);
    let mut body = OrderedMap::new();
    body.insert("workflows", json!(workflows));
    Ok(ok(&body))
}

pub(crate) async fn create_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    let name = match object.get("name") {
        None => {
            return Err(validation_errors(&[problem(
                "missing",
                json!(["body", "name"]),
                "Field required",
                Value::Object(object),
            )]))
        }
        Some(Value::String(text)) => text.clone(),
        Some(other) => {
            return Err(validation_errors(&[problem(
                "string_type",
                json!(["body", "name"]),
                "Input should be a valid string",
                other.clone(),
            )]))
        }
    };
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let nodes = object
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let metadata = object.get("metadata").cloned().unwrap_or_else(|| json!({}));
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    if !errors.is_empty() {
        let mut detail = OrderedMap::new();
        detail.insert("validation_errors", json!(errors));
        let mut body = OrderedMap::new();
        body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
        return Ok(json_response(
            StatusCode::BAD_REQUEST,
            &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
            None,
        ));
    }
    let steps = legacy_steps_from_nodes(&nodes);
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, "pending"));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let workflow = state.store.create_workflow(
        &name,
        steps,
        nodes,
        metadata,
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

pub(crate) async fn get_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    match state.store.get_workflow(&workflow_id, &scope) {
        Ok(workflow) => {
            let mut body = OrderedMap::new();
            body.insert("workflow", workflow);
            Ok(ok(&body))
        }
        Err(()) => Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow not found: {workflow_id}"),
        )),
    }
}

pub(crate) async fn update_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let name = object.get("name").and_then(Value::as_str);
    let nodes = object.get("nodes").and_then(Value::as_array).cloned();
    if let Some(nodes) = nodes.as_ref() {
        let errors = validate_definition(&json!({"name": name.unwrap_or("wf"), "nodes": nodes}));
        if !errors.is_empty() {
            let mut detail = OrderedMap::new();
            detail.insert("validation_errors", json!(errors));
            let mut body = OrderedMap::new();
            body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
            return Ok(json_response(
                StatusCode::BAD_REQUEST,
                &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
                None,
            ));
        }
    }
    match state.store.update_workflow_definition(
        &workflow_id,
        &scope,
        name,
        nodes,
        object.get("metadata").cloned(),
    ) {
        Ok(workflow) => {
            let mut body = OrderedMap::new();
            body.insert("workflow", workflow);
            Ok(ok(&body))
        }
        Err(()) => Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow not found: {workflow_id}"),
        )),
    }
}

pub(crate) async fn validate_workflow(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    require_user(&state, &headers)?;
    let name = object
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Draft");
    let nodes = object
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    let mut body = OrderedMap::new();
    body.insert("ok", json!(errors.is_empty()));
    body.insert("errors", json!(errors));
    Ok(ok(&body))
}

pub(crate) async fn run_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = if body.is_empty() {
        Map::new()
    } else {
        parse_object(&body).unwrap_or_default()
    };
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let workflow = state
        .store
        .get_workflow(&workflow_id, &scope)
        .map_err(|_| {
            detail_error(
                StatusCode::NOT_FOUND,
                &format!("Workflow not found: {workflow_id}"),
            )
        })?;
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let inputs = object.get("inputs").cloned().unwrap_or_else(|| json!({}));
    // The accept payload is the queued snapshot. The executor then walks every
    // step so list/replay/stop see a terminal (or paused) row.
    let response_run = start_run(
        &state.store,
        &workflow,
        &scope,
        user_email,
        inputs,
        None,
        Some(&store_data_dir(&state.store)),
    );
    let run_id = response_run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let mut body = OrderedMap::new();
    body.insert(
        "run",
        serde_json::to_value(&response_run).unwrap_or(json!({})),
    );
    body.insert("execution_mode", json!("async"));
    body.insert("accepted", json!(true));
    body.insert(
        "events_url",
        json!(format!("/workflows/api/runs/{run_id}/replay")),
    );
    body.insert(
        "stop_url",
        json!(format!("/workflows/api/runs/{run_id}/stop")),
    );
    Ok(ok(&body))
}

pub(crate) async fn stop_run(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Ok(run) = state.store.get_workflow_run(&run_id, &scope) else {
        let mut body = OrderedMap::new();
        body.insert("stopped", json!(false));
        body.insert("reason", json!("run not found"));
        body.insert("run_id", json!(run_id));
        return Ok(ok(&body));
    };
    let status = run.get("status").and_then(Value::as_str).unwrap_or("");
    if !ACTIVE_STATUSES.contains(&status) {
        let mut body = OrderedMap::new();
        body.insert("stopped", json!(false));
        body.insert("reason", json!("run already finished"));
        body.insert("run_id", json!(run_id));
        body.insert("status", json!(status));
        return Ok(ok(&body));
    }
    let mut timeline = run
        .get("timeline")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    timeline.push(json!({
        "event": "execution_cancelled",
        "status": "cancelled",
        "reason": "cancelled; no active worker owned this run",
        "timestamp": now_iso(),
    }));
    let _ = state.store.update_workflow_run(
        &run_id,
        &scope,
        &[
            ("status", json!("cancelled")),
            (
                "cancel_reason",
                json!("cancelled; no active worker owned this run"),
            ),
            ("cancelled_at", json!(now_iso())),
            ("timeline", json!(timeline)),
            ("pause", Value::Null),
        ],
    );
    let mut body = OrderedMap::new();
    body.insert("stopped", json!(true));
    body.insert("run_id", json!(run_id));
    body.insert("status", json!("cancelled"));
    body.insert("cancellation", json!("cooperative"));
    body.insert(
        "reason",
        json!("cancellation requested; synchronous work finishes its current step before the final cancelled status is stored"),
    );
    Ok(ok(&body))
}

pub(crate) async fn resume_run(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = if body.is_empty() {
        Map::new()
    } else {
        parse_object(&body).unwrap_or_default()
    };
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Ok(run) = state.store.get_workflow_run(&run_id, &scope) else {
        return Err(plain_500());
    };
    if !is_awaiting_approval(&run) {
        return Err(detail_error(
            StatusCode::CONFLICT,
            "run is not awaiting approval",
        ));
    }
    let workflow_id = run
        .get("workflow_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let workflow = state
        .store
        .get_workflow(&workflow_id, &scope)
        .map_err(|_| {
            detail_error(
                StatusCode::NOT_FOUND,
                &format!("Workflow not found: {workflow_id}"),
            )
        })?;
    let approved = object
        .get("approved")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    match resume_paused(
        &state.store,
        &workflow,
        &run,
        &scope,
        approved,
        None,
        Some(&store_data_dir(&state.store)),
    ) {
        Ok(updated) => {
            let mut body = OrderedMap::new();
            body.insert("run", updated);
            body.insert("resumed", json!(true));
            Ok(ok(&body))
        }
        Err(detail) => Err(detail_error(StatusCode::CONFLICT, &detail)),
    }
}

fn plain_500() -> Response {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header(
            axum::http::header::CONTENT_TYPE,
            "text/plain; charset=utf-8",
        )
        .body(axum::body::Body::from("Internal Server Error"))
        .unwrap_or_else(|_| Response::new(axum::body::Body::from("Internal Server Error")))
}

pub(crate) async fn list_definition_runs(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let limit = query.limit.unwrap_or(50).clamp(1, 300) as usize;
    let runs = state
        .store
        .list_workflow_runs(Some(&workflow_id), limit, &scope);
    let mut body = OrderedMap::new();
    body.insert("runs", json!(runs));
    Ok(ok(&body))
}

pub(crate) async fn list_all_runs(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let limit = query.limit.unwrap_or(50).clamp(1, 300) as usize;
    let runs = state.store.list_workflow_runs(None, limit, &scope);
    let mut body = OrderedMap::new();
    body.insert("runs", json!(runs));
    Ok(ok(&body))
}

pub(crate) async fn trigger_status(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let mut body = OrderedMap::new();
    body.insert("running", json!(true));
    body.insert("tick_seconds", json!(state.trigger_tick_seconds));
    body.insert("tz", json!(state.trigger_tz));
    body.insert("armed", json!([]));
    Ok(ok(&body))
}

pub(crate) async fn automation_recipes(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let recipes: Vec<Value> = RECIPES
        .iter()
        .map(|recipe| serde_json::to_value(recipe_as_dict(recipe)).unwrap_or(json!({})))
        .collect();
    let mut principles = OrderedMap::new();
    principles.insert("local_first", json!(true));
    principles.insert("drafts_before_automation", json!(true));
    principles.insert("no_external_actions_without_consent", json!(true));
    let mut body = OrderedMap::new();
    body.insert("recipes", json!(recipes));
    body.insert(
        "principles",
        serde_json::to_value(&principles).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

pub(crate) async fn install_recipe(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(recipe_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = if body.is_empty() {
        Map::new()
    } else {
        parse_object(&body).unwrap_or_default()
    };
    let enabled = object
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Some(recipe) = RECIPES.iter().find(|item| item.id == recipe_id) else {
        return Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Automation recipe not found: {recipe_id}"),
        ));
    };
    let existing = state.store.list_workflows("", &scope);
    if let Some(found) = find_installed_recipe(&existing, &recipe_id) {
        let metadata = found.get("metadata").cloned().unwrap_or_else(|| json!({}));
        let already_enabled =
            metadata.get("automation_state").and_then(Value::as_str) == Some("enabled");
        let mut body = OrderedMap::new();
        body.insert("workflow", found.clone());
        body.insert("recipe", metadata);
        body.insert("enabled", json!(already_enabled));
        body.insert("already_installed", json!(true));
        let _ = enabled;
        return Ok(ok(&body));
    }
    let (name, nodes, metadata) = build_recipe_workflow(recipe, enabled);
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    if !errors.is_empty() {
        let mut detail = OrderedMap::new();
        detail.insert("validation_errors", json!(errors));
        let mut body = OrderedMap::new();
        body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
        return Ok(json_response(
            StatusCode::BAD_REQUEST,
            &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
            None,
        ));
    }
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, recipe.id));
    let workflow = state.store.create_workflow(
        &name,
        legacy_steps_from_nodes(&nodes),
        nodes,
        serde_json::to_value(&metadata).unwrap_or(json!({})),
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    body.insert(
        "recipe",
        serde_json::to_value(&metadata).unwrap_or(json!({})),
    );
    body.insert("enabled", json!(enabled));
    body.insert("already_installed", json!(false));
    Ok(ok(&body))
}

pub(crate) async fn run_replay(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let run = state.store.get_workflow_run(&run_id, &scope).map_err(|_| {
        detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow run not found: {run_id}"),
        )
    })?;
    let mut ordered = OrderedMap::new();
    if let Some(map) = run.as_object() {
        for (key, value) in map {
            ordered.insert(key.clone(), value.clone());
        }
    }
    let contract = run.get("contract").cloned().unwrap_or_else(|| {
        serde_json::to_value(workflow_run_contract(&ordered)).unwrap_or(json!({}))
    });
    let mut frames = Vec::new();
    if let Some(timeline) = run.get("timeline").and_then(Value::as_array) {
        for (index, item) in timeline.iter().enumerate() {
            let event = item
                .get("event")
                .or_else(|| item.get("event_type"))
                .or_else(|| item.get("type"))
                .and_then(Value::as_str)
                .unwrap_or("event");
            let actor = item
                .get("agent_id")
                .or_else(|| item.get("role"))
                .or_else(|| item.get("node"))
                .and_then(Value::as_str)
                .unwrap_or("workflow");
            let mut frame = OrderedMap::new();
            frame.insert("index", json!(index));
            frame.insert("event", json!(event));
            frame.insert("actor", json!(actor));
            frame.insert(
                "when",
                item.get("timestamp")
                    .cloned()
                    .or_else(|| run.get("created_at").cloned())
                    .unwrap_or(Value::Null),
            );
            frame.insert(
                "why",
                json!(item
                    .get("reason")
                    .or_else(|| item.get("note"))
                    .or_else(|| item.get("name"))
                    .and_then(Value::as_str)
                    .unwrap_or("")),
            );
            frame.insert("input", run.get("input").cloned().unwrap_or(Value::Null));
            frame.insert(
                "output",
                item.get("result")
                    .cloned()
                    .or_else(|| item.get("output").cloned())
                    .unwrap_or(Value::Null),
            );
            frame.insert(
                "decision",
                item.get("outcome")
                    .or_else(|| item.get("verdict"))
                    .or_else(|| item.get("status"))
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            frame.insert("raw", item.clone());
            frames.push(serde_json::to_value(&frame).unwrap_or(json!({})));
        }
    }
    let mut replay = OrderedMap::new();
    replay.insert("kind", json!("workflow"));
    replay.insert("run_id", json!(run_id));
    replay.insert("status", run.get("status").cloned().unwrap_or(Value::Null));
    replay.insert(
        "workspace_id",
        json!(run
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or(DEFAULT_WORKSPACE_ID)),
    );
    replay.insert("contract", contract);
    replay.insert("replayable", json!(true));
    replay.insert("frames", json!(frames));
    replay.insert(
        "outputs",
        run.get("outputs").cloned().unwrap_or_else(|| json!({})),
    );
    let mut body = OrderedMap::new();
    body.insert("replay", serde_json::to_value(&replay).unwrap_or(json!({})));
    Ok(ok(&body))
}

pub(crate) async fn export_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let workflow = state
        .store
        .get_workflow(&workflow_id, &scope)
        .map_err(|_| {
            detail_error(
                StatusCode::NOT_FOUND,
                &format!("Workflow not found: {workflow_id}"),
            )
        })?;
    Ok(ok(&export_workflow(&workflow)))
}

pub(crate) async fn import_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let data = object.get("data").cloned().unwrap_or_else(|| json!({}));
    let definition =
        import_workflow(&data).map_err(|detail| detail_error(StatusCode::BAD_REQUEST, &detail))?;
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Imported workflow")
        .to_string();
    let nodes = definition
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let metadata = definition
        .get("metadata")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, "import"));
    let workflow = state.store.create_workflow(
        &name,
        legacy_steps_from_nodes(&nodes),
        nodes,
        metadata,
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}
