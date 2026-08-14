//! Review-item persistence and status transitions.

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
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::http::{internal_server_error, json_hash, naive_now_secs, now_iso, parse_iso};
use super::os::scoped;
use super::{GovernanceState, DEFAULT_WORKSPACE_ID, REVIEW_ITEM_KEYS};

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

pub(crate) fn update_review_item(
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

pub(crate) fn transition(
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

pub(crate) fn transition_dismiss(
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

pub(crate) fn transition_snooze(
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

pub(crate) fn transition_approve(
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

pub(crate) fn promote_agent_followup(
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

pub(crate) fn guard(action: &str, item: &Value) -> Result<(), String> {
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

pub(crate) fn effective_status(item: &Value) -> Result<String, Response> {
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
