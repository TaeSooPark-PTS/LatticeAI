//! Validation, import, and export of workflow definitions.

use lattice_auth::pyjson::OrderedMap;
use serde_json::{json, Value};

use super::{NODE_TYPES, WORKFLOW_ENGINE_VERSION};

// ── validation / import / export (lattice_brain.workflow) ─────────────────────

pub(crate) fn legacy_steps_from_nodes(nodes: &[Value]) -> Vec<Value> {
    nodes
        .iter()
        .map(|node| {
            json!({
                "action": node.get("type"),
                "node": node.get("id"),
            })
        })
        .collect()
}

pub(crate) fn normalize_definition(workflow: &Value) -> Value {
    if let Some(nodes) = workflow.get("nodes").and_then(Value::as_array) {
        if !nodes.is_empty() {
            return json!({
                "id": workflow.get("id"),
                "name": workflow.get("name").and_then(Value::as_str).unwrap_or("Untitled workflow"),
                "nodes": nodes,
                "metadata": workflow.get("metadata").cloned().unwrap_or_else(|| json!({})),
            });
        }
    }
    json!({
        "id": workflow.get("id"),
        "name": workflow.get("name").and_then(Value::as_str).unwrap_or("Untitled workflow"),
        "nodes": workflow.get("nodes").cloned().unwrap_or_else(|| json!([])),
        "metadata": workflow.get("metadata").cloned().unwrap_or_else(|| json!({})),
    })
}

pub(crate) fn validate_definition(workflow: &Value) -> Vec<String> {
    let definition = normalize_definition(workflow);
    let nodes = match definition.get("nodes").and_then(Value::as_array) {
        Some(nodes) if !nodes.is_empty() => nodes,
        _ => return vec!["workflow has no nodes".into()],
    };
    let mut errors = Vec::new();
    let ids: Vec<Option<&str>> = nodes
        .iter()
        .map(|node| node.get("id").and_then(Value::as_str))
        .collect();
    let present: Vec<&str> = ids.iter().copied().flatten().collect();
    if present.len() != {
        let mut uniq = present.clone();
        uniq.sort_unstable();
        uniq.dedup();
        uniq.len()
    } {
        errors.push("duplicate node ids".into());
    }
    let id_set: std::collections::HashSet<&str> = present.into_iter().collect();
    let triggers: Vec<_> = nodes
        .iter()
        .filter(|node| node.get("type").and_then(Value::as_str) == Some("trigger"))
        .collect();
    if triggers.is_empty() {
        errors.push("workflow must have a trigger node".into());
    } else if triggers.len() > 1 {
        errors.push("workflow must have exactly one trigger node".into());
    }
    for node in nodes {
        let nid = node.get("id").and_then(Value::as_str);
        let ntype = node.get("type").and_then(Value::as_str);
        if nid.is_none() {
            errors.push("node missing id".into());
        }
        if let Some(ntype) = ntype {
            if !NODE_TYPES.contains(&ntype) {
                errors.push(format!(
                    "node '{}': unknown type '{ntype}'",
                    nid.unwrap_or("None")
                ));
            }
        } else {
            errors.push(format!(
                "node '{}': unknown type 'None'",
                nid.unwrap_or("None")
            ));
        }
        let mut targets: Vec<Option<&Value>> = Vec::new();
        if ntype == Some("condition") {
            match node.get("branches").and_then(Value::as_object) {
                Some(branches) if !branches.is_empty() => {
                    targets.extend(branches.values().map(Some));
                }
                _ => errors.push(format!(
                    "condition node '{}' must define branches (e.g. true/false)",
                    nid.unwrap_or("")
                )),
            }
        } else {
            targets.push(node.get("next"));
        }
        for target in targets.into_iter().flatten() {
            if target.is_null() {
                continue;
            }
            if let Some(name) = target.as_str() {
                if !id_set.contains(name) {
                    errors.push(format!(
                        "node '{}' points at unknown node '{name}'",
                        nid.unwrap_or("")
                    ));
                }
            }
        }
    }
    errors
}

pub(crate) fn export_workflow(workflow: &Value) -> OrderedMap {
    let definition = normalize_definition(workflow);
    let mut metadata = definition
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.remove("lifted_from_steps");
    let mut body = OrderedMap::new();
    body.insert("lattice_workflow_export", json!(WORKFLOW_ENGINE_VERSION));
    body.insert(
        "name",
        definition.get("name").cloned().unwrap_or(json!(null)),
    );
    body.insert(
        "nodes",
        definition.get("nodes").cloned().unwrap_or(json!([])),
    );
    body.insert("metadata", Value::Object(metadata));
    body
}

pub(crate) fn import_workflow(data: &Value) -> Result<Value, String> {
    if !data.is_object() {
        return Err("import payload must be a JSON object".into());
    }
    let mut metadata = data
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.insert("imported".into(), json!(true));
    let definition = json!({
        "name": data.get("name").and_then(Value::as_str).unwrap_or("Imported workflow"),
        "nodes": data.get("nodes").cloned().unwrap_or_else(|| json!([])),
        "metadata": metadata,
    });
    let errors = validate_definition(&definition);
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }
    Ok(definition)
}
