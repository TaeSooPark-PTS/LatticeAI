use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};

use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::http_reply::{detail, json_ok, parse_json_object};

use super::archive_tables::{
    apply_decrypted_payload, check_sqlite_integrity, export_counts_json, export_full_tables,
};
use super::crypto::{
    ct_eq, read_archive_envelope, seal_and_write_archive, sha256_hex, validate_zip_entries,
    verify_and_decrypt_archive, DecryptionKey, EncryptionMode, ARCHIVE_CIPHER, ARCHIVE_FORMAT,
    ARCHIVE_FORMAT_VERSION,
};
use super::graph::{
    add_dir_to_zip, blobs_dir, restore_backup_zip, snapshot_live_db, write_backup_zip,
};
use super::postgres::stamp;
use super::share::emit_portability_audit;
use super::status::{require_graph, schema_versions};
use super::*;

pub(super) const GRAPH_EXPORT_ENTRY: &str = "graph_export.json";
pub(super) const SQLITE_ENTRY: &str = "knowledge_graph.sqlite";

// ── Encrypted .latticebrain Archive Handlers ────────────────────────────────

pub(crate) async fn encrypted_archive(
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
            StatusCode::BAD_REQUEST,
            "A passphrase is required for encrypted .latticebrain archives.",
        );
    }
    let dest = object
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            state
                .exports_dir()
                .join(format!("kg-archive-{}.latticebrain", stamp()))
        });

    match build_and_write_encrypted_archive(&state, &dest, passphrase) {
        Ok(map) => {
            emit_portability_audit(
                &state,
                "archive_created",
                json!({
                    "path": dest.to_string_lossy(),
                    "counts": map.get("counts").cloned().unwrap_or(Value::Null),
                }),
            );
            json_ok(map)
        }
        Err(err) => detail(StatusCode::BAD_REQUEST, &err),
    }
}

pub(crate) async fn archive_inspect(
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
    let archive_path = Path::new(path);
    if !archive_path.exists() {
        return missing_archive(path);
    }
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    match inspect_archive_file(&state, archive_path, passphrase) {
        Ok(map) => json_ok(map),
        Err(err) => detail(StatusCode::BAD_REQUEST, &err),
    }
}

pub(crate) async fn archive_verify(
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
    let archive_path = Path::new(path);
    if !archive_path.exists() {
        return missing_archive(path);
    }
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    match verify_archive_file(&state, archive_path, passphrase) {
        Ok(map) => {
            emit_portability_audit(
                &state,
                "archive_verified",
                json!({
                    "path": path,
                    "valid": true,
                }),
            );
            json_ok(map)
        }
        Err(err) => {
            emit_portability_audit(
                &state,
                "archive_verify_failed",
                json!({
                    "path": path,
                    "error": err,
                }),
            );
            detail(StatusCode::BAD_REQUEST, &err)
        }
    }
}

pub(crate) async fn archive_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    archive_restore_or_import(&state, &headers, &body, false)
}

pub(crate) async fn archive_restore(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    archive_restore_or_import(&state, &headers, &body, true)
}

fn archive_restore_or_import(
    state: &PortabilityState,
    headers: &HeaderMap,
    body: &Bytes,
    is_restore: bool,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(state, headers) {
        return refusal;
    }
    let object = match parse_json_object(body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let path = object.get("path").and_then(Value::as_str).unwrap_or("");
    let archive_path = Path::new(path);
    if !archive_path.exists() {
        return missing_archive(path);
    }
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let dry_run = object
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mode = object
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("replace");
    if mode != "replace" && mode != "merge" {
        return detail(
            StatusCode::BAD_REQUEST,
            &format!("Unsupported archive import mode: {mode}"),
        );
    }
    let effective_mode = if is_restore { "replace" } else { mode };

    match execute_archive_restore(
        state,
        archive_path,
        passphrase,
        dry_run,
        effective_mode,
        is_restore,
    ) {
        Ok(map) => {
            if !dry_run {
                emit_portability_audit(
                    state,
                    if is_restore {
                        "archive_restored"
                    } else {
                        "archive_imported"
                    },
                    json!({
                        "path": path,
                        "mode": effective_mode,
                        "pre_restore_backup": map.get("pre_restore_backup").cloned().unwrap_or(Value::Null),
                    }),
                );
            }
            json_ok(map)
        }
        Err(err) => detail(StatusCode::BAD_REQUEST, &err),
    }
}

pub(super) fn missing_archive(path: &str) -> Response {
    detail(
        StatusCode::BAD_REQUEST,
        &format!("Brain archive not found: {path}"),
    )
}

// ── Archive Creation, Inspection, Verification & Restore Implementation ──────

fn build_and_write_encrypted_archive(
    state: &PortabilityState,
    dest: &Path,
    passphrase: &str,
) -> Result<OrderedMap, String> {
    let exports = state.exports_dir();
    std::fs::create_dir_all(&exports).map_err(|e| e.to_string())?;
    let snapshot_path = exports.join(format!(".kg-archive-snap-{}.sqlite", stamp()));
    let db_path = state.config.graph_db_path();
    let snapshot_written = snapshot_live_db(&db_path, state.graph.as_ref(), &snapshot_path)?;

    let export_source = if snapshot_written {
        snapshot_path.as_path()
    } else {
        db_path.as_path()
    };
    let export_payload = export_full_tables(export_source);
    let counts_val = export_counts_json(&export_payload, 0);
    let export_bytes = serde_json::to_vec_pretty(&export_payload)
        .map_err(|e| format!("Failed to encode graph export JSON: {e}"))?;
    let export_sha = sha256_hex(&export_bytes);

    let mut inner_buf = Cursor::new(Vec::new());
    let mut db_sha = String::new();
    let blob_count;
    {
        let mut inner_zip = zip::ZipWriter::new(&mut inner_buf);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        if snapshot_written {
            let db_bytes = std::fs::read(&snapshot_path).map_err(|e| e.to_string())?;
            db_sha = sha256_hex(&db_bytes);
            inner_zip
                .start_file(SQLITE_ENTRY, options)
                .map_err(|e| e.to_string())?;
            inner_zip.write_all(&db_bytes).map_err(|e| e.to_string())?;
        }
        inner_zip
            .start_file(GRAPH_EXPORT_ENTRY, options)
            .map_err(|e| e.to_string())?;
        inner_zip
            .write_all(&export_bytes)
            .map_err(|e| e.to_string())?;

        let blobs = blobs_dir(state);
        blob_count = if blobs.is_dir() {
            add_dir_to_zip(&mut inner_zip, options, &blobs, "knowledge_graph_blobs")?
        } else {
            0
        };
        inner_zip.finish().map_err(|e| e.to_string())?;
    }
    let _ = std::fs::remove_file(&snapshot_path);
    let plaintext = inner_buf.into_inner();

    let counts = export_counts_json(&export_payload, blob_count);
    let _ = counts_val;
    let mut manifest = schema_versions();
    manifest.insert("has_sqlite_snapshot", json!(snapshot_written));
    manifest.insert("has_blobs", json!(blob_count > 0));
    manifest.insert("counts", counts.clone());
    manifest.insert(
        "checksums",
        json!({
            "db_sha256": db_sha,
            "export_json_sha256": export_sha,
        }),
    );

    let (header, manifest_val) = seal_and_write_archive(
        dest,
        "full",
        &plaintext,
        &mut manifest,
        EncryptionMode::Passphrase(passphrase),
        &state.identity,
    )?;

    let bytes = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
    let mut map = OrderedMap::new();
    map.insert("status", json!("created"));
    map.insert("path", json!(dest.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert("format", json!(ARCHIVE_FORMAT));
    map.insert("format_version", json!(ARCHIVE_FORMAT_VERSION));
    map.insert("encrypted", json!(true));
    map.insert("kdf", json!("scrypt"));
    map.insert("cipher", json!(ARCHIVE_CIPHER));
    map.insert("nodes", json!(counts["nodes"]));
    map.insert("edges", json!(counts["edges"]));
    map.insert("chunks", json!(counts["chunks"]));
    map.insert("vectors", json!(counts["vectors"]));
    map.insert("vector_embeddings", json!(counts["vector_embeddings"]));
    map.insert("provenance", json!(counts["provenance"]));
    map.insert("knowledge_sources", json!(counts["knowledge_sources"]));
    map.insert("blobs", json!(blob_count));
    map.insert("counts", counts);
    map.insert("header", header);
    map.insert("manifest", manifest_val);
    Ok(map)
}

fn inspect_archive_file(
    state: &PortabilityState,
    archive_path: &Path,
    passphrase: Option<&str>,
) -> Result<OrderedMap, String> {
    let envelope = read_archive_envelope(archive_path)?;
    let (verified, inner_entries) = if let Some(pw) = passphrase {
        let verified_map = verify_archive_file(state, archive_path, Some(pw))?;
        let entries = verified_map
            .get("inner_entries")
            .cloned()
            .unwrap_or_else(|| json!([]));
        (true, entries)
    } else {
        (false, json!([]))
    };

    let counts = envelope
        .manifest
        .get("counts")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let bytes = std::fs::metadata(archive_path)
        .map(|m| m.len())
        .unwrap_or(0);

    let mut map = OrderedMap::new();
    map.insert("status", json!("inspected"));
    map.insert("path", json!(archive_path.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert(
        "format",
        envelope
            .header
            .get("format")
            .cloned()
            .unwrap_or_else(|| json!(ARCHIVE_FORMAT)),
    );
    map.insert(
        "format_version",
        envelope
            .header
            .get("format_version")
            .cloned()
            .unwrap_or_else(|| json!(ARCHIVE_FORMAT_VERSION)),
    );
    map.insert(
        "version",
        envelope
            .header
            .get("format_version")
            .cloned()
            .unwrap_or_else(|| json!(ARCHIVE_FORMAT_VERSION)),
    );
    map.insert(
        "kind",
        envelope
            .header
            .get("kind")
            .cloned()
            .unwrap_or_else(|| json!("full")),
    );
    map.insert("encrypted", json!(true));
    map.insert("verified", json!(verified));
    map.insert(
        "encryption",
        envelope
            .header
            .get("encryption")
            .cloned()
            .unwrap_or_else(|| json!("passphrase")),
    );
    map.insert(
        "created_at",
        envelope
            .manifest
            .get("created_at")
            .cloned()
            .unwrap_or(Value::Null),
    );
    map.insert(
        "nodes",
        json!(counts.get("nodes").and_then(Value::as_u64).unwrap_or(0)),
    );
    map.insert(
        "edges",
        json!(counts.get("edges").and_then(Value::as_u64).unwrap_or(0)),
    );
    map.insert(
        "chunks",
        json!(counts.get("chunks").and_then(Value::as_u64).unwrap_or(0)),
    );
    map.insert(
        "vectors",
        json!(counts.get("vectors").and_then(Value::as_u64).unwrap_or(0)),
    );
    map.insert(
        "provenance",
        json!(counts
            .get("provenance")
            .and_then(Value::as_u64)
            .unwrap_or(0)),
    );
    map.insert("counts", counts);
    map.insert("outer_entries", json!(envelope.outer_entries));
    map.insert("inner_entries", inner_entries);
    map.insert("header", envelope.header);
    map.insert("manifest", envelope.manifest);
    Ok(map)
}

fn verify_archive_file(
    state: &PortabilityState,
    archive_path: &Path,
    passphrase: Option<&str>,
) -> Result<OrderedMap, String> {
    verify_archive_with_decrypted(state, archive_path, passphrase).map(|(map, _)| map)
}

fn verify_archive_with_decrypted(
    state: &PortabilityState,
    archive_path: &Path,
    passphrase: Option<&str>,
) -> Result<(OrderedMap, super::crypto::DecryptedArchive), String> {
    let decrypted = verify_and_decrypt_archive(
        archive_path,
        DecryptionKey::PassphraseOrIdentity {
            passphrase,
            identity: &state.identity,
        },
    )?;

    let cursor = Cursor::new(&decrypted.plaintext);
    let mut inner_zip =
        zip::ZipArchive::new(cursor).map_err(|e| format!("Corrupt inner ZIP payload: {e}"))?;
    let inner_entries = validate_zip_entries(&mut inner_zip)?;

    let checksums = decrypted.manifest.get("checksums");
    let expected_db_sha = checksums
        .and_then(|c| c.get("db_sha256"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let expected_json_sha = checksums
        .and_then(|c| c.get("export_json_sha256"))
        .and_then(Value::as_str)
        .unwrap_or("");

    let mut integrity_status = "skipped".to_string();
    if inner_entries.iter().any(|e| e == SQLITE_ENTRY) {
        let mut db_bytes = Vec::new();
        {
            let mut entry = inner_zip.by_name(SQLITE_ENTRY).map_err(|e| e.to_string())?;
            entry
                .read_to_end(&mut db_bytes)
                .map_err(|e| e.to_string())?;
        }
        if !expected_db_sha.is_empty()
            && !ct_eq(expected_db_sha.as_bytes(), sha256_hex(&db_bytes).as_bytes())
        {
            return Err("Archive verification failed: SQLite snapshot SHA-256 mismatch.".into());
        }
        let exports = state.exports_dir();
        let _ = std::fs::create_dir_all(&exports);
        let tmp_db = exports.join(format!(".kg-verify-{}.sqlite", stamp()));
        std::fs::write(&tmp_db, &db_bytes).map_err(|e| e.to_string())?;
        let check = check_sqlite_integrity(&tmp_db);
        let _ = std::fs::remove_file(&tmp_db);
        integrity_status = check?;
    }
    if inner_entries.iter().any(|e| e == GRAPH_EXPORT_ENTRY) {
        let mut json_bytes = Vec::new();
        {
            let mut entry = inner_zip
                .by_name(GRAPH_EXPORT_ENTRY)
                .map_err(|e| e.to_string())?;
            entry
                .read_to_end(&mut json_bytes)
                .map_err(|e| e.to_string())?;
        }
        if !expected_json_sha.is_empty()
            && !ct_eq(
                expected_json_sha.as_bytes(),
                sha256_hex(&json_bytes).as_bytes(),
            )
        {
            return Err("Archive verification failed: graph export JSON SHA-256 mismatch.".into());
        }
    }

    let counts = decrypted
        .manifest
        .get("counts")
        .cloned()
        .unwrap_or_else(|| json!({}));

    let mut map = OrderedMap::new();
    map.insert("status", json!("verified"));
    map.insert("valid", json!(true));
    map.insert("verified", json!(true));
    map.insert("signature_verified", json!(decrypted.signature_verified));
    map.insert("integrity_check", json!(integrity_status));
    map.insert("path", json!(archive_path.to_string_lossy().to_string()));
    map.insert("format", json!(ARCHIVE_FORMAT));
    map.insert("format_version", json!(ARCHIVE_FORMAT_VERSION));
    map.insert("outer_entries", json!(decrypted.outer_entries));
    map.insert("inner_entries", json!(inner_entries));
    map.insert("counts", counts);
    map.insert("checksums", checksums.cloned().unwrap_or_else(|| json!({})));
    map.insert("header", decrypted.header.clone());
    map.insert("manifest", decrypted.manifest.clone());
    Ok((map, decrypted))
}

fn execute_archive_restore(
    state: &PortabilityState,
    archive_path: &Path,
    passphrase: Option<&str>,
    dry_run: bool,
    mode: &str,
    is_restore: bool,
) -> Result<OrderedMap, String> {
    let (verified, decrypted) = verify_archive_with_decrypted(state, archive_path, passphrase)?;

    if dry_run {
        let mut map = OrderedMap::new();
        map.insert("status", json!("dry_run"));
        map.insert("dry_run", json!(true));
        map.insert("verified", json!(true));
        map.insert("mode", json!(mode));
        map.insert("path", json!(archive_path.to_string_lossy().to_string()));
        map.insert(
            "counts",
            verified.get("counts").cloned().unwrap_or_else(|| json!({})),
        );
        map.insert("manifest", decrypted.manifest);
        return Ok(map);
    }

    // Create automatic pre-restore backup before any mutation.
    let exports = state.exports_dir();
    std::fs::create_dir_all(&exports).map_err(|e| e.to_string())?;
    let pre_restore_path = exports.join(format!("kg-pre-restore-{}.zip", stamp()));
    let pre_restore_str = pre_restore_path.to_string_lossy().to_string();
    let _ = write_backup_zip(state, Some(&pre_restore_str))?;

    let (blobs_restored, live_counts) =
        match apply_decrypted_payload(state, &decrypted.plaintext, &decrypted.manifest, mode) {
            Ok(res) => res,
            Err(err) => {
                // Attempt rollback from pre-restore backup so DB is never left half-restored.
                if pre_restore_path.exists() {
                    let _ = restore_backup_zip(state, &pre_restore_path, false);
                }
                return Err(err);
            }
        };

    let mut map = OrderedMap::new();
    map.insert(
        "status",
        json!(if is_restore { "restored" } else { "imported" }),
    );
    map.insert("restored", json!(true));
    map.insert("imported", json!(true));
    map.insert("verified", json!(true));
    map.insert("integrity_check", json!("ok"));
    map.insert("mode", json!(mode));
    map.insert("path", json!(archive_path.to_string_lossy().to_string()));
    map.insert("pre_restore_backup", json!(pre_restore_str));
    map.insert("nodes", json!(live_counts.nodes));
    map.insert("edges", json!(live_counts.edges));
    map.insert("chunks", json!(live_counts.chunks));
    map.insert("vectors", json!(live_counts.vector_embeddings));
    map.insert("vector_embeddings", json!(live_counts.vector_embeddings));
    map.insert("provenance", json!(live_counts.ingestion_provenance));
    map.insert(
        "ingestion_provenance",
        json!(live_counts.ingestion_provenance),
    );
    map.insert("knowledge_sources", json!(live_counts.knowledge_sources));
    map.insert("blobs", json!(blobs_restored));
    map.insert(
        "counts",
        verified.get("counts").cloned().unwrap_or_else(|| json!({})),
    );
    map.insert("manifest", decrypted.manifest);
    Ok(map)
}
