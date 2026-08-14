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
    clippy::useless_format,
    clippy::collapsible_str_replace,
    clippy::manual_repeat_n,
    clippy::module_inception
)]

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::network::DeviceIdentity;
use crate::project_sessions::{detail, json_ok, message_detail, missing_body, parse_json_object};

use super::postgres::stamp;
use super::status::{require_graph, schema_versions};
use super::*;

fn export_artifact(state: &PortabilityState) -> OrderedMap {
    let (nodes, edges, chunks) = export_rows(&state.config.graph_db_path());
    let mut header = schema_versions();
    header.insert("format", json!(FORMAT));
    // format first in Python: format, format_version, then schema versions.
    let mut ordered = OrderedMap::new();
    ordered.insert("format", json!(FORMAT));
    ordered.insert("format_version", json!(FORMAT_VERSION));
    for (k, v) in schema_versions().iter() {
        ordered.insert(k, v.clone());
    }
    ordered.insert("exported_at", json!(crate::project_sessions::now_iso_utc()));
    ordered.insert("workspace_id", Value::Null);
    ordered.insert("include_legacy_global", json!(false));
    let mut counts = OrderedMap::new();
    counts.insert("nodes", json!(nodes.len()));
    counts.insert("edges", json!(edges.len()));
    counts.insert("chunks", json!(chunks.len()));
    counts.insert("knowledge_sources", json!(0));
    counts.insert("provenance", json!(0));
    ordered.insert("counts", json!(counts));
    let mut artifact = OrderedMap::new();
    artifact.insert("header", json!(ordered));
    artifact.insert("nodes", json!(nodes));
    artifact.insert("edges", json!(edges));
    artifact.insert("chunks", json!(chunks));
    artifact.insert("knowledge_sources", json!([]));
    artifact.insert("provenance", json!([]));
    let mut sig = OrderedMap::new();
    sig.insert("algorithm", json!("ed25519"));
    sig.insert("public_key", json!(state.identity.public_key_b64()));
    sig.insert("fingerprint", json!(state.identity.fingerprint()));
    sig.insert("signature", json!(state.identity.sign(b"header")));
    artifact.insert("signature", json!(sig));
    artifact
}

fn export_rows(path: &Path) -> (Vec<Value>, Vec<Value>, Vec<Value>) {
    let mut nodes = Vec::new();
    let mut edges = Vec::new();
    let chunks = Vec::new();
    if !path.exists() {
        return (nodes, edges, chunks);
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return (nodes, edges, chunks);
    };
    if let Ok(mut stmt) =
        conn.prepare("SELECT id, type, title, summary, metadata_json, raw_json FROM nodes")
    {
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "type": row.get::<_, String>(1)?,
                "title": row.get::<_, String>(2).unwrap_or_default(),
                "summary": row.get::<_, String>(3).unwrap_or_default(),
                "metadata_json": row.get::<_, String>(4).unwrap_or_default(),
                "raw_json": row.get::<_, String>(5).unwrap_or_default(),
            }))
        }) {
            nodes.extend(rows.flatten());
        }
    }
    if let Ok(mut stmt) = conn.prepare("SELECT id, type, src, dst FROM edges") {
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok(json!({
                "id": row.get::<_, i64>(0).ok().map(|n| n.to_string()).unwrap_or_default(),
                "type": row.get::<_, String>(1).unwrap_or_default(),
                "src": row.get::<_, String>(2).unwrap_or_default(),
                "dst": row.get::<_, String>(3).unwrap_or_default(),
            }))
        }) {
            edges.extend(rows.flatten());
        }
    }
    (nodes, edges, chunks)
}

pub(crate) async fn export_graph(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    json_ok(export_artifact(&state))
}

pub(crate) async fn export_graph_file(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let artifact = export_artifact(&state);
    let dir = state.exports_dir();
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join(format!("kg-export-{}.json", stamp()));
    let text = serde_json::to_string_pretty(&artifact).unwrap_or_else(|_| "{}".into());
    let _ = std::fs::write(&path, &text);
    let mut map = OrderedMap::new();
    map.insert("path", json!(path.to_string_lossy().to_string()));
    map.insert(
        "header",
        artifact.get("header").cloned().unwrap_or(json!({})),
    );
    map.insert("bytes", json!(text.len()));
    json_ok(map)
}

pub(crate) async fn import_graph(
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
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let artifact = object.get("artifact").cloned().unwrap_or(json!({}));
    if !artifact.is_object() || artifact.get("nodes").is_none() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "Invalid Knowledge Graph export artifact.",
        );
    }
    let mode = object
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("merge");
    let dry_run = object
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if let Some(graph) = state.graph.clone() {
        let request = lattice_core::graph_write::types::ImportRequest {
            data: artifact.as_object().cloned().unwrap_or_default(),
            mode: mode.to_string(),
            dry_run,
        };
        match tokio::task::spawn_blocking(move || graph.import_graph_data(&request)).await {
            Ok(Ok(outcome)) => json_ok(outcome.to_json()),
            Ok(Err(err)) => detail(axum::http::StatusCode::BAD_REQUEST, &err.to_string()),
            Err(err) => detail(axum::http::StatusCode::BAD_GATEWAY, &err.to_string()),
        }
    } else if let Some(seam) = &state.seam {
        match seam
            .post_json(
                "/worker/graph/mutate",
                &json!({"op":"import_graph_data","args":{"data": artifact, "mode": mode, "dry_run": dry_run}}),
            )
            .await
        {
            Ok(value) => json_ok(value.get("result").cloned().unwrap_or(value)),
            Err(err) => detail(
                axum::http::StatusCode::from_u16(err.status().unwrap_or(502))
                    .unwrap_or(axum::http::StatusCode::BAD_GATEWAY),
                &err.to_string(),
            ),
        }
    } else {
        detail(
            axum::http::StatusCode::BAD_REQUEST,
            "Invalid Knowledge Graph export artifact.",
        )
    }
}

pub(crate) async fn backup_graph(
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
    let dest = if body.is_empty() {
        None
    } else {
        parse_json_object(&body)
            .ok()
            .and_then(|o| o.get("path").and_then(Value::as_str).map(str::to_string))
    };
    match write_backup_zip(&state, dest.as_deref()) {
        Ok(map) => json_ok(map),
        Err(message) => detail(axum::http::StatusCode::BAD_REQUEST, &message),
    }
}

fn write_backup_zip(state: &PortabilityState, dest: Option<&str>) -> Result<OrderedMap, String> {
    let dir = state.exports_dir();
    let _ = std::fs::create_dir_all(&dir);
    let dest = dest
        .map(PathBuf::from)
        .unwrap_or_else(|| dir.join(format!("kg-backup-{}.zip", stamp())));
    let db = state.config.graph_db_path();
    let file = std::fs::File::create(&dest).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    if db.exists() {
        zip.start_file("knowledge_graph.sqlite", options)
            .map_err(|e| e.to_string())?;
        let bytes = std::fs::read(&db).map_err(|e| e.to_string())?;
        zip.write_all(&bytes).map_err(|e| e.to_string())?;
    }
    let mut manifest = schema_versions();
    manifest.insert("format", json!("latticeai.kg.backup"));
    manifest.insert("format_version", json!(FORMAT_VERSION));
    manifest.insert("created_at", json!(crate::project_sessions::now_iso_utc()));
    manifest.insert("db_sha256", json!(sha256_file(&db)));
    manifest.insert("has_blobs", json!(false));
    zip.start_file("manifest.json", options)
        .map_err(|e| e.to_string())?;
    let manifest_text = serde_json::to_string_pretty(&manifest).unwrap_or_else(|_| "{}".into());
    zip.write_all(manifest_text.as_bytes())
        .map_err(|e| e.to_string())?;
    zip.finish().map_err(|e| e.to_string())?;
    let bytes = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    let mut map = OrderedMap::new();
    map.insert("path", json!(dest.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert("manifest", json!(manifest));
    Ok(map)
}

fn sha256_file(path: &Path) -> String {
    let Ok(bytes) = std::fs::read(path) else {
        return String::new();
    };
    Sha256::digest(&bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub(crate) async fn restore_graph(
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
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let path = object.get("path").and_then(Value::as_str).unwrap_or("");
    if !Path::new(path).exists() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            &format!("Backup archive not found: {path}"),
        );
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Explicit confirmation is required before restoring a Knowledge Graph backup.",
    )
}
