//! Knowledge Graph full-table JSON export, SQLite integrity check, and
//! transactional SQLite/JSON restore helpers.

use std::io::{Cursor, Read};
use std::path::Path;

use base64::engine::general_purpose::STANDARD as B64_STANDARD;
use base64::Engine;
use rusqlite::params;
use serde_json::{json, Value};

use super::archive::{GRAPH_EXPORT_ENTRY, SQLITE_ENTRY};
use super::crypto::validate_zip_entries;
use super::graph::{
    blobs_dir, extract_named_file, query_values, replace_live_db, restore_blobs_from_archive,
    table_count,
};
use super::postgres::stamp;
use super::PortabilityState;

#[derive(Debug, Clone, Copy)]
pub(super) struct LiveTableCounts {
    pub(super) nodes: u64,
    pub(super) edges: u64,
    pub(super) chunks: u64,
    pub(super) vector_embeddings: u64,
    pub(super) ingestion_provenance: u64,
    pub(super) knowledge_sources: u64,
}

pub(super) fn apply_decrypted_payload(
    state: &PortabilityState,
    plaintext: &[u8],
    manifest: &Value,
    mode: &str,
) -> Result<(usize, LiveTableCounts), String> {
    let cursor = Cursor::new(plaintext);
    let mut inner_zip =
        zip::ZipArchive::new(cursor).map_err(|e| format!("Invalid inner ZIP payload: {e}"))?;
    let entries = validate_zip_entries(&mut inner_zip)?;
    let has_sqlite = entries.iter().any(|e| e == SQLITE_ENTRY);
    let has_json = entries.iter().any(|e| e == GRAPH_EXPORT_ENTRY);

    let blobs_restored = restore_blobs_from_archive(&mut inner_zip, &blobs_dir(state))?;

    if mode == "replace" && has_sqlite {
        let exports = state.exports_dir();
        let snapshot = exports.join(format!(".kg-archive-restore-{}.sqlite", stamp()));
        let extracted = extract_named_file(&mut inner_zip, SQLITE_ENTRY, &snapshot)?;
        if extracted {
            check_sqlite_integrity(&snapshot)?;
            if let Some(graph) = &state.graph {
                graph.store().discard_idle_handles();
            }
            let replace_res = replace_live_db(&state.config.graph_db_path(), &snapshot);
            let _ = std::fs::remove_file(&snapshot);
            replace_res?;
            if let Some(graph) = &state.graph {
                graph.store().bump_generation();
            }
        }
    } else if has_json {
        let mut entry = inner_zip
            .by_name(GRAPH_EXPORT_ENTRY)
            .map_err(|e| e.to_string())?;
        let mut text = String::new();
        entry.read_to_string(&mut text).map_err(|e| e.to_string())?;
        let data: Value =
            serde_json::from_str(&text).map_err(|e| format!("Corrupt graph_export.json: {e}"))?;
        restore_tables_transactionally(state, &data, mode == "replace")?;
    } else {
        return Err(
            "Encrypted archive contains neither knowledge_graph.sqlite nor graph_export.json."
                .into(),
        );
    }

    // Verify live post-restore DB integrity and table count equivalence.
    let live_path = state.config.graph_db_path();
    let integrity = check_sqlite_integrity(&live_path)?;
    if integrity != "ok" {
        return Err(format!(
            "Post-restore SQLite integrity check failed: {integrity}"
        ));
    }
    let live_counts = query_live_counts(&live_path)?;
    verify_post_restore_counts(manifest, &live_counts, mode)?;
    Ok((blobs_restored, live_counts))
}

fn verify_post_restore_counts(
    manifest: &Value,
    live: &LiveTableCounts,
    mode: &str,
) -> Result<(), String> {
    let Some(expected) = manifest.get("counts").and_then(Value::as_object) else {
        return Ok(());
    };
    let check = |label: &str, actual: u64| -> Result<(), String> {
        let exp = expected.get(label).and_then(Value::as_u64).unwrap_or(0);
        let ok = if mode == "replace" {
            actual == exp
        } else {
            actual >= exp
        };
        if !ok {
            return Err(format!(
                "Post-restore equivalence check failed for {label}: expected {exp}, found {actual}"
            ));
        }
        Ok(())
    };
    check("nodes", live.nodes)?;
    check("edges", live.edges)?;
    check("chunks", live.chunks)?;
    if expected.contains_key("vector_embeddings") {
        check("vector_embeddings", live.vector_embeddings)?;
    }
    if expected.contains_key("ingestion_provenance") {
        check("ingestion_provenance", live.ingestion_provenance)?;
    }
    if expected.contains_key("knowledge_sources") {
        check("knowledge_sources", live.knowledge_sources)?;
    }
    Ok(())
}

pub(super) fn check_sqlite_integrity(path: &Path) -> Result<String, String> {
    if !path.exists() {
        return Err("SQLite database file missing during integrity check.".into());
    }
    let conn = lattice_core::db::open_read_only(path).map_err(|e| e.to_string())?;
    let status: String = conn
        .query_row("PRAGMA integrity_check", [], |row| row.get(0))
        .map_err(|e| format!("PRAGMA integrity_check failed: {e}"))?;
    if !status.eq_ignore_ascii_case("ok") {
        return Err(format!("SQLite integrity check failed: {status}"));
    }
    Ok(status)
}

fn query_live_counts(path: &Path) -> Result<LiveTableCounts, String> {
    let conn = lattice_core::db::open_read_only(path).map_err(|e| e.to_string())?;
    Ok(LiveTableCounts {
        nodes: table_count(&conn, "nodes"),
        edges: table_count(&conn, "edges"),
        chunks: table_count(&conn, "chunks"),
        vector_embeddings: table_count(&conn, "vector_embeddings"),
        ingestion_provenance: table_count(&conn, "ingestion_provenance"),
        knowledge_sources: table_count(&conn, "knowledge_sources"),
    })
}

pub(super) fn export_full_tables(path: &Path) -> Value {
    if !path.exists() {
        return json!({
            "nodes": [],
            "edges": [],
            "chunks": [],
            "knowledge_sources": [],
            "vector_embeddings": [],
            "ingestion_provenance": [],
            "nodes_v2": [],
            "edges_v2": [],
        });
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return json!({});
    };
    let nodes = query_values(
        &conn,
        "SELECT id, type, title, summary, metadata_json, raw_json, created_at, updated_at FROM nodes",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "type": row.get::<_, String>(1)?,
                "title": row.get::<_, String>(2).unwrap_or_default(),
                "summary": row.get::<_, Option<String>>(3)?,
                "metadata_json": row.get::<_, String>(4).unwrap_or_else(|_| "{}".into()),
                "raw_json": row.get::<_, String>(5).unwrap_or_else(|_| "{}".into()),
                "created_at": row.get::<_, String>(6).unwrap_or_default(),
                "updated_at": row.get::<_, String>(7).unwrap_or_default(),
            }))
        },
    );
    let edges = query_values(
        &conn,
        "SELECT id, from_node, to_node, type, weight, metadata_json, created_at FROM edges",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "from_node": row.get::<_, String>(1).unwrap_or_default(),
                "to_node": row.get::<_, String>(2).unwrap_or_default(),
                "type": row.get::<_, String>(3).unwrap_or_default(),
                "weight": row.get::<_, f64>(4).unwrap_or(1.0),
                "metadata_json": row.get::<_, String>(5).unwrap_or_else(|_| "{}".into()),
                "created_at": row.get::<_, String>(6).unwrap_or_default(),
            }))
        },
    );
    let chunks = query_values(
        &conn,
        "SELECT id, source_node, text, metadata_json, created_at FROM chunks",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "source_node": row.get::<_, String>(1).unwrap_or_default(),
                "text": row.get::<_, String>(2).unwrap_or_default(),
                "metadata_json": row.get::<_, String>(3).unwrap_or_else(|_| "{}".into()),
                "created_at": row.get::<_, String>(4).unwrap_or_default(),
            }))
        },
    );
    let knowledge_sources = query_values(
        &conn,
        "SELECT id, root_path, os_type, drive_id, label, status, include_ocr, watch_enabled, consent_json, created_at, updated_at, last_scanned_at FROM knowledge_sources",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "root_path": row.get::<_, String>(1)?,
                "os_type": row.get::<_, String>(2)?,
                "drive_id": row.get::<_, Option<String>>(3)?,
                "label": row.get::<_, Option<String>>(4)?,
                "status": row.get::<_, String>(5)?,
                "include_ocr": row.get::<_, i64>(6).unwrap_or(0),
                "watch_enabled": row.get::<_, i64>(7).unwrap_or(0),
                "consent_json": row.get::<_, String>(8).unwrap_or_else(|_| "{}".into()),
                "created_at": row.get::<_, String>(9)?,
                "updated_at": row.get::<_, String>(10)?,
                "last_scanned_at": row.get::<_, Option<String>>(11)?,
            }))
        },
    );
    let vector_embeddings = query_values(
        &conn,
        "SELECT item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at FROM vector_embeddings",
        |row| {
            let blob: Vec<u8> = row.get(4).unwrap_or_default();
            Ok(json!({
                "item_id": row.get::<_, String>(0)?,
                "item_type": row.get::<_, String>(1)?,
                "source_node": row.get::<_, String>(2)?,
                "text_hash": row.get::<_, String>(3)?,
                "embedding_b64": B64_STANDARD.encode(&blob),
                "embedding_dim": row.get::<_, i64>(5)?,
                "embedding_model": row.get::<_, String>(6)?,
                "metadata_json": row.get::<_, String>(7).unwrap_or_else(|_| "{}".into()),
                "indexed_at": row.get::<_, String>(8)?,
            }))
        },
    );
    let ingestion_provenance = query_values(
        &conn,
        "SELECT id, node_id, source_type, source_uri, content_hash, title, pipeline, owner, workspace_id, captured_at, modified_at, embedded, linked, duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at FROM ingestion_provenance",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "node_id": row.get::<_, String>(1)?,
                "source_type": row.get::<_, String>(2)?,
                "source_uri": row.get::<_, Option<String>>(3)?,
                "content_hash": row.get::<_, Option<String>>(4)?,
                "title": row.get::<_, Option<String>>(5)?,
                "pipeline": row.get::<_, String>(6)?,
                "owner": row.get::<_, Option<String>>(7)?,
                "workspace_id": row.get::<_, Option<String>>(8)?,
                "captured_at": row.get::<_, Option<String>>(9)?,
                "modified_at": row.get::<_, Option<String>>(10)?,
                "embedded": row.get::<_, i64>(11).unwrap_or(0),
                "linked": row.get::<_, i64>(12).unwrap_or(0),
                "duplicate": row.get::<_, i64>(13).unwrap_or(0),
                "agent_used": row.get::<_, Option<String>>(14)?,
                "chunk_count": row.get::<_, i64>(15).unwrap_or(0),
                "permissions_json": row.get::<_, String>(16).unwrap_or_else(|_| "{}".into()),
                "metadata_json": row.get::<_, String>(17).unwrap_or_else(|_| "{}".into()),
                "created_at": row.get::<_, String>(18)?,
            }))
        },
    );
    let nodes_v2 = query_values(
        &conn,
        "SELECT id, type, legacy_type, label, summary, attrs, embedding, owner_id, workspace_id, visibility, superseded_by, valid_from, valid_to, created_at, updated_at, style, tone, importance_score, last_used FROM nodes_v2",
        |row| {
            let emb: Option<Vec<u8>> = row.get(6).ok().flatten();
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "type": row.get::<_, String>(1)?,
                "legacy_type": row.get::<_, Option<String>>(2)?,
                "label": row.get::<_, String>(3)?,
                "summary": row.get::<_, Option<String>>(4)?,
                "attrs": row.get::<_, String>(5).unwrap_or_else(|_| "{}".into()),
                "embedding_b64": emb.map(|b| B64_STANDARD.encode(&b)),
                "owner_id": row.get::<_, Option<String>>(7)?,
                "workspace_id": row.get::<_, Option<String>>(8)?,
                "visibility": row.get::<_, String>(9).unwrap_or_else(|_| "private".into()),
                "superseded_by": row.get::<_, Option<String>>(10)?,
                "valid_from": row.get::<_, Option<String>>(11)?,
                "valid_to": row.get::<_, Option<String>>(12)?,
                "created_at": row.get::<_, String>(13)?,
                "updated_at": row.get::<_, String>(14)?,
                "style": row.get::<_, Option<String>>(15)?,
                "tone": row.get::<_, Option<String>>(16)?,
                "importance_score": row.get::<_, f64>(17).unwrap_or(0.0),
                "last_used": row.get::<_, Option<String>>(18)?,
            }))
        },
    );
    let edges_v2 = query_values(
        &conn,
        "SELECT id, source, target, type, legacy_type, weight, confidence, evidence, metadata, created_by, created_at, valid_from, valid_to, superseded_by FROM edges_v2",
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "source": row.get::<_, String>(1)?,
                "target": row.get::<_, String>(2)?,
                "type": row.get::<_, String>(3)?,
                "legacy_type": row.get::<_, String>(4).unwrap_or_default(),
                "weight": row.get::<_, f64>(5).unwrap_or(1.0),
                "confidence": row.get::<_, f64>(6).unwrap_or(1.0),
                "evidence": row.get::<_, String>(7).unwrap_or_else(|_| "[]".into()),
                "metadata": row.get::<_, String>(8).unwrap_or_else(|_| "{}".into()),
                "created_by": row.get::<_, String>(9).unwrap_or_else(|_| "user".into()),
                "created_at": row.get::<_, String>(10)?,
                "valid_from": row.get::<_, Option<String>>(11)?,
                "valid_to": row.get::<_, Option<String>>(12)?,
                "superseded_by": row.get::<_, Option<String>>(13)?,
            }))
        },
    );

    json!({
        "nodes": nodes,
        "edges": edges,
        "chunks": chunks,
        "knowledge_sources": knowledge_sources,
        "vector_embeddings": vector_embeddings,
        "ingestion_provenance": ingestion_provenance,
        "nodes_v2": nodes_v2,
        "edges_v2": edges_v2,
    })
}

pub(super) fn export_counts_json(export_payload: &Value, blob_count: usize) -> Value {
    let len = |k: &str| {
        export_payload
            .get(k)
            .and_then(Value::as_array)
            .map(|a| a.len())
            .unwrap_or(0)
    };
    let vectors = len("vector_embeddings");
    let provenance = len("ingestion_provenance");
    json!({
        "nodes": len("nodes"),
        "edges": len("edges"),
        "chunks": len("chunks"),
        "vectors": vectors,
        "vector_embeddings": vectors,
        "provenance": provenance,
        "ingestion_provenance": provenance,
        "knowledge_sources": len("knowledge_sources"),
        "nodes_v2": len("nodes_v2"),
        "edges_v2": len("edges_v2"),
        "blobs": blob_count,
    })
}

fn restore_tables_transactionally(
    state: &PortabilityState,
    data: &Value,
    clear_existing: bool,
) -> Result<(), String> {
    let Some(graph) = &state.graph else {
        return Err("Graph writer is unavailable for transactional restore.".into());
    };
    graph
        .store()
        .with_write_txn(|txn| {
            if clear_existing {
                for tbl in [
                    "vector_embeddings",
                    "chunks",
                    "edges",
                    "ingestion_provenance",
                    "knowledge_sources",
                    "nodes",
                    "edges_v2",
                    "nodes_v2",
                ] {
                    txn.execute(&format!("DELETE FROM {tbl}"), [])?;
                }
            }
            for row in array_rows(data, "nodes") {
                txn.execute(
                    "INSERT OR REPLACE INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "type"),
                        str_col(row, "title"),
                        opt_str_col(row, "summary"),
                        str_col_or(row, "metadata_json", "{}"),
                        str_col_or(row, "raw_json", "{}"),
                        str_col(row, "created_at"),
                        str_col(row, "updated_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "edges") {
                txn.execute(
                    "INSERT OR REPLACE INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "from_node"),
                        str_col(row, "to_node"),
                        str_col(row, "type"),
                        row.get("weight").and_then(Value::as_f64).unwrap_or(1.0),
                        str_col_or(row, "metadata_json", "{}"),
                        str_col(row, "created_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "chunks") {
                txn.execute(
                    "INSERT OR REPLACE INTO chunks (id, source_node, text, metadata_json, created_at)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "source_node"),
                        str_col(row, "text"),
                        str_col_or(row, "metadata_json", "{}"),
                        str_col(row, "created_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "knowledge_sources") {
                txn.execute(
                    "INSERT OR REPLACE INTO knowledge_sources (id, root_path, os_type, drive_id, label, status, include_ocr, watch_enabled, consent_json, created_at, updated_at, last_scanned_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "root_path"),
                        str_col(row, "os_type"),
                        opt_str_col(row, "drive_id"),
                        opt_str_col(row, "label"),
                        str_col(row, "status"),
                        row.get("include_ocr").and_then(Value::as_i64).unwrap_or(0),
                        row.get("watch_enabled").and_then(Value::as_i64).unwrap_or(0),
                        str_col_or(row, "consent_json", "{}"),
                        str_col(row, "created_at"),
                        str_col(row, "updated_at"),
                        opt_str_col(row, "last_scanned_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "vector_embeddings") {
                let blob = row
                    .get("embedding_b64")
                    .and_then(Value::as_str)
                    .and_then(|s| B64_STANDARD.decode(s).ok())
                    .unwrap_or_default();
                txn.execute(
                    "INSERT OR REPLACE INTO vector_embeddings (item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    params![
                        str_col(row, "item_id"),
                        str_col(row, "item_type"),
                        str_col(row, "source_node"),
                        str_col(row, "text_hash"),
                        blob,
                        row.get("embedding_dim").and_then(Value::as_i64).unwrap_or(0),
                        str_col(row, "embedding_model"),
                        str_col_or(row, "metadata_json", "{}"),
                        str_col(row, "indexed_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "ingestion_provenance") {
                txn.execute(
                    "INSERT OR REPLACE INTO ingestion_provenance (id, node_id, source_type, source_uri, content_hash, title, pipeline, owner, workspace_id, captured_at, modified_at, embedded, linked, duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "node_id"),
                        str_col(row, "source_type"),
                        opt_str_col(row, "source_uri"),
                        opt_str_col(row, "content_hash"),
                        opt_str_col(row, "title"),
                        str_col(row, "pipeline"),
                        opt_str_col(row, "owner"),
                        opt_str_col(row, "workspace_id"),
                        opt_str_col(row, "captured_at"),
                        opt_str_col(row, "modified_at"),
                        row.get("embedded").and_then(Value::as_i64).unwrap_or(0),
                        row.get("linked").and_then(Value::as_i64).unwrap_or(0),
                        row.get("duplicate").and_then(Value::as_i64).unwrap_or(0),
                        opt_str_col(row, "agent_used"),
                        row.get("chunk_count").and_then(Value::as_i64).unwrap_or(0),
                        str_col_or(row, "permissions_json", "{}"),
                        str_col_or(row, "metadata_json", "{}"),
                        str_col(row, "created_at"),
                    ],
                )?;
            }
            for row in array_rows(data, "nodes_v2") {
                let emb = row
                    .get("embedding_b64")
                    .and_then(Value::as_str)
                    .and_then(|s| B64_STANDARD.decode(s).ok());
                txn.execute(
                    "INSERT OR REPLACE INTO nodes_v2 (id, type, legacy_type, label, summary, attrs, embedding, owner_id, workspace_id, visibility, superseded_by, valid_from, valid_to, created_at, updated_at, style, tone, importance_score, last_used)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "type"),
                        opt_str_col(row, "legacy_type"),
                        str_col(row, "label"),
                        opt_str_col(row, "summary"),
                        str_col_or(row, "attrs", "{}"),
                        emb,
                        opt_str_col(row, "owner_id"),
                        opt_str_col(row, "workspace_id"),
                        str_col_or(row, "visibility", "private"),
                        opt_str_col(row, "superseded_by"),
                        opt_str_col(row, "valid_from"),
                        opt_str_col(row, "valid_to"),
                        str_col(row, "created_at"),
                        str_col(row, "updated_at"),
                        opt_str_col(row, "style"),
                        opt_str_col(row, "tone"),
                        row.get("importance_score").and_then(Value::as_f64).unwrap_or(0.0),
                        opt_str_col(row, "last_used"),
                    ],
                )?;
            }
            for row in array_rows(data, "edges_v2") {
                txn.execute(
                    "INSERT OR REPLACE INTO edges_v2 (id, source, target, type, legacy_type, weight, confidence, evidence, metadata, created_by, created_at, valid_from, valid_to, superseded_by)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
                    params![
                        str_col(row, "id"),
                        str_col(row, "source"),
                        str_col(row, "target"),
                        str_col(row, "type"),
                        str_col(row, "legacy_type"),
                        row.get("weight").and_then(Value::as_f64).unwrap_or(1.0),
                        row.get("confidence").and_then(Value::as_f64).unwrap_or(1.0),
                        str_col_or(row, "evidence", "[]"),
                        str_col_or(row, "metadata", "{}"),
                        str_col_or(row, "created_by", "user"),
                        str_col(row, "created_at"),
                        opt_str_col(row, "valid_from"),
                        opt_str_col(row, "valid_to"),
                        opt_str_col(row, "superseded_by"),
                    ],
                )?;
            }
            Ok(())
        })
        .map_err(|e| format!("Transactional archive restore failed: {e}"))
}

pub(super) fn array_rows<'a>(val: &'a Value, key: &str) -> &'a [Value] {
    val.get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

pub(super) fn str_col(row: &Value, key: &str) -> String {
    row.get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn str_col_or(row: &Value, key: &str, default: &str) -> String {
    row.get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

pub(super) fn opt_str_col(row: &Value, key: &str) -> Option<String> {
    row.get(key).and_then(Value::as_str).map(str::to_string)
}
