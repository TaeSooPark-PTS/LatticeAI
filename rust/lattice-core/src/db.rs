//! SQLite access to the shared stores: the read lane, the write lane, and the
//! map that says which side of the seam a table lives on.
//!
//! Until v11.6.0 this module was read-only by construction — Python owned every
//! write, and `open_read_only` made that a property of the connection rather
//! than a promise in a comment. One Door splits that ownership rather than
//! removing it:
//!
//! * **Rust owns platform state** — users/sessions, workspaces, review queue,
//!   proposal metadata, settings, jobs bookkeeping, history. Those tables and
//!   files are written here, through [`Store`].
//! * **The worker stays the single writer of the Brain** — nodes, edges,
//!   chunks, vectors, provenance, ingestion. A Rust route that must change the
//!   graph delegates over the seam (`POST /agent/tool`,
//!   `POST /knowledge-graph/ingest`) instead of opening a write connection.
//!
//! [`tables`] is that split written down, table by table, with the Python file
//! that writes each one. Wave-2 packages follow it rather than guessing, and
//! `tests/db_write_ownership.rs` checks it against the committed fixture store
//! so a table that appears in the product cannot quietly go unclassified.
//!
//! The pragmas on a write connection are Python's, not ours — see
//! [`open_read_write`].

use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::paths::{resolve_data_dir, DATA_DIR_ENV, DB_FILE_NAME};

/// Milliseconds a read waits for a writer's lock before giving up.
pub const BUSY_TIMEOUT_MS: u32 = 2_000;

/// Milliseconds a **write** waits for another writer's lock.
///
/// 5 000 is not a number this crate chose: `sqlite3.connect()` defaults to
/// `timeout=5.0` and CPython feeds that straight to `sqlite3_busy_timeout`, so
/// every Python writer in the product — `lattice_brain/storage/sqlite.py:47`,
/// `lattice_brain/graph/schema.py:504`, `latticeai/core/workspace_os.py:127` —
/// already waits exactly this long. A Rust writer that gave up sooner would
/// turn a busy moment into a 500 that Python survives.
pub const WRITE_BUSY_TIMEOUT_MS: u32 = 5_000;

/// Read connections a [`Store`] keeps open by default.
///
/// Small on purpose: SQLite readers are cheap but not free, and the native
/// lanes are short blocking calls dispatched onto tokio's blocking pool. Four
/// keeps a handful of concurrent requests off each other's backs without
/// holding a descriptor per in-flight connection.
pub const DEFAULT_READER_POOL: usize = 4;

/// Write connections a [`Store`] keeps open by default.
///
/// One, because SQLite has one write lock per database. A second connection
/// buys nothing but `SQLITE_BUSY` retries against ourselves; serialising on the
/// pool instead means the busy timeout is reserved for the writer we do not
/// control — the Python worker.
pub const DEFAULT_WRITER_POOL: usize = 1;

/// `LATTICEAI_STATIC_DIR` — the SPA/asset root, read by
/// `latticeai/core/config.py:225`.
pub const STATIC_DIR_ENV: &str = "LATTICEAI_STATIC_DIR";

/// `LATTICEAI_WORKER_ORIGIN` — where the AI worker answers.
///
/// Python does not read this: in Python's world it *is* the server. It exists
/// so a Rust process that was not started by the supervisor (a test, a CLI, a
/// Wave-2 crate exercised on its own) can still find the seam. The supervised
/// path does not use it — `lattice-host` knows the port it spawned and passes
/// the origin in directly.
pub const WORKER_ORIGIN_ENV: &str = "LATTICEAI_WORKER_ORIGIN";

/// Every failure this crate can hand back to a caller.
#[derive(Debug)]
pub enum CoreError {
    /// A SQLite call failed (missing table, locked database, corrupt file …).
    Sqlite(rusqlite::Error),
    /// Two vectors of different lengths were compared — see
    /// [`crate::embeddings::LocalEmbeddingModel::similarity`].
    DimensionMismatch { left: usize, right: usize },
    /// The caller asked for something the ported Python raises `ValueError`
    /// for: a blank node id, or a seed the caller's workspace scope cannot see.
    ///
    /// Separate from [`CoreError::Sqlite`] because it is not a failure of the
    /// store — it is the answer, and the routes turn it into a 4xx rather than
    /// a 500.
    InvalidRequest(String),
    /// The filesystem refused something the store needed — creating the data
    /// directory, most often. Distinct from [`CoreError::Sqlite`] because
    /// nothing about the database was wrong; the path was.
    Io(String),
    /// The process could not carry out the work: a poisoned pool mutex, or a
    /// blocking task that was cancelled out from under an async caller.
    Runtime(String),
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
            CoreError::InvalidRequest(message) => f.write_str(message),
            CoreError::Io(message) => f.write_str(message),
            CoreError::Runtime(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for CoreError {}

impl From<rusqlite::Error> for CoreError {
    fn from(err: rusqlite::Error) -> Self {
        CoreError::Sqlite(err)
    }
}

impl CoreError {
    /// Whether this is SQLite saying `no such table: {table}`.
    ///
    /// Readers use it to answer empty rather than 500 on a store that has
    /// never been written by the table's owner (a fresh Brain has no
    /// `conversation_messages` until the first chat turn, unless bootstrap
    /// created it).
    pub fn is_missing_table(&self, table: &str) -> bool {
        match self {
            CoreError::Sqlite(err) => {
                let text = err.to_string();
                text.contains("no such table") && text.contains(table)
            }
            _ => false,
        }
    }
}

/// Open the graph database read-only, in WAL, with a bounded busy timeout.
///
/// `SQLITE_OPEN_READ_ONLY` is the load-bearing part: a read lane must not be
/// able to migrate, vacuum, or otherwise mutate a store, and a read-only handle
/// makes that a property of the connection rather than a promise in a comment.
/// The write lane is [`open_read_write`], and which tables it may legitimately
/// touch is [`tables`].
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

/// Open a store read/write with **Python's** connection settings.
///
/// The three settings are transcribed, not chosen:
///
/// | pragma | value | Python source |
/// |---|---|---|
/// | `journal_mode` | `wal` | `lattice_brain/storage/sqlite.py:49` |
/// | `foreign_keys` | `1` | `lattice_brain/storage/sqlite.py:50` |
/// | `busy_timeout` | `5000` | `sqlite3.connect(timeout=5.0)` default, applied at `lattice_brain/storage/sqlite.py:47` |
///
/// Nothing else is set. `synchronous` in particular stays at SQLite's default
/// (`FULL`) because Python never touches it, and a Rust process quietly running
/// `NORMAL` against the same file would change the product's durability story
/// without anybody deciding to.
///
/// `SQLITE_OPEN_CREATE` and the parent `create_dir_all` mirror Python too:
/// `SQLiteEngine.connect` makes the directory and `sqlite3.connect` makes the
/// file, so a first run on a fresh machine works the same from either runtime.
pub fn open_read_write(path: &Path) -> Result<Connection, CoreError> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent).map_err(|err| {
                CoreError::Io(format!(
                    "cannot create the data directory {}: {err}",
                    parent.display()
                ))
            })?;
        }
    }
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.busy_timeout(Duration::from_millis(WRITE_BUSY_TIMEOUT_MS as u64))?;
    // `PRAGMA journal_mode=WAL` answers with the mode it settled on, so it is a
    // query rather than a statement; `execute` would refuse it for returning a
    // row. The answer is discarded for the same reason Python discards it — a
    // filesystem that cannot do WAL (some network mounts) still has to work.
    let _mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |row| row.get(0))?;
    conn.execute_batch("PRAGMA foreign_keys=ON")?;
    Ok(conn)
}

/// Which lane a pooled connection belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    /// Opened by [`open_read_only`].
    Read,
    /// Opened by [`open_read_write`].
    Write,
}

/// A fixed set of open connections, handed out one at a time.
///
/// Rolled by hand rather than pulled from `r2d2`: the whole requirement is
/// "block until a connection is free", which is a `Mutex<Vec<_>>` and a
/// `Condvar`. A pool crate would add a dependency, a runtime, and a
/// health-check policy this workspace has no use for.
///
/// Connections are opened eagerly at construction so a bad path fails at
/// startup rather than on the first request that needed the store.
#[derive(Debug)]
pub struct Pool {
    slots: Mutex<Vec<Connection>>,
    free: Condvar,
    capacity: usize,
    access: Access,
}

impl Pool {
    /// Open `capacity` connections in `access` mode against `path`.
    pub fn open(path: &Path, access: Access, capacity: usize) -> Result<Self, CoreError> {
        if capacity == 0 {
            return Err(CoreError::InvalidRequest(
                "a connection pool needs at least one connection".into(),
            ));
        }
        let mut slots = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            slots.push(match access {
                Access::Read => open_read_only(path)?,
                Access::Write => open_read_write(path)?,
            });
        }
        Ok(Self {
            slots: Mutex::new(slots),
            free: Condvar::new(),
            capacity,
            access,
        })
    }

    /// How many connections this pool owns.
    pub fn capacity(&self) -> usize {
        self.capacity
    }

    /// Which lane this pool opened.
    pub fn access(&self) -> Access {
        self.access
    }

    /// Take a connection, **blocking** until one is free.
    ///
    /// Blocking is why every caller inside an axum handler goes through
    /// `spawn_blocking` (or the async helpers on [`Store`]): holding a tokio
    /// worker thread here would starve the runtime the moment the pool is busy.
    pub fn checkout(&self) -> Result<PooledConnection<'_>, CoreError> {
        let mut slots = self.slots.lock().map_err(|_| poisoned())?;
        loop {
            if let Some(conn) = slots.pop() {
                return Ok(PooledConnection {
                    pool: self,
                    conn: Some(conn),
                });
            }
            slots = self.free.wait(slots).map_err(|_| poisoned())?;
        }
    }
}

fn poisoned() -> CoreError {
    CoreError::Runtime("the connection pool lock was poisoned by a panicking caller".into())
}

/// A connection borrowed from a [`Pool`], returned when it drops.
#[derive(Debug)]
pub struct PooledConnection<'pool> {
    pool: &'pool Pool,
    // `Option` only so `Drop` can move the connection back out; it is `Some`
    // for the whole observable life of the guard.
    conn: Option<Connection>,
}

impl std::ops::Deref for PooledConnection<'_> {
    type Target = Connection;

    fn deref(&self) -> &Connection {
        self.conn.as_ref().expect("pooled connection taken twice")
    }
}

impl std::ops::DerefMut for PooledConnection<'_> {
    fn deref_mut(&mut self) -> &mut Connection {
        self.conn.as_mut().expect("pooled connection taken twice")
    }
}

impl Drop for PooledConnection<'_> {
    fn drop(&mut self) {
        if let Some(conn) = self.conn.take() {
            // A poisoned lock drops the connection instead of returning it: the
            // pool is already unusable (every `checkout` errors), so shrinking
            // it changes nothing a caller can observe.
            if let Ok(mut slots) = self.pool.slots.lock() {
                slots.push(conn);
            }
            self.pool.free.notify_one();
        }
    }
}

/// One database, one read pool, one write pool.
///
/// This is what a Wave-2 crate holds (behind an `Arc`, in its router state).
/// The read pool exists so ported read routes stop paying a fresh
/// `sqlite3_open` per request; the write pool exists so the tables Rust owns
/// have exactly one door.
///
/// It does **not** enforce [`tables`] — SQLite has no per-table permissions and
/// a check here would be a string match a caller could route around. The map is
/// the contract; this is the connection.
#[derive(Debug)]
pub struct Store {
    path: PathBuf,
    readers: Pool,
    writers: Pool,
}

impl Store {
    /// Open a store with the default pool sizes.
    pub fn open(path: &Path) -> Result<Self, CoreError> {
        Self::open_with_sizes(path, DEFAULT_READER_POOL, DEFAULT_WRITER_POOL)
    }

    /// Open a store with explicit pool sizes.
    ///
    /// The write pool is opened first: it carries `SQLITE_OPEN_CREATE`, so on a
    /// machine whose Brain has never been built it is what brings the file into
    /// existence for the read-only handles that follow.
    pub fn open_with_sizes(path: &Path, readers: usize, writers: usize) -> Result<Self, CoreError> {
        let writers = Pool::open(path, Access::Write, writers)?;
        let readers = Pool::open(path, Access::Read, readers)?;
        Ok(Self {
            path: path.to_path_buf(),
            readers,
            writers,
        })
    }

    /// The file this store is open against.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The read pool, for callers that want to hold a connection themselves.
    pub fn readers(&self) -> &Pool {
        &self.readers
    }

    /// The write pool, for callers that want to hold a connection themselves.
    pub fn writers(&self) -> &Pool {
        &self.writers
    }

    /// Run `work` against a read-only connection. **Blocks** on a busy pool.
    pub fn with_read_conn<T, F>(&self, work: F) -> Result<T, CoreError>
    where
        F: FnOnce(&Connection) -> Result<T, CoreError>,
    {
        let conn = self.readers.checkout()?;
        work(&conn)
    }

    /// Run `work` against a read/write connection in autocommit mode.
    /// **Blocks** on a busy pool.
    ///
    /// `&mut Connection` so the caller can open its own transaction with the
    /// behaviour it needs; [`Store::with_write_txn`] is the common case.
    pub fn with_write_conn<T, F>(&self, work: F) -> Result<T, CoreError>
    where
        F: FnOnce(&mut Connection) -> Result<T, CoreError>,
    {
        let mut conn = self.writers.checkout()?;
        work(&mut conn)
    }

    /// Run `work` inside one transaction: commit on `Ok`, roll back on `Err`.
    ///
    /// This is Python's `with conn:` — the semantics every platform-state
    /// writer in `latticeai/` already has — with one stated difference. Python's
    /// `sqlite3` opens a *deferred* transaction; this opens an **immediate**
    /// one. Deferred is safe for Python only because its write blocks start
    /// with the write, so the lock is taken on the first statement anyway. A
    /// Rust caller that reads before it writes would, under a deferred begin,
    /// hit `SQLITE_BUSY_SNAPSHOT` on the upgrade — and that error is *not*
    /// retried by the busy handler, so the 5 s timeout would not save it.
    /// Taking the write lock up front makes the wait a wait rather than a
    /// failure.
    pub fn with_write_txn<T, F>(&self, work: F) -> Result<T, CoreError>
    where
        F: FnOnce(&Transaction<'_>) -> Result<T, CoreError>,
    {
        let mut conn = self.writers.checkout()?;
        let txn = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let value = work(&txn)?;
        txn.commit()?;
        Ok(value)
    }

    /// [`Store::with_read_conn`] on tokio's blocking pool.
    ///
    /// The shape axum handlers want: `state.store.read(|conn| …).await?`.
    pub async fn read<T, F>(self: &Arc<Self>, work: F) -> Result<T, CoreError>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> Result<T, CoreError> + Send + 'static,
    {
        let store = Arc::clone(self);
        tokio::task::spawn_blocking(move || store.with_read_conn(work))
            .await
            .map_err(|err| CoreError::Runtime(format!("blocking read task failed: {err}")))?
    }

    /// [`Store::with_write_txn`] on tokio's blocking pool.
    pub async fn write<T, F>(self: &Arc<Self>, work: F) -> Result<T, CoreError>
    where
        T: Send + 'static,
        F: FnOnce(&Transaction<'_>) -> Result<T, CoreError> + Send + 'static,
    {
        let store = Arc::clone(self);
        tokio::task::spawn_blocking(move || store.with_write_txn(work))
            .await
            .map_err(|err| CoreError::Runtime(format!("blocking write task failed: {err}")))?
    }
}

/// Where this process's durable state lives, resolved once.
///
/// Every Wave-2 crate takes one of these rather than reading the environment at
/// its own call sites. That is not tidiness: `latticeai/core/config.py:60`
/// carries a comment about three runtime modules each holding a byte-identical
/// copy of the data-directory rule, and how the loser of that disagreement
/// writes a second, invisible copy of the user's data. The Rust side gets one
/// resolver for the same reason.
///
/// It deliberately does **not** resolve the agent workspace root: that already
/// has an owner in `lattice_host::gateway::mounts::resolve_agent_root`, whose
/// three-step order (`LATTICEAI_AGENT_ROOT` → the desktop runtime dir →
/// Python's relative `agent_workspace`) is more than an env read, and a second
/// implementation here would be exactly the divergence the paragraph above
/// warns about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeConfig {
    data_dir: PathBuf,
    static_dir: Option<PathBuf>,
    worker_origin: Option<String>,
    reader_pool: usize,
    writer_pool: usize,
}

impl RuntimeConfig {
    /// Resolve from values the caller already read.
    ///
    /// Pure, so a test can pin the rules without mutating process-global
    /// environment state — the kind of test that breaks under a parallel
    /// harness. Blank-string handling is Python's, per variable:
    /// `LATTICEAI_DATA_DIR` is stripped before the emptiness check
    /// (`config.py:70`), while `LATTICEAI_STATIC_DIR` goes through `_value`
    /// (`config.py:76`), which is `or`-based and therefore treats `""` as unset
    /// but keeps `"   "` as a real, if unwise, path.
    pub fn resolve(
        data_dir_env: Option<&str>,
        static_dir_env: Option<&str>,
        worker_origin_env: Option<&str>,
        home: Option<&Path>,
    ) -> Self {
        Self {
            data_dir: resolve_data_dir(data_dir_env, home),
            static_dir: static_dir_env.filter(|v| !v.is_empty()).map(PathBuf::from),
            worker_origin: worker_origin_env
                .map(str::trim)
                .filter(|v| !v.is_empty())
                .map(|v| v.trim_end_matches('/').to_string()),
            reader_pool: DEFAULT_READER_POOL,
            writer_pool: DEFAULT_WRITER_POOL,
        }
    }

    /// Resolve from this process's environment.
    pub fn from_env() -> Self {
        let data_dir = std::env::var(DATA_DIR_ENV).ok();
        let static_dir = std::env::var(STATIC_DIR_ENV).ok();
        let worker_origin = std::env::var(WORKER_ORIGIN_ENV).ok();
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .filter(|p| !p.as_os_str().is_empty());
        Self::resolve(
            data_dir.as_deref(),
            static_dir.as_deref(),
            worker_origin.as_deref(),
            home.as_deref(),
        )
    }

    /// Override the pool sizes [`RuntimeConfig::open_store`] will use.
    pub fn with_pool_sizes(mut self, readers: usize, writers: usize) -> Self {
        self.reader_pool = readers;
        self.writer_pool = writers;
        self
    }

    /// Point this config at a worker origin the environment did not name —
    /// what `lattice-host` does with the port it spawned.
    pub fn with_worker_origin(mut self, origin: impl AsRef<str>) -> Self {
        let origin = origin.as_ref().trim().trim_end_matches('/');
        self.worker_origin = (!origin.is_empty()).then(|| origin.to_string());
        self
    }

    /// `LATTICEAI_DATA_DIR`, else `~/.ltcai`.
    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    /// The one SQLite file the product uses — see [`tables`].
    pub fn graph_db_path(&self) -> PathBuf {
        self.data_dir.join(DB_FILE_NAME)
    }

    /// A JSON/JSONL store under the data directory, by name.
    ///
    /// Use the constants in [`tables::state_files`] rather than a literal, so
    /// the ownership map and the call site cannot drift apart.
    pub fn state_file(&self, name: &str) -> PathBuf {
        self.data_dir.join(name)
    }

    /// `LATTICEAI_STATIC_DIR`, when the operator set it.
    ///
    /// `None` is not "no static directory": Python falls back to the repo's
    /// `static/` and then to `sys.prefix/static` (`config.py:225-229`), a chain
    /// that depends on how the package was installed. WP-I4 owns that fallback
    /// because it owns the serving; this only reports what was configured.
    pub fn static_dir(&self) -> Option<&Path> {
        self.static_dir.as_deref()
    }

    /// Where the AI worker answers, when anything named it.
    pub fn worker_origin(&self) -> Option<&str> {
        self.worker_origin.as_deref()
    }

    /// Read connections [`RuntimeConfig::open_store`] will open.
    pub fn reader_pool(&self) -> usize {
        self.reader_pool
    }

    /// Write connections [`RuntimeConfig::open_store`] will open.
    pub fn writer_pool(&self) -> usize {
        self.writer_pool
    }

    /// Open the graph store with this config's pool sizes.
    pub fn open_store(&self) -> Result<Store, CoreError> {
        Store::open_with_sizes(&self.graph_db_path(), self.reader_pool, self.writer_pool)
    }
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self::from_env()
    }
}

/// `lattice_brain/graph/schema.py`, `lattice_brain/graph/projection/`,
/// `lattice_brain/graph/vector_index/jobs.py`,
/// `lattice_brain/graph/image_vectors.py`, `lattice_brain/ingestion_jobs.py`,
/// `lattice_brain/conversations.py`, `lattice_brain/storage/sqlite.py`,
/// `latticeai/core/workspace_os.py`, plus the live schema of a built Brain and
/// of `rust/fixtures/parity_store.sqlite`.
///
/// **There is exactly one SQLite file.** Everything else durable is JSON or
/// JSONL under the data directory — see [`state_files`].
pub mod tables;

#[cfg(test)]
mod tests;
