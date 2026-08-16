//! Workflow-run contract projection.

use lattice_auth::pyjson::OrderedMap;
use serde_json::{json, Value};

use super::time::now_iso;
use super::TERMINAL_STATUSES;

// ── run contract ─────────────────────────────────────────────────────────────

pub(crate) fn workflow_run_contract(run: &OrderedMap) -> OrderedMap {
    let run_id = run
        .get("id")
        .or_else(|| run.get("run_id"))
        .cloned()
        .unwrap_or(Value::Null);
    let workflow_id = run.get("workflow_id").cloned();
    let status = run
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let mut artifacts = OrderedMap::new();
    artifacts.insert("type", json!("workflow_outputs"));
    artifacts.insert("workflow_id", workflow_id.clone().unwrap_or(Value::Null));
    artifacts.insert(
        "outputs",
        run.get("outputs").cloned().unwrap_or_else(|| json!({})),
    );
    artifacts.insert(
        "pause",
        run.get("pause")
            .cloned()
            .or_else(|| run.get("pending_approval").cloned())
            .unwrap_or(Value::Null),
    );
    let blocking = match run
        .get("outputs")
        .and_then(|value| value.get("error"))
        .cloned()
    {
        Some(error) if !error.is_null() => json!([error.to_string()]),
        _ => json!([]),
    };
    let mut body = OrderedMap::new();
    body.insert("run_id", run_id.clone());
    body.insert(
        "agent_id",
        json!(format!(
            "workflow:{}",
            workflow_id
                .as_ref()
                .and_then(Value::as_str)
                .or_else(|| run.get("name").and_then(Value::as_str))
                .unwrap_or("workflow")
        )),
    );
    body.insert("runtime", json!("workflow"));
    body.insert(
        "mode",
        run.get("mode").cloned().unwrap_or_else(|| json!("live")),
    );
    body.insert(
        "goal",
        run.get("name")
            .cloned()
            .unwrap_or_else(|| json!("workflow")),
    );
    body.insert("roles", json!(["workflow"]));
    body.insert(
        "current_role",
        run.get("current_node")
            .cloned()
            .or_else(|| run.get("paused_node").cloned())
            .unwrap_or(Value::Null),
    );
    body.insert("retries", json!(0));
    body.insert(
        "timeline",
        run.get("timeline").cloned().unwrap_or_else(|| json!([])),
    );
    body.insert(
        "artifacts",
        json!([serde_json::to_value(&artifacts).unwrap_or(json!({}))]),
    );
    body.insert("blocking_reasons", blocking);
    body.insert("is_terminal", json!(TERMINAL_STATUSES.contains(&status)));
    body.insert("family", json!("agent-run-contract/v1"));
    body.insert("schema_version", json!("workflow-run-contract/v1"));
    body.insert("kind", json!("workflow_run"));
    body.insert("id", run_id);
    body.insert("status", json!(status));
    body.insert(
        "timestamp",
        run.get("started_at")
            .cloned()
            .or_else(|| run.get("created_at").cloned())
            .unwrap_or_else(|| json!(now_iso())),
    );
    body
}
