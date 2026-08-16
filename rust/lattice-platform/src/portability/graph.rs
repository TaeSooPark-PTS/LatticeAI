use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::db::tables::state_files;
use rusqlite::Connection;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::project_sessions::{detail, json_ok, parse_json_object};

use super::postgres::stamp;
use super::status::{require_graph, schema_versions};
use super::*;

const SNAPSHOT_KIND: &str = "vacuum-into";
const BLOBS_PREFIX: &str = "knowledge_graph_blobs/";

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
    if !path.exists() {
        return (Vec::new(), Vec::new(), Vec::new());
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return (Vec::new(), Vec::new(), Vec::new());
    };
    (
        query_values(
            &conn,
            "SELECT id, type, title, summary, metadata_json, raw_json, created_at, updated_at FROM nodes",
            |row| {
                Ok(json!({
                    "id": row.get::<_, String>(0)?,
                    "type": row.get::<_, String>(1)?,
                    "title": row.get::<_, String>(2).unwrap_or_default(),
                    "summary": row.get::<_, String>(3).unwrap_or_default(),
                    "metadata_json": row.get::<_, String>(4).unwrap_or_default(),
                    "raw_json": row.get::<_, String>(5).unwrap_or_default(),
                    "created_at": row.get::<_, String>(6).unwrap_or_default(),
                    "updated_at": row.get::<_, String>(7).unwrap_or_default(),
                }))
            },
        ),
        query_values(
            &conn,
            "SELECT id, from_node, to_node, type, weight, metadata_json, created_at FROM edges",
            |row| {
                Ok(json!({
                    "id": row.get::<_, String>(0)?,
                    "from_node": row.get::<_, String>(1).unwrap_or_default(),
                    "to_node": row.get::<_, String>(2).unwrap_or_default(),
                    "type": row.get::<_, String>(3).unwrap_or_default(),
                    "weight": row.get::<_, f64>(4).unwrap_or(1.0),
                    "metadata_json": row.get::<_, String>(5).unwrap_or_default(),
                    "created_at": row.get::<_, String>(6).unwrap_or_default(),
                }))
            },
        ),
        query_values(
            &conn,
            "SELECT id, source_node, text, metadata_json, created_at FROM chunks",
            |row| {
                Ok(json!({
                    "id": row.get::<_, String>(0)?,
                    "source_node": row.get::<_, String>(1).unwrap_or_default(),
                    "text": row.get::<_, String>(2).unwrap_or_default(),
                    "metadata_json": row.get::<_, String>(3).unwrap_or_default(),
                    "created_at": row.get::<_, String>(4).unwrap_or_default(),
                }))
            },
        ),
    )
}

fn query_values(
    conn: &Connection,
    sql: &str,
    map: impl Fn(&rusqlite::Row<'_>) -> rusqlite::Result<Value>,
) -> Vec<Value> {
    let Ok(mut stmt) = conn.prepare(sql) else {
        return Vec::new();
    };
    let Ok(rows) = stmt.query_map([], map) else {
        return Vec::new();
    };
    rows.flatten().collect()
}

fn table_count(conn: &Connection, table: &str) -> u64 {
    conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
        row.get::<_, i64>(0)
    })
    .map(|n| n as u64)
    .unwrap_or(0)
}

fn snapshot_counts(path: &Path) -> (u64, u64, u64) {
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return (0, 0, 0);
    };
    (
        table_count(&conn, "nodes"),
        table_count(&conn, "edges"),
        table_count(&conn, "chunks"),
    )
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
    } else {
        // `require_graph` above already refused a graph-less install, so this
        // arm is the mis-wired one: a store without the native writer. The
        // retired `/worker/graph/mutate` seam is not a second chance.
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
    let blobs = blobs_dir(state);
    let snapshot = dir.join(format!(".kg-snapshot-{}.sqlite", stamp()));
    let snapshot_written = snapshot_live_db(&db, state.graph.as_ref(), &snapshot)?;
    let file = File::create(&dest).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    if snapshot_written {
        zip.start_file("knowledge_graph.sqlite", options)
            .map_err(|e| e.to_string())?;
        let bytes = std::fs::read(&snapshot).map_err(|e| e.to_string())?;
        zip.write_all(&bytes).map_err(|e| e.to_string())?;
    }
    let blob_count = if blobs.is_dir() {
        add_dir_to_zip(&mut zip, options, &blobs, "knowledge_graph_blobs")?
    } else {
        0
    };
    let has_blobs = blob_count > 0;
    let (nodes, edges, chunks) = if snapshot_written {
        snapshot_counts(&snapshot)
    } else {
        (0, 0, 0)
    };
    let mut manifest = schema_versions();
    manifest.insert("format", json!("latticeai.kg.backup"));
    manifest.insert("format_version", json!(FORMAT_VERSION));
    manifest.insert("created_at", json!(crate::project_sessions::now_iso_utc()));
    manifest.insert(
        "db_sha256",
        json!(if snapshot_written {
            sha256_file(&snapshot)
        } else {
            String::new()
        }),
    );
    manifest.insert("has_blobs", json!(has_blobs));
    manifest.insert("blob_count", json!(blob_count));
    manifest.insert("snapshot", json!(SNAPSHOT_KIND));
    manifest.insert("nodes", json!(nodes));
    manifest.insert("edges", json!(edges));
    manifest.insert("chunks", json!(chunks));
    zip.start_file("manifest.json", options)
        .map_err(|e| e.to_string())?;
    let manifest_text = serde_json::to_string_pretty(&manifest).unwrap_or_else(|_| "{}".into());
    zip.write_all(manifest_text.as_bytes())
        .map_err(|e| e.to_string())?;
    zip.finish().map_err(|e| e.to_string())?;
    let _ = std::fs::remove_file(&snapshot);
    let bytes = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    let mut map = OrderedMap::new();
    map.insert("path", json!(dest.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert("manifest", json!(manifest));
    Ok(map)
}

fn snapshot_live_db(
    db: &Path,
    graph: Option<&lattice_core::graph_write::GraphWriter>,
    dest: &Path,
) -> Result<bool, String> {
    if !db.exists() {
        return Ok(false);
    }
    let run = |conn: &Connection| {
        lattice_core::graph_write::schema::backup_database(conn, dest).map_err(|e| e.to_string())
    };
    if let Some(graph) = graph {
        graph
            .store()
            .with_write_conn(|conn| run(conn).map_err(lattice_core::CoreError::Io))
            .map_err(|e| e.to_string())?;
    } else {
        let conn = lattice_core::db::open_read_write(db).map_err(|e| e.to_string())?;
        run(&conn)?;
    }
    Ok(dest.exists())
}

fn blobs_dir(state: &PortabilityState) -> PathBuf {
    state
        .config
        .data_dir()
        .join(state_files::KNOWLEDGE_GRAPH_BLOBS)
}

fn add_dir_to_zip(
    zip: &mut zip::ZipWriter<File>,
    options: zip::write::SimpleFileOptions,
    root: &Path,
    prefix: &str,
) -> Result<usize, String> {
    let mut count = 0usize;
    add_dir_to_zip_inner(zip, options, root, root, prefix, &mut count)?;
    Ok(count)
}

fn add_dir_to_zip_inner(
    zip: &mut zip::ZipWriter<File>,
    options: zip::write::SimpleFileOptions,
    root: &Path,
    current: &Path,
    prefix: &str,
    count: &mut usize,
) -> Result<(), String> {
    let mut entries: Vec<_> = std::fs::read_dir(current)
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .collect();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            add_dir_to_zip_inner(zip, options, root, &path, prefix, count)?;
            continue;
        }
        let rel = path.strip_prefix(root).unwrap_or(&path);
        let name = format!("{prefix}/{}", rel.to_string_lossy().replace('\\', "/"));
        zip.start_file(&name, options).map_err(|e| e.to_string())?;
        let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
        zip.write_all(&bytes).map_err(|e| e.to_string())?;
        *count += 1;
    }
    Ok(())
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

pub(super) fn peek_backup_manifest(path: &Path) -> Option<Value> {
    let file = File::open(path).ok()?;
    let mut archive = zip::ZipArchive::new(file).ok()?;
    let mut entry = archive.by_name("manifest.json").ok()?;
    let mut text = String::new();
    entry.read_to_string(&mut text).ok()?;
    serde_json::from_str(&text).ok()
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
    let confirm = object
        .get("confirm")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let dry_run = object
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !confirm && !dry_run {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "Explicit confirmation is required before restoring a Knowledge Graph backup.",
        );
    }
    match restore_backup_zip(&state, Path::new(path), dry_run) {
        Ok(map) => json_ok(map),
        Err(message) => detail(axum::http::StatusCode::BAD_REQUEST, &message),
    }
}

fn restore_backup_zip(
    state: &PortabilityState,
    archive_path: &Path,
    dry_run: bool,
) -> Result<OrderedMap, String> {
    let file = File::open(archive_path).map_err(|e| e.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
    let manifest = read_archive_manifest(&mut archive);
    let has_blobs = manifest
        .get("has_blobs")
        .and_then(Value::as_bool)
        .unwrap_or_else(|| archive_has_blobs(&archive));
    if dry_run {
        let mut map = OrderedMap::new();
        map.insert("dry_run", json!(true));
        map.insert("path", json!(archive_path.to_string_lossy().to_string()));
        map.insert("has_blobs", json!(has_blobs));
        map.insert("manifest", json!(manifest));
        return Ok(map);
    }
    let exports = state.exports_dir();
    let _ = std::fs::create_dir_all(&exports);
    let snapshot = exports.join(format!(".kg-restore-{}.sqlite", stamp()));
    let extracted = extract_named_file(&mut archive, "knowledge_graph.sqlite", &snapshot)?;
    let blobs_restored = restore_blobs_from_archive(&mut archive, &blobs_dir(state))?;
    if extracted {
        replace_live_db(&state.config.graph_db_path(), &snapshot)?;
    }
    let _ = std::fs::remove_file(&snapshot);
    let live = state.config.graph_db_path();
    let (nodes, edges, chunks) = if live.exists() {
        snapshot_counts(&live)
    } else {
        (0, 0, 0)
    };
    let mut map = OrderedMap::new();
    map.insert("restored", json!(true));
    map.insert("path", json!(archive_path.to_string_lossy().to_string()));
    map.insert("has_blobs", json!(has_blobs));
    map.insert("blobs", json!(blobs_restored));
    map.insert("nodes", json!(nodes));
    map.insert("edges", json!(edges));
    map.insert("chunks", json!(chunks));
    map.insert("manifest", json!(manifest));
    Ok(map)
}

fn read_archive_manifest(archive: &mut zip::ZipArchive<File>) -> Value {
    let Ok(mut entry) = archive.by_name("manifest.json") else {
        return json!({});
    };
    let mut text = String::new();
    if entry.read_to_string(&mut text).is_err() {
        return json!({});
    };
    serde_json::from_str(&text).unwrap_or(json!({}))
}

fn archive_has_blobs(archive: &zip::ZipArchive<File>) -> bool {
    (0..archive.len()).any(|i| {
        archive
            .name_for_index(i)
            .is_some_and(|name| name.starts_with(BLOBS_PREFIX) && !name.ends_with('/'))
    })
}

fn extract_named_file(
    archive: &mut zip::ZipArchive<File>,
    name: &str,
    dest: &Path,
) -> Result<bool, String> {
    let Ok(mut entry) = archive.by_name(name) else {
        return Ok(false);
    };
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let mut out = File::create(dest).map_err(|e| e.to_string())?;
    std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
    Ok(true)
}

fn restore_blobs_from_archive(
    archive: &mut zip::ZipArchive<File>,
    dest: &Path,
) -> Result<usize, String> {
    let parent = dest.parent().unwrap_or_else(|| Path::new("."));
    let tmp = parent.join("knowledge_graph_blobs.restore-tmp");
    let _ = std::fs::remove_dir_all(&tmp);
    let mut count = 0usize;
    let names: Vec<String> = (0..archive.len())
        .filter_map(|i| archive.name_for_index(i).map(str::to_string))
        .collect();
    for name in names {
        let Some(rel) = name.strip_prefix(BLOBS_PREFIX) else {
            continue;
        };
        if rel.is_empty() || rel.ends_with('/') {
            continue;
        }
        if rel.split('/').any(|part| part == ".." || part.is_empty()) {
            return Err(format!("refusing to restore unsafe blob path: {name}"));
        }
        let mut entry = archive.by_name(&name).map_err(|e| e.to_string())?;
        let out_path = tmp.join(rel);
        if let Some(dir) = out_path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let mut out = File::create(&out_path).map_err(|e| e.to_string())?;
        std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
        count += 1;
    }
    if count == 0 {
        let _ = std::fs::remove_dir_all(&tmp);
        return Ok(0);
    }
    let bak = parent.join("knowledge_graph_blobs.restore-bak");
    let _ = std::fs::remove_dir_all(&bak);
    if dest.exists() {
        std::fs::rename(dest, &bak).map_err(|e| e.to_string())?;
    }
    if let Err(error) = std::fs::rename(&tmp, dest) {
        if bak.exists() {
            let _ = std::fs::rename(&bak, dest);
        }
        let _ = std::fs::remove_dir_all(&tmp);
        return Err(error.to_string());
    }
    let _ = std::fs::remove_dir_all(&bak);
    Ok(count)
}

/// Replace the live database file with a consistent snapshot, atomically.
///
/// Same rename dance as `lattice_auth::atomic`: the live name only ever
/// appears as a complete file. Sidecar WAL/SHM files from the previous
/// generation are moved aside so they cannot be replayed onto the snapshot.
fn replace_live_db(live: &Path, snapshot: &Path) -> Result<(), String> {
    if !snapshot.exists() {
        return Err("restore snapshot is missing".into());
    }
    let parent = live.parent().unwrap_or_else(|| Path::new("."));
    let name = live
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "knowledge_graph.sqlite".into());
    let bak = parent.join(format!("{name}.restore-bak"));
    let _ = std::fs::remove_file(&bak);
    if live.exists() {
        std::fs::rename(live, &bak).map_err(|e| e.to_string())?;
    }
    for suffix in ["-wal", "-shm"] {
        let side = PathBuf::from(format!("{}{suffix}", live.display()));
        if side.exists() {
            let _ = std::fs::rename(
                &side,
                PathBuf::from(format!("{}.restore-bak", side.display())),
            );
        }
    }
    if let Err(error) = std::fs::rename(snapshot, live) {
        let copied = std::fs::copy(snapshot, live);
        if copied.is_err() {
            if bak.exists() {
                let _ = std::fs::rename(&bak, live);
            }
            return Err(error.to_string());
        }
    }
    let _ = std::fs::remove_file(&bak);
    for suffix in ["-wal", "-shm"] {
        let _ = std::fs::remove_file(format!("{}{suffix}.restore-bak", live.display()));
    }
    Ok(())
}
