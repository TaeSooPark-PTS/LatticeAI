//! The embed backlog, counted straight out of SQLite.
//!
//! `GET /host/jobs` must be able to say how much work is owed even while the
//! worker is down — that is precisely when someone is looking — so the counts
//! do not travel through the worker's HTTP API. They come from the same
//! read-only handle the native search lanes use
//! ([`lattice_core::open_read_only`]): `SQLITE_OPEN_READ_ONLY`, WAL, bounded
//! busy timeout. This crate cannot write, migrate, or vacuum a store whose
//! schema Python owns, and that is a property of the connection rather than a
//! promise in a comment.
//!
//! The query is Python's verbatim (`vector_index/jobs.py`
//! `VectorJobStore.counts`): `SELECT status, COUNT(*) … GROUP BY status`,
//! zero-filled over the four known statuses, with any unknown status carried
//! through rather than dropped.

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
    clippy::module_inception
)]
use std::collections::BTreeMap;
use std::path::Path;

use serde::Serialize;

/// The queue's lifecycle, as Python's `VECTOR_JOB_STATUSES` names it.
pub const VECTOR_JOB_STATUSES: [&str; 4] = ["pending", "running", "done", "failed"];

/// Backlog counts, or an honest "nothing is counting".
///
/// `available: false` is deliberately not the same answer as four zeros: a
/// store with no `vector_jobs` table has no backlog *measured*, and a
/// scheduler that read that as "no backlog" would report an idle queue for a
/// Brain it cannot see.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct QueueCounts {
    /// Whether the table was actually read.
    pub available: bool,
    /// `{status: count}`, zero-filled over the known statuses.
    pub counts: BTreeMap<String, u64>,
    /// Nodes still owed an embedding — `pending` plus `running`, the same sum
    /// `VectorEmbedQueue.pending_count` returns.
    pub pending: u64,
    /// Why the counts are unavailable, when they are.
    pub detail: Option<String>,
}

impl QueueCounts {
    /// Zeros with a reason — never mistaken for a measured empty queue.
    pub fn unavailable(detail: impl Into<String>) -> Self {
        Self {
            available: false,
            counts: zeroed(),
            pending: 0,
            detail: Some(detail.into()),
        }
    }

    /// How many nodes are in a given status (0 when absent).
    pub fn get(&self, status: &str) -> u64 {
        self.counts.get(status).copied().unwrap_or(0)
    }
}

/// Count `vector_jobs` in `db`. Never fails: an unreadable store is an
/// [`QueueCounts::unavailable`] with the cause attached.
pub fn read_counts(db: &Path) -> QueueCounts {
    let conn = match lattice_core::open_read_only(db) {
        Ok(conn) => conn,
        Err(err) => return QueueCounts::unavailable(format!("{err}")),
    };
    let mut statement =
        match conn.prepare("SELECT status, COUNT(*) FROM vector_jobs GROUP BY status") {
            Ok(statement) => statement,
            // The overwhelmingly common case: a Brain that has never had to defer
            // an embedding, so the lazily created table does not exist yet.
            Err(err) => return QueueCounts::unavailable(format!("{err}")),
        };
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    });
    let rows = match rows {
        Ok(rows) => rows,
        Err(err) => return QueueCounts::unavailable(format!("{err}")),
    };
    let mut counts = zeroed();
    for row in rows {
        match row {
            Ok((status, total)) => {
                counts.insert(status, total.max(0) as u64);
            }
            Err(err) => return QueueCounts::unavailable(format!("{err}")),
        }
    }
    let pending =
        counts.get("pending").copied().unwrap_or(0) + counts.get("running").copied().unwrap_or(0);
    QueueCounts {
        available: true,
        counts,
        pending,
        detail: None,
    }
}

fn zeroed() -> BTreeMap<String, u64> {
    VECTOR_JOB_STATUSES
        .iter()
        .map(|status| ((*status).to_string(), 0))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The DDL from `lattice_brain/graph/vector_index/jobs.py`, verbatim.
    const DDL: &str = "CREATE TABLE IF NOT EXISTS vector_jobs (
                  node_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  detail TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );";

    fn store(rows: &[(&str, &str)]) -> (tempfile::TempDir, std::path::PathBuf) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("knowledge_graph.sqlite");
        let conn = rusqlite::Connection::open(&path).expect("open");
        conn.execute_batch(&format!("PRAGMA journal_mode=WAL; {DDL}"))
            .expect("ddl");
        for (node_id, status) in rows {
            conn.execute(
                "INSERT INTO vector_jobs(node_id, status, attempts, detail, created_at, updated_at)
                 VALUES (?1, ?2, 0, NULL, '2026-08-11T00:00:00', '2026-08-11T00:00:00')",
                rusqlite::params![node_id, status],
            )
            .expect("insert");
        }
        (dir, path)
    }

    #[test]
    fn a_real_backlog_is_counted_by_status() {
        let (_dir, path) = store(&[
            ("a", "pending"),
            ("b", "pending"),
            ("c", "running"),
            ("d", "done"),
            ("e", "failed"),
        ]);

        let counts = read_counts(&path);

        assert!(counts.available);
        assert_eq!(counts.detail, None);
        assert_eq!(counts.get("pending"), 2);
        assert_eq!(counts.get("running"), 1);
        assert_eq!(counts.get("done"), 1);
        assert_eq!(counts.get("failed"), 1);
        // Owed = queued plus in flight, the same sum Python reports.
        assert_eq!(counts.pending, 3);
    }

    #[test]
    fn an_empty_table_is_measured_zeros_not_an_unavailable_queue() {
        let (_dir, path) = store(&[]);

        let counts = read_counts(&path);

        assert!(counts.available);
        assert_eq!(counts.pending, 0);
        assert_eq!(
            counts.counts.keys().collect::<Vec<_>>(),
            vec!["done", "failed", "pending", "running"]
        );
        assert!(counts.counts.values().all(|total| *total == 0));
    }

    #[test]
    fn a_store_without_the_table_says_so_instead_of_reporting_zero() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("knowledge_graph.sqlite");
        rusqlite::Connection::open(&path)
            .expect("open")
            .execute_batch("CREATE TABLE nodes_v2(id TEXT)")
            .expect("ddl");

        let counts = read_counts(&path);

        assert!(!counts.available);
        assert_eq!(counts.pending, 0);
        assert!(counts.detail.expect("reason").contains("vector_jobs"));
    }

    #[test]
    fn a_missing_store_is_unavailable_rather_than_a_panic() {
        let dir = tempfile::tempdir().expect("tempdir");

        let counts = read_counts(&dir.path().join("nope.sqlite"));

        assert!(!counts.available);
        assert!(counts.detail.is_some());
        assert_eq!(counts.get("pending"), 0);
    }

    #[test]
    fn an_unknown_status_is_carried_through_rather_than_dropped() {
        let (_dir, path) = store(&[("a", "quarantined"), ("b", "pending")]);

        let counts = read_counts(&path);

        assert_eq!(counts.get("quarantined"), 1);
        assert_eq!(counts.pending, 1);
    }

    #[test]
    fn the_payload_serializes_with_the_keys_the_route_promises() {
        let (_dir, path) = store(&[("a", "pending")]);

        let json = serde_json::to_value(read_counts(&path)).expect("json");

        assert_eq!(json["available"], serde_json::json!(true));
        assert_eq!(json["counts"]["pending"], serde_json::json!(1));
        assert_eq!(json["pending"], serde_json::json!(1));
        assert_eq!(json["detail"], serde_json::Value::Null);
    }

    #[test]
    fn the_read_handle_cannot_write() {
        let (_dir, path) = store(&[]);
        let conn = lattice_core::open_read_only(&path).expect("open");

        let err = conn
            .execute(
                "INSERT INTO vector_jobs(node_id, status, created_at, updated_at) \
                 VALUES ('x', 'pending', '', '')",
                [],
            )
            .expect_err("a read-only handle must refuse");

        assert!(format!("{err}").contains("read"), "unexpected error: {err}");
    }
}
