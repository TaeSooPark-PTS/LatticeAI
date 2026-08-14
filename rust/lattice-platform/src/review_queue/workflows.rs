//! Workflow helpers used by automation.rs and Review Center.

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
use serde_json::{json, Value};

use super::http::{json_hash, now_iso};
use super::os::scoped;
use super::store::ReviewError;
use super::{GovernanceState, DEFAULT_WORKSPACE_ID};

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
