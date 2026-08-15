//! Review Center HTTP handlers.

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
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::{Identity, OrderedMap};
use lattice_core::messages;
use serde_json::{json, Value};

use crate::change_proposals::{self, ProposalConflict};

use super::http::{
    detail_to_string, gate_read, gate_write, http_detail, into_value, json_ok, json_status,
    language, localized, map_str, not_found_localized, parse_object, parse_object_optional,
    require_field, require_user, string_field, string_field_or, ListQuery,
};
use super::store::{
    create_review_item, effective_status, guard, list_review_items, load_review_item,
    review_item_view, transition, transition_approve, transition_dismiss, transition_snooze,
    ReviewError,
};
use super::{GovernanceState, BULK_ACTION_CAP, REVIEW_SOURCES};

// ── handlers ──────────────────────────────────────────────────────────────

pub(crate) async fn list_items(
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

pub(crate) async fn create_item(
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
    Ok(json_ok(&review_item_view(&item, None)))
}

pub(crate) async fn review_counts(
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

pub(crate) async fn get_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = gate_read(&headers);
    let item = load_review_item(&state, &item_id, scope.as_deref())
        .map_err(|_| not_found_localized(&headers, "review.item_not_found"))?;
    Ok(json_ok(&review_item_view(&item, None)))
}

pub(crate) async fn approve_item(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = gate_write(&headers);
    let item = approve_one(&state, &headers, &item_id, &user, scope.as_deref()).await?;
    Ok(json_ok(&review_item_view(&item, None)))
}

pub(crate) async fn dismiss_item(
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
    match transition_dismiss(
        &state,
        &item_id,
        scope.as_deref(),
        reason.as_deref(),
        "dismiss",
    ) {
        Ok(item) => Ok(json_ok(&review_item_view(&item, None))),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::Invalid(msg)) => Err(http_detail(StatusCode::UNPROCESSABLE_ENTITY, &msg)),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

pub(crate) async fn snooze_item(
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
        Ok(item) => Ok(json_ok(&review_item_view(&item, None))),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::Invalid(msg)) => Err(http_detail(StatusCode::UNPROCESSABLE_ENTITY, &msg)),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

pub(crate) async fn unsnooze_item(
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
        Ok(item) => Ok(json_ok(&review_item_view(&item, None))),
        Err(ReviewError::NotFound) => Err(not_found_localized(&headers, "review.item_not_found")),
        Err(ReviewError::Conflict(msg)) => Err(http_detail(StatusCode::CONFLICT, &msg)),
        Err(ReviewError::Invalid(msg)) => Err(http_detail(StatusCode::UNPROCESSABLE_ENTITY, &msg)),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

pub(crate) async fn run_now_item(
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
    Ok(json_ok(&review_item_view(&stored, None)))
}

pub(crate) async fn bulk_approve(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    bulk_action(&state, &headers, &body, "approve").await
}

pub(crate) async fn bulk_dismiss(
    State(state): State<GovernanceState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    bulk_action(&state, &headers, &body, "dismiss").await
}

pub(crate) async fn bulk_action(
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

pub(crate) async fn bulk_one(
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
            "dismiss",
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
        Err(ReviewError::Invalid(msg)) => {
            row.insert("status", json!("failed"));
            row.insert("item_status", Value::Null);
            row.insert("detail", json!(msg));
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
        Err(ReviewError::Invalid(msg)) => Err(http_detail(StatusCode::UNPROCESSABLE_ENTITY, &msg)),
        Err(ReviewError::Failed(msg)) => Err(http_detail(StatusCode::BAD_REQUEST, &msg)),
    }
}

pub(crate) async fn approve_one_result(
    state: &GovernanceState,
    item_id: &str,
    user: &Identity,
    scope: Option<&str>,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, scope)?;
    if stored.get("source").and_then(Value::as_str) == Some("change_proposal") {
        let effective = effective_status(&stored);
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
