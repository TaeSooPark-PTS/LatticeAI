use std::path::{Path, PathBuf};

use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::db::RuntimeConfig;
use serde_json::{json, Value};

use crate::workspaceos::project_sessions::{json_ok, message_detail};

use super::*;

pub(crate) fn require_graph(state: &PortabilityState, headers: &HeaderMap) -> Result<(), Response> {
    if !state.graph_available() {
        return Err(message_detail(503, "common.graph_disabled", headers));
    }
    Ok(())
}

pub(crate) fn brain_network_on() -> bool {
    matches!(
        std::env::var(BRAIN_NETWORK_ENV)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn embed_dim() -> u64 {
    std::env::var("LATTICEAI_VECTOR_DIM")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(384)
}

pub(crate) fn schema_versions() -> OrderedMap {
    let mut map = OrderedMap::new();
    map.insert("graph_schema_version", json!(GRAPH_SCHEMA_VERSION));
    map.insert("db_format_version", json!(DB_FORMAT_VERSION));
    map.insert("kg_v2_schema_version", json!(KG_V2_SCHEMA_VERSION));
    map.insert("projection_version", json!(PROJECTION_VERSION));
    map.insert("embed_dim", json!(embed_dim()));
    map
}

pub(crate) fn sqlite_capabilities(db_path: &Path) -> OrderedMap {
    let mut metadata = OrderedMap::new();
    metadata.insert("db_path", json!(db_path.to_string_lossy().to_string()));
    metadata.insert("sqlite_vec_loaded", json!(false));
    metadata.insert("sqlite_vec_ann_available", json!(false));
    metadata.insert("vector_mode", json!("fallback"));
    metadata.insert("degraded", json!(true));
    metadata.insert(
        "honest_fallback",
        json!("Vector search is available through the deterministic brute-force cosine backend. sqlite-vec ANN is unavailable."),
    );
    let mut map = OrderedMap::new();
    map.insert("engine", json!("sqlite"));
    map.insert("available", json!(true));
    map.insert(
        "reason",
        json!("sqlite-vec Python package not installed: No module named 'sqlite_vec'; using real brute-force cosine fallback, not sqlite-vec ANN"),
    );
    map.insert("vector_backend", json!("bruteforce-cosine"));
    map.insert("vector_available", json!(true));
    map.insert("backup_restore", json!(true));
    map.insert("migrations", json!(true));
    map.insert("encrypted_archives", json!(true));
    map.insert("metadata", json!(metadata));
    map
}

pub(crate) fn postgres_capabilities() -> OrderedMap {
    let mut metadata = OrderedMap::new();
    metadata.insert("schema", json!("lattice_brain"));
    let mut map = OrderedMap::new();
    map.insert("engine", json!("postgres"));
    map.insert("available", json!(false));
    map.insert(
        "reason",
        json!("Postgres storage requires LATTICEAI_POSTGRES_DSN; no SQLite fallback is attempted."),
    );
    map.insert("vector_backend", json!("pgvector"));
    map.insert("vector_available", json!(false));
    map.insert("backup_restore", json!(false));
    map.insert("migrations", json!(false));
    map.insert("encrypted_archives", json!(false));
    map.insert("metadata", json!(metadata));
    map
}

fn graph_stats(config: &RuntimeConfig) -> OrderedMap {
    let path = config.graph_db_path();
    let mut map = OrderedMap::new();
    map.insert("db_path", json!(path.to_string_lossy().to_string()));
    map.insert("schema_version", json!(GRAPH_SCHEMA_VERSION));
    map.insert("v2_schema_available", json!(true));
    let (nodes, edges) = count_graph(&path);
    map.insert("nodes", json!(nodes));
    map.insert("edges", json!(edges));
    map.insert("local_sources", json!(0));
    map.insert("local_file_status", json!({}));
    let mut v2 = OrderedMap::new();
    v2.insert("schema_version", json!(KG_V2_SCHEMA_VERSION));
    v2.insert("embed_dim", json!(embed_dim()));
    let node_total: u64 = nodes.values().copied().sum();
    let edge_total: u64 = edges.values().copied().sum();
    v2.insert("nodes", json!(node_total));
    v2.insert("edges", json!(edge_total));
    v2.insert("by_node_type", json!(uppercase_keys(&nodes)));
    v2.insert("by_edge_type", json!(edges));
    map.insert("v2", json!(v2));
    map
}

fn count_graph(
    path: &Path,
) -> (
    std::collections::BTreeMap<String, u64>,
    std::collections::BTreeMap<String, u64>,
) {
    let mut nodes = std::collections::BTreeMap::new();
    let mut edges = std::collections::BTreeMap::new();
    if !path.exists() {
        return (nodes, edges);
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return (nodes, edges);
    };
    if let Ok(mut stmt) = conn.prepare("SELECT type, COUNT(*) FROM nodes GROUP BY type") {
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        }) {
            for row in rows.flatten() {
                nodes.insert(row.0, row.1 as u64);
            }
        }
    }
    if let Ok(mut stmt) = conn.prepare("SELECT type, COUNT(*) FROM edges GROUP BY type") {
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        }) {
            for row in rows.flatten() {
                edges.insert(row.0, row.1 as u64);
            }
        }
    }
    (nodes, edges)
}

fn uppercase_keys(
    map: &std::collections::BTreeMap<String, u64>,
) -> std::collections::BTreeMap<String, u64> {
    map.iter()
        .map(|(k, v)| (k.to_ascii_uppercase(), *v))
        .collect()
}

pub(crate) fn backup_health_payload(dir: &Path) -> OrderedMap {
    let _ = std::fs::create_dir_all(dir);
    let mut backups: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.is_file()
                && matches!(
                    p.extension().and_then(|e| e.to_str()),
                    Some("zip") | Some("latticebrain")
                )
        })
        .collect();
    backups.sort_by_key(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok());
    backups.reverse();
    let latest = backups.first();
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    map.insert("directory", json!(dir.to_string_lossy().to_string()));
    map.insert("count", json!(backups.len()));
    map.insert(
        "latest",
        json!(latest.map(|p| p.to_string_lossy().to_string())),
    );
    map.insert(
        "latest_bytes",
        json!(latest
            .and_then(|p| std::fs::metadata(p).ok())
            .map(|m| m.len())
            .unwrap_or(0)),
    );
    map.insert(
        "encrypted_archives",
        json!(backups
            .iter()
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("latticebrain"))
            .count()),
    );
    map.insert(
        "zip_backups",
        json!(backups
            .iter()
            .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("zip"))
            .count()),
    );
    if let Some(latest) = latest {
        if let Some(manifest) = super::graph::peek_backup_manifest(latest) {
            map.insert(
                "has_blobs",
                json!(manifest
                    .get("has_blobs")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)),
            );
            map.insert(
                "snapshot",
                json!(manifest
                    .get("snapshot")
                    .and_then(Value::as_str)
                    .unwrap_or("")),
            );
            map.insert(
                "latest_nodes",
                json!(manifest.get("nodes").and_then(Value::as_u64).unwrap_or(0)),
            );
            map.insert(
                "latest_edges",
                json!(manifest.get("edges").and_then(Value::as_u64).unwrap_or(0)),
            );
            map.insert(
                "latest_chunks",
                json!(manifest.get("chunks").and_then(Value::as_u64).unwrap_or(0)),
            );
            map.insert("latest_manifest", manifest);
        }
    }
    map
}

pub(crate) async fn portability_status(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    for (k, v) in schema_versions().iter() {
        map.insert(k, v.clone());
    }
    map.insert("stats", json!(graph_stats(&state.config)));
    let mut provenance = OrderedMap::new();
    provenance.insert("total", json!(0));
    provenance.insert("by_source_type", json!({}));
    provenance.insert("embedded", json!(0));
    provenance.insert("duplicates", json!(0));
    provenance.insert("last_ingested_at", Value::Null);
    map.insert("provenance", json!(provenance));
    map.insert(
        "storage",
        json!(sqlite_capabilities(&state.config.graph_db_path())),
    );
    json_ok(map)
}

pub(crate) async fn brain_storage(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    map.insert(
        "active",
        json!(sqlite_capabilities(&state.config.graph_db_path())),
    );
    map.insert("postgres", json!(postgres_capabilities()));
    map.insert(
        "backup_health",
        json!(backup_health_payload(&state.exports_dir())),
    );
    json_ok(map)
}

pub(crate) async fn backup_health(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    json_ok(backup_health_payload(&state.exports_dir()))
}

pub(crate) async fn provenance(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    Query(_query): Query<std::collections::HashMap<String, String>>,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let mut map = OrderedMap::new();
    map.insert("items", json!([]));
    map.insert("count", json!(0));
    json_ok(map)
}
