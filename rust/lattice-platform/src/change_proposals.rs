//! Change-proposal Review Center surface (`latticeai/api/change_proposals.py`).
//!
//! The whole lifecycle is native since v11.6.0 §P1c. List / detail / reject
//! were already; **approve-and-apply** — the base-SHA conflict check and the
//! file write — used to be one `POST /agent/change-proposal` to the Python
//! worker, and §P1a retired that route. The write now goes through
//! [`lattice_agent::sandbox::Workspace`], the same sandbox the agent's own
//! native `write_file` resolves through (§W4), so a proposal cannot apply to a
//! path the agent could not have written in the first place.
//!
//! The two conflict shapes are the contract and are unchanged: the
//! `/api/proposals` path turns a base-SHA conflict into a **400 string**
//! (`change proposal conflict (reason): path`), and the Review Center
//! `/automation/reviews/{id}/approve` path answers **409** with the structured
//! `{error,conflict,reason,path,kind,base_sha256,current_sha256,rebase_hint}`
//! body. Both are pinned by `rust/fixtures/http/review_proposals.json`.
//!
//! This module is also where the *other* end of the pipeline is wired: the
//! agent loop stages proposals through [`lattice_agent::proposals::ProposalStore`],
//! and [`GovernanceState`] implements it, so a loop-staged proposal lands in
//! the very store these routes read.

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
use lattice_agent::proposals::{NewReviewItem, ProposalStore};
use lattice_agent::sandbox::Workspace;
use lattice_auth::{Identity, OrderedMap};
use serde_json::{json, Value};

use crate::review_queue::{
    create_review_item, gate_read, gate_write, http_detail, json_ok, load_review_item,
    parse_object_optional, require_user, review_item_raw_view, sha256_text, GovernanceState,
    ReviewError,
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

/// `workspace_reorganization.REORG_KIND`.
///
/// It is **not** `"reorganization"`, which is what this module compared against
/// until §P1c — a real folder-reorganization proposal was answered
/// `unknown change proposal kind: folder_reorganization` and could never be
/// approved. No fixture covered the kind, so nothing caught it.
const REORG_KIND: &str = "folder_reorganization";

/// `_MAX_STAGED_BYTES`, from the crate that stages (one constant, two ends).
const MAX_STAGED_BYTES: usize = lattice_agent::proposals::MAX_STAGED_BYTES;

/// Why approve-and-apply did not land.
#[derive(Debug)]
pub enum ProposalConflict {
    /// No such review item, or it is not a change proposal.
    NotFound(String),
    /// Structured conflict (file drifted / already resolved).
    Conflict(Value),
    /// Unknown kind, a path outside the agent sandbox, or other ValueError.
    BadRequest(String),
    /// The agent workspace itself cannot be used (→ 503).
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
        Ok(item) => Ok(json_ok(&review_item_raw_view(&item))),
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
    let dismissed = reject_dismiss(&state, &item_id, scope.as_deref(), &reason)?;
    let mut body = OrderedMap::new();
    body.insert("item", dismissed);
    body.insert("applied", json!(false));
    body.insert("reason", json!(reason));
    Ok(json_ok(&body))
}

/// Reject == dismiss, through the Review Center's own transition.
///
/// v11.6.0 rejected an already-rejected proposal with a **500**: Python's
/// router did not catch `InvalidReviewTransition`, and this module reproduced
/// the escape faithfully, down to a hand-copied dismiss so the review queue's
/// own conflict could not be reused. A second rejection is not a server fault
/// — it is the caller telling us something we already did — so it now answers
/// **409** with the very detail the sibling
/// `POST /automation/reviews/{id}/dismiss` answers with, from the very same
/// guard. The wording stays `cannot 'dismiss' …` because the transition
/// underneath *is* the dismiss; only the timeline label says `reject`.
fn reject_dismiss(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
    reason: &str,
) -> Result<Value, Response> {
    let updated = crate::review_queue::transition_dismiss(
        state,
        item_id,
        scope,
        (!reason.is_empty()).then_some(reason),
        "reject",
    )
    .map_err(|error| match error {
        // FileNotFoundError(item_id) → the detail is the bare id, as the
        // load-side 404 on this route already is.
        ReviewError::NotFound => http_detail(StatusCode::NOT_FOUND, item_id),
        ReviewError::Conflict(detail) => http_detail(StatusCode::CONFLICT, &detail),
        ReviewError::Invalid(detail) => http_detail(StatusCode::UNPROCESSABLE_ENTITY, &detail),
        ReviewError::Failed(detail) => http_detail(StatusCode::BAD_REQUEST, &detail),
    })?;
    Ok(crate::review_queue::into_value(review_item_raw_view(
        &updated,
    )))
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
        .map(|item| review_item_raw_view(&item))
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

/// `approve_and_apply` — apply the staged change exactly as reviewed, but only
/// if the file on disk still matches the base snapshot the reviewer looked at.
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
    if kind != "file_update" && kind != "file_delete" && kind != REORG_KIND {
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

    // The staged change lands here, in this process, under the agent sandbox.
    let workspace = Workspace::new(&state.agent_root).map_err(|error| {
        ProposalConflict::Unavailable(format!("agent workspace is unavailable: {error}"))
    })?;
    let moves = apply_staged_change(&workspace, &payload, &path, &kind)?;

    // Status flip after the write landed — never before, so a conflict leaves
    // the proposal pending and re-approvable once the file is rebased.
    let approved = mark_approved(state, item_id, scope)?;
    let view = review_item_raw_view(&approved);
    // `_audit("change_proposal_applied", …)` — the review timeline doubles as
    // the change audit log, and the applying half of it used to be recorded by
    // the Python service the worker route reached.
    let mut event = serde_json::Map::new();
    event.insert("user_email".into(), json!(user.email));
    event.insert("proposal_id".into(), json!(item_id));
    event.insert("path".into(), json!(path));
    event.insert("kind".into(), json!(kind));
    crate::admin::append_audit_event(
        &crate::admin::audit_log_path(&state.data_dir),
        "change_proposal_applied",
        event,
    );
    let mut result = json!({
        "item": crate::review_queue::into_value(view),
        "applied": true,
        "path": path,
        "kind": kind,
    });
    if let Some(moves) = moves {
        result["moves"] = moves;
    }
    Ok(result)
}

/// Flip the item to `approved` — through the Review Center's write funnel, so
/// an approval that lands here emits the same timeline event an approval that
/// lands through `/automation/reviews/{id}/approve` does, exactly once.
fn mark_approved(
    state: &GovernanceState,
    item_id: &str,
    scope: Option<&str>,
) -> Result<Value, ProposalConflict> {
    crate::review_queue::update_review_item(state, item_id, scope, "approve", |item| {
        item["status"] = json!("approved");
        if item.get("snoozed_until").map_or(false, |v| !v.is_null()) {
            item["snoozed_until"] = Value::Null;
        }
    })
    .map_err(|error| match error {
        ReviewError::Conflict(detail)
        | ReviewError::Invalid(detail)
        | ReviewError::Failed(detail) => ProposalConflict::BadRequest(detail),
        ReviewError::NotFound => ProposalConflict::NotFound(item_id.to_string()),
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

/// The file side of approve-and-apply: check the base, then land the change.
///
/// `Ok(Some(moves))` is a reorganization's per-move report; `Ok(None)` is a
/// file update or delete. Nothing touches disk when the base check fails, so a
/// user's out-of-band edit survives a stale approval.
pub fn apply_staged_change(
    workspace: &Workspace,
    payload: &Value,
    path: &str,
    kind: &str,
) -> Result<Option<Value>, ProposalConflict> {
    if kind == REORG_KIND {
        // A reorganization has no single base file to hash: each move is
        // re-checked at apply time and a drifted one is skipped, never forced.
        return apply_reorganization(workspace, payload).map(Some);
    }
    check_base_unchanged(workspace, payload, path, kind)?;
    let target = resolve(workspace, path)?;
    if kind == "file_update" {
        atomic_write(
            &target,
            payload
                .get("new_content")
                .and_then(Value::as_str)
                .unwrap_or(""),
        );
    } else if kind == "file_delete" && target.is_file() {
        // Re-verified here, inside the same call that checked the base.
        let _ = std::fs::remove_file(&target);
    }
    Ok(None)
}

/// `_check_base_unchanged` — disk state *now* against the staged snapshot.
fn check_base_unchanged(
    workspace: &Workspace,
    payload: &Value,
    path: &str,
    kind: &str,
) -> Result<(), ProposalConflict> {
    if payload.get("base_sha256").is_none() || payload.get("base_exists").is_none() {
        // Legacy proposal staged before base snapshots existed — keep the
        // historical apply-as-reviewed behaviour rather than rejecting it.
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
    let (current_exists, current_content) = snapshot(workspace, path);
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
        return Ok(());
    }
    if !current_exists {
        return Err(ProposalConflict::Conflict(conflict_body(
            "file_deleted_since_proposal",
            path,
            kind,
            &base_sha256,
            "",
            REBASE_HINT,
        )));
    }
    if current_sha256 != base_sha256 {
        return Err(ProposalConflict::Conflict(conflict_body(
            "file_modified_since_proposal",
            path,
            kind,
            &base_sha256,
            &current_sha256,
            REBASE_HINT,
        )));
    }
    Ok(())
}

/// `apply_reorganization` — moves only, nothing deleted or overwritten.
fn apply_reorganization(workspace: &Workspace, payload: &Value) -> Result<Value, ProposalConflict> {
    let root = payload
        .get("root")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim_matches('/')
        .to_string();
    let join = |relative: &str| {
        if root.is_empty() {
            relative.to_string()
        } else {
            format!("{root}/{relative}")
        }
    };
    let mut applied: Vec<Value> = Vec::new();
    let mut skipped: Vec<Value> = Vec::new();
    for move_spec in payload
        .get("moves")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
    {
        let source_rel = move_spec
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let target_rel = move_spec
            .get("target")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if source_rel.is_empty() || target_rel.is_empty() {
            skipped.push(json!({"source": source_rel, "reason": "incomplete_move"}));
            continue;
        }
        let source = resolve(workspace, &join(&source_rel))?;
        let target = resolve(workspace, &join(&target_rel))?;
        if !source.is_file() {
            skipped.push(json!({"source": source_rel, "reason": "source_missing"}));
            continue;
        }
        if target.exists() {
            skipped.push(json!({"source": source_rel, "reason": "target_exists"}));
            continue;
        }
        if let Some(parent) = target.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::rename(&source, &target);
        applied.push(json!({"source": source_rel, "target": target_rel}));
    }
    Ok(json!({
        "applied": applied,
        "applied_count": applied.len(),
        "skipped": skipped,
        "skipped_count": skipped.len(),
        "deleted": 0,
    }))
}

/// `resolve_workspace_path` — the agent sandbox, shared with the native tools.
///
/// Python let the `ToolError` escape as a 500; a refusal is a request problem,
/// so it is the 400 the rest of this surface answers with, carrying the same
/// message [`lattice_agent::tools`] uses.
fn resolve(workspace: &Workspace, path: &str) -> Result<std::path::PathBuf, ProposalConflict> {
    workspace
        .resolve(path)
        .map_err(|error| ProposalConflict::BadRequest(error.message))
}

/// `_snapshot` — the same truncate-then-decode both ends of the check use, so
/// an unchanged file hashes identically at staging and at approval.
fn snapshot(workspace: &Workspace, path: &str) -> (bool, String) {
    let Ok(target) = workspace.resolve(path) else {
        return (false, String::new());
    };
    if !target.is_file() {
        return (false, String::new());
    }
    match std::fs::read(&target) {
        Ok(bytes) => {
            let clipped = if bytes.len() > MAX_STAGED_BYTES {
                &bytes[..MAX_STAGED_BYTES]
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

/// Where the agent loop's staged proposals land.
///
/// `lattice-agent` cannot depend on this crate (the dependency runs the other
/// way), so it declares the port and this is the product's implementation. It
/// must be the one the host injects: `GovernanceState` holds the workspace-OS
/// document in memory and mirrors it into SQLite, so the loop's default
/// JSON-file store would be invisible to a Review Center running in the same
/// process — and overwritten by its next save.
/// The port requires `Debug`; the state holds an auth handle and a mutex, and
/// neither belongs in a log line. The data directory is what identifies it.
impl std::fmt::Debug for GovernanceState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("GovernanceState")
            .field("data_dir", &self.data_dir)
            .field("agent_root", &self.agent_root)
            .finish()
    }
}

impl ProposalStore for GovernanceState {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        if item.title.trim().is_empty() {
            return Err("title is required".into());
        }
        Ok(create_review_item(
            self,
            &item.title,
            &item.summary,
            &item.source,
            &item.kind,
            item.payload.clone(),
            item.provenance.clone(),
            item.user_email.as_deref(),
            item.workspace_id.as_deref(),
        ))
    }
}
