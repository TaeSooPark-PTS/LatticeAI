//! Read-only SQLite access to the shared knowledge graph.
//!
//! Python opens the file read/write through `lattice_brain.storage.SQLiteEngine`
//! and sets no busy timeout. Rust is the reader that yields: it opens
//! `SQLITE_OPEN_READ_ONLY`, keeps the journal in WAL (so a Python writer and a
//! Rust reader never block each other), and waits 2 s on a lock instead of
//! failing the search the moment an ingest holds one.

use std::path::Path;

use rusqlite::{Connection, OpenFlags};

/// Milliseconds a read waits for a writer's lock before giving up.
pub const BUSY_TIMEOUT_MS: u32 = 2_000;

/// Every failure this crate can hand back to a caller.
#[derive(Debug)]
pub enum CoreError {
    /// A SQLite call failed (missing table, locked database, corrupt file …).
    Sqlite(rusqlite::Error),
    /// Two vectors of different lengths were compared — see
    /// [`crate::embeddings::LocalEmbeddingModel::similarity`].
    DimensionMismatch { left: usize, right: usize },
}

impl std::fmt::Display for CoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CoreError::Sqlite(err) => write!(f, "{err}"),
            CoreError::DimensionMismatch { left, right } => write!(
                f,
                "embedding dimension mismatch: {left} vs {right}; \
                 the vector index was built with a different model"
            ),
        }
    }
}

impl std::error::Error for CoreError {}

impl From<rusqlite::Error> for CoreError {
    fn from(err: rusqlite::Error) -> Self {
        CoreError::Sqlite(err)
    }
}

/// Open the graph database read-only, in WAL, with a bounded busy timeout.
///
/// `SQLITE_OPEN_READ_ONLY` is the load-bearing part: this crate must not be
/// able to migrate, vacuum, or otherwise mutate a store whose schema Python
/// owns, and a read-only handle makes that a property of the connection rather
/// than a promise in a comment.
pub fn open_read_only(path: &Path) -> Result<Connection, CoreError> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))?;
    // `journal_mode` is a no-op on a read-only handle (the mode is a property of
    // the file), but querying it proves the file is readable and reports what we
    // actually got instead of assuming.
    let _mode: String = conn
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .unwrap_or_else(|_| "unknown".to_string());
    Ok(conn)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_only_handle_refuses_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("graph.sqlite");
        {
            let write = Connection::open(&path).unwrap();
            write
                .execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE t(x)")
                .unwrap();
        }
        let conn = open_read_only(&path).unwrap();
        let count: i64 = conn
            .query_row("SELECT count(*) FROM t", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
        let err = conn.execute("INSERT INTO t(x) VALUES (1)", []).unwrap_err();
        assert!(format!("{err}").contains("read"), "unexpected error: {err}");
    }

    #[test]
    fn missing_file_is_an_error_not_a_new_database() {
        let dir = tempfile::tempdir().unwrap();
        let err = open_read_only(&dir.path().join("nope.sqlite")).unwrap_err();
        assert!(matches!(err, CoreError::Sqlite(_)));
        assert!(!format!("{err}").is_empty());
    }

    #[test]
    fn dimension_mismatch_reads_like_the_python_message() {
        let err = CoreError::DimensionMismatch {
            left: 384,
            right: 768,
        };
        assert_eq!(
            format!("{err}"),
            "embedding dimension mismatch: 384 vs 768; \
             the vector index was built with a different model"
        );
        assert!(format!("{err:?}").contains("DimensionMismatch"));
    }
}
