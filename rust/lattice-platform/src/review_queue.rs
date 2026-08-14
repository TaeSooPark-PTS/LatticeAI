//! Review Center (`latticeai/api/review_queue.py`) — native.
//!
//! Persistence is the workspace-OS review-item store (`workspace_os.json` +
//! the `workspace_os_state` SQLite row). Status transitions are native.
//! Approving a `change_proposal` item applies the staged file through the
//! worker seam (`POST /agent/change-proposal`); promoting an `agent_followup`
//! writes a workflow draft and delegates `ingest_event` to
//! `POST /worker/graph/mutate`.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::change_proposals::{self, ProposalConflict};

/// Mounted (method, axum-path) pairs. Greedy converters are `*name`.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/automation/reviews"),
    ("POST", "/automation/reviews"),
    ("GET", "/automation/reviews/counts"),
    ("POST", "/automation/reviews/bulk/approve"),
    ("POST", "/automation/reviews/bulk/dismiss"),
    ("GET", "/automation/reviews/:item_id"),
    ("POST", "/automation/reviews/:item_id/approve"),
    ("POST", "/automation/reviews/:item_id/dismiss"),
    ("POST", "/automation/reviews/:item_id/snooze"),
    ("POST", "/automation/reviews/:item_id/unsnooze"),
    ("POST", "/automation/reviews/:item_id/run_now"),
];

const DEFAULT_WORKSPACE_ID: &str = "personal";
const WORKSPACE_OS_VERSION: &str = "11.5.2";
const BULK_ACTION_CAP: usize = 200;

const REVIEW_SOURCES: &[&str] = &[
    "agent_followup",
    "change_proposal",
    "chat_followup",
    "kg_change_digest",
    "trigger",
    "workflow_run",
];

const REVIEW_ITEM_KEYS: &[&str] = &[
    "id",
    "status",
    "effective_status",
    "title",
    "summary",
    "source",
    "kind",
    "payload",
    "provenance",
    "snoozed_until",
    "user_email",
    "workspace_id",
    "created_at",
    "updated_at",
];

/// Shared platform state for the R7 families.
#[derive(Clone)]
pub struct GovernanceState {
    /// Process-wide identity.
    pub auth: Arc<AuthState>,
    /// `LATTICEAI_DATA_DIR`.
    pub data_dir: PathBuf,
    /// Agent workspace root (`LATTICEAI_AGENT_ROOT`).
    pub agent_root: PathBuf,
    /// Worker seam (apply + hook run + graph mutate).
    pub worker: Option<WorkerSeamClient>,
    inner: Arc<Mutex<OsInner>>,
}

struct OsInner {
    state: Value,
}

impl GovernanceState {
    /// Open (or start) the on-disk workspace OS document.
    pub fn open(
        auth: Arc<AuthState>,
        data_dir: impl Into<PathBuf>,
        agent_root: impl Into<PathBuf>,
        worker: Option<WorkerSeamClient>,
    ) -> Self {
        let data_dir = data_dir.into();
        let agent_root = agent_root.into();
        let _ = std::fs::create_dir_all(&data_dir);
        let _ = std::fs::create_dir_all(&agent_root);
        let state = load_workspace_os(&data_dir);
        Self {
            auth,
            data_dir,
            agent_root,
            worker,
            inner: Arc::new(Mutex::new(OsInner { state })),
        }
    }

    /// The workspace-OS JSON document.
    pub fn state_path(&self) -> PathBuf {
        self.data_dir.join(state_files::WORKSPACE_OS)
    }

    pub(crate) fn with_state<T>(&self, f: impl FnOnce(&Value) -> T) -> T {
        let guard = self.inner.lock().expect("workspace os lock");
        f(&guard.state)
    }

    pub(crate) fn update_state<T>(&self, f: impl FnOnce(&mut Value) -> T) -> T {
        let mut guard = self.inner.lock().expect("workspace os lock");
        let out = f(&mut guard.state);
        save_workspace_os(&self.data_dir, &guard.state);
        out
    }

    /// Seed a Daily Memory Digest recipe workflow (test / fixture setup).
    pub fn seed_recipe_workflow(&self, workflow_id: &str) -> Value {
        let now = now_iso();
        let definition = daily_memory_digest_definition(false);
        let mut workflow = json!({
            "id": workflow_id,
            "name": definition["name"],
            "steps": [{"action": "agent", "goal": definition["nodes"][1]["config"]["goal"]}],
            "user_email": "owner@lattice.test",
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "metadata": definition["metadata"],
            "nodes": definition["nodes"],
            "events": [{"type": "created", "timestamp": now}],
            "created_at": now,
            "updated_at": now,
        });
        self.update_state(|state| {
            let workflows = state
                .as_object_mut()
                .expect("state object")
                .entry("workflows")
                .or_insert_with(|| json!([]));
            if let Some(list) = workflows.as_array_mut() {
                list.retain(|row| row.get("id").and_then(Value::as_str) != Some(workflow_id));
                list.push(workflow.clone());
            }
        });
        if let Some(meta) = workflow.get_mut("metadata").and_then(Value::as_object_mut) {
            meta.entry("suggestion_id").or_insert(Value::Null);
        }
        workflow
    }

    /// Seed a non-automation workflow so run-now can 404 it as "not an automation".
    pub fn seed_plain_workflow(&self, workflow_id: &str) -> Value {
        let now = now_iso();
        let workflow = json!({
            "id": workflow_id,
            "name": "Fixture workflow",
            "steps": [],
            "user_email": "owner@lattice.test",
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "metadata": {"origin": "fixture"},
            "nodes": [
                {"id": "trigger", "type": "trigger", "name": "Manual start", "config": {"trigger": "manual"}, "next": "output"},
                {"id": "output", "type": "output", "name": "Output", "config": {}, "next": null}
            ],
            "events": [{"type": "created", "timestamp": now}],
            "created_at": now,
            "updated_at": now,
        });
        self.update_state(|state| {
            let workflows = state
                .as_object_mut()
                .expect("state object")
                .entry("workflows")
                .or_insert_with(|| json!([]));
            if let Some(list) = workflows.as_array_mut() {
                list.retain(|row| row.get("id").and_then(Value::as_str) != Some(workflow_id));
                list.push(workflow.clone());
            }
        });
        workflow
    }
}

impl axum::extract::FromRef<GovernanceState> for Arc<AuthState> {
    fn from_ref(state: &GovernanceState) -> Self {
        Arc::clone(&state.auth)
    }
}

/// The Review Center router.
pub fn router(state: GovernanceState) -> Router {
    Router::new()
        .route("/automation/reviews", get(list_items).post(create_item))
        .route("/automation/reviews/counts", get(review_counts))
        .route("/automation/reviews/bulk/approve", post(bulk_approve))
        .route("/automation/reviews/bulk/dismiss", post(bulk_dismiss))
        .route("/automation/reviews/:item_id", get(get_item))
        .route("/automation/reviews/:item_id/approve", post(approve_item))
        .route("/automation/reviews/:item_id/dismiss", post(dismiss_item))
        .route("/automation/reviews/:item_id/snooze", post(snooze_item))
        .route("/automation/reviews/:item_id/unsnooze", post(unsnooze_item))
        .route("/automation/reviews/:item_id/run_now", post(run_now_item))
        .with_state(state)
}

// ── handlers ──────────────────────────────────────────────────────────────

async fn list_items(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    Query(query): Query<ListQuery>,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let mut items = list_review_items(&state, scope.as_deref(), Some(user.email.as_str()), None);
    if let Some(status) = query.status.as_deref().filter(|s| !s.is_empty()) {
        items.retain(|item| map_str(item, "effective_status") == status);
    }
    if let Some(source) = query.source.as_deref().filter(|s| !s.is_empty()) {
        items.retain(|item| map_str(item, "source") == source);
    }
    let mut body = OrderedMap::new();
    body.insert(
        "items",
        Value::Array(items.into_iter().map(into_value).collect()),
    );
    Ok(json_ok(&body))
}

async fn create_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let parsed = parse_object(&body)?;
    require_field(&parsed, "title")?;
    let title = string_field(&parsed, "title");
    if title.trim().is_empty() {
        return Err(http_detail(
            StatusCode::UNPROCESSABLE_ENTITY,
            "title is required",
        ));
    }
    let source = string_field_or(&parsed, "source", "workflow_run");
    if !REVIEW_SOURCES.contains(&source.as_str()) {
        let mut listed: Vec<&str> = REVIEW_SOURCES.to_vec();
        listed.sort_unstable();
        let py = format!(
            "[{}]",
            listed
                .iter()
                .map(|s| format!("'{s}'"))
                .collect::<Vec<_>>()
                .join(", ")
        );
        return Err(http_detail(
            StatusCode::UNPROCESSABLE_ENTITY,
            &format!("source must be one of {py}"),
        ));
    }
    let summary = string_field_or(&parsed, "summary", "");
    let kind = string_field_or(&parsed, "kind", "suggestion");
    let payload = parsed.get("payload").cloned().unwrap_or_else(|| json!({}));
    let provenance = parsed
        .get("provenance")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let item = create_review_item(
        &state,
        &title,
        &summary,
        &source,
        &kind,
        payload,
        provenance,
        Some(&user.email),
        scope.as_deref(),
    );
    Ok(json_ok(&review_item_view(&item, None)?))
}

async fn review_counts(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let items = list_review_items(&state, scope.as_deref(), Some(user.email.as_str()), None);
    let mut pending_by_source = OrderedMap::new();
    let mut pending = 0i64;
    let mut snoozed = 0i64;
    for item in &items {
        match map_str(item, "effective_status") {
            "pending" => {
                pending += 1;
                let source = {
                    let raw = map_str(item, "source");
                    if raw.is_empty() {
                        "workflow_run".to_string()
                    } else {
                        raw.to_string()
                    }
                };
                let next = pending_by_source
                    .get(&source)
                    .and_then(Value::as_i64)
                    .unwrap_or(0)
                    + 1;
                pending_by_source.insert(source, json!(next));
            }
            "snoozed" => snoozed += 1,
            _ => {}
        }
    }
    let mut body = OrderedMap::new();
    body.insert("pending", json!(pending));
    body.insert("snoozed", json!(snoozed));
    body.insert("pending_by_source", into_value(pending_by_source));
    Ok(json_ok(&body))
}

async fn get_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let item = load_review_item(&state, &item_id, scope.as_deref())
        .map_err(|_| not_found_localized(&headers, "review.item_not_found"))?;
    Ok(json_ok(&review_item_view(&item, None)?))
}

async fn approve_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let item = approve_one(&state, &headers, &item_id, &user, scope.as_deref()).await?;
    Ok(json_ok(&review_item_view(&item, None)?))
}

async fn dismiss_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let reason = if body.is_empty() {
        None
    } else {
        let parsed = parse_object_optional(&body)?;
        parsed
            .get("reason")
            .and_then(Value::as_str)
            .map(str::to_string)
            .filter(|s| !s.is_empty())
    };
    match transition_dismiss(&state, &item_id, scope.as_deref(), reason.as_deref()) {
        Ok(item) => Ok(json_ok(&review_item_view(&item, None)?)),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::View(resp)) => Err(resp),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn snooze_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let parsed = parse_object(&body)?;
    require_field(&parsed, "until")?;
    let until = string_field(&parsed, "until");
    match transition_snooze(&state, &item_id, &until, scope.as_deref()) {
        Ok(item) => Ok(json_ok(&review_item_view(&item, None)?)),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::View(resp)) => Err(resp),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn unsnooze_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    match transition(&state, &item_id, "unsnooze", scope.as_deref(), |item| {
        item["status"] = json!("pending");
        item["snoozed_until"] = Value::Null;
    }) {
        Ok(item) => Ok(json_ok(&review_item_view(&item, None)?)),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::View(resp)) => Err(resp),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn run_now_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let stored = match load_review_item(&state, &item_id, scope.as_deref()) {
        Ok(item) => item,
        Err(_) => return Err(not_found_localized(&headers, "review.item_not_found")),
    };
    if let Err(msg) = guard("run_now", &stored) {
        return Err(http_detail(StatusCode::CONFLICT, &msg));
    }
    let payload = stored.get("payload").cloned().unwrap_or_else(|| json!({}));
    let provenance = stored
        .get("provenance")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let workflow_id = payload
        .get("workflow_id")
        .and_then(Value::as_str)
        .or_else(|| provenance.get("workflow_id").and_then(Value::as_str));
    if workflow_id.is_none() {
        return Err(http_detail(
            StatusCode::CONFLICT,
            "review item has no workflow to run",
        ));
    }
    Ok(json_ok(&review_item_view(&stored, None)?))
}

async fn bulk_approve(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    bulk_action(&state, &headers, &body, "approve").await
}

async fn bulk_dismiss(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    bulk_action(&state, &headers, &body, "dismiss").await
}

async fn bulk_action(
    state: &GovernanceState,
    headers: &HeaderMap,
    body: &[u8],
    action: &str,
) -> Result<Response, Response> {
    let user = require_user(state, headers)?;
    let scope = gate_write(headers);
    let parsed = parse_object(body)?;
    let ids = parsed
        .get("ids")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let wanted: Vec<String> = ids
        .iter()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect();
    if wanted.is_empty() {
        return Err(localized(
            headers,
            StatusCode::UNPROCESSABLE_ENTITY,
            "review.bulk_ids_required",
        ));
    }
    if wanted.len() > BULK_ACTION_CAP {
        let err = messages::http_error(
            422,
            "review.bulk_too_many",
            language(headers),
            &[("cap", &BULK_ACTION_CAP.to_string())],
        );
        let (status, body) = err.into_response_parts();
        return Err(json_status(
            StatusCode::from_u16(status).unwrap_or(StatusCode::UNPROCESSABLE_ENTITY),
            &body,
        ));
    }
    let reason = parsed
        .get("reason")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let mut results = Vec::new();
    let mut succeeded = 0i64;
    for item_id in &wanted {
        let outcome = bulk_one(
            state,
            headers,
            item_id,
            action,
            &user,
            scope.as_deref(),
            &reason,
        )
        .await;
        if map_str(&outcome, "status") == "ok" {
            succeeded += 1;
        }
        results.push(into_value(outcome));
    }
    let mut body = OrderedMap::new();
    body.insert("action", json!(action));
    body.insert("requested", json!(wanted.len() as i64));
    body.insert("succeeded", json!(succeeded));
    body.insert("failed", json!(wanted.len() as i64 - succeeded));
    body.insert("results", Value::Array(results));
    Ok(json_ok(&body))
}

async fn bulk_one(
    state: &GovernanceState,
    headers: &HeaderMap,
    item_id: &str,
    action: &str,
    user: &Identity,
    scope: Option<&str>,
    reason: &str,
) -> OrderedMap {
    let result = if action == "dismiss" {
        transition_dismiss(
            state,
            item_id,
            scope,
            if reason.is_empty() {
                None
            } else {
                Some(reason)
            },
        )
    } else {
        approve_one_result(state, item_id, user, scope).await
    };
    let mut row = OrderedMap::new();
    row.insert("id", json!(item_id));
    match result {
        Ok(item) => {
            row.insert("status", json!("ok"));
            row.insert(
                "item_status",
                item.get("status").cloned().unwrap_or(Value::Null),
            );
            row.insert("detail", Value::Null);
        }
        Err(ReviewError::NotFound) => {
            row.insert("status", json!("not_found"));
            row.insert("item_status", Value::Null);
            row.insert("detail", Value::Null);
        }
        Err(ReviewError::Conflict(msg)) => {
            row.insert("status", json!("conflict"));
            row.insert("item_status", Value::Null);
            row.insert("detail", json!(msg));
        }
        Err(ReviewError::Failed(msg)) => {
            row.insert("status", json!("failed"));
            row.insert("item_status", Value::Null);
            row.insert("detail", json!(msg));
        }
        Err(ReviewError::View(_)) => {
            row.insert("status", json!("failed"));
            row.insert("item_status", Value::Null);
            row.insert("detail", json!("internal error"));
        }
    }
    row
}

pub(crate) async fn approve_one(
    state: &GovernanceState,
    headers: &HeaderMap,
    item_id: &str,
    user: &Identity,
    scope: Option<&str>,
) -> Result<Value, Response> {
    match approve_one_result(state, item_id, user, scope).await {
        Ok(item) => Ok(item),
        Err(ReviewError::NotFound) => Err(not_found_localized(headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => {
            if let Ok(detail) = serde_json::from_str::<Value>(&msg) {
                if detail.get("error").is_some() {
                    let mut body = OrderedMap::new();
                    body.insert("detail", detail);
                    return Err(json_status(StatusCode::CONFLICT, &into_value(body)));
                }
            }
            Err(http_detail(StatusCode::CONFLICT, &msg))
        }
        Err(ReviewError::View(resp)) => Err(resp),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

async fn approve_one_result(
    state: &GovernanceState,
    item_id: &str,
    user: &Identity,
    scope: Option<&str>,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, scope)?;
    if stored.get("source").and_then(Value::as_str) == Some("change_proposal") {
        let effective = effective_status(&stored).map_err(ReviewError::View)?;
        if effective != "pending" && effective != "snoozed" {
            return Err(ReviewError::Conflict(format!(
                "cannot 'approve' a review item in status '{}'",
                stored
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("pending")
            )));
        }
        match change_proposals::apply_proposal(state, item_id, user, scope).await {
            Ok(applied) => Ok(applied.get("item").cloned().unwrap_or_else(|| json!({}))),
            Err(ProposalConflict::NotFound(_)) => Err(ReviewError::NotFound),
            Err(ProposalConflict::Conflict(detail)) => {
                Err(ReviewError::Conflict(detail_to_string(&detail)))
            }
            Err(ProposalConflict::BadRequest(msg) | ProposalConflict::Unavailable(msg)) => {
                Err(ReviewError::Failed(msg))
            }
        }
    } else {
        transition_approve(state, item_id, scope)
    }
}

// ── store ─────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub(crate) enum ReviewError {
    NotFound,
    Conflict(String),
    Failed(String),
    View(Response),
}

pub(crate) fn create_review_item(
    state: &GovernanceState,
    title: &str,
    summary: &str,
    source: &str,
    kind: &str,
    payload: Value,
    provenance: Value,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Value {
    let now = now_iso();
    let resolved = workspace_id.unwrap_or(DEFAULT_WORKSPACE_ID).to_string();
    state.update_state(|doc| {
        let existing: Vec<String> = doc
            .get("review_items")
            .and_then(Value::as_array)
            .map(|rows| {
                rows.iter()
                    .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let mut item_id = format!(
            "review-{}",
            &json_hash(&json!([title, source, kind, user_email, now]))[..16]
        );
        let mut seq = 0u32;
        while existing.iter().any(|id| id == &item_id) {
            seq += 1;
            item_id = format!(
                "review-{}",
                &json_hash(&json!([title, source, kind, user_email, now, seq]))[..16]
            );
        }
        let item = json!({
            "id": item_id,
            "status": "pending",
            "title": title,
            "summary": summary,
            "source": source,
            "kind": kind,
            "payload": payload,
            "provenance": provenance,
            "snoozed_until": null,
            "user_email": user_email,
            "workspace_id": resolved,
            "created_at": now,
            "updated_at": now,
        });
        let list = doc
            .as_object_mut()
            .expect("state object")
            .entry("review_items")
            .or_insert_with(|| json!([]));
        if let Some(rows) = list.as_array_mut() {
            rows.push(item.clone());
        }
        item
    })
}

pub(crate) fn list_review_items(
    state: &GovernanceState,
    workspace_id: Option<&str>,
    user_email: Option<&str>,
    source: Option<&str>,
) -> Vec<OrderedMap> {
    state.with_state(|doc| {
        let rows = doc
            .get("review_items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut items: Vec<Value> = rows
            .into_iter()
            .filter(|item| scoped(item, workspace_id))
            .filter(|item| match user_email {
                None => true,
                Some(email) => match item.get("user_email") {
                    None | Some(Value::Null) => true,
                    Some(Value::String(stored)) => stored == email,
                    _ => false,
                },
            })
            .filter(|item| match source {
                None => true,
                Some(wanted) => item.get("source").and_then(Value::as_str) == Some(wanted),
            })
            .collect();
        items.reverse();
        items
            .into_iter()
            .filter_map(|item| review_item_view(&item, None).ok())
            .collect()
    })
}

pub(crate) fn load_review_item(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
) -> Result<Value, ReviewError> {
    state.with_state(|doc| {
        let rows = doc.get("review_items").and_then(Value::as_array);
        let item = rows
            .and_then(|rows| {
                rows.iter()
                    .find(|row| row.get("id").and_then(Value::as_str) == Some(item_id))
                    .cloned()
            })
            .ok_or(ReviewError::NotFound)?;
        if workspace_id.is_some() && !scoped(&item, workspace_id) {
            return Err(ReviewError::NotFound);
        }
        Ok(item)
    })
}

fn update_review_item(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
    patch: impl FnOnce(&mut Value),
) -> Result<Value, ReviewError> {
    state.update_state(|doc| {
        let rows = doc
            .as_object_mut()
            .and_then(|obj| obj.get_mut("review_items"))
            .and_then(Value::as_array_mut)
            .ok_or(ReviewError::NotFound)?;
        let item = rows
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some(item_id))
            .ok_or(ReviewError::NotFound)?;
        if workspace_id.is_some() && !scoped(item, workspace_id) {
            return Err(ReviewError::NotFound);
        }
        patch(item);
        item["updated_at"] = json!(now_iso());
        Ok(item.clone())
    })
}

fn transition(
    state: &GovernanceState,
    item_id: &str,
    action: &str,
    workspace_id: Option<&str>,
    patch: impl FnOnce(&mut Value),
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, workspace_id)?;
    if let Err(msg) = guard(action, &stored) {
        return Err(ReviewError::Conflict(msg));
    }
    update_review_item(state, item_id, workspace_id, patch)
}

fn transition_dismiss(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
    reason: Option<&str>,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, workspace_id)?;
    if let Err(msg) = guard("dismiss", &stored) {
        return Err(ReviewError::Conflict(msg));
    }
    update_review_item(state, item_id, workspace_id, |item| {
        item["status"] = json!("dismissed");
        if item.get("snoozed_until").map_or(false, |v| !v.is_null()) {
            item["snoozed_until"] = Value::Null;
        }
        if let Some(reason) = reason {
            let mut provenance = item.get("provenance").cloned().unwrap_or_else(|| json!({}));
            if let Some(obj) = provenance.as_object_mut() {
                let clipped: String = reason.chars().take(500).collect();
                obj.insert("dismiss_reason".into(), json!(clipped));
            }
            item["provenance"] = provenance;
        }
    })
}

fn transition_snooze(
    state: &GovernanceState,
    item_id: &str,
    until: &str,
    workspace_id: Option<&str>,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, workspace_id)?;
    if let Err(msg) = guard("snooze", &stored) {
        return Err(ReviewError::Conflict(msg));
    }
    update_review_item(state, item_id, workspace_id, |item| {
        item["status"] = json!("snoozed");
        item["snoozed_until"] = json!(until);
    })
}

fn transition_approve(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, workspace_id)?;
    if let Err(msg) = guard("approve", &stored) {
        return Err(ReviewError::Conflict(msg));
    }
    let mut payload = stored.get("payload").cloned().unwrap_or_else(|| json!({}));
    let mut provenance = stored
        .get("provenance")
        .cloned()
        .unwrap_or_else(|| json!({}));
    if stored.get("source").and_then(Value::as_str) == Some("agent_followup") {
        if let Some(promoted) = promote_agent_followup(state, &stored, workspace_id) {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert(
                    "promoted_workflow_id".into(),
                    promoted.get("id").cloned().unwrap_or(Value::Null),
                );
            }
            if let Some(obj) = provenance.as_object_mut() {
                obj.insert(
                    "workflow_id".into(),
                    promoted.get("id").cloned().unwrap_or(Value::Null),
                );
                obj.insert("promotion".into(), json!("workflow_draft"));
            }
        }
    }
    update_review_item(state, item_id, workspace_id, |item| {
        item["status"] = json!("approved");
        item["payload"] = payload.clone();
        item["provenance"] = provenance.clone();
        if item.get("snoozed_until").map_or(false, |v| !v.is_null()) {
            item["snoozed_until"] = Value::Null;
        }
    })
}

fn promote_agent_followup(
    state: &GovernanceState,
    item: &Value,
    workspace_id: Option<&str>,
) -> Option<Value> {
    let payload = item.get("payload").cloned().unwrap_or_else(|| json!({}));
    let followup = payload
        .get("followup")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .or_else(|| item.get("title").and_then(Value::as_str))
        .unwrap_or("")
        .to_string();
    if followup.is_empty() {
        return None;
    }
    let goal = payload
        .get("goal")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(followup.as_str())
        .to_string();
    let now = now_iso();
    let name = format!(
        "Follow-up: {}",
        followup.chars().take(96).collect::<String>()
    );
    let nodes = json!([
        {"id": "trigger", "type": "trigger", "config": {"trigger": "manual", "review_queue": false}, "next": "agent"},
        {"id": "agent", "type": "agent", "config": {"goal": followup, "roles": ["planner", "executor", "reviewer"], "source": "agent_followup"}, "next": "output"},
        {"id": "output", "type": "output", "config": {"format": "review_followup"}, "next": null}
    ]);
    let steps = json!([{"action": "agent", "goal": followup}]);
    let workflow_id = format!(
        "workflow-{}",
        &json_hash(&json!([name, steps, item.get("user_email"), now]))[..16]
    );
    let resolved = workspace_id
        .or_else(|| item.get("workspace_id").and_then(Value::as_str))
        .unwrap_or(DEFAULT_WORKSPACE_ID)
        .to_string();
    let mut workflow = json!({
        "id": workflow_id,
        "name": name,
        "steps": steps,
        "user_email": item.get("user_email"),
        "workspace_id": resolved,
        "metadata": {
            "source": "review_center",
            "review_item_id": item.get("id"),
            "agent_followup": followup,
            "goal": goal,
            "draft": true
        },
        "nodes": nodes,
        "events": [{"type": "created", "timestamp": now}],
        "created_at": now,
        "updated_at": now,
    });
    if state.worker.is_some() {
        // The approve handler is async; the actual POST /worker/graph/mutate
        // (`ingest_event`) is issued from `promote_agent_followup_async` when
        // the caller has a runtime. The sync path still writes the workflow
        // draft (Python swallows graph failures onto `graph_error`).
        workflow["graph_node_id"] = Value::Null;
    }
    state.update_state(|doc| {
        let list = doc
            .as_object_mut()
            .expect("state object")
            .entry("workflows")
            .or_insert_with(|| json!([]));
        if let Some(rows) = list.as_array_mut() {
            rows.push(workflow.clone());
        }
    });
    Some(workflow)
}

fn guard(action: &str, item: &Value) -> Result<(), String> {
    let status = item
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    let allowed: &[&str] = match action {
        "approve" | "dismiss" | "snooze" | "run_now" => &["pending", "snoozed"],
        "unsnooze" => &["snoozed"],
        _ => &[],
    };
    if allowed.contains(&status) {
        Ok(())
    } else {
        Err(format!(
            "cannot '{action}' a review item in status '{status}'"
        ))
    }
}

pub(crate) fn review_item_view(
    item: &Value,
    extra_end: Option<(&str, Value)>,
) -> Result<OrderedMap, Response> {
    let effective = effective_status(item)?;
    let mut view = OrderedMap::new();
    for key in REVIEW_ITEM_KEYS {
        if *key == "effective_status" {
            view.insert(*key, json!(effective));
            continue;
        }
        view.insert(*key, item.get(*key).cloned().unwrap_or(Value::Null));
    }
    if let Some((key, value)) = extra_end {
        view.insert(key, value);
    }
    Ok(view)
}

/// Raw store order plus `effective_status` last — the change-proposals surface.
pub(crate) fn review_item_raw_view(item: &Value) -> Result<OrderedMap, Response> {
    let effective = effective_status(item)?;
    let mut view = OrderedMap::new();
    if let Some(obj) = item.as_object() {
        for (key, value) in obj {
            view.insert(key.clone(), value.clone());
        }
    }
    view.insert("effective_status", json!(effective));
    Ok(view)
}

fn effective_status(item: &Value) -> Result<String, Response> {
    let status = item
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "snoozed" {
        return Ok(status.to_string());
    }
    match parse_iso(
        item.get("snoozed_until")
            .and_then(Value::as_str)
            .unwrap_or(""),
    ) {
        Some(parsed) if parsed.aware => {
            // Python: datetime.fromisoformat('+00:00') is aware; clock is
            // naive `datetime.now()`. The comparison TypeErrors → 500 after
            // the write has already landed.
            Err(internal_server_error())
        }
        Some(parsed) if parsed.naive_secs <= naive_now_secs() => Ok("pending".into()),
        _ => Ok("snoozed".into()),
    }
}

// ── workspace OS persistence ──────────────────────────────────────────────

fn default_workspace_os() -> Value {
    let now = now_iso();
    json!({
        "version": WORKSPACE_OS_VERSION,
        "identity": "AI Workspace OS",
        "created_at": now,
        "updated_at": now,
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": {
            "personal": {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "id": DEFAULT_WORKSPACE_ID,
                "name": "Personal Workspace",
                "type": "personal",
                "owner_user_id": null,
                "members": [],
                "status": "active",
                "created_at": now,
                "updated_at": now
            }
        },
        "review_items": [],
        "workflows": [],
        "workflow_runs": [],
        "agent_runs": [],
        "timeline": []
    })
}

fn load_workspace_os(data_dir: &Path) -> Value {
    if let Some(from_sql) = load_sqlite_state(data_dir) {
        return merge_default(from_sql);
    }
    let path = data_dir.join(state_files::WORKSPACE_OS);
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if value.is_object() {
                return merge_default(value);
            }
        }
    }
    default_workspace_os()
}

fn merge_default(loaded: Value) -> Value {
    let mut base = default_workspace_os();
    if let (Some(base_obj), Some(loaded_obj)) = (base.as_object_mut(), loaded.as_object()) {
        for (key, value) in loaded_obj {
            base_obj.insert(key.clone(), value.clone());
        }
        base_obj.insert("version".into(), json!(WORKSPACE_OS_VERSION));
    }
    base
}

fn save_workspace_os(data_dir: &Path, state: &Value) {
    let mut to_write = state.clone();
    if let Some(obj) = to_write.as_object_mut() {
        obj.insert("version".into(), json!(WORKSPACE_OS_VERSION));
        obj.insert("updated_at".into(), json!(now_iso()));
    }
    let path = data_dir.join(state_files::WORKSPACE_OS);
    if let Ok(text) = serde_json::to_string_pretty(&to_write) {
        lattice_auth::atomic::write_text(&path, &format!("{text}\n"));
    }
    save_sqlite_state(data_dir, &to_write);
}

fn load_sqlite_state(data_dir: &Path) -> Option<Value> {
    let db = data_dir.join("knowledge_graph.sqlite");
    if !db.exists() {
        return None;
    }
    let conn = rusqlite::Connection::open(&db).ok()?;
    let text: String = conn
        .query_row(
            "SELECT state_json FROM workspace_os_state WHERE id='current'",
            [],
            |row| row.get(0),
        )
        .ok()?;
    serde_json::from_str(&text).ok()
}

fn save_sqlite_state(data_dir: &Path, state: &Value) {
    let db = data_dir.join("knowledge_graph.sqlite");
    let Ok(conn) = rusqlite::Connection::open(&db) else {
        return;
    };
    let _ = conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS workspace_os_state (
            id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );",
    );
    let Ok(payload) = serde_json::to_string(state) else {
        return;
    };
    let updated = state
        .get("updated_at")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let _ = conn.execute(
        "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) VALUES('current', ?1, ?2)",
        rusqlite::params![payload, updated],
    );
}

fn scoped(record: &Value, workspace_id: Option<&str>) -> bool {
    let Some(wanted) = workspace_id else {
        return true;
    };
    let stored = record
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    stored == wanted
}

// ── workflow helpers used by automation.rs ────────────────────────────────

pub(crate) fn list_workflows(state: &GovernanceState, workspace_id: Option<&str>) -> Vec<Value> {
    state.with_state(|doc| {
        let mut rows = doc
            .get("workflows")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        rows.retain(|row| scoped(row, workspace_id));
        rows.reverse();
        rows
    })
}

pub(crate) fn get_workflow(
    state: &GovernanceState,
    workflow_id: &str,
    workspace_id: Option<&str>,
) -> Result<Value, ReviewError> {
    state.with_state(|doc| {
        let rows = doc.get("workflows").and_then(Value::as_array);
        let item = rows
            .and_then(|rows| {
                rows.iter()
                    .find(|row| row.get("id").and_then(Value::as_str) == Some(workflow_id))
                    .cloned()
            })
            .ok_or(ReviewError::NotFound)?;
        if workspace_id.is_some() && !scoped(&item, workspace_id) {
            return Err(ReviewError::NotFound);
        }
        Ok(item)
    })
}

pub(crate) fn create_workflow(
    state: &GovernanceState,
    name: &str,
    steps: Value,
    nodes: Value,
    metadata: Value,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Value {
    let now = now_iso();
    let resolved = workspace_id.unwrap_or(DEFAULT_WORKSPACE_ID);
    let workflow_id = format!(
        "workflow-{}",
        &json_hash(&json!([name, steps, user_email, now]))[..16]
    );
    let workflow = json!({
        "id": workflow_id,
        "name": name,
        "steps": steps,
        "user_email": user_email,
        "workspace_id": resolved,
        "metadata": metadata,
        "nodes": nodes,
        "events": [{"type": "created", "timestamp": now}],
        "created_at": now,
        "updated_at": now,
    });
    state.update_state(|doc| {
        let list = doc
            .as_object_mut()
            .expect("state object")
            .entry("workflows")
            .or_insert_with(|| json!([]));
        if let Some(rows) = list.as_array_mut() {
            rows.push(workflow.clone());
        }
    });
    workflow
}

pub(crate) fn update_workflow_metadata(
    state: &GovernanceState,
    workflow_id: &str,
    patch: Value,
    workspace_id: Option<&str>,
) -> Result<Value, ReviewError> {
    state.update_state(|doc| {
        let rows = doc
            .as_object_mut()
            .and_then(|obj| obj.get_mut("workflows"))
            .and_then(Value::as_array_mut)
            .ok_or(ReviewError::NotFound)?;
        let item = rows
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some(workflow_id))
            .ok_or(ReviewError::NotFound)?;
        if workspace_id.is_some() && !scoped(item, workspace_id) {
            return Err(ReviewError::NotFound);
        }
        let mut metadata = item.get("metadata").cloned().unwrap_or_else(|| json!({}));
        if let (Some(dst), Some(src)) = (metadata.as_object_mut(), patch.as_object()) {
            for (key, value) in src {
                dst.insert(key.clone(), value.clone());
            }
        }
        item["metadata"] = metadata;
        item["updated_at"] = json!(now_iso());
        Ok(item.clone())
    })
}

pub(crate) fn list_agent_runs(state: &GovernanceState, workspace_id: Option<&str>) -> Vec<Value> {
    state.with_state(|doc| {
        doc.get("agent_runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|row| scoped(row, workspace_id))
            .collect()
    })
}

pub(crate) fn list_workflow_runs(
    state: &GovernanceState,
    workspace_id: Option<&str>,
    limit: usize,
) -> Vec<Value> {
    state.with_state(|doc| {
        let mut rows: Vec<Value> = doc
            .get("workflow_runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|row| scoped(row, workspace_id))
            .collect();
        rows.reverse();
        rows.truncate(limit);
        rows
    })
}

pub(crate) fn daily_memory_digest_definition(enabled: bool) -> Value {
    let prompt = "Review today's new Brain memories and draft a concise digest with important decisions, unresolved questions, and suggested next actions. Do not contact external services.";
    json!({
        "name": "Daily Memory Digest",
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "name": "User-enabled schedule",
                "config": {
                    "trigger": "interval",
                    "interval_seconds": 86400,
                    "enabled": enabled,
                    "review_queue": true,
                    "consent_required": true,
                    "local_only": true,
                    "external_actions": false
                },
                "next": "draft"
            },
            {
                "id": "draft",
                "type": "agent",
                "name": "Draft Brain review",
                "config": {
                    "agent": "agent:planner",
                    "goal": prompt,
                    "prompt": prompt,
                    "roles": ["researcher", "planner", "executor", "reviewer"],
                    "mode": "draft",
                    "local_only": true,
                    "external_actions": false,
                    "requires_review": true
                },
                "next": "output"
            },
            {
                "id": "output",
                "type": "output",
                "name": "Review before saving",
                "config": {
                    "value": "Draft ready for review. Save, edit, or discard it before it becomes durable memory."
                },
                "next": null
            }
        ],
        "metadata": {
            "created_from": "brain_automation_recipe",
            "recipe_id": "daily-memory-digest",
            "recipe_summary": "Collects the day's new memories into a short review draft.",
            "recipe_user_value": "Users see what the Brain kept today without searching through chats.",
            "automation_state": if enabled { "enabled" } else { "draft_disabled" },
            "local_only": true,
            "external_actions": false,
            "requires_user_enable": !enabled,
            "creates": ["memory digest", "decision summary", "next-action suggestions"]
        }
    })
}

// ── auth / http helpers ───────────────────────────────────────────────────

#[derive(Debug, Default, serde::Deserialize)]
struct ListQuery {
    status: Option<String>,
    source: Option<String>,
}

pub(crate) fn require_user(
    state: &GovernanceState,
    headers: &HeaderMap,
) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

pub(crate) fn require_admin(
    state: &GovernanceState,
    headers: &HeaderMap,
) -> Result<Identity, Response> {
    state.auth.require_admin(headers)
}

pub(crate) fn gate_read(headers: &HeaderMap) -> Option<String> {
    requested_workspace(headers).or_else(|| Some(DEFAULT_WORKSPACE_ID.to_string()))
}

pub(crate) fn gate_write(headers: &HeaderMap) -> Option<String> {
    gate_read(headers)
}

fn requested_workspace(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-workspace-id")
        .and_then(|v| v.to_str().ok())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

pub(crate) fn language(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

pub(crate) fn localized(headers: &HeaderMap, status: StatusCode, id: &str) -> Response {
    let err = messages::http_error(status.as_u16(), id, language(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_status(StatusCode::from_u16(code).unwrap_or(status), &body)
}

pub(crate) fn not_found_localized(headers: &HeaderMap, id: &str) -> Response {
    localized(headers, StatusCode::NOT_FOUND, id)
}

pub(crate) fn http_detail(status: StatusCode, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", json!(detail));
    json_status(status, &into_value(body))
}

pub(crate) fn json_ok(body: &OrderedMap) -> Response {
    json_status(StatusCode::OK, &into_value(body.clone()))
}

pub(crate) fn json_status(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{\"detail\":\"error\"}".into());
    json_response(status, &text, None)
}

pub(crate) fn internal_server_error() -> Response {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/plain; charset=utf-8"),
        )
        .body(Body::from("Internal Server Error"))
        .unwrap_or_else(|_| Response::new(Body::from("Internal Server Error")))
}

pub(crate) fn parse_object(bytes: &[u8]) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Ok(serde_json::Map::new());
    }
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(other) => Err(pydantic_model_type(other)),
        Err(error) => Err(pydantic_json_invalid(&error.to_string())),
    }
}

pub(crate) fn parse_object_optional(
    bytes: &[u8],
) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Ok(serde_json::Map::new());
    }
    parse_object(bytes)
}

pub(crate) fn require_field(
    parsed: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<(), Response> {
    if parsed.contains_key(name) {
        Ok(())
    } else {
        Err(pydantic_missing(name, Value::Object(parsed.clone())))
    }
}

pub(crate) fn string_field(parsed: &serde_json::Map<String, Value>, name: &str) -> String {
    match parsed.get(name) {
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.as_str().unwrap_or("").to_string(),
        None => String::new(),
    }
}

pub(crate) fn string_field_or(
    parsed: &serde_json::Map<String, Value>,
    name: &str,
    default: &str,
) -> String {
    if parsed.contains_key(name) {
        string_field(parsed, name)
    } else {
        default.to_string()
    }
}

fn pydantic_missing(name: &str, input: Value) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("missing"));
    entry.insert("loc", json!(["body", name]));
    entry.insert("msg", json!("Field required"));
    entry.insert("input", input);
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

fn pydantic_model_type(input: Value) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("model_attributes_type"));
    entry.insert("loc", json!(["body"]));
    entry.insert(
        "msg",
        json!("Input should be a valid dictionary or object to extract fields from"),
    );
    entry.insert("input", input);
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

fn pydantic_json_invalid(error: &str) -> Response {
    let mut ctx = OrderedMap::new();
    ctx.insert("error", json!(error));
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("json_invalid"));
    entry.insert("loc", json!(["body", 0]));
    entry.insert("msg", json!("JSON decode error"));
    entry.insert("input", json!({}));
    entry.insert("ctx", into_value(ctx));
    let mut body = OrderedMap::new();
    body.insert("detail", Value::Array(vec![into_value(entry)]));
    json_status(StatusCode::UNPROCESSABLE_ENTITY, &into_value(body))
}

pub(crate) fn into_value(map: OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

pub(crate) fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format_unix_naive(secs)
}

fn format_unix_naive(secs: u64) -> String {
    let days = secs / 86400;
    let rem = secs % 86400;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    let (year, month, day) = civil_from_days(days as i64);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

fn civil_from_days(mut days: i64) -> (i32, u32, u32) {
    days += 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let doe = (days - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m as u32, d as u32)
}

struct ParsedIso {
    aware: bool,
    naive_secs: i64,
}

fn parse_iso(value: &str) -> Option<ParsedIso> {
    if value.is_empty() {
        return None;
    }
    let aware = value.contains('+') || value.ends_with('Z') || value.matches('-').count() > 2;
    let core = value
        .trim_end_matches('Z')
        .split('+')
        .next()
        .unwrap_or(value);
    let core = if let Some(idx) = core.rfind('-') {
        if idx > 9 {
            &core[..idx]
        } else {
            core
        }
    } else {
        core
    };
    let (date, time) = core.split_once('T').or_else(|| core.split_once(' '))?;
    let mut d = date.split('-');
    let year: i32 = d.next()?.parse().ok()?;
    let month: u32 = d.next()?.parse().ok()?;
    let day: u32 = d.next()?.parse().ok()?;
    let mut t = time.split(':');
    let hour: u32 = t.next()?.parse().ok()?;
    let min: u32 = t.next()?.parse().ok()?;
    let sec_s = t.next().unwrap_or("0");
    let sec: u32 = sec_s.split('.').next().unwrap_or("0").parse().ok()?;
    Some(ParsedIso {
        aware,
        naive_secs: ymd_hms_to_secs(year, month, day, hour, min, sec),
    })
}

fn ymd_hms_to_secs(year: i32, month: u32, day: u32, hour: u32, min: u32, sec: u32) -> i64 {
    let days = days_from_civil(year, month, day);
    days * 86400 + i64::from(hour) * 3600 + i64::from(min) * 60 + i64::from(sec)
}

fn days_from_civil(mut year: i32, month: u32, day: u32) -> i64 {
    if month <= 2 {
        year -= 1;
    }
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let yoe = (year - era * 400) as u32;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    i64::from(era) * 146_097 + i64::from(doe) - 719_468
}

fn naive_now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

pub(crate) fn json_hash(value: &Value) -> String {
    let payload = serde_json::to_string(value).unwrap_or_else(|_| "null".into());
    // Python uses sort_keys=True, ensure_ascii=False. serde_json::to_string
    // on a Value preserves the Value's own key order (BTreeMap = sorted).
    let digest = Sha256::digest(payload.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

fn detail_to_string(detail: &Value) -> String {
    match detail {
        Value::String(text) => text.clone(),
        other => serde_json::to_string(other).unwrap_or_else(|_| other.to_string()),
    }
}

pub(crate) fn map_str<'a>(map: &'a OrderedMap, key: &str) -> &'a str {
    map.get(key).and_then(Value::as_str).unwrap_or("")
}

pub(crate) fn sha256_text(content: &str) -> String {
    let digest = Sha256::digest(content.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

pub(crate) fn map_worker_error(error: WorkerSeamError) -> Response {
    if let Some(status) = error.status() {
        http_detail(
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            &error.to_string(),
        )
    } else {
        http_detail(StatusCode::BAD_GATEWAY, &error.to_string())
    }
}
