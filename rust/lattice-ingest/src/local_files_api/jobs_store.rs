//! SQLite `ingestion_jobs` persistence and interop source availability helpers.

use std::sync::Arc;

use lattice_auth::OrderedMap;
use lattice_core::graph_write::clock::{Clock, SystemClock};
use lattice_core::CoreError;
use serde_json::{json, Value};

use super::LocalFilesState;

const GIT_UNAVAILABLE: &str = "reading a repository's history needs git on this machine and none was found; nothing was ingested";

pub(super) async fn next_job_id(store: &Arc<lattice_core::db::Store>) -> Result<String, String> {
    let store = Arc::clone(store);
    tokio::task::spawn_blocking(move || {
        store.with_write_conn(|conn| {
            ensure_jobs_table(conn)?;
            let count: i64 = conn
                .query_row("SELECT COUNT(*) FROM ingestion_jobs", [], |row| row.get(0))
                .unwrap_or(0);
            Ok(format!("bg_ingest_{:04}", count + 1))
        })
    })
    .await
    .map_err(|error| error.to_string())?
    .map_err(|error: CoreError| error.to_string())
}

#[allow(clippy::too_many_arguments)]
pub(super) async fn write_job(
    store: &Arc<lattice_core::db::Store>,
    job_id: &str,
    status: &str,
    total: i64,
    processed: i64,
    failed: i64,
    errors: &Value,
    report: &Value,
    created_at: &str,
    updated_at: &str,
) -> Result<(), String> {
    let store = Arc::clone(store);
    let job_id = job_id.to_string();
    let status = status.to_string();
    let errors = errors.to_string();
    let report = report.to_string();
    let created_at = created_at.to_string();
    let updated_at = updated_at.to_string();
    tokio::task::spawn_blocking(move || {
        store.with_write_conn(|conn| {
            ensure_jobs_table(conn)?;
            conn.execute(
                "INSERT INTO ingestion_jobs(
                    job_id, status, total, processed, failed, errors_json, 
                    created_at, updated_at, items_json, done_indices_json
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]') 
                 ON CONFLICT(job_id) DO UPDATE SET 
                    status=excluded.status, 
                    total=excluded.total, 
                    processed=excluded.processed, 
                    failed=excluded.failed, 
                    errors_json=excluded.errors_json, 
                    items_json=excluded.items_json, 
                    updated_at=excluded.updated_at",
                rusqlite::params![
                    job_id, status, total, processed, failed, errors, created_at, updated_at,
                    report
                ],
            )?;
            Ok(())
        })
    })
    .await
    .map_err(|error| error.to_string())?
    .map_err(|error: CoreError| error.to_string())
}

fn ensure_jobs_table(conn: &rusqlite::Connection) -> Result<(), CoreError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS ingestion_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT,
            total INTEGER,
            processed INTEGER,
            failed INTEGER,
            errors_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            items_json TEXT,
            done_indices_json TEXT
        )",
    )?;
    Ok(())
}

pub(super) fn now_stamp(state: &LocalFilesState) -> String {
    match state.graph() {
        Some(graph) => graph.clock().now_iso(),
        None => SystemClock.now_iso(),
    }
}

pub(super) fn list_jobs(conn: &rusqlite::Connection, limit: i64) -> Result<Vec<Value>, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT job_id, status, total, processed, failed, errors_json, created_at, updated_at 
         FROM ingestion_jobs ORDER BY created_at DESC, job_id DESC LIMIT ?",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(Vec::new()),
    };
    let rows = statement.query_map([limit], job_from_row)?;
    Ok(rows.filter_map(Result::ok).collect())
}

pub(super) fn load_job(
    conn: &rusqlite::Connection,
    job_id: &str,
) -> Result<Option<Value>, CoreError> {
    let mut statement = match conn.prepare(
        "SELECT job_id, status, total, processed, failed, errors_json, created_at, updated_at, 
                items_json, done_indices_json 
         FROM ingestion_jobs WHERE job_id=?",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(None),
    };
    let mut rows = statement.query([job_id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    Ok(Some(job_from_row(row)?))
}

fn job_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let mut job = OrderedMap::new();
    job.insert("job_id", json!(row.get::<_, String>(0)?));
    job.insert("status", json!(row.get::<_, String>(1)?));
    job.insert("total", json!(row.get::<_, i64>(2).unwrap_or(0)));
    job.insert("processed", json!(row.get::<_, i64>(3).unwrap_or(0)));
    job.insert("failed", json!(row.get::<_, i64>(4).unwrap_or(0)));
    let errors: Value = row
        .get::<_, Option<String>>(5)
        .ok()
        .flatten()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!([]));
    job.insert("errors", errors);
    job.insert(
        "created_at",
        json!(row.get::<_, Option<String>>(6)?.unwrap_or_default()),
    );
    job.insert(
        "updated_at",
        json!(row.get::<_, Option<String>>(7)?.unwrap_or_default()),
    );
    if let Ok(Some(raw)) = row.get::<_, Option<String>>(8) {
        if let Ok(Value::Object(report)) = serde_json::from_str::<Value>(&raw) {
            for key in [
                "scanned",
                "skipped",
                "skipped_unchanged",
                "reingested",
                "ingested",
                "deleted",
            ] {
                if let Some(value) = report.get(key) {
                    job.insert(key, value.clone());
                }
            }
            if let Some(processed) = report.get("processed") {
                job.insert("processed", processed.clone());
            }
        }
    }
    Ok(serde_json::to_value(job).unwrap_or(Value::Null))
}

pub(super) fn remaining_of(job: &Value) -> i64 {
    let total = job.get("total").and_then(Value::as_i64).unwrap_or(0);
    let processed = job.get("processed").and_then(Value::as_i64).unwrap_or(0);
    (total - processed).max(0)
}

pub(super) fn bridge_status() -> Value {
    let git = which_git();
    json!({
        "sources": {
            "notion": {
                "available": true,
                "accepts": ["directory", ".zip"],
                "detail": "a Notion export you downloaded — never the Notion API"
            },
            "git": {
                "available": git,
                "accepts": ["repository directory"],
                "detail": if git { Value::Null } else { json!(GIT_UNAVAILABLE) }
            },
            "mail": {
                "available": true,
                "accepts": [".eml", ".ics", "a folder of either"],
                "detail": "local files only; connecting a live mailbox or system calendar is deliberately out of scope"
            }
        }
    })
}

fn which_git() -> bool {
    std::process::Command::new("git")
        .arg("--version")
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}
