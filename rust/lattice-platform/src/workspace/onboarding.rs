//! First-run onboarding progress.
//!
//! Port of `core/workspace_onboarding.py`. Owns the `onboarding` branch of the
//! state document: which named step the person is on, what each step recorded,
//! and whether the whole run is finished.
//!
//! An unknown step or status raises in Python and **no handler catches it**, so
//! the client sees Starlette's plain-text 500. That is reproduced rather than
//! improved: turning it into a 400 here would be a contract change the fixture
//! (`workspace_onboarding_step/error_unknown_step`) would fail on, and the
//! decision of what those routes should answer is not this port's to make.

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
use serde_json::{json, Map, Value};

use super::constants::{ONBOARDING_STATUSES, ONBOARDING_STEPS};
use super::pyutil::now_iso;
use super::store::{StoreError, WorkspaceOsStore};

/// One account as `status` reads it: the email and the role.
pub type Account<'a> = (&'a str, &'a str);

/// `onboarding_status(users, graph_stats)`.
pub fn status(store: &WorkspaceOsStore, users: &[Account<'_>], graph_stats: &Value) -> Value {
    project(&store.load_state(), users, graph_stats)
}

/// The same projection, over an already-loaded document.
fn project(state: &Value, users: &[Account<'_>], graph_stats: &Value) -> Value {
    let onboarding = state.get("onboarding").and_then(Value::as_object);
    let steps = onboarding
        .and_then(|map| map.get("steps"))
        .and_then(Value::as_object);
    let ordered: Vec<Value> = ONBOARDING_STEPS
        .iter()
        .map(|step| {
            steps
                .and_then(|map| map.get(*step))
                .cloned()
                .unwrap_or_else(|| json!({"id": step, "status": "pending"}))
        })
        .collect();

    let has_account = !users.is_empty();
    let has_admin = users.iter().any(|(_, role)| *role == "admin") || has_account;
    let graph_ready = graph_stats
        .as_object()
        .is_some_and(|stats| !stats.is_empty() && !truthy(stats.get("disabled")));

    let mut payload = onboarding.cloned().unwrap_or_else(Map::new);
    payload.insert("steps".into(), Value::Array(ordered));
    payload.insert("has_account".into(), json!(has_account));
    payload.insert("has_admin".into(), json!(has_admin));
    payload.insert("graph_ready".into(), json!(graph_ready));
    payload.insert("required_steps".into(), json!(ONBOARDING_STEPS));
    Value::Object(payload)
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
        _ => false,
    }
}

/// `update_onboarding_step`.
///
/// Returns the fresh [`status`] payload, which is what the route answers.
pub fn update_step(
    store: &WorkspaceOsStore,
    step: &str,
    step_status: &str,
    data: Option<&Value>,
    error: &str,
    user_email: Option<&str>,
    users: &[Account<'_>],
    graph_stats: &Value,
) -> Result<Value, StoreError> {
    if !ONBOARDING_STEPS.contains(&step) {
        return Err(StoreError::Value(format!(
            "unknown onboarding step: {step}"
        )));
    }
    if !ONBOARDING_STATUSES.contains(&step_status) {
        return Err(StoreError::Value(format!(
            "unknown onboarding status: {step_status}"
        )));
    }
    store.mutate(|state| {
        let onboarding = state
            .as_object_mut()
            .expect("state document is an object")
            .entry("onboarding")
            .or_insert_with(|| json!({}));
        if !onboarding.is_object() {
            *onboarding = json!({});
        }
        let steps = onboarding
            .as_object_mut()
            .expect("onboarding is an object")
            .entry("steps")
            .or_insert_with(|| json!({}));
        if !steps.is_object() {
            *steps = json!({});
        }
        let record = steps
            .as_object_mut()
            .expect("steps is an object")
            .entry(step)
            .or_insert_with(|| json!({"id": step}));
        // `data or record.get("data") or {}`: a caller that sends nothing keeps
        // whatever the step already recorded.
        let kept = record.get("data").cloned();
        let resolved = match data {
            Some(value) if truthy(Some(value)) => value.clone(),
            _ => match kept {
                Some(value) if truthy(Some(&value)) => value,
                _ => json!({}),
            },
        };
        record["id"] = json!(step);
        record["status"] = json!(step_status);
        record["data"] = resolved;
        record["error"] = json!(error);
        record["updated_at"] = json!(now_iso());
        record["user_email"] = user_email.map_or(Value::Null, |email| json!(email));

        let onboarding = state
            .get_mut("onboarding")
            .and_then(Value::as_object_mut)
            .expect("onboarding is an object");
        if matches!(step_status, "complete" | "skipped") {
            let index = ONBOARDING_STEPS
                .iter()
                .position(|name| *name == step)
                .unwrap_or(0);
            if step == "complete" {
                onboarding.insert("completed".into(), json!(true));
                onboarding.insert("completed_at".into(), json!(now_iso()));
                onboarding.insert("current_step".into(), json!("complete"));
            } else if index + 1 < ONBOARDING_STEPS.len() {
                onboarding.insert("current_step".into(), json!(ONBOARDING_STEPS[index + 1]));
            }
        } else if step_status == "failed" {
            onboarding.insert("current_step".into(), json!(step));
        }
        Ok(())
    })?;
    store.record_timeline_event(
        "workspace",
        "onboarding_step",
        json!({"step": step, "status": step_status}),
        None,
    );
    // Python's `update_step` returns `self.status()` with no arguments.
    let _ = (users, graph_stats);
    Ok(status(store, &[], &json!({})))
}

/// `complete_onboarding` — walk every step to `complete`, in order.
pub fn complete(
    store: &WorkspaceOsStore,
    data: Option<&Value>,
    user_email: Option<&str>,
    users: &[Account<'_>],
    graph_stats: &Value,
) -> Result<Value, StoreError> {
    for step in ONBOARDING_STEPS {
        update_step(
            store,
            step,
            "complete",
            if step == "complete" { data } else { None },
            "",
            user_email,
            users,
            graph_stats,
        )?;
    }
    let _ = (users, graph_stats);
    Ok(status(store, &[], &json!({})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    #[test]
    fn a_fresh_status_lists_every_step_in_order() {
        let (_dir, store) = store();
        let payload = status(&store, &[], &json!({"disabled": true}));
        let steps = payload["steps"].as_array().unwrap();
        assert_eq!(steps.len(), ONBOARDING_STEPS.len());
        assert_eq!(steps[0]["id"], json!("account"));
        assert_eq!(payload["current_step"], json!("account"));
        assert_eq!(payload["completed"], json!(false));
        assert_eq!(payload["has_account"], json!(false));
        assert_eq!(payload["has_admin"], json!(false));
        assert_eq!(payload["graph_ready"], json!(false));
        assert_eq!(payload["required_steps"], json!(ONBOARDING_STEPS));
    }

    #[test]
    fn any_account_counts_as_an_admin_and_a_live_graph_as_ready() {
        let (_dir, store) = store();
        let payload = status(
            &store,
            &[("a@b.test", "user")],
            &json!({"nodes": {"Concept": 1}}),
        );
        assert_eq!(payload["has_account"], json!(true));
        assert_eq!(payload["has_admin"], json!(true));
        assert_eq!(payload["graph_ready"], json!(true));
        let empty_stats = status(&store, &[("a@b.test", "admin")], &json!({}));
        assert_eq!(empty_stats["graph_ready"], json!(false));
    }

    #[test]
    fn completing_a_step_advances_to_the_next_one() {
        let (_dir, store) = store();
        let payload = update_step(
            &store,
            "account",
            "complete",
            Some(&json!({"note": "픽스처"})),
            "",
            Some("owner@lattice.test"),
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(payload["current_step"], json!("admin"));
        assert_eq!(payload["steps"][0]["status"], json!("complete"));
        assert_eq!(payload["steps"][0]["data"], json!({"note": "픽스처"}));
        assert_eq!(
            payload["steps"][0]["user_email"],
            json!("owner@lattice.test")
        );
        assert!(payload["steps"][0]["updated_at"].is_string());
    }

    #[test]
    fn a_failed_step_stays_current_and_a_skipped_one_advances() {
        let (_dir, store) = store();
        let failed = update_step(
            &store,
            "model_install",
            "failed",
            None,
            "boom",
            None,
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(failed["current_step"], json!("model_install"));
        assert_eq!(failed["steps"][4]["error"], json!("boom"));
        let skipped = update_step(
            &store,
            "folder_connection",
            "skipped",
            None,
            "",
            None,
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(skipped["current_step"], json!("first_question"));
    }

    #[test]
    fn the_final_step_marks_the_run_complete() {
        let (_dir, store) = store();
        let payload = update_step(
            &store,
            "complete",
            "complete",
            None,
            "",
            None,
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(payload["completed"], json!(true));
        assert_eq!(payload["current_step"], json!("complete"));
        assert!(payload["completed_at"].is_string());
    }

    #[test]
    fn an_unknown_step_or_status_refuses_before_anything_is_written() {
        let (_dir, store) = store();
        assert_eq!(
            update_step(&store, "nope", "complete", None, "", None, &[], &json!({})).unwrap_err(),
            StoreError::Value("unknown onboarding step: nope".into())
        );
        assert_eq!(
            update_step(&store, "account", "nope", None, "", None, &[], &json!({})).unwrap_err(),
            StoreError::Value("unknown onboarding status: nope".into())
        );
        assert_eq!(
            status(&store, &[], &json!({}))["steps"][0]["status"],
            json!("pending")
        );
    }

    #[test]
    fn a_second_update_without_data_keeps_what_the_step_recorded() {
        let (_dir, store) = store();
        update_step(
            &store,
            "hardware",
            "running",
            Some(&json!({"chip": "M4"})),
            "",
            None,
            &[],
            &json!({}),
        )
        .unwrap();
        let payload = update_step(
            &store,
            "hardware",
            "complete",
            None,
            "",
            None,
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(payload["steps"][2]["data"], json!({"chip": "M4"}));
    }

    #[test]
    fn complete_walks_every_step_and_carries_data_only_on_the_last() {
        let (_dir, store) = store();
        let payload = complete(
            &store,
            Some(&json!({"finished": true})),
            Some("owner@lattice.test"),
            &[],
            &json!({}),
        )
        .unwrap();
        assert_eq!(payload["completed"], json!(true));
        let steps = payload["steps"].as_array().unwrap();
        assert!(steps.iter().all(|step| step["status"] == json!("complete")));
        assert_eq!(steps[0]["data"], json!({}));
        assert_eq!(steps[8]["data"], json!({"finished": true}));
    }
}
