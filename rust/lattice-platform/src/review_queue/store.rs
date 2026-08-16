//! Review-item persistence and status transitions.

use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::http::{json_hash, naive_now_secs, now_iso, parse_iso};
use super::os::scoped;
use super::{
    GovernanceState, DEFAULT_WORKSPACE_ID, REVIEW_ITEM_CREATED_EVENT, REVIEW_ITEM_KEYS,
    REVIEW_ITEM_UPDATED_EVENT,
};

// ── store ─────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub(crate) enum ReviewError {
    NotFound,
    Conflict(String),
    Failed(String),
    /// A field the request supplied cannot be used (→ 422, as the missing-field
    /// refusal on the same route is).
    Invalid(String),
}

/// Stage one review item — and record that it happened.
///
/// This and [`update_review_item`] are the only two writers of the
/// `review_items` list, which is what lets every route that touches a review
/// item emit its timeline event from one place instead of eight.
// Nine parameters because `review_queue.create` takes nine; a params struct
// would only rename the same nine at every call site.
#[allow(clippy::too_many_arguments)]
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
    let created = state.update_state(|doc| {
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
    });
    // After the save, never inside it: `record_timeline_event` mutates too.
    state.record_review_event(REVIEW_ITEM_CREATED_EVENT, &created, "create");
    created
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
            .map(|item| review_item_view(&item, None))
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

/// Patch one stored review item, then record the change on the timeline.
///
/// `action` names what the caller did (`"approve"`, `"dismiss"`, `"reject"`,
/// …) and only ever reaches the event payload — the status guard is the
/// caller's, because a `/reject` route is a dismiss underneath and must keep
/// answering with the dismiss guard's wording.
///
/// A refusal (no such item, or one in another workspace) leaves the document
/// byte-for-byte as it was: nothing is saved and no event is emitted.
pub(crate) fn update_review_item(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
    action: &str,
    patch: impl FnOnce(&mut Value),
) -> Result<Value, ReviewError> {
    let updated = state.try_update_state(|doc| {
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
    })?;
    state.record_review_event(REVIEW_ITEM_UPDATED_EVENT, &updated, action);
    Ok(updated)
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
    update_review_item(state, item_id, workspace_id, action, patch)
}

/// Dismiss one item. `action` is the timeline label only: `/api/proposals/…
/// /reject` passes `"reject"` and still guards — and refuses — as a dismiss.
pub(crate) fn transition_dismiss(
    state: &GovernanceState,
    item_id: &str,
    workspace_id: Option<&str>,
    reason: Option<&str>,
    action: &str,
) -> Result<Value, ReviewError> {
    let stored = load_review_item(state, item_id, workspace_id)?;
    if let Err(msg) = guard("dismiss", &stored) {
        return Err(ReviewError::Conflict(msg));
    }
    update_review_item(state, item_id, workspace_id, action, |item| {
        item["status"] = json!("dismissed");
        if item.get("snoozed_until").is_some_and(|v| !v.is_null()) {
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

/// The wording an unparseable `until` is refused with.
///
/// Raw English, as this module's other field refusals are (`"title is
/// required"`); the catalog id this wants is listed in the WP's wiring note.
pub(crate) const SNOOZE_UNTIL_INVALID: &str =
    "until must be an ISO-8601 datetime (for example 2099-01-01T00:00:00+00:00)";

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
    // Validated *before* the write, so an unreadable stamp cannot sit in the
    // store and be re-read forever by `effective_status`.
    if parse_iso(until).is_none() {
        return Err(ReviewError::Invalid(SNOOZE_UNTIL_INVALID.to_string()));
    }
    update_review_item(state, item_id, workspace_id, "snooze", |item| {
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
    update_review_item(state, item_id, workspace_id, "approve", |item| {
        item["status"] = json!("approved");
        item["payload"] = payload.clone();
        item["provenance"] = provenance.clone();
        if item.get("snoozed_until").is_some_and(|v| !v.is_null()) {
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
        // The approve handler is async; the actual `ingest_event` write is
        // issued from `promote_agent_followup_async` when the caller has a
        // runtime. The sync path still writes the workflow draft (Python
        // swallows graph failures onto `graph_error`).
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

pub(crate) fn review_item_view(item: &Value, extra_end: Option<(&str, Value)>) -> OrderedMap {
    let effective = effective_status(item);
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
    view
}

/// Raw store order plus `effective_status` last — the change-proposals surface.
pub(crate) fn review_item_raw_view(item: &Value) -> OrderedMap {
    let mut view = OrderedMap::new();
    if let Some(obj) = item.as_object() {
        for (key, value) in obj {
            view.insert(key.clone(), value.clone());
        }
    }
    view.insert("effective_status", json!(effective_status(item)));
    view
}

/// Read-time snooze expiry: a snooze whose deadline has passed reads `pending`.
///
/// Until v11.6.0 an **offset-aware** deadline answered 500 here — Python
/// compared an aware `datetime.fromisoformat` against a naive `datetime.now()`
/// and the `TypeError` escaped, *after* the snooze had already been written, so
/// the item was unreachable from then on. The offset is now honoured: an aware
/// stamp is converted to the instant it names and compared against the same
/// clock. A stamp that cannot be read at all keeps the item snoozed rather
/// than failing the read — and `transition_snooze` refuses to store one.
pub(crate) fn effective_status(item: &Value) -> String {
    let status = item
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "snoozed" {
        return status.to_string();
    }
    match parse_iso(
        item.get("snoozed_until")
            .and_then(Value::as_str)
            .unwrap_or(""),
    ) {
        Some(parsed) if parsed.utc_secs <= naive_now_secs() => "pending".into(),
        _ => "snoozed".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snoozed(until: Value) -> Value {
        json!({"id": "review-1", "status": "snoozed", "snoozed_until": until})
    }

    #[test]
    fn a_status_other_than_snoozed_is_reported_verbatim() {
        assert_eq!(effective_status(&json!({"status": "approved"})), "approved");
        assert_eq!(effective_status(&json!({})), "pending");
        assert_eq!(effective_status(&json!({"status": 7})), "pending");
    }

    #[test]
    fn a_snooze_expires_at_the_instant_it_names_whatever_its_offset() {
        // Far past, in four spellings of the same idea.
        for until in [
            json!("2020-01-01T00:00:00"),
            json!("2020-01-01T00:00:00+00:00"),
            json!("2020-01-01T00:00:00Z"),
            json!("2020-01-01T00:00:00-05:00"),
        ] {
            assert_eq!(
                effective_status(&snoozed(until.clone())),
                "pending",
                "{until}"
            );
        }
        // Far future, likewise — including the offset-aware literal that used
        // to make this function answer 500.
        for until in [
            json!("2099-01-01T00:00:00"),
            json!("2099-01-01T00:00:00+00:00"),
            json!("2099-01-01T09:00:00+09:00"),
        ] {
            assert_eq!(
                effective_status(&snoozed(until.clone())),
                "snoozed",
                "{until}"
            );
        }
        // An unreadable or absent deadline keeps the item snoozed rather than
        // failing the read; `transition_snooze` is what stops one being stored.
        for until in [json!("next tuesday"), json!(""), Value::Null, json!(3)] {
            assert_eq!(
                effective_status(&snoozed(until.clone())),
                "snoozed",
                "{until}"
            );
        }
    }

    #[test]
    fn the_transition_guard_names_the_action_and_the_status_it_refused() {
        assert!(guard("approve", &json!({"status": "pending"})).is_ok());
        assert!(guard("dismiss", &json!({"status": "snoozed"})).is_ok());
        assert!(guard("unsnooze", &json!({"status": "snoozed"})).is_ok());
        assert_eq!(
            guard("unsnooze", &json!({"status": "pending"})).unwrap_err(),
            "cannot 'unsnooze' a review item in status 'pending'"
        );
        assert_eq!(
            guard("dismiss", &json!({"status": "dismissed"})).unwrap_err(),
            "cannot 'dismiss' a review item in status 'dismissed'"
        );
        // A reject is a dismiss underneath and is never handed to the guard
        // under its own name; an unknown action refuses everything.
        assert!(guard("reject", &json!({"status": "pending"})).is_err());
    }

    #[test]
    fn the_two_views_agree_on_the_effective_status_and_differ_only_in_order() {
        let item = json!({
            "id": "review-1", "status": "snoozed", "title": "t",
            "snoozed_until": "2099-01-01T00:00:00+00:00", "extra": 1,
        });
        let view = review_item_view(&item, None);
        assert_eq!(view.get("effective_status"), Some(&json!("snoozed")));
        assert_eq!(view.get("payload"), Some(&Value::Null));
        assert!(view.get("extra").is_none(), "the model drops unknown keys");
        let keys: Vec<&str> = view.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, REVIEW_ITEM_KEYS.to_vec());

        let raw = review_item_raw_view(&item);
        assert_eq!(raw.get("extra"), Some(&json!(1)));
        assert_eq!(raw.get("effective_status"), Some(&json!("snoozed")));
        let last = raw.iter().map(|(key, _)| key).last();
        assert_eq!(last, Some("effective_status"));

        let with_extra = review_item_view(&item, Some(("applied", json!(true))));
        assert_eq!(with_extra.get("applied"), Some(&json!(true)));
    }
}
