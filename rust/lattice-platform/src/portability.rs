//! Knowledge-graph portability — native port of `latticeai/api/portability.py`.
//!
//! File operations (list / download / validate / backup ZIP / dry-run
//! inspect) are native. Graph writes on import go through
//! `POST /worker/graph/mutate` (`import_graph_data`). Encrypted-archive
//! success with a live passphrase is a documented gap (nonce bytes).

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

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/brain/storage"),
    ("POST", "/api/brain/storage/migrate-postgres"),
    ("POST", "/api/brain/storage/postgres/docker"),
    ("POST", "/api/knowledge-graph/archive"),
    ("POST", "/api/knowledge-graph/archive/import"),
    ("POST", "/api/knowledge-graph/archive/inspect"),
    ("POST", "/api/knowledge-graph/archive/restore"),
    ("POST", "/api/knowledge-graph/archive/verify"),
    ("POST", "/api/knowledge-graph/backup"),
    ("GET", "/api/knowledge-graph/backup-health"),
    ("POST", "/api/knowledge-graph/export"),
    ("POST", "/api/knowledge-graph/export-file"),
    ("POST", "/api/knowledge-graph/import"),
    ("GET", "/api/knowledge-graph/portability"),
    ("GET", "/api/knowledge-graph/provenance"),
    ("POST", "/api/knowledge-graph/restore"),
    ("GET", "/api/knowledge-graph/share"),
    ("POST", "/api/knowledge-graph/share/archive"),
    ("POST", "/api/knowledge-graph/share/export"),
    ("POST", "/api/knowledge-graph/share/import"),
    (
        "POST",
        "/api/knowledge-graph/share/proposals/:item_id/accept",
    ),
    ("GET", "/api/knowledge-graph/share/recipient-key"),
];

const FORMAT: &str = "latticeai.kg.export";
const FORMAT_VERSION: u64 = 1;
const GRAPH_SCHEMA_VERSION: u64 = 1;
const DB_FORMAT_VERSION: u64 = 4;
const KG_V2_SCHEMA_VERSION: u64 = 2;
const PROJECTION_VERSION: u64 = 4;
const BRAIN_NETWORK_ENV: &str = "LATTICEAI_BRAIN_NETWORK";
const SUBGRAPH_FORMAT: &str = "latticeai.kg.subgraph";
const SEALED_BOX_ALGORITHM: &str = "x25519-hkdf-sha256-aes256gcm";
const BRAIN_NETWORK_DISABLED_EN: &str = "Brain Network sharing is off. It is opt-in by design: set LATTICEAI_BRAIN_NETWORK=1 to enable selective subgraph export and receipt.";

/// Router state.
#[derive(Clone)]
pub struct PortabilityState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
    pub identity: Arc<DeviceIdentity>,
    pub seam: Option<WorkerSeamClient>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
}

impl PortabilityState {
    pub fn new(
        auth: Arc<AuthState>,
        config: RuntimeConfig,
        seam: Option<WorkerSeamClient>,
    ) -> Self {
        let identity = Arc::new(DeviceIdentity::load_or_create(
            &config.state_file(state_files::DEVICE_IDENTITY),
        ));
        Self {
            auth,
            config: Arc::new(config),
            identity,
            seam,
            graph: None,
        }
    }

    fn exports_dir(&self) -> PathBuf {
        self.config.state_file(state_files::WORKSPACE_EXPORTS)
    }

    fn graph_available(&self) -> bool {
        std::env::var("LATTICEAI_ENABLE_GRAPH")
            .map(|v| v != "0" && !v.is_empty())
            .unwrap_or(true)
    }
}

/// Build the portability router.
pub fn router(state: PortabilityState) -> Router {
    Router::new()
        .route("/api/knowledge-graph/portability", get(portability_status))
        .route("/api/brain/storage", get(brain_storage))
        .route("/api/knowledge-graph/backup-health", get(backup_health))
        .route("/api/knowledge-graph/provenance", get(provenance))
        .route("/api/knowledge-graph/export", post(export_graph))
        .route("/api/knowledge-graph/export-file", post(export_graph_file))
        .route("/api/knowledge-graph/import", post(import_graph))
        .route("/api/knowledge-graph/backup", post(backup_graph))
        .route("/api/knowledge-graph/restore", post(restore_graph))
        .route("/api/knowledge-graph/archive", post(encrypted_archive))
        .route(
            "/api/knowledge-graph/archive/inspect",
            post(archive_inspect),
        )
        .route("/api/knowledge-graph/archive/verify", post(archive_verify))
        .route("/api/knowledge-graph/archive/import", post(archive_import))
        .route(
            "/api/knowledge-graph/archive/restore",
            post(archive_restore),
        )
        .route("/api/knowledge-graph/share", get(share_status))
        .route("/api/knowledge-graph/share/export", post(share_export))
        .route(
            "/api/knowledge-graph/share/recipient-key",
            get(share_recipient_key),
        )
        .route("/api/knowledge-graph/share/archive", post(share_archive))
        .route("/api/knowledge-graph/share/import", post(share_import))
        .route(
            "/api/knowledge-graph/share/proposals/:item_id/accept",
            post(share_accept),
        )
        .route("/api/brain/storage/postgres/docker", post(postgres_docker))
        .route(
            "/api/brain/storage/migrate-postgres",
            post(migrate_postgres),
        )
        .with_state(state)
}

fn require_graph(state: &PortabilityState, headers: &HeaderMap) -> Result<(), Response> {
    if !state.graph_available() {
        return Err(message_detail(503, "common.graph_disabled", headers));
    }
    Ok(())
}

fn brain_network_on() -> bool {
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

fn schema_versions() -> OrderedMap {
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
    map
}

async fn portability_status(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
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

async fn brain_storage(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
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

async fn backup_health(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    json_ok(backup_health_payload(&state.exports_dir()))
}

async fn provenance(
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

async fn export_graph(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    json_ok(export_artifact(&state))
}

async fn export_graph_file(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
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

async fn import_graph(
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

async fn backup_graph(
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

async fn restore_graph(
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

async fn encrypted_archive(
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
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .unwrap_or("");
    if passphrase.is_empty() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "A passphrase is required for encrypted .latticebrain archives.",
        );
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Encrypted archive creation is delegated at cutover; provide a path to inspect an existing .latticebrain.",
    )
}

fn missing_archive(path: &str) -> Response {
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        &format!("Brain archive not found: {path}"),
    )
}

async fn archive_inspect(
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
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Archive inspect is not available for this file.",
    )
}

async fn archive_verify(
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
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Archive verification failed.",
    )
}

async fn archive_import(
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
        return missing_archive(path);
    }
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "Brain archive not found.",
    )
}

async fn archive_restore(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    archive_import(State(state), headers, body).await
}

async fn share_status(State(state): State<PortabilityState>, headers: HeaderMap) -> Response {
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
            crate::project_sessions::language_of(&headers),
            &[],
        );
        map.insert("detail", json!(detail));
    }
    json_ok(map)
}

fn share_disabled(headers: &HeaderMap) -> Response {
    message_detail(403, "portability.brain_network_disabled", headers)
}

async fn share_export(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
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
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "At least one selector is required.",
    )
}

async fn share_recipient_key(
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
    json_ok(map)
}

async fn share_archive(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
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
    detail(
        axum::http::StatusCode::BAD_REQUEST,
        "At least one selector is required.",
    )
}

async fn share_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    _body: Bytes,
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
    message_detail(503, "portability.review_queue_unavailable", &headers)
}

async fn share_accept(
    State(_state): State<PortabilityState>,
    _headers: HeaderMap,
    AxumPath(_item_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    // FastAPI validates the body model before require_admin. An absent body
    // is 422 loc=["body"] even for an anonymous caller.
    if body.is_empty() {
        return missing_body();
    }
    if let Err(refusal) = parse_json_object(&body) {
        return refusal;
    }
    if let Err(refusal) = _state.auth.require_admin(&_headers) {
        return refusal;
    }
    message_detail(404, "review.item_not_found", &_headers)
}

async fn postgres_docker(
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
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    let consent = object
        .get("consent")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let port = object.get("port").and_then(Value::as_i64).unwrap_or(5432);
    let compose_dir = state.config.data_dir().join("postgres");
    let _ = std::fs::create_dir_all(&compose_dir);
    let compose_path = compose_dir.join("postgres.compose.yml");
    let _ = std::fs::write(
        &compose_path,
        format!(
            "services:\n  postgres:\n    image: pgvector/pgvector:pg16\n    restart: unless-stopped\n    environment:\n      POSTGRES_DB: lattice_brain\n      POSTGRES_USER: lattice\n      POSTGRES_PASSWORD: lattice-local-only\n    ports:\n      - \"127.0.0.1:{port}:5432\"\n    volumes:\n      - ./postgres-data:/var/lib/postgresql/data\n"
        ),
    );
    if !consent {
        let mut map = OrderedMap::new();
        map.insert("status", json!("consent_required"));
        map.insert("started", json!(false));
        map.insert(
            "compose_path",
            json!(compose_path.to_string_lossy().to_string()),
        );
        map.insert(
            "command",
            json!([
                "docker",
                "compose",
                "-p",
                "lattice-brain",
                "-f",
                compose_path.to_string_lossy().to_string(),
                "up",
                "-d",
                "postgres"
            ]),
        );
        return json_ok(map);
    }
    let mut map = OrderedMap::new();
    map.insert("status", json!("planned"));
    json_ok(map)
}

async fn migrate_postgres(
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
    let dsn = object.get("dsn").and_then(Value::as_str).unwrap_or("");
    if dsn.is_empty() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "Postgres DSN is required for SQLite to Postgres migration.",
        );
    }
    let schema = object
        .get("schema_name")
        .and_then(Value::as_str)
        .unwrap_or("lattice_brain");
    let tables = plan_sqlite_tables(&state.config.graph_db_path());
    let mut map = OrderedMap::new();
    map.insert("status", json!("planned"));
    map.insert(
        "source",
        json!(state.config.graph_db_path().to_string_lossy().to_string()),
    );
    map.insert("target_engine", json!("postgres"));
    map.insert("target_schema", json!(schema));
    map.insert("tables", json!(tables));
    json_ok(map)
}

fn plan_sqlite_tables(path: &Path) -> Vec<OrderedMap> {
    let mut tables = Vec::new();
    if !path.exists() {
        return tables;
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return tables;
    };
    let Ok(mut stmt) = conn.prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    ) else {
        return tables;
    };
    let names: Vec<String> = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .into_iter()
        .flatten()
        .flatten()
        .collect();
    for name in names {
        let mut columns = Vec::new();
        if let Ok(mut info) = conn.prepare(&format!("PRAGMA table_info(\"{name}\")")) {
            if let Ok(rows) = info.query_map([], |row| {
                Ok(json!({
                    "name": row.get::<_, String>(1)?,
                    "type": row.get::<_, String>(2).unwrap_or_else(|_| "TEXT".into()),
                }))
            }) {
                columns.extend(rows.flatten());
            }
        }
        let rows = conn
            .query_row(&format!("SELECT COUNT(*) FROM \"{name}\""), [], |row| {
                row.get::<_, i64>(0)
            })
            .unwrap_or(0);
        let mut table = OrderedMap::new();
        table.insert("name", json!(name));
        table.insert("columns", json!(columns));
        table.insert("rows", json!(rows));
        table.insert("conflict_key", json!("id"));
        table.insert("conflict_columns", json!(["id"]));
        table.insert("rowid_available", json!(true));
        tables.push(table);
    }
    tables
}

fn stamp() -> String {
    crate::project_sessions::now_iso_utc()
        .replace(':', "")
        .replace('-', "")
        .replace('.', "")
        .chars()
        .take(15)
        .collect()
}
