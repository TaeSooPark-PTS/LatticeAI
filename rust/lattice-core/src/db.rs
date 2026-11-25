use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::paths::{resolve_data_dir, DATA_DIR_ENV, DB_FILE_NAME};

pub const BUSY_TIMEOUT_MS: u32 = 2_000;

pub const WRITE_BUSY_TIMEOUT_MS: u32 = 5_000;

pub const DEFAULT_READER_POOL: usize = 4;

pub const DEFAULT_WRITER_POOL: usize = 1;

pub const STATIC_DIR_ENV: &str = "LATTICEAI_STATIC_DIR";

pub const WORKER_ORIGIN_ENV: &str = "LATTICEAI_WORKER_ORIGIN";

#[derive(Debug)]
pub enum CoreError {
    Sqlite(rusqlite::Error),
    DimensionMismatch { left: usize, right: usize },
    InvalidRequest(String),
    Io(String),
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

pub fn open_read_only(path: &Path) -> Result<Connection, CoreError> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))?;
    let _mode: String = conn
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .unwrap_or_else(|_| "unknown".to_string());
    Ok(conn)
}

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
    let _mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |row| row.get(0))?;
    conn.execute_batch("PRAGMA foreign_keys=ON")?;
    Ok(conn)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    Read,
    Write,
}

#[derive(Debug)]
struct Slot {
    conn: Connection,
    generation: u64,
}

#[derive(Debug)]
struct PoolInner {
    slots: Vec<Slot>,
    outstanding: usize,
}

#[derive(Debug)]
pub struct Pool {
    inner: Mutex<PoolInner>,
    free: Condvar,
    capacity: usize,
    access: Access,
    path: PathBuf,
    generation: Arc<AtomicU64>,
}

impl Pool {
    pub fn open(path: &Path, access: Access, capacity: usize) -> Result<Self, CoreError> {
        Self::open_with_generation(path, access, capacity, Arc::new(AtomicU64::new(0)))
    }

    fn open_with_generation(
        path: &Path,
        access: Access,
        capacity: usize,
        generation: Arc<AtomicU64>,
    ) -> Result<Self, CoreError> {
        if capacity == 0 {
            return Err(CoreError::InvalidRequest(
                "a connection pool needs at least one connection".into(),
            ));
        }
        let current = generation.load(Ordering::Acquire);
        let mut slots = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            slots.push(Slot {
                conn: match access {
                    Access::Read => open_read_only(path)?,
                    Access::Write => open_read_write(path)?,
                },
                generation: current,
            });
        }
        Ok(Self {
            inner: Mutex::new(PoolInner {
                slots,
                outstanding: 0,
            }),
            free: Condvar::new(),
            capacity,
            access,
            path: path.to_path_buf(),
            generation,
        })
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    pub fn access(&self) -> Access {
        self.access
    }

    fn discard_idle(&self) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.slots.clear();
        }
    }

    fn open_fresh(&self) -> Result<Connection, CoreError> {
        match self.access {
            Access::Read => open_read_only(&self.path),
            Access::Write => open_read_write(&self.path),
        }
    }

    fn take_fresh(&self, generation: u64) -> Result<PooledConnection<'_>, CoreError> {
        match self.open_fresh() {
            Ok(conn) => Ok(PooledConnection {
                pool: self,
                conn: Some(conn),
                generation,
            }),
            Err(err) => {
                if let Ok(mut inner) = self.inner.lock() {
                    inner.outstanding = inner.outstanding.saturating_sub(1);
                }
                self.free.notify_one();
                Err(err)
            }
        }
    }

    pub fn checkout(&self) -> Result<PooledConnection<'_>, CoreError> {
        let current = self.generation.load(Ordering::Acquire);
        let mut inner = self.inner.lock().map_err(|_| poisoned())?;
        loop {
            if let Some(slot) = inner.slots.pop() {
                if slot.generation == current {
                    inner.outstanding += 1;
                    return Ok(PooledConnection {
                        pool: self,
                        conn: Some(slot.conn),
                        generation: current,
                    });
                }
                inner.outstanding += 1;
                drop(inner);
                drop(slot.conn);
                return self.take_fresh(current);
            }
            if inner.outstanding < self.capacity {
                inner.outstanding += 1;
                drop(inner);
                return self.take_fresh(current);
            }
            inner = self.free.wait(inner).map_err(|_| poisoned())?;
        }
    }
}

fn poisoned() -> CoreError {
    CoreError::Runtime("the connection pool lock was poisoned by a panicking caller".into())
}

#[derive(Debug)]
pub struct PooledConnection<'pool> {
    pool: &'pool Pool,
    conn: Option<Connection>,
    generation: u64,
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
            if let Ok(mut inner) = self.pool.inner.lock() {
                let current = self.pool.generation.load(Ordering::Acquire);
                if self.generation == current {
                    inner.slots.push(Slot {
                        conn,
                        generation: self.generation,
                    });
                }
                inner.outstanding = inner.outstanding.saturating_sub(1);
            }
            self.pool.free.notify_one();
        }
    }
}

#[derive(Debug)]
pub struct Store {
    path: PathBuf,
    readers: Pool,
    writers: Pool,
    generation: Arc<AtomicU64>,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self, CoreError> {
        Self::open_with_sizes(path, DEFAULT_READER_POOL, DEFAULT_WRITER_POOL)
    }

    pub fn open_with_sizes(path: &Path, readers: usize, writers: usize) -> Result<Self, CoreError> {
        let generation = Arc::new(AtomicU64::new(0));
        let writers =
            Pool::open_with_generation(path, Access::Write, writers, Arc::clone(&generation))?;
        let readers =
            Pool::open_with_generation(path, Access::Read, readers, Arc::clone(&generation))?;
        Ok(Self {
            path: path.to_path_buf(),
            readers,
            writers,
            generation,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::Acquire)
    }

    pub fn discard_idle_handles(&self) {
        self.readers.discard_idle();
        self.writers.discard_idle();
    }

    pub fn bump_generation(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::AcqRel) + 1
    }

    pub fn readers(&self) -> &Pool {
        &self.readers
    }

    pub fn writers(&self) -> &Pool {
        &self.writers
    }

    pub fn with_read_conn<T, F>(&self, work: F) -> Result<T, CoreError>
    where
        F: FnOnce(&Connection) -> Result<T, CoreError>,
    {
        let conn = self.readers.checkout()?;
        work(&conn)
    }

    pub fn with_write_conn<T, F>(&self, work: F) -> Result<T, CoreError>
    where
        F: FnOnce(&mut Connection) -> Result<T, CoreError>,
    {
        let mut conn = self.writers.checkout()?;
        work(&mut conn)
    }

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

    pub async fn write_conn<T, F>(self: &Arc<Self>, work: F) -> Result<T, CoreError>
    where
        T: Send + 'static,
        F: FnOnce(&mut Connection) -> Result<T, CoreError> + Send + 'static,
    {
        let store = Arc::clone(self);
        tokio::task::spawn_blocking(move || store.with_write_conn(work))
            .await
            .map_err(|err| CoreError::Runtime(format!("blocking write_conn task failed: {err}")))?
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeConfig {
    data_dir: PathBuf,
    static_dir: Option<PathBuf>,
    worker_origin: Option<String>,
    reader_pool: usize,
    writer_pool: usize,
}

impl RuntimeConfig {
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

    pub fn with_pool_sizes(mut self, readers: usize, writers: usize) -> Self {
        self.reader_pool = readers;
        self.writer_pool = writers;
        self
    }

    pub fn with_worker_origin(mut self, origin: impl AsRef<str>) -> Self {
        let origin = origin.as_ref().trim().trim_end_matches('/');
        self.worker_origin = (!origin.is_empty()).then(|| origin.to_string());
        self
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub fn graph_db_path(&self) -> PathBuf {
        self.data_dir.join(DB_FILE_NAME)
    }

    pub fn state_file(&self, name: &str) -> PathBuf {
        self.data_dir.join(name)
    }

    pub fn static_dir(&self) -> Option<&Path> {
        self.static_dir.as_deref()
    }

    pub fn worker_origin(&self) -> Option<&str> {
        self.worker_origin.as_deref()
    }

    pub fn reader_pool(&self) -> usize {
        self.reader_pool
    }

    pub fn writer_pool(&self) -> usize {
        self.writer_pool
    }

    pub fn open_store(&self) -> Result<Store, CoreError> {
        Store::open_with_sizes(&self.graph_db_path(), self.reader_pool, self.writer_pool)
    }
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self::from_env()
    }
}

pub mod tables;

#[cfg(test)]
mod tests;
