use std::collections::{HashMap, HashSet};

use base64::engine::general_purpose::STANDARD as B64_STANDARD;
use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::{json, Map, Value};

use super::super::archive_tables::{array_rows, export_full_tables};
use super::super::crypto::decode_b64_key32;
use super::super::status::schema_versions;
use super::super::{PortabilityState, SUBGRAPH_FORMAT};
use crate::adminops::admin::enterprise::emit_sanitized_governance_audit;
use crate::http_reply;
use crate::knowledge::network_boundary::{HARD_BLOCK_METADATA_FLAGS, HARD_BLOCK_NODE_TYPES};

pub(super) fn build_subgraph_bundle(
    state: &PortabilityState,
    object: &Map<String, Value>,
) -> Result<(Value, Value, usize), String> {
    let node_ids = str_list(object, "node_ids");
    let node_types = str_list(object, "node_types");
    let source_types = str_list(object, "source_types");
    if node_ids.is_empty() && node_types.is_empty() && source_types.is_empty() {
        return Err("At least one selector is required.".into());
    }
    let include_neighbors = object
        .get("include_neighbors")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let include_legacy_global = object
        .get("include_legacy_global")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let redact_provenance = object
        .get("redact_provenance")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let db_path = state.config.graph_db_path();
    let export = export_full_tables(&db_path);
    let all_nodes = array_rows(&export, "nodes");
    let all_edges = array_rows(&export, "edges");
    let all_chunks = array_rows(&export, "chunks");
    let all_prov = array_rows(&export, "ingestion_provenance");

    let prov_by_node: HashMap<&str, &Value> = all_prov
        .iter()
        .filter_map(|p| p.get("node_id").and_then(Value::as_str).map(|nid| (nid, p)))
        .collect();

    let node_id_set: HashSet<&str> = node_ids.iter().map(String::as_str).collect();
    let node_type_set: HashSet<String> =
        node_types.iter().map(|t| t.to_ascii_lowercase()).collect();
    let source_type_set: HashSet<String> = source_types
        .iter()
        .map(|t| t.to_ascii_lowercase())
        .collect();

    let mut selected_ids: HashSet<String> = HashSet::new();
    let mut excluded_local_only = 0usize;

    for node in all_nodes {
        let Some(nid) = node.get("id").and_then(Value::as_str) else {
            continue;
        };
        let ntype = node.get("type").and_then(Value::as_str).unwrap_or("");
        let matches_id = node_id_set.contains(nid);
        let matches_type = node_type_set.contains(&ntype.to_ascii_lowercase());
        let matches_source = prov_by_node
            .get(nid)
            .and_then(|p| p.get("source_type"))
            .and_then(Value::as_str)
            .is_some_and(|st| source_type_set.contains(&st.to_ascii_lowercase()));
        if !(matches_id || matches_type || matches_source) {
            continue;
        }
        if !matches_workspace_boundary(node, workspace_id, include_legacy_global) {
            continue;
        }
        if is_node_share_blocked(node) {
            excluded_local_only += 1;
            continue;
        }
        selected_ids.insert(nid.to_string());
    }

    if include_neighbors && !selected_ids.is_empty() {
        let seeds = selected_ids.clone();
        let node_lookup: HashMap<&str, &Value> = all_nodes
            .iter()
            .filter_map(|n| n.get("id").and_then(Value::as_str).map(|id| (id, n)))
            .collect();
        for edge in all_edges {
            let from = edge.get("from_node").and_then(Value::as_str).unwrap_or("");
            let to = edge.get("to_node").and_then(Value::as_str).unwrap_or("");
            let neighbor = if seeds.contains(from) && !seeds.contains(to) {
                Some(to)
            } else if seeds.contains(to) && !seeds.contains(from) {
                Some(from)
            } else {
                None
            };
            if let Some(nb) = neighbor {
                if let Some(node) = node_lookup.get(nb) {
                    if !matches_workspace_boundary(node, workspace_id, include_legacy_global) {
                        continue;
                    }
                    if is_node_share_blocked(node) {
                        excluded_local_only += 1;
                    } else {
                        selected_ids.insert(nb.to_string());
                    }
                }
            }
        }
    }

    let nodes: Vec<Value> = all_nodes
        .iter()
        .filter(|n| {
            n.get("id")
                .and_then(Value::as_str)
                .is_some_and(|id| selected_ids.contains(id))
        })
        .cloned()
        .collect();
    let edges: Vec<Value> = all_edges
        .iter()
        .filter(|e| {
            let from = e.get("from_node").and_then(Value::as_str).unwrap_or("");
            let to = e.get("to_node").and_then(Value::as_str).unwrap_or("");
            selected_ids.contains(from) && selected_ids.contains(to)
        })
        .cloned()
        .collect();
    let chunks: Vec<Value> = all_chunks
        .iter()
        .filter(|c| {
            c.get("source_node")
                .and_then(Value::as_str)
                .is_some_and(|id| selected_ids.contains(id))
        })
        .cloned()
        .collect();
    let provenance: Vec<Value> = all_prov
        .iter()
        .filter(|p| {
            p.get("node_id")
                .and_then(Value::as_str)
                .is_some_and(|id| selected_ids.contains(id))
        })
        .map(|p| {
            if !redact_provenance {
                return p.clone();
            }
            let mut redacted = p.clone();
            if let Some(obj) = redacted.as_object_mut() {
                obj.insert("source_uri".into(), Value::Null);
                obj.insert("owner".into(), Value::Null);
            }
            redacted
        })
        .collect();

    let counts = json!({
        "nodes": nodes.len(),
        "edges": edges.len(),
        "chunks": chunks.len(),
        "provenance": provenance.len(),
        "knowledge_sources": 0,
    });

    let mut header = schema_versions();
    header.insert("format", json!(SUBGRAPH_FORMAT));
    header.insert("format_version", json!(1));
    header.insert("exported_at", json!(http_reply::now_iso_utc()));
    header.insert("workspace_id", json!(workspace_id));
    header.insert("redact_provenance", json!(redact_provenance));
    header.insert("counts", counts.clone());

    let canonical_payload = json!({
        "header": header,
        "nodes": nodes,
        "edges": edges,
        "chunks": chunks,
        "knowledge_sources": [],
        "provenance": provenance,
    });
    let sign_bytes = serde_json::to_vec(&canonical_payload)
        .map_err(|e| format!("Failed to canonicalize subgraph payload: {e}"))?;
    let sig_b64 = state.identity.sign(&sign_bytes);

    let mut bundle = canonical_payload;
    bundle["signature"] = json!({
        "algorithm": "ed25519",
        "public_key": state.identity.public_key_b64(),
        "fingerprint": state.identity.fingerprint(),
        "signature": sig_b64,
    });
    Ok((bundle, counts, excluded_local_only))
}

pub(super) fn verify_subgraph_bundle_signature(bundle: &Value) -> Result<bool, String> {
    let Some(sig_obj) = bundle.get("signature").and_then(Value::as_object) else {
        return Ok(false);
    };
    let pub_b64 = sig_obj
        .get("public_key")
        .and_then(Value::as_str)
        .ok_or_else(|| "Shared bundle signature missing public_key.".to_string())?;
    let sig_b64 = sig_obj
        .get("signature")
        .and_then(Value::as_str)
        .ok_or_else(|| "Shared bundle signature missing signature.".to_string())?;

    let pub_raw = decode_b64_key32(pub_b64)?;
    let verifying = VerifyingKey::from_bytes(&pub_raw)
        .map_err(|e| format!("Invalid sender Ed25519 key: {e}"))?;
    let sig_raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(sig_b64.trim())
        .or_else(|_| B64_STANDARD.decode(sig_b64.trim()))
        .map_err(|e| format!("Invalid sender signature encoding: {e}"))?;
    if sig_raw.len() != 64 {
        return Err("Invalid sender signature length.".into());
    }
    let mut sig_arr = [0u8; 64];
    sig_arr.copy_from_slice(&sig_raw);
    let signature = Signature::from_bytes(&sig_arr);

    let canonical_payload = json!({
        "header": bundle.get("header").cloned().unwrap_or(json!({})),
        "nodes": bundle.get("nodes").cloned().unwrap_or(json!([])),
        "edges": bundle.get("edges").cloned().unwrap_or(json!([])),
        "chunks": bundle.get("chunks").cloned().unwrap_or(json!([])),
        "knowledge_sources": bundle.get("knowledge_sources").cloned().unwrap_or(json!([])),
        "provenance": bundle.get("provenance").cloned().unwrap_or(json!([])),
    });
    let payload_bytes = serde_json::to_vec(&canonical_payload)
        .map_err(|e| format!("Failed to encode signed bundle payload: {e}"))?;
    verifying
        .verify(&payload_bytes, &signature)
        .map_err(|_| "Shared subgraph Ed25519 signature verification failed.".to_string())?;
    Ok(true)
}

fn str_list(object: &Map<String, Value>, key: &str) -> Vec<String> {
    object
        .get(key)
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn is_node_share_blocked(node: &Value) -> bool {
    let ntype = node.get("type").and_then(Value::as_str).unwrap_or("");
    if HARD_BLOCK_NODE_TYPES
        .iter()
        .any(|blocked| blocked.eq_ignore_ascii_case(ntype))
    {
        return true;
    }
    let meta_str = node
        .get("metadata_json")
        .and_then(Value::as_str)
        .unwrap_or("{}");
    let meta: Value = serde_json::from_str(meta_str).unwrap_or(json!({}));
    for flag in HARD_BLOCK_METADATA_FLAGS {
        if meta.get(*flag).and_then(Value::as_bool) == Some(true) {
            return true;
        }
    }
    false
}

fn matches_workspace_boundary(
    node: &Value,
    workspace_id: Option<&str>,
    include_legacy_global: bool,
) -> bool {
    let Some(wanted) = workspace_id else {
        return true;
    };
    let meta_str = node
        .get("metadata_json")
        .and_then(Value::as_str)
        .unwrap_or("{}");
    let meta: Value = serde_json::from_str(meta_str).unwrap_or(json!({}));
    match meta.get("workspace_id").and_then(Value::as_str) {
        Some(ws) if !ws.is_empty() => ws == wanted,
        _ => include_legacy_global || wanted == "personal",
    }
}

pub(in crate::knowledge::portability) fn emit_portability_audit(
    state: &PortabilityState,
    event_type: &str,
    details: Value,
) {
    let mut payload = Map::new();
    payload.insert("event_source".into(), json!("knowledge_portability"));
    if let Some(obj) = details.as_object() {
        for (k, v) in obj {
            payload.insert(k.clone(), v.clone());
        }
    }
    emit_sanitized_governance_audit(state.config.data_dir(), event_type, None, None, payload);
}
