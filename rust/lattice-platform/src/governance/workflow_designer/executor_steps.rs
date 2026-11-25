use std::path::{Path, PathBuf};

use lattice_core::db::tables::GRAPH_DB;
use lattice_core::graph_write::types::IngestEventRequest;
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};

use super::super::store::WorkflowStore;
use super::super::DEFAULT_WORKSPACE_ID;
use super::{step_id, ToolContext};
use crate::toolsurface::mcp;

pub(super) fn execute_tool(
    step: &Value,
    config: &Value,
    inputs: &Value,
    tools: Option<&ToolContext<'_>>,
) -> Value {
    let name = config
        .get("tool")
        .or_else(|| config.get("name"))
        .or_else(|| step.get("tool"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if name.is_empty() {
        return json!({
            "status": "manual",
            "detail": "tool step has no tool name",
        });
    }
    let Some(ctx) = tools else {
        return json!({
            "status": "manual",
            "detail": format!("tool '{name}' has no governed dispatch attached to this process"),
        });
    };
    if !mcp::is_native_tool(&name) {
        return json!({
            "status": "manual",
            "detail": format!("tool '{name}' has no native executor"),
        });
    }
    let mut args = config
        .get("args")
        .cloned()
        .or_else(|| config.get("arguments").cloned())
        .unwrap_or(json!({}));
    if let (Some(dst), Some(src)) = (args.as_object_mut(), inputs.as_object()) {
        for (key, value) in src {
            dst.entry(key.clone()).or_insert(value.clone());
        }
    }
    match mcp::dispatch(
        Some(ctx.tools),
        Path::new(""),
        ctx.identity,
        ctx.headers,
        &name,
        &args,
    ) {
        Ok(result) => json!({
            "status": "ok",
            "detail": format!("ran governed tool '{name}'"),
            "result": result,
        }),
        Err(error) => json!({
            "status": "failed",
            "detail": format!("tool '{name}': {error:?}"),
        }),
    }
}

pub(super) fn execute_notification(
    step: &Value,
    config: &Value,
    scope: &str,
    store: &WorkflowStore,
) -> Value {
    let message = config
        .get("message")
        .or_else(|| config.get("value"))
        .or_else(|| step.get("message"))
        .and_then(Value::as_str)
        .unwrap_or("workflow notification");
    let event = store.record_notification(
        scope,
        json!({
            "node": step_id(step),
            "message": message,
        }),
    );
    json!({
        "status": "ok",
        "detail": "recorded a workspace timeline notification",
        "result": event,
    })
}

pub(super) fn execute_graph(
    step: &Value,
    config: &Value,
    data_dir: Option<&Path>,
    scope: &str,
    user_email: Option<&str>,
) -> Value {
    let Some(data_dir) = data_dir else {
        return json!({
            "status": "manual",
            "detail": "graph writer is not attached to this process",
        });
    };
    let db = data_dir.join(GRAPH_DB);
    if !db.exists() {
        return json!({
            "status": "manual",
            "detail": "knowledge graph store is not present",
        });
    }
    let Ok(store) = lattice_core::db::Store::open(&db) else {
        return json!({
            "status": "failed",
            "detail": "could not open the knowledge graph store",
        });
    };
    let blobs = data_dir.join("knowledge_graph_blobs");
    let Ok(writer) = GraphWriter::open(std::sync::Arc::new(store), blobs) else {
        return json!({
            "status": "failed",
            "detail": "could not open the graph writer",
        });
    };
    let title = config
        .get("title")
        .or_else(|| step.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("workflow graph step");
    let event_type = config
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or("workflow_step");
    let mut metadata = Map::new();
    metadata.insert("node".into(), json!(step_id(step)));
    let request = IngestEventRequest {
        event_type: event_type.to_string(),
        title: title.to_string(),
        user_email: user_email.map(str::to_string),
        source: Some("workflow".into()),
        workspace_id: Some(if scope.is_empty() {
            DEFAULT_WORKSPACE_ID.to_string()
        } else {
            scope.to_string()
        }),
        metadata,
        ..IngestEventRequest::default()
    };
    match writer.ingest_event(&request) {
        Ok(outcome) => json!({
            "status": "ok",
            "detail": "ingested a graph event",
            "result": outcome.to_json_brief(),
        }),
        Err(error) => json!({
            "status": "failed",
            "detail": format!("graph ingest failed: {error}"),
        }),
    }
}

pub(super) fn execute_output(
    step: &Value,
    config: &Value,
    scope: &str,
    store: &WorkflowStore,
) -> Value {
    let value = config
        .get("value")
        .or_else(|| step.get("value"))
        .cloned()
        .unwrap_or(json!("output recorded"));
    let review_inbox = config.get("review_queue").and_then(Value::as_bool) == Some(true)
        || config
            .get("value")
            .and_then(Value::as_str)
            .is_some_and(|text| text.to_ascii_lowercase().contains("review"));
    if review_inbox {
        let event = store.record_notification(
            scope,
            json!({
                "node": step_id(step),
                "message": value,
            }),
        );
        return json!({
            "status": "ok",
            "detail": "delivered output to the workspace timeline (review inbox)",
            "result": event,
        });
    }
    json!({
        "status": "ok",
        "detail": "output recorded",
        "result": value,
    })
}

pub(crate) fn store_data_dir(store: &WorkflowStore) -> PathBuf {
    store
        .path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}
