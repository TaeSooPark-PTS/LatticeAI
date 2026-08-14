//! Pragma parity: a Rust write connection must be configured exactly the way
//! Python configures one, because both open the same file at the same time.
//!
//! The expected values are transcribed from these lines and hardcoded here on
//! purpose — a test that read them back from the same constant would prove
//! nothing:
//!
//! * `lattice_brain/storage/sqlite.py:49` — `conn.execute("PRAGMA journal_mode=WAL")`
//! * `lattice_brain/storage/sqlite.py:50` — `conn.execute("PRAGMA foreign_keys=ON")`
//! * `lattice_brain/storage/sqlite.py:47` — `sqlite3.connect(str(self.db_path))`,
//!   whose `timeout` parameter defaults to 5.0 s and is handed to
//!   `sqlite3_busy_timeout`, i.e. `PRAGMA busy_timeout = 5000`.
//! * nothing sets `synchronous`, so it stays at SQLite's default of 2 (FULL).

use std::path::PathBuf;

use lattice_core::db::{open_read_only, open_read_write, Access, Pool, Store};

/// `PRAGMA journal_mode` — `lattice_brain/storage/sqlite.py:49`.
const PYTHON_JOURNAL_MODE: &str = "wal";
/// `PRAGMA foreign_keys` — `lattice_brain/storage/sqlite.py:50`.
const PYTHON_FOREIGN_KEYS: i64 = 1;
/// `PRAGMA busy_timeout` — the `sqlite3.connect(timeout=5.0)` default applied
/// at `lattice_brain/storage/sqlite.py:47`.
const PYTHON_BUSY_TIMEOUT_MS: i64 = 5_000;
/// `PRAGMA synchronous` — never set by Python, so SQLite's FULL.
const PYTHON_SYNCHRONOUS: i64 = 2;

fn scratch(name: &str) -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join(name);
    (dir, path)
}

fn text(conn: &rusqlite::Connection, pragma: &str) -> String {
    conn.query_row(&format!("PRAGMA {pragma}"), [], |row| row.get(0))
        .unwrap_or_else(|err| panic!("PRAGMA {pragma} failed: {err}"))
}

fn number(conn: &rusqlite::Connection, pragma: &str) -> i64 {
    conn.query_row(&format!("PRAGMA {pragma}"), [], |row| row.get(0))
        .unwrap_or_else(|err| panic!("PRAGMA {pragma} failed: {err}"))
}

#[test]
fn a_write_connection_carries_pythons_settings() {
    let (_dir, path) = scratch("graph.sqlite");
    let conn = open_read_write(&path).expect("write handle");

    assert_eq!(
        text(&conn, "journal_mode"),
        PYTHON_JOURNAL_MODE,
        "SQLiteEngine.connect puts the file in WAL; a Rust writer that left it \
         in the default rollback journal would block every reader for the \
         length of a write"
    );
    assert_eq!(
        number(&conn, "foreign_keys"),
        PYTHON_FOREIGN_KEYS,
        "foreign_keys is per connection, not per file: `edges` declares \
         ON DELETE CASCADE and a Rust writer with enforcement off would leave \
         the orphans Python never leaves"
    );
    assert_eq!(
        number(&conn, "busy_timeout"),
        PYTHON_BUSY_TIMEOUT_MS,
        "a shorter wait than Python's turns a moment of contention with the \
         worker into a 500 the worker itself would have survived"
    );
    assert_eq!(
        number(&conn, "synchronous"),
        PYTHON_SYNCHRONOUS,
        "Python never touches synchronous; a Rust process quietly running \
         NORMAL against the same file would change the product's durability \
         story without anybody deciding to"
    );
}

#[test]
fn the_read_lane_keeps_its_own_shorter_wait() {
    let (_dir, path) = scratch("graph.sqlite");
    drop(open_read_write(&path).expect("create"));
    let conn = open_read_only(&path).expect("read handle");
    assert_eq!(
        number(&conn, "busy_timeout"),
        lattice_core::db::BUSY_TIMEOUT_MS as i64,
        "the read lane is the one that yields; it was 2 s before this WP and \
         stays 2 s"
    );
    assert_eq!(text(&conn, "journal_mode"), PYTHON_JOURNAL_MODE);
}

#[test]
fn every_pooled_write_connection_gets_the_same_settings() {
    // A pool that configured only its first connection would pass the single
    // -handle test above and still hand out an unconfigured one under load.
    let (_dir, path) = scratch("graph.sqlite");
    let pool = Pool::open(&path, Access::Write, 3).expect("pool");
    let a = pool.checkout().expect("first");
    let b = pool.checkout().expect("second");
    let c = pool.checkout().expect("third");
    for conn in [&a, &b, &c] {
        assert_eq!(number(conn, "foreign_keys"), PYTHON_FOREIGN_KEYS);
        assert_eq!(number(conn, "busy_timeout"), PYTHON_BUSY_TIMEOUT_MS);
        assert_eq!(text(conn, "journal_mode"), PYTHON_JOURNAL_MODE);
    }
}

#[test]
fn foreign_keys_are_enforced_through_the_store_helpers() {
    // The pragma value proved above is a number; this proves it is a rule.
    let (_dir, path) = scratch("graph.sqlite");
    let store = Store::open_with_sizes(&path, 1, 1).expect("store");
    store
        .with_write_conn(|conn| {
            conn.execute_batch(
                "CREATE TABLE parent(id TEXT PRIMARY KEY);
                 CREATE TABLE child(id TEXT PRIMARY KEY,
                     parent TEXT NOT NULL REFERENCES parent(id) ON DELETE CASCADE)",
            )?;
            Ok(())
        })
        .expect("schema");

    let orphan = store.with_write_txn(|txn| {
        txn.execute("INSERT INTO child(id, parent) VALUES ('c', 'missing')", [])?;
        Ok(())
    });
    assert!(
        orphan.is_err(),
        "an orphan insert must be refused, not silently accepted"
    );

    store
        .with_write_txn(|txn| {
            txn.execute("INSERT INTO parent(id) VALUES ('p')", [])?;
            txn.execute("INSERT INTO child(id, parent) VALUES ('c', 'p')", [])?;
            Ok(())
        })
        .expect("valid rows");
    store
        .with_write_txn(|txn| {
            txn.execute("DELETE FROM parent WHERE id = 'p'", [])?;
            Ok(())
        })
        .expect("cascade");
    let children = store
        .with_read_conn(|conn| {
            Ok(conn.query_row("SELECT count(*) FROM child", [], |row| row.get::<_, i64>(0))?)
        })
        .expect("count");
    assert_eq!(children, 0, "the cascade must actually cascade");
}
