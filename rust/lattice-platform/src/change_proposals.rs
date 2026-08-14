//! Change-proposal Review Center surface (`latticeai/api/change_proposals.py`).
//!
//! List / detail / reject are native over the workspace-OS review-item store.
//! Approve-and-apply — the file write — is delegated to
//! `POST /agent/change-proposal` via [`WorkerSeamClient`]. The `/api/proposals`
//! path turns a base-SHA conflict into a **400 string**; the Review Center
//! `/automation/reviews/{id}/approve` path answers **409** with the structured
//! `{error,conflict,reason,path,base_sha256,rebase_hint}` body.

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
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{Identity, OrderedMap};
use lattice_core::worker::WorkerSeamError;
use serde_json::{json, Value};

use crate::review_queue::{
    gate_read, gate_write, http_detail, json_ok, load_review_item, parse_object_optional,
    require_user, review_item_raw_view, sha256_text, GovernanceState, ReviewError,
};

/// Mounted (method, axum-path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/proposals"),
    ("GET", "/api/proposals/counts"),
    ("GET", "/api/proposals/:item_id"),
    ("POST", "/api/proposals/:item_id/approve"),
    ("POST", "/api/proposals/:item_id/reject"),
];

const REBASE_HINT: &str = "제안 생성 이후 파일 상태가 바뀌었습니다. 이 제안을 거부하고 현재 파일 내용을 기준으로 제안을 다시 생성하세요.";
const ALREADY_HINT: &str = "이미 처리된 제안입니다. 다시 적용할 수 없습니다.";

/// Why approve-and-apply did not land.
#[derive(Debug)]
pub enum ProposalConflict {
    /// No such review item, or it is not a change proposal.
    NotFound(String),
    /// Structured conflict (file drifted / already resolved).
    Conflict(Value),
    /// Unknown kind or other ValueError.
    BadRequest(String),
    /// Worker seam is not configured.
    Unavailable(String),
}

/// The `/api/proposals*` router.
pub fn router(state: GovernanceState) -> Router {
    Router::new()
        .route("/api/proposals", get(list_proposals))
        .route("/api/proposals/counts", get(proposal_counts))
        .route("/api/proposals/:item_id", get(get_proposal))
        .route("/api/proposals/:item_id/approve", post(approve_proposal))
        .route("/api/proposals/:item_id/reject", post(reject_proposal))
        .with_state(state)
}

async fn list_proposals(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let items = crate::review_queue::list_review_items(
        &state,
        scope.as_deref(),
        Some(user.email.as_str()),
        Some("change_proposal"),
    )
    .into_iter()
    .filter(|item| crate::review_queue::map_str(item, "effective_status") == "pending")
    .map(crate::review_queue::into_value)
    .collect::<Vec<_>>();
    // list_review_items already emits ReviewItem key order (effective_status
    // third). The proposals surface has no response_model, so we re-list from
    // the store in raw order.
    let raw = raw_pending(&state, scope.as_deref(), Some(user.email.as_str()));
    let mut body = OrderedMap::new();
    body.insert("items", Value::Array(raw));
    body.insert("count", json!(items.len() as i64));
    let mut contract = OrderedMap::new();
    contract.insert("additive_writes", json!("auto"));
    contract.insert("mutations", json!("proposal"));
    contract.insert("deletions", json!("proposal"));
    contract.insert("applied_content", json!("exactly_as_reviewed"));
    body.insert("contract", crate::review_queue::into_value(contract));
    Ok(json_ok(&body))
}

async fn proposal_counts(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let pending = raw_pending(&state, scope.as_deref(), Some(user.email.as_str()));
    let mut body = OrderedMap::new();
    body.insert("pending", json!(pending.len() as i64));
    Ok(json_ok(&body))
}

async fn get_proposal(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    match load_proposal(&state, &item_id, scope.as_deref()) {
        Ok(item) => Ok(json_ok(&review_item_raw_view(&item).map_err(|e| e)?)),
        Err(ProposalConflict::NotFound(msg)) => Err(http_detail(StatusCode::NOT_FOUND, &msg)),
        Err(ProposalConflict::BadRequest(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
        Err(other) => Err(http_detail(StatusCode::BAD_REQUEST, &format!("{other:?}"))),
    }
}

async fn approve_proposal(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    match apply_proposal(&state, &item_id, &user, scope.as_deref()).await {
        Ok(result) => {
            let mut body = OrderedMap::new();
            if let Some(item) = result.get("item") {
                body.insert("item", item.clone());
            }
            body.insert("applied", json!(true));
            if let Some(path) = result.get("path") {
                body.insert("path", path.clone());
            }
            if let Some(kind) = result.get("kind") {
                body.insert("kind", kind.clone());
            }
            if let Some(moves) = result.get("moves") {
                body.insert("moves", moves.clone());
            }
            Ok(json_ok(&body))
        }
        Err(ProposalConflict::NotFound(msg)) => Err(http_detail(StatusCode::NOT_FOUND, &msg)),
        Err(ProposalConflict::Conflict(detail)) => {
            // /api/proposals catches ProposalConflictError as ValueError → 400 string.
            let reason = detail
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("conflict");
            let path = detail.get("path").and_then(Value::as_str).unwrap_or("");
            Err(http_detail(
                StatusCode::BAD_REQUEST,
                &format!("change proposal conflict ({reason}): {path}"),
            ))
        }
        Err(ProposalConflict::BadRequest(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
        Err(ProposalConflict::Unavailable(msg)) => {
            Err(http_detail(StatusCode::SERVICE_UNAVAILABLE, &msg))
        }
    }
}

async fn reject_proposal(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let _ = user;
    load_proposal(&state, &item_id, scope.as_deref()).map_err(|err| match err {
        ProposalConflict::NotFound(msg) => http_detail(StatusCode::NOT_FOUND, &msg),
        ProposalConflict::BadRequest(msg) => http_detail(StatusCode::BAD_REQUEST, &msg),
        other => http_detail(StatusCode::BAD_REQUEST, &format!("{other:?}")),
    })?;
    let parsed = parse_object_optional(&body)?;
    let reason = parsed
        .get("reason")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .chars()
        .take(500)
        .collect::<String>();
    // InvalidReviewTransition (already dismissed) is *not* caught by the
    // Python router — it 500s. Reproduce that.
    let dismissed =
        match crate::review_queue::list_review_items(&state, scope.as_deref(), None, None)
            .into_iter()
            .find(|item| crate::review_queue::map_str(item, "id") == item_id)
        {
            Some(_) => dismiss_or_500(&state, &item_id, scope.as_deref(), &reason)?,
            None => {
                return Err(http_detail(StatusCode::NOT_FOUND, &item_id));
            }
        };
    let mut body = OrderedMap::new();
    body.insert("item", dismissed);
    body.insert("applied", json!(false));
    body.insert("reason", json!(reason));
    Ok(json_ok(&body))
}

fn dismiss_or_500(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
    reason: &str,
) -> Result<Value, Response> {
    let stored = load_review_item(state, item_id, scope)
        .map_err(|_| crate::review_queue::internal_server_error())?;
    let status = stored
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "pending" && status != "snoozed" {
        // Uncaught InvalidReviewTransition.
        return Err(crate::review_queue::internal_server_error());
    }
    match crate::review_queue::load_review_item(state, item_id, scope) {
        Ok(_) => {
            // Re-enter through the same dismiss path the review queue uses.
            // We cannot call the private transition; apply a public update.
            reject_update(state, item_id, scope, reason)
        }
        Err(_) => Err(http_detail(StatusCode::NOT_FOUND, item_id)),
    }
}

fn reject_update(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
    reason: &str,
) -> Result<Value, Response> {
    // Duplicate a dismiss with an optional reason. The review-queue module
    // already implements this; we call approve-shaped update via a tiny
    // public wrapper by creating a review-queue dismiss through the store.
    let updated = dismiss_native(state, item_id, scope, reason).map_err(|err| match err {
        ReviewError::NotFound => http_detail(StatusCode::NOT_FOUND, item_id),
        ReviewError::Conflict(_) => crate::review_queue::internal_server_error(),
        ReviewError::View(resp) => resp,
        ReviewError::Failed(msg) => http_detail(StatusCode::BAD_REQUEST, &msg),
    })?;
    review_item_raw_view(&updated)
        .map(crate::review_queue::into_value)
        .map_err(|e| e)
}

fn dismiss_native(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
    reason: &str,
) -> Result<Value, ReviewError> {
    // Use the same field patch as ReviewQueueService.dismiss.
    let stored = load_review_item(state, item_id, scope)?;
    let status = stored
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "pending" && status != "snoozed" {
        return Err(ReviewError::Conflict(format!(
            "cannot 'dismiss' a review item in status '{status}'"
        )));
    }
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
        if let Some(wanted) = scope {
            let stored_ws = item
                .get("workspace_id")
                .and_then(Value::as_str)
                .unwrap_or("personal");
            if stored_ws != wanted {
                return Err(ReviewError::NotFound);
            }
        }
        item["status"] = json!("dismissed");
        if item.get("snoozed_until").map_or(false, |v| !v.is_null()) {
            item["snoozed_until"] = Value::Null;
        }
        if !reason.is_empty() {
            let mut provenance = item.get("provenance").cloned().unwrap_or_else(|| json!({}));
            if let Some(obj) = provenance.as_object_mut() {
                obj.insert("dismiss_reason".into(), json!(reason));
            }
            item["provenance"] = provenance;
        }
        item["updated_at"] = json!(crate::review_queue::now_iso());
        Ok(item.clone())
    })
}

fn raw_pending(state: &GovernanceState, scope: Option<&str>, user: Option<&str>) -> Vec<Value> {
    let ids: Vec<String> =
        crate::review_queue::list_review_items(state, scope, user, Some("change_proposal"))
            .into_iter()
            .filter(|item| crate::review_queue::map_str(item, "effective_status") == "pending")
            .map(|item| crate::review_queue::map_str(&item, "id").to_string())
            .collect();
    ids.into_iter()
        .filter_map(|id| load_review_item(state, &id, scope).ok())
        .filter_map(|item| review_item_raw_view(&item).ok())
        .map(crate::review_queue::into_value)
        .collect()
}

fn load_proposal(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
) -> Result<Value, ProposalConflict> {
    match load_review_item(state, item_id, scope) {
        Ok(item) => {
            if item.get("source").and_then(Value::as_str) != Some("change_proposal") {
                // KeyError(f"not a change proposal: {id}") → str() wraps quotes.
                return Err(ProposalConflict::NotFound(format!(
                    "'not a change proposal: {item_id}'"
                )));
            }
            Ok(item)
        }
        Err(ReviewError::NotFound) => {
            // FileNotFoundError(item_id) → detail is the bare id.
            Err(ProposalConflict::NotFound(item_id.to_string()))
        }
        Err(other) => Err(ProposalConflict::BadRequest(format!("{other:?}"))),
    }
}

/// Apply the staged change. The file write goes through the worker seam.
pub async fn apply_proposal(
    state: &GovernanceState,
    item_id: &str,
    user: &Identity,
    scope: Option<&str>,
) -> Result<Value, ProposalConflict> {
    let item = load_proposal(state, item_id, scope)?;
    let payload = item.get("payload").cloned().unwrap_or_else(|| json!({}));
    let kind = item
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let path = payload
        .get("path")
        .or_else(|| payload.get("root"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if kind != "file_update" && kind != "file_delete" && kind != "reorganization" {
        return Err(ProposalConflict::BadRequest(format!(
            "unknown change proposal kind: {kind}"
        )));
    }
    let status = item
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "pending" && status != "snoozed" {
        return Err(ProposalConflict::Conflict(conflict_body(
            &format!("already_{status}"),
            &path,
            &kind,
            "",
            "",
            ALREADY_HINT,
        )));
    }

    if kind == "reorganization" {
        // Reorg apply stays with the worker (file moves). Same seam.
        apply_via_worker(state, &item, &kind, &path, user).await?;
    } else {
        apply_via_worker(state, &item, &kind, &path, user).await?;
    }

    // Native status flip after the file write landed.
    let approved = mark_approved(state, item_id, scope)?;
    let view = review_item_raw_view(&approved)
        .map_err(|_| ProposalConflict::BadRequest("failed to render approved item".into()))?;
    Ok(json!({
        "item": crate::review_queue::into_value(view),
        "applied": true,
        "path": path,
        "kind": kind,
    }))
}

async fn apply_via_worker(
    state: &GovernanceState,
    item: &Value,
    kind: &str,
    path: &str,
    user: &Identity,
) -> Result<(), ProposalConflict> {
    let payload = item.get("payload").cloned().unwrap_or_else(|| json!({}));
    let Some(worker) = state.worker.clone() else {
        return Err(ProposalConflict::Unavailable(
            "change proposal apply worker is not configured".into(),
        ));
    };
    let body = json!({
        "tool": "write_file",
        "args": {
            "apply": true,
            "path": path,
            "content": payload.get("new_content").and_then(Value::as_str).unwrap_or(""),
            "kind": kind,
            "base_exists": payload.get("base_exists"),
            "base_sha256": payload.get("base_sha256"),
            "new_content": payload.get("new_content"),
        },
        "workspace_id": item.get("workspace_id"),
        "user_email": user.email,
    });
    match worker.post_json("/agent/change-proposal", &body).await {
        Ok(_) => Ok(()),
        Err(WorkerSeamError::Rejected {
            status: 409,
            detail,
            ..
        }) => {
            let parsed = serde_json::from_str::<Value>(&detail).unwrap_or(json!({}));
            let detail = if parsed.get("error").is_some() {
                parsed
            } else if let Some(inner) = parsed.get("detail").cloned() {
                inner
            } else {
                conflict_body(
                    "file_modified_since_proposal",
                    path,
                    kind,
                    payload
                        .get("base_sha256")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                    "",
                    REBASE_HINT,
                )
            };
            Err(ProposalConflict::Conflict(detail))
        }
        Err(WorkerSeamError::Rejected { status, detail, .. }) if status == 400 => {
            Err(ProposalConflict::BadRequest(detail))
        }
        Err(error) => Err(ProposalConflict::Unavailable(error.to_string())),
    }
}

fn mark_approved(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
) -> Result<Value, ProposalConflict> {
    state.update_state(|doc| {
        let rows = doc
            .as_object_mut()
            .and_then(|obj| obj.get_mut("review_items"))
            .and_then(Value::as_array_mut)
            .ok_or_else(|| ProposalConflict::NotFound(item_id.to_string()))?;
        let item = rows
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some(item_id))
            .ok_or_else(|| ProposalConflict::NotFound(item_id.to_string()))?;
        if let Some(wanted) = scope {
            let stored_ws = item
                .get("workspace_id")
                .and_then(Value::as_str)
                .unwrap_or("personal");
            if stored_ws != wanted {
                return Err(ProposalConflict::NotFound(item_id.to_string()));
            }
        }
        item["status"] = json!("approved");
        if item.get("snoozed_until").map_or(false, |v| !v.is_null()) {
            item["snoozed_until"] = Value::Null;
        }
        item["updated_at"] = json!(crate::review_queue::now_iso());
        Ok(item.clone())
    })
}

fn conflict_body(
    reason: &str,
    path: &str,
    kind: &str,
    base_sha256: &str,
    current_sha256: &str,
    rebase_hint: &str,
) -> Value {
    let mut body = OrderedMap::new();
    body.insert("error", json!("change_proposal_conflict"));
    body.insert("conflict", json!(true));
    body.insert("reason", json!(reason));
    body.insert("path", json!(path));
    body.insert("kind", json!(kind));
    body.insert("base_sha256", json!(base_sha256));
    body.insert("current_sha256", json!(current_sha256));
    body.insert("rebase_hint", json!(rebase_hint));
    crate::review_queue::into_value(body)
}

/// The file-side of approve-and-apply, used by the test FakeWorker and as a
/// reference for the worker-side contract.
pub fn check_and_apply_file(
    agent_root: &std::path::Path,
    payload: &Value,
    path: &str,
    kind: &str,
) -> Result<(), ProposalConflict> {
    if payload.get("base_sha256").is_none() || payload.get("base_exists").is_none() {
        // Legacy proposal: apply as reviewed.
        if kind == "file_update" {
            atomic_write(
                &agent_root.join(path),
                payload
                    .get("new_content")
                    .and_then(Value::as_str)
                    .unwrap_or(""),
            );
        }
        return Ok(());
    }
    let base_exists = payload
        .get("base_exists")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let base_sha256 = payload
        .get("base_sha256")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let target = agent_root.join(path);
    let (current_exists, current_content) = snapshot(&target);
    let current_sha256 = if current_exists {
        sha256_text(&current_content)
    } else {
        String::new()
    };
    if !base_exists {
        if current_exists {
            return Err(ProposalConflict::Conflict(conflict_body(
                "file_created_since_proposal",
                path,
                kind,
                "",
                &current_sha256,
                REBASE_HINT,
            )));
        }
    } else if !current_exists {
        return Err(ProposalConflict::Conflict(conflict_body(
            "file_deleted_since_proposal",
            path,
            kind,
            &base_sha256,
            "",
            REBASE_HINT,
        )));
    } else if current_sha256 != base_sha256 {
        return Err(ProposalConflict::Conflict(conflict_body(
            "file_modified_since_proposal",
            path,
            kind,
            &base_sha256,
            &current_sha256,
            REBASE_HINT,
        )));
    }
    if kind == "file_update" {
        atomic_write(
            &target,
            payload
                .get("new_content")
                .and_then(Value::as_str)
                .unwrap_or(""),
        );
    } else if kind == "file_delete" && target.is_file() {
        let _ = std::fs::remove_file(&target);
    }
    Ok(())
}

fn snapshot(path: &std::path::Path) -> (bool, String) {
    match std::fs::read(path) {
        Ok(bytes) => {
            let clipped = if bytes.len() > 400_000 {
                &bytes[..400_000]
            } else {
                &bytes
            };
            (true, String::from_utf8_lossy(clipped).into_owned())
        }
        Err(_) => (false, String::new()),
    }
}

fn atomic_write(target: &std::path::Path, content: &str) {
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    lattice_auth::atomic::write_text(target, content);
}
