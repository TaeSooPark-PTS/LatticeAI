//! Selective Knowledge Graph subgraph sharing, X25519 sealed-box archives,
//! Ed25519 signature verification, and Review Queue staging handlers.

use std::collections::{HashMap, HashSet};
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use base64::engine::general_purpose::STANDARD as B64_STANDARD;
use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use lattice_auth::OrderedMap;
use lattice_core::graph_write::types::ImportRequest;
use serde_json::{json, Map, Value};

use crate::adminops::admin::enterprise::{
    emit_sanitized_governance_audit, load_org_governance_policy, normalize_tenant_scope,
    validate_tenant_scope,
};
use crate::governance::review_queue::store::{
    create_review_item, load_review_item, transition_approve, ReviewError,
};
use crate::governance::review_queue::GovernanceState;
use crate::http_reply::{self, detail, json_ok, message_detail, missing_body, parse_json_object};
use crate::knowledge::network_boundary::{HARD_BLOCK_METADATA_FLAGS, HARD_BLOCK_NODE_TYPES};

use super::archive::missing_archive;
use super::archive_tables::{array_rows, export_full_tables};
use super::crypto::{
    decode_b64_key32, seal_and_write_archive, sha256_hex, validate_zip_entries,
    verify_and_decrypt_archive, DecryptionKey, EncryptionMode, ARCHIVE_FORMAT,
    ARCHIVE_FORMAT_VERSION,
};
use super::postgres::stamp;
use super::status::{brain_network_on, require_graph, schema_versions};
use super::*;

const SUBGRAPH_ENTRY: &str = "bundle.json";
const SHARE_REVIEW_SOURCE: &str = "kg_change_digest";
const SHARE_REVIEW_KIND: &str = "kg_share_bundle";

// ── Selective Graph Share Handlers ───────────────────────────────────────────

pub(crate) async fn share_status(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let enabled = brain_network_on();
    let mut gate = OrderedMap::new();
    gate.insert("name", json!("brain_network"));
    gate.insert("flag", json!(BRAIN_NETWORK_ENV));
    gate.insert("enabled", json!(enabled));
    gate.insert("default", json!(false));
    gate.insert("source", json!("resolver"));
    gate.insert("detail", json!(BRAIN_NETWORK_DISABLED_EN));
    let mut map = OrderedMap::new();
    map.insert("enabled", json!(enabled));
    map.insert("flag", json!(BRAIN_NETWORK_ENV));
    map.insert("format", json!(SUBGRAPH_FORMAT));
    map.insert("format_version", json!(1));
    map.insert("graph_available", json!(state.graph_available()));
    map.insert("signing", json!(true));
    map.insert("device", json!(state.identity.share_device()));
    map.insert("proposal_cap", json!(200));
    map.insert("encryption", json!(["passphrase", "recipient_public_key"]));
    map.insert("recipient_public_key_encryption", json!(true));
    map.insert("sealed_box_algorithm", json!(SEALED_BOX_ALGORITHM));
    map.insert("gate", json!(gate));
    if enabled {
        map.insert("detail", Value::Null);
    } else {
        let detail = lattice_core::messages::text(
            "portability.brain_network_disabled",
            http_reply::language_of(&headers),
            &[],
        );
        map.insert("detail", json!(detail));
    }
    json_ok(map)
}

fn share_disabled(headers: &HeaderMap) -> Response {
    message_detail(403, "portability.brain_network_disabled", headers)
}

pub(crate) async fn share_recipient_key(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    map.insert("algorithm", json!(SEALED_BOX_ALGORITHM));
    map.insert("signing_algorithm", json!("ed25519"));
    map.insert("public_key", json!(state.identity.x25519_public_key_b64()));
    map.insert(
        "x25519_public_key",
        json!(state.identity.x25519_public_key_b64()),
    );
    map.insert("ed25519_public_key", json!(state.identity.public_key_b64()));
    map.insert("fingerprint", json!(state.identity.fingerprint()));
    json_ok(map)
}

pub(crate) async fn share_export(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    match build_subgraph_bundle(&state, &object) {
        Ok((bundle, counts, excluded_local_only)) => {
            emit_portability_audit(
                &state,
                "share_exported",
                json!({
                    "counts": counts,
                    "excluded_local_only": excluded_local_only,
                }),
            );
            let sig_ok = verify_subgraph_bundle_signature(&bundle).unwrap_or(false);
            let mut map = OrderedMap::new();
            map.insert("format", json!(SUBGRAPH_FORMAT));
            map.insert("format_version", json!(1));
            map.insert("counts", counts);
            map.insert("excluded_local_only", json!(excluded_local_only));
            map.insert("signed", json!(sig_ok));
            map.insert("signature_verified", json!(sig_ok));
            map.insert(
                "signature",
                bundle.get("signature").cloned().unwrap_or(Value::Null),
            );
            map.insert("bundle", bundle.clone());
            map.insert("artifact", bundle);
            json_ok(map)
        }
        Err(err) => detail(StatusCode::BAD_REQUEST, &err),
    }
}

pub(crate) async fn share_archive(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let recipient_key = object
        .get("recipient_public_key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let mode = match (passphrase, recipient_key) {
        (Some(pw), None) => EncryptionMode::Passphrase(pw),
        (None, Some(pk)) => EncryptionMode::RecipientPublicKey { public_key_b64: pk },
        _ => {
            return detail(
                StatusCode::BAD_REQUEST,
                "Choose exactly one encryption mechanism: passphrase or recipient_public_key.",
            );
        }
    };

    let (bundle, counts, excluded_local_only) = match build_subgraph_bundle(&state, &object) {
        Ok(v) => v,
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };

    let dest = object
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            state
                .exports_dir()
                .join(format!("kg-share-{}.latticebrain", stamp()))
        });

    let bundle_bytes = match serde_json::to_vec_pretty(&bundle) {
        Ok(b) => b,
        Err(e) => return detail(StatusCode::BAD_REQUEST, &e.to_string()),
    };
    let bundle_sha = sha256_hex(&bundle_bytes);

    let mut inner_buf = Cursor::new(Vec::new());
    {
        let mut inner_zip = zip::ZipWriter::new(&mut inner_buf);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        if let Err(e) = inner_zip.start_file(SUBGRAPH_ENTRY, options) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        if let Err(e) = inner_zip.write_all(&bundle_bytes) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        if let Err(e) = inner_zip.finish() {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
    }
    let plaintext = inner_buf.into_inner();

    let encryption_label = match mode {
        EncryptionMode::Passphrase(_) => "passphrase",
        EncryptionMode::RecipientPublicKey { .. } => "recipient_public_key",
    };

    let mut manifest = schema_versions();
    manifest.insert("subgraph_format", json!(SUBGRAPH_FORMAT));
    manifest.insert("counts", counts.clone());
    manifest.insert("excluded_local_only", json!(excluded_local_only));
    manifest.insert("checksums", json!({ "bundle_sha256": bundle_sha }));

    let (header, manifest_val) = match seal_and_write_archive(
        &dest,
        "subgraph",
        &plaintext,
        &mut manifest,
        mode,
        &state.identity,
    ) {
        Ok(v) => v,
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };

    emit_portability_audit(
        &state,
        "share_archive_created",
        json!({
            "path": dest.to_string_lossy(),
            "encryption_mode": encryption_label,
            "counts": counts,
        }),
    );

    let bytes = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    let mut map = OrderedMap::new();
    map.insert("status", json!("created"));
    map.insert("path", json!(dest.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert("encryption_mode", json!(encryption_label));
    map.insert("format", json!(ARCHIVE_FORMAT));
    map.insert("format_version", json!(ARCHIVE_FORMAT_VERSION));
    map.insert("counts", counts);
    map.insert("excluded_local_only", json!(excluded_local_only));
    map.insert("header", header);
    map.insert("manifest", manifest_val);
    json_ok(map)
}

pub(crate) async fn share_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };

    let dry_run = object
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let bundle = if let Some(artifact) = object.get("artifact").filter(|v| v.is_object()) {
        artifact.clone()
    } else if let Some(path) = object
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        let p = Path::new(path);
        if !p.exists() {
            return missing_archive(path);
        }
        let decrypted = match verify_and_decrypt_archive(
            p,
            DecryptionKey::PassphraseOrIdentity {
                passphrase,
                identity: &state.identity,
            },
        ) {
            Ok(d) => d,
            Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
        };
        let cursor = Cursor::new(&decrypted.plaintext);
        let mut inner_zip = match zip::ZipArchive::new(cursor) {
            Ok(z) => z,
            Err(e) => return detail(StatusCode::BAD_REQUEST, &e.to_string()),
        };
        if let Err(err) = validate_zip_entries(&mut inner_zip) {
            return detail(StatusCode::BAD_REQUEST, &err);
        }
        let mut entry = match inner_zip.by_name(SUBGRAPH_ENTRY) {
            Ok(e) => e,
            Err(_) => {
                return detail(
                    StatusCode::BAD_REQUEST,
                    "Archive does not contain a shared subgraph bundle.",
                )
            }
        };
        let mut text = String::new();
        if let Err(e) = entry.read_to_string(&mut text) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        match serde_json::from_str::<Value>(&text) {
            Ok(v) if v.is_object() => v,
            _ => return detail(StatusCode::BAD_REQUEST, "Corrupt shared subgraph JSON."),
        }
    } else {
        return detail(
            StatusCode::BAD_REQUEST,
            "Either artifact or path must be provided to import shared knowledge.",
        );
    };

    let signature_verified = match verify_subgraph_bundle_signature(&bundle) {
        Ok(v) => v,
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };

    let nodes = array_rows(&bundle, "nodes").len();
    let edges = array_rows(&bundle, "edges").len();
    let chunks = array_rows(&bundle, "chunks").len();
    let provenance = array_rows(&bundle, "provenance").len();
    let counts = json!({
        "nodes": nodes,
        "edges": edges,
        "chunks": chunks,
        "provenance": provenance,
    });

    if dry_run {
        let mut map = OrderedMap::new();
        map.insert("status", json!("dry_run"));
        map.insert("dry_run", json!(true));
        map.insert("staged", json!(false));
        map.insert("signature_verified", json!(signature_verified));
        map.insert("counts", counts);
        return json_ok(map);
    }

    let gov = GovernanceState::open(
        state.auth.clone(),
        state.config.data_dir(),
        state.config.data_dir(),
        None,
    );
    let sender_fp = bundle
        .get("signature")
        .and_then(|s| s.get("fingerprint"))
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let title = format!("Shared Subgraph ({nodes} nodes, {edges} edges) from {sender_fp}");
    let summary = format!(
        "Staged selective subgraph package ({nodes} nodes, {edges} edges, {chunks} chunks) awaiting review approval."
    );
    let provenance_meta = json!({
        "sender_fingerprint": sender_fp,
        "signature_verified": signature_verified,
        "format": bundle.get("format").cloned().unwrap_or_else(|| json!(SUBGRAPH_FORMAT)),
        "received_at": http_reply::now_iso_utc(),
        "workspace_id": workspace_id,
    });
    let user_email = if identity.email.is_empty() {
        None
    } else {
        Some(identity.email.as_str())
    };
    let review_item = create_review_item(
        &gov,
        &title,
        &summary,
        SHARE_REVIEW_SOURCE,
        SHARE_REVIEW_KIND,
        bundle,
        provenance_meta,
        user_email,
        workspace_id,
    );
    let proposal_id = review_item
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    emit_portability_audit(
        &state,
        "share_imported",
        json!({
            "proposal_id": proposal_id,
            "sender_fingerprint": sender_fp,
            "counts": counts,
            "workspace_id": workspace_id,
        }),
    );

    let mut map = OrderedMap::new();
    map.insert("staged", json!(true));
    map.insert("status", json!("pending_review"));
    map.insert("dry_run", json!(false));
    map.insert("signature_verified", json!(signature_verified));
    map.insert("proposal_id", json!(proposal_id));
    map.insert("counts", counts);
    map.insert("review_item", review_item);
    json_ok(map)
}

pub(crate) async fn share_accept(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    // FastAPI validates the body model before require_admin. An absent body
    // is 422 loc=["body"] even for an anonymous caller.
    if body.is_empty() {
        return missing_body();
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let gov = GovernanceState::open(
        state.auth.clone(),
        state.config.data_dir(),
        state.config.data_dir(),
        None,
    );
    let stored = match load_review_item(&gov, &item_id, None) {
        Ok(item) => item,
        Err(ReviewError::NotFound) => {
            return message_detail(404, "review.item_not_found", &headers)
        }
        Err(ReviewError::Conflict(msg)) => return detail(StatusCode::CONFLICT, &msg),
        Err(ReviewError::Invalid(msg)) => return detail(StatusCode::UNPROCESSABLE_ENTITY, &msg),
        Err(ReviewError::Failed(msg)) => return detail(StatusCode::BAD_REQUEST, &msg),
    };
    let stored_ws = stored.get("workspace_id").and_then(Value::as_str);
    if let Some(requested_ws) = workspace_id {
        if let Err(_msg) = validate_tenant_scope(
            state.config.data_dir(),
            Some(requested_ws),
            stored_ws,
            if identity.email.is_empty() {
                None
            } else {
                Some(identity.email.as_str())
            },
            "share_accept",
        ) {
            return message_detail(404, "review.item_not_found", &headers);
        }
    }
    let actor_org = normalize_tenant_scope(workspace_id.or(stored_ws));
    let org_policy = load_org_governance_policy(state.config.data_dir(), actor_org);
    if stored_ws.is_some_and(|s| normalize_tenant_scope(Some(s)) != actor_org)
        && !org_policy.allow_cross_org_share
    {
        return detail(
            StatusCode::FORBIDDEN,
            "Organization governance policy disallows cross-organization share acceptance.",
        );
    }
    let status = stored
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "pending" && status != "snoozed" {
        return detail(
            StatusCode::CONFLICT,
            &format!("cannot 'accept' a share proposal in status '{status}'"),
        );
    }
    if stored.get("kind").and_then(Value::as_str) != Some(SHARE_REVIEW_KIND) {
        return detail(
            StatusCode::BAD_REQUEST,
            "Review item is not a staged Knowledge Graph share proposal.",
        );
    }
    let payload_map = stored
        .get("payload")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let Some(graph) = state.graph.clone() else {
        return message_detail(503, "portability.review_queue_unavailable", &headers);
    };
    let req = ImportRequest {
        data: payload_map,
        mode: "merge".into(),
        dry_run: false,
    };
    let outcome = match tokio::task::spawn_blocking(move || graph.import_graph_data(&req)).await {
        Ok(Ok(outcome)) => outcome,
        Ok(Err(err)) => return detail(StatusCode::BAD_REQUEST, &err.to_string()),
        Err(err) => return detail(StatusCode::BAD_GATEWAY, &err.to_string()),
    };

    let updated = match transition_approve(&gov, &item_id, workspace_id) {
        Ok(item) => item,
        Err(ReviewError::Conflict(msg)) => return detail(StatusCode::CONFLICT, &msg),
        Err(ReviewError::NotFound) => {
            return message_detail(404, "review.item_not_found", &headers)
        }
        Err(ReviewError::Invalid(msg)) => return detail(StatusCode::UNPROCESSABLE_ENTITY, &msg),
        Err(ReviewError::Failed(msg)) => return detail(StatusCode::BAD_REQUEST, &msg),
    };

    emit_portability_audit(
        &state,
        "share_accepted",
        json!({
            "proposal_id": item_id,
            "workspace_id": workspace_id,
            "import": outcome.to_json(),
        }),
    );

    let mut map = OrderedMap::new();
    map.insert("accepted", json!(true));
    map.insert("status", json!("approved"));
    map.insert("proposal_id", json!(item_id));
    map.insert("workspace_id", json!(workspace_id));
    map.insert("import", outcome.to_json());
    map.insert("review_item", updated);
    json_ok(map)
}

// ── Selective Subgraph Builder & Signature Helpers ───────────────────────────

fn build_subgraph_bundle(
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

fn verify_subgraph_bundle_signature(bundle: &Value) -> Result<bool, String> {
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

pub(super) fn emit_portability_audit(state: &PortabilityState, event_type: &str, details: Value) {
    let mut payload = Map::new();
    payload.insert("event_source".into(), json!("knowledge_portability"));
    if let Some(obj) = details.as_object() {
        for (k, v) in obj {
            payload.insert(k.clone(), v.clone());
        }
    }
    emit_sanitized_governance_audit(state.config.data_dir(), event_type, None, None, payload);
}
