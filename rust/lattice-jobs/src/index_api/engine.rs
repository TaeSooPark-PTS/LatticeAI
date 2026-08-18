//! Claim pending `vector_jobs`, embed them, and settle the queue.
//!
//! The HTTP handlers in [`super`] decide *whether* to drain and what the
//! client is told. This module is the write path those handlers (and the
//! scheduler's native tick) share: claim, optional worker embed, write
//! vectors, mark `done` / `pending`.

use lattice_core::graph_write::types::SuppliedVector;
use lattice_core::graph_write::{Clock, GraphWriter, SystemClock};
use lattice_core::worker::WorkerSeamClient;
use serde_json::Value;

use super::status;

/// One native drain tick, optionally via the worker embed seam.
pub(crate) async fn drain_once(
    graph: GraphWriter,
    db: std::path::PathBuf,
    limit: usize,
    seam: Option<WorkerSeamClient>,
) -> Result<crate::tick::DrainOutcome, String> {
    let Some(seam) = seam else {
        return tokio::task::spawn_blocking(move || drain_queue(&graph, &db, limit))
            .await
            .map_err(|error| error.to_string());
    };
    let claimed = {
        let db = db.clone();
        tokio::task::spawn_blocking(move || claim_pending(&db, limit))
            .await
            .map_err(|error| error.to_string())?
    };
    if claimed.is_empty() {
        return Ok(crate::tick::DrainOutcome::default());
    }
    let items = {
        let db = db.clone();
        let claimed = claimed.clone();
        tokio::task::spawn_blocking(move || embed_items_for_nodes(&db, &claimed))
            .await
            .map_err(|error| error.to_string())?
    };
    let supplied = match embed_texts_batched(&seam, &items).await {
        Ok(supplied) => supplied,
        Err(error) => {
            // Busy or down: put the claim back and tell the scheduler.
            let db = db.clone();
            let claimed = claimed.clone();
            let _ = tokio::task::spawn_blocking(move || release_claim(&db, &claimed)).await;
            return Err(error);
        }
    };
    tokio::task::spawn_blocking(move || drain_claimed(&graph, &db, &claimed, &supplied))
        .await
        .map_err(|error| error.to_string())
}

/// Batch `POST /worker/embed` for the collected texts. Concurrent in-flight
/// requests stay inside [`crate::drain::EMBED_INFLIGHT`] so we do not trip
/// the worker's `agent_seam` rate bucket (default 60/s).
async fn embed_texts_batched(
    seam: &WorkerSeamClient,
    items: &[(String, String, String)],
) -> Result<Vec<(String, SuppliedVector)>, String> {
    use crate::drain::{embed_batches, embed_inflight, is_worker_busy_status, EMBED_BATCH};
    use lattice_core::worker::WorkerSeamError;

    if items.is_empty() {
        return Ok(Vec::new());
    }
    let batches = embed_batches(items, EMBED_BATCH);
    let width = embed_inflight(batches.len());
    let mut supplied = Vec::with_capacity(items.len());
    for wave in batches.chunks(width) {
        let mut set = tokio::task::JoinSet::new();
        for batch in wave {
            let seam = seam.clone();
            let texts: Vec<String> = batch.iter().map(|item| item.2.clone()).collect();
            let ids: Vec<String> = batch.iter().map(|item| item.1.clone()).collect();
            set.spawn(async move {
                let payload = seam
                    .post_json(
                        "/worker/embed",
                        &serde_json::json!({ "texts": texts, "kind": "passage" }),
                    )
                    .await;
                (ids, payload)
            });
        }
        while let Some(joined) = set.join_next().await {
            let (ids, payload) = joined.map_err(|error| error.to_string())?;
            let payload = payload.map_err(|error| match &error {
                WorkerSeamError::Rejected { status, .. } if is_worker_busy_status(*status) => {
                    format!("worker embed answered {status}: busy")
                }
                other => other.to_string(),
            })?;
            let model_id = payload
                .get("model_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let dim = payload.get("dim").and_then(Value::as_u64).unwrap_or(0) as usize;
            let rows = payload
                .get("vectors")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            for (index, item_id) in ids.into_iter().enumerate() {
                let values: Vec<f64> = rows
                    .get(index)
                    .and_then(Value::as_array)
                    .map(|cells| cells.iter().filter_map(Value::as_f64).collect())
                    .unwrap_or_default();
                if values.is_empty() {
                    continue;
                }
                let width = if dim == 0 { values.len() } else { dim };
                supplied.push((
                    item_id,
                    SuppliedVector {
                        values,
                        model_id: model_id.clone(),
                        dim: width,
                    },
                ));
            }
        }
    }
    Ok(supplied)
}

fn release_claim(db: &std::path::Path, ids: &[String]) {
    let Ok(conn) = rusqlite::Connection::open(db) else {
        return;
    };
    for node_id in ids {
        let _ = conn.execute(
            "UPDATE vector_jobs SET status='pending' WHERE node_id=?1 AND status='running'",
            [node_id],
        );
    }
}

/// One native drain tick: claim pending `vector_jobs`, `write_vectors` each.
///
/// Claim is an atomic `UPDATE … RETURNING` so two ticks (or two in-flight
/// POSTs) cannot process the same node. Rows stay `running` until this
/// function writes `done` / `pending`, which is what makes the concurrent
/// HTTP fan-out in [`crate::scheduler::Scheduler`] safe.
pub fn drain_queue(
    graph: &GraphWriter,
    db: &std::path::Path,
    limit: usize,
) -> crate::tick::DrainOutcome {
    drain_claimed(graph, db, &claim_pending(db, limit), &[])
}

/// Claim up to `limit` pending jobs, marking them `running`.
///
/// An unreadable store or a missing `vector_jobs` table returns an empty
/// vec — the same "nothing to do" the previous `SELECT` path reported.
pub fn claim_pending(db: &std::path::Path, limit: usize) -> Vec<String> {
    let Ok(conn) = rusqlite::Connection::open(db) else {
        return Vec::new();
    };
    let _ = conn.busy_timeout(std::time::Duration::from_secs(5));
    claim_pending_on(&conn, limit).unwrap_or_default()
}

fn claim_pending_on(
    conn: &rusqlite::Connection,
    limit: usize,
) -> Result<Vec<String>, rusqlite::Error> {
    if limit == 0 {
        return Ok(Vec::new());
    }
    recover_stale_running(conn);
    let now = SystemClock.now_iso();
    let txn = conn.unchecked_transaction()?;
    let sql_ordered = "UPDATE vector_jobs SET status='running', updated_at=?1 \
         WHERE node_id IN (\
            SELECT node_id FROM vector_jobs WHERE status='pending' \
            ORDER BY created_at ASC, node_id ASC LIMIT ?2\
         ) RETURNING node_id";
    let sql_plain = "UPDATE vector_jobs SET status='running', updated_at=?1 \
         WHERE node_id IN (\
            SELECT node_id FROM vector_jobs WHERE status='pending' LIMIT ?2\
         ) RETURNING node_id";
    let ids = match collect_returning(&txn, sql_ordered, &now, limit) {
        Ok(ids) => ids,
        Err(_) => collect_returning(&txn, sql_plain, &now, limit)?,
    };
    txn.commit()?;
    Ok(ids)
}

fn collect_returning(
    txn: &rusqlite::Transaction<'_>,
    sql: &str,
    now: &str,
    limit: usize,
) -> Result<Vec<String>, rusqlite::Error> {
    let mut statement = txn.prepare(sql)?;
    let rows = statement.query_map(rusqlite::params![now, limit as i64], |row| row.get(0))?;
    Ok(rows.filter_map(Result::ok).collect())
}

/// Steal `running` rows whose `updated_at` is older than
/// [`crate::drain::STALE_RUNNING_SECS`]. Comparison is lexicographic ISO-8601
/// (the format GraphWriter and this crate both write).
fn recover_stale_running(conn: &rusqlite::Connection) {
    let clock = SystemClock;
    let cutoff_unix = (clock.unix_time() as i64)
        .saturating_sub(crate::drain::STALE_RUNNING_SECS as i64)
        .max(0) as u64;
    // `now_iso` is naive local. The cutoff is that stamp minus the stale
    // window on the clock face — good enough to unstick a crashed host.
    let now = clock.now_iso();
    let cutoff = stale_iso_cutoff(&now, cutoff_unix, clock.unix_time() as u64);
    let _ = conn.execute(
        "UPDATE vector_jobs SET status='pending' \
         WHERE status='running' AND updated_at < ?1",
        [&cutoff],
    );
}

fn stale_iso_cutoff(now_iso: &str, cutoff_unix: u64, now_unix: u64) -> String {
    // Prefer a string that sorts before `now_iso` by the stale window. When
    // the stamp is `YYYY-MM-DDTHH:MM:SS` we subtract seconds on the clock
    // face; anything else falls back to `now_iso` itself (no recovery).
    if now_iso.len() < 19 || now_unix < cutoff_unix {
        return now_iso.to_string();
    }
    let delta = now_unix.saturating_sub(cutoff_unix);
    shift_iso_seconds(now_iso, -(delta as i64)).unwrap_or_else(|| now_iso.to_string())
}

fn shift_iso_seconds(stamp: &str, delta: i64) -> Option<String> {
    if stamp.len() < 19 || !stamp.is_char_boundary(10) || stamp.as_bytes().get(10) != Some(&b'T') {
        return None;
    }
    let date = &stamp[..10];
    let hour: i64 = stamp.get(11..13)?.parse().ok()?;
    let minute: i64 = stamp.get(14..16)?.parse().ok()?;
    let second: i64 = stamp.get(17..19)?.parse().ok()?;
    let tod = hour * 3600 + minute * 60 + second + delta;
    if (0..86_400).contains(&tod) {
        return Some(format!(
            "{date}T{:02}:{:02}:{:02}",
            tod / 3600,
            (tod % 3600) / 60,
            tod % 60
        ));
    }
    // A two-minute window that crosses midnight: pin to the start of today
    // so yesterday's abandoned `running` rows still compare as stale.
    if tod < 0 {
        return Some(format!("{date}T00:00:00"));
    }
    Some(format!("{date}T23:59:59"))
}

/// Finish already-claimed node ids: embed (supplied or native) and settle status.
pub fn drain_claimed(
    graph: &GraphWriter,
    db: &std::path::Path,
    ids: &[String],
    supplied: &[(String, SuppliedVector)],
) -> crate::tick::DrainOutcome {
    let mut outcome = crate::tick::DrainOutcome::default();
    let Ok(conn) = rusqlite::Connection::open(db) else {
        outcome.detail = Some("vector_jobs unavailable".into());
        return outcome;
    };
    let _ = conn.busy_timeout(std::time::Duration::from_secs(5));
    outcome.claimed = ids.len() as u64;
    for node_id in ids {
        let result = if supplied.is_empty() {
            graph.write_vectors(node_id)
        } else {
            graph.write_vectors_with(node_id, supplied)
        };
        if result.status == "indexed" || result.status == "noop" {
            outcome.indexed += 1;
            let _ = conn.execute(
                "UPDATE vector_jobs SET status='done' WHERE node_id=?1",
                [node_id],
            );
        } else {
            outcome.retried += 1;
            let _ = conn.execute(
                "UPDATE vector_jobs SET status='pending' WHERE node_id=?1",
                [node_id],
            );
        }
    }
    outcome
}

/// `(node_id, item_id, text)` the worker embed seam should see for these nodes.
///
/// Same text GraphWriter would hash: the node vector-text plus each chunk.
pub fn embed_items_for_nodes(
    db: &std::path::Path,
    node_ids: &[String],
) -> Vec<(String, String, String)> {
    let Ok(conn) = rusqlite::Connection::open(db) else {
        return Vec::new();
    };
    embed_items_on(&conn, node_ids)
}

fn embed_items_on(
    conn: &rusqlite::Connection,
    node_ids: &[String],
) -> Vec<(String, String, String)> {
    let mut items = Vec::new();
    for node_id in node_ids {
        if let Ok((id, node_type, title, summary, metadata_json)) = conn.query_row(
            "SELECT id, type, title, summary, metadata_json FROM nodes WHERE id=?",
            [node_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                ))
            },
        ) {
            if node_type != "Chunk" {
                let metadata = lattice_core::pytext::safe_loads(Some(&metadata_json));
                let text = status::vector_text_for_node(
                    &title,
                    summary.as_deref().unwrap_or(""),
                    &metadata,
                );
                if !text.is_empty() {
                    items.push((id.clone(), id.clone(), text));
                }
            }
        }
        if let Ok(mut statement) = conn.prepare(
            "SELECT c.id, c.text FROM chunks c WHERE c.source_node=? \
             ORDER BY c.created_at ASC, c.id ASC",
        ) {
            if let Ok(rows) = statement.query_map([node_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
            }) {
                for row in rows.flatten() {
                    let text = lattice_core::pytext::clean_text(row.1.as_deref().unwrap_or(""));
                    if !text.is_empty() {
                        items.push((node_id.clone(), row.0, text));
                    }
                }
            }
        }
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_claims_on_the_same_store_never_share_a_node() {
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("knowledge_graph.sqlite");
        let conn = rusqlite::Connection::open(&db).unwrap();
        conn.execute_batch(
            "CREATE TABLE vector_jobs (
               node_id TEXT PRIMARY KEY,
               status TEXT NOT NULL DEFAULT 'pending',
               attempts INTEGER NOT NULL DEFAULT 0,
               detail TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
             );",
        )
        .unwrap();
        for index in 0..10 {
            conn.execute(
                "INSERT INTO vector_jobs(node_id, status, created_at, updated_at) \
                 VALUES (?1, 'pending', '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
                [format!("n{index:02}")],
            )
            .unwrap();
        }
        drop(conn);

        let first = claim_pending(&db, 6);
        let second = claim_pending(&db, 6);
        assert_eq!(first.len(), 6);
        assert_eq!(second.len(), 4);
        let mut all = first;
        all.extend(second);
        all.sort();
        let before = all.len();
        all.dedup();
        assert_eq!(all.len(), before, "a node was claimed twice");
        assert_eq!(all.len(), 10);
    }

    #[test]
    fn the_stale_cutoff_rewinds_two_minutes_on_the_same_day() {
        assert_eq!(
            shift_iso_seconds("2026-08-17T12:00:00", -120).as_deref(),
            Some("2026-08-17T11:58:00")
        );
        assert_eq!(
            shift_iso_seconds("2026-08-17T00:00:30", -120).as_deref(),
            Some("2026-08-17T00:00:00")
        );
    }
}
