//! The pool under contention: real threads, a real file, a real busy timeout.
//!
//! Nothing here is mocked, because the two failure modes this pool exists to
//! prevent are both timing: a checkout that hands the same connection to two
//! threads, and a write that gives up on `SQLITE_BUSY` instead of waiting the
//! way Python waits.

use std::path::PathBuf;
use std::sync::mpsc;
use std::sync::Arc;
use std::time::{Duration, Instant};

use lattice_core::db::{open_read_write, Access, CoreError, Pool, Store};

fn scratch(name: &str) -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join(name);
    (dir, path)
}

fn seeded(path: &std::path::Path) -> Store {
    let store = Store::open_with_sizes(path, 2, 2).expect("store");
    store
        .with_write_conn(|conn| {
            conn.execute_batch("CREATE TABLE t(id INTEGER PRIMARY KEY, who TEXT NOT NULL)")?;
            Ok(())
        })
        .expect("schema");
    store
}

#[test]
fn parallel_writers_all_land_exactly_once() {
    let (_dir, path) = scratch("graph.sqlite");
    let store = Arc::new(seeded(&path));

    // Two write connections against one file means real SQLITE_BUSY between
    // them; the busy timeout — not luck — is what makes every insert land.
    let threads: Vec<_> = (0..8)
        .map(|worker| {
            let store = Arc::clone(&store);
            std::thread::spawn(move || {
                for round in 0..10 {
                    store
                        .with_write_txn(|txn| {
                            txn.execute(
                                "INSERT INTO t(who) VALUES (?1)",
                                [format!("w{worker}-{round}")],
                            )?;
                            Ok(())
                        })
                        .expect("a contended write must wait, not fail");
                }
            })
        })
        .collect();
    for thread in threads {
        thread.join().expect("worker thread");
    }

    let (rows, distinct) = store
        .with_read_conn(|conn| {
            Ok(
                conn.query_row("SELECT count(*), count(DISTINCT who) FROM t", [], |row| {
                    Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
                })?,
            )
        })
        .expect("count");
    assert_eq!(rows, 80, "every write must be committed exactly once");
    assert_eq!(distinct, 80, "no write may be lost or duplicated");
}

#[test]
fn a_write_waits_out_a_lock_instead_of_failing() {
    let (_dir, path) = scratch("graph.sqlite");
    let store = seeded(&path);

    // An outside connection — Python, as far as SQLite is concerned — holds the
    // write lock for a beat. Without the 5 s busy timeout this write returns
    // SQLITE_BUSY immediately and the assertion below fails on the error, not
    // on the timing.
    let held = Duration::from_millis(400);
    let (ready, is_ready) = mpsc::channel();
    let outsider = {
        let path = path.clone();
        std::thread::spawn(move || {
            let mut conn = open_read_write(&path).expect("outside handle");
            let txn = conn
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
                .expect("outside transaction");
            txn.execute("INSERT INTO t(who) VALUES ('outsider')", [])
                .expect("outside insert");
            ready.send(()).expect("handshake");
            std::thread::sleep(held);
            txn.commit().expect("outside commit");
        })
    };

    is_ready
        .recv()
        .expect("the outsider must take the lock first");
    let started = Instant::now();
    store
        .with_write_txn(|txn| {
            txn.execute("INSERT INTO t(who) VALUES ('waiter')", [])?;
            Ok(())
        })
        .expect("the write must wait for the lock, not give up on it");
    let waited = started.elapsed();
    outsider.join().expect("outsider thread");

    assert!(
        waited >= held / 2,
        "the write returned in {waited:?}, which is too fast to have waited \
         out a lock held for {held:?} — the busy timeout is not in force"
    );
    let rows = store
        .with_read_conn(|conn| {
            Ok(conn.query_row("SELECT count(*) FROM t", [], |row| row.get::<_, i64>(0))?)
        })
        .expect("count");
    assert_eq!(rows, 2, "both writers' rows must survive");
}

#[test]
fn checkout_blocks_until_a_connection_comes_back() {
    let (_dir, path) = scratch("graph.sqlite");
    drop(open_read_write(&path).expect("create"));
    let pool = Arc::new(Pool::open(&path, Access::Read, 1).expect("pool"));

    let held = pool.checkout().expect("the only connection");
    let (done, is_done) = mpsc::channel();
    let waiter = {
        let pool = Arc::clone(&pool);
        std::thread::spawn(move || {
            let conn = pool.checkout().expect("second checkout");
            let one: i64 = conn
                .query_row("SELECT 1", [], |row| row.get(0))
                .expect("query");
            done.send(one).expect("report");
        })
    };

    assert!(
        is_done.recv_timeout(Duration::from_millis(150)).is_err(),
        "a pool of one must not hand out a second connection while the first \
         is checked out — that would be two threads on one sqlite3 handle \
         opened SQLITE_OPEN_NOMUTEX"
    );
    drop(held);
    assert_eq!(
        is_done
            .recv_timeout(Duration::from_secs(5))
            .expect("the waiter must be woken when the connection returns"),
        1
    );
    waiter.join().expect("waiter thread");
}

#[test]
fn a_panicking_caller_poisons_the_pool_rather_than_hanging_it() {
    // A panic inside `with_write_conn` unwinds through the guard's Drop, which
    // tries to re-lock a mutex the panicking thread may have poisoned. The
    // contract is that the next caller gets an error — never a deadlock.
    let (_dir, path) = scratch("graph.sqlite");
    let pool = Arc::new(Pool::open(&path, Access::Write, 1).expect("pool"));
    let panicking = {
        let pool = Arc::clone(&pool);
        std::thread::spawn(move || {
            let _conn = pool.checkout().expect("checkout");
            panic!("the caller fell over mid-write");
        })
    };
    assert!(panicking.join().is_err(), "the thread must have panicked");

    // Drop ran during the unwind, so the connection is back and the pool works.
    let conn = pool
        .checkout()
        .expect("the pool survives a panicking caller");
    let one: i64 = conn
        .query_row("SELECT 1", [], |row| row.get(0))
        .expect("query");
    assert_eq!(one, 1);
}

#[tokio::test]
async fn the_async_helpers_do_the_same_work_off_the_runtime() {
    let (_dir, path) = scratch("graph.sqlite");
    let store = Arc::new(seeded(&path));

    store
        .write(|txn| {
            txn.execute("INSERT INTO t(who) VALUES ('async')", [])?;
            Ok(())
        })
        .await
        .expect("async write");

    let who: String = store
        .read(|conn| Ok(conn.query_row("SELECT who FROM t", [], |row| row.get(0))?))
        .await
        .expect("async read");
    assert_eq!(who, "async");

    // An error inside the closure is the closure's error, not a task failure.
    let err = store
        .write(|_txn| Err::<(), _>(CoreError::InvalidRequest("no".into())))
        .await
        .unwrap_err();
    assert!(matches!(err, CoreError::InvalidRequest(_)));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_async_writes_serialise_on_the_pool() {
    let (_dir, path) = scratch("graph.sqlite");
    let store = Arc::new(seeded(&path));
    let mut tasks = Vec::new();
    for worker in 0..12 {
        let store = Arc::clone(&store);
        tasks.push(tokio::spawn(async move {
            store
                .write(move |txn| {
                    txn.execute("INSERT INTO t(who) VALUES (?1)", [format!("t{worker}")])?;
                    Ok(())
                })
                .await
        }));
    }
    for task in tasks {
        task.await.expect("join").expect("write");
    }
    let rows = store
        .read(|conn| Ok(conn.query_row("SELECT count(*) FROM t", [], |row| row.get::<_, i64>(0))?))
        .await
        .expect("count");
    assert_eq!(rows, 12);
}
