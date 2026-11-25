use std::collections::BTreeMap;
use std::path::Path;

use serde::Serialize;

pub const VECTOR_JOB_STATUSES: [&str; 4] = ["pending", "running", "done", "failed"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct QueueCounts {
    pub available: bool,
    pub counts: BTreeMap<String, u64>,
    pub pending: u64,
    pub detail: Option<String>,
}

impl QueueCounts {
    pub fn unavailable(detail: impl Into<String>) -> Self {
        Self {
            available: false,
            counts: zeroed(),
            pending: 0,
            detail: Some(detail.into()),
        }
    }

    pub fn get(&self, status: &str) -> u64 {
        self.counts.get(status).copied().unwrap_or(0)
    }
}

pub fn read_counts(db: &Path) -> QueueCounts {
    let conn = match lattice_core::open_read_only(db) {
        Ok(conn) => conn,
        Err(err) => return QueueCounts::unavailable(format!("{err}")),
    };
    let mut statement =
        match conn.prepare("SELECT status, COUNT(*) FROM vector_jobs GROUP BY status") {
            Ok(statement) => statement,
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
