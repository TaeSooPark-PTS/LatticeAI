use super::tables::{state_files, Owner, GRAPH_DB, TABLES};
use super::*;

fn temp_db(name: &str) -> (tempfile::TempDir, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join(name);
    (dir, path)
}

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
fn an_invalid_request_carries_its_own_message() {
    let err = CoreError::InvalidRequest("graph node not found: x".into());
    assert_eq!(format!("{err}"), "graph node not found: x");
    assert!(format!("{err:?}").contains("InvalidRequest"));
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

#[test]
fn the_new_variants_say_what_went_wrong_in_words() {
    assert_eq!(
        format!("{}", CoreError::Io("no such dir".into())),
        "no such dir"
    );
    assert_eq!(
        format!("{}", CoreError::Runtime("cancelled".into())),
        "cancelled"
    );
    assert!(format!("{:?}", CoreError::Io("x".into())).contains("Io"));
    assert!(format!("{:?}", CoreError::Runtime("x".into())).contains("Runtime"));
}

#[test]
fn a_write_handle_creates_the_directory_python_would_have_created() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("nested/deeper/graph.sqlite");
    let conn = open_read_write(&path).unwrap();
    conn.execute_batch("CREATE TABLE t(x)").unwrap();
    assert!(path.exists());
}

#[test]
fn an_empty_pool_is_refused_rather_than_deadlocking() {
    let (_dir, path) = temp_db("graph.sqlite");
    drop(open_read_write(&path).unwrap());
    let err = Pool::open(&path, Access::Read, 0).unwrap_err();
    assert!(matches!(err, CoreError::InvalidRequest(_)));
}

#[test]
fn a_pool_hands_back_what_it_lent() {
    let (_dir, path) = temp_db("graph.sqlite");
    drop(open_read_write(&path).unwrap());
    let pool = Pool::open(&path, Access::Read, 1).unwrap();
    assert_eq!(pool.capacity(), 1);
    assert_eq!(pool.access(), Access::Read);
    {
        let conn = pool.checkout().unwrap();
        let one: i64 = conn.query_row("SELECT 1", [], |r| r.get(0)).unwrap();
        assert_eq!(one, 1);
    }
    // Same slot, twice, without growing the pool.
    let again = pool.checkout().unwrap();
    assert_eq!(
        again
            .query_row("SELECT 2", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        2
    );
}

#[test]
fn a_write_transaction_rolls_back_on_error() {
    let (_dir, path) = temp_db("graph.sqlite");
    let store = Store::open_with_sizes(&path, 1, 1).unwrap();
    store
        .with_write_conn(|conn| {
            conn.execute_batch("CREATE TABLE t(x INTEGER)")?;
            Ok(())
        })
        .unwrap();
    let err = store
        .with_write_txn(|txn| {
            txn.execute("INSERT INTO t(x) VALUES (1)", [])?;
            Err::<(), _>(CoreError::InvalidRequest("changed my mind".into()))
        })
        .unwrap_err();
    assert!(matches!(err, CoreError::InvalidRequest(_)));
    let count = store
        .with_read_conn(|conn| {
            Ok(conn.query_row("SELECT count(*) FROM t", [], |r| r.get::<_, i64>(0))?)
        })
        .unwrap();
    assert_eq!(count, 0, "the failed transaction must leave nothing behind");
}

#[test]
fn the_store_reports_the_path_it_opened() {
    let (_dir, path) = temp_db("graph.sqlite");
    let store = Store::open(&path).unwrap();
    assert_eq!(store.path(), path);
    assert_eq!(store.readers().capacity(), DEFAULT_READER_POOL);
    assert_eq!(store.writers().capacity(), DEFAULT_WRITER_POOL);
    assert_eq!(store.generation(), 0);
}

fn cell(store: &Store) -> String {
    store
        .with_read_conn(|conn| {
            Ok(conn.query_row("SELECT x FROM t", [], |row| row.get::<_, String>(0))?)
        })
        .unwrap()
}

fn write_snapshot(path: &Path, value: &str) {
    let snap = open_read_write(path).unwrap();
    snap.execute_batch(&format!(
        "CREATE TABLE t(x TEXT); INSERT INTO t VALUES ('{value}')"
    ))
    .unwrap();
}

fn replace_live(live: &Path, snapshot: &Path) {
    let parent = live.parent().unwrap();
    let name = live.file_name().unwrap().to_string_lossy();
    let bak = parent.join(format!("{name}.restore-bak"));
    let _ = std::fs::remove_file(&bak);
    if live.exists() {
        std::fs::rename(live, &bak).unwrap();
    }
    for suffix in ["-wal", "-shm"] {
        let side = PathBuf::from(format!("{}{suffix}", live.display()));
        if side.exists() {
            let _ = std::fs::rename(
                &side,
                PathBuf::from(format!("{}.restore-bak", side.display())),
            );
        }
    }
    std::fs::rename(snapshot, live).unwrap();
    let _ = std::fs::remove_file(&bak);
    for suffix in ["-wal", "-shm"] {
        let _ = std::fs::remove_file(format!("{}{suffix}.restore-bak", live.display()));
    }
}

/// The admitted 11.9.0 restore gap: pooled fds keep the pre-swap inode.
#[test]
fn pooled_handles_keep_pre_restore_bytes_until_the_generation_bumps() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("knowledge_graph.sqlite");
    let store = Store::open_with_sizes(&path, 1, 1).unwrap();
    store
        .with_write_conn(|conn| {
            conn.execute_batch("CREATE TABLE t(x TEXT); INSERT INTO t VALUES ('before')")?;
            Ok(())
        })
        .unwrap();
    assert_eq!(cell(&store), "before");

    let snapshot = dir.path().join("snapshot.sqlite");
    write_snapshot(&snapshot, "after");
    replace_live(&path, &snapshot);

    assert_eq!(
        cell(&store),
        "before",
        "cached handles must still see the pre-restore inode until the epoch advances"
    );
}

/// Production restore: close idle fds, swap (including WAL/SHM), bump.
/// The same `Store` then sees the snapshot — no process restart.
#[test]
fn restore_swap_is_visible_on_the_same_store_without_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("knowledge_graph.sqlite");
    let store = Store::open_with_sizes(&path, 1, 1).unwrap();
    store
        .with_write_conn(|conn| {
            conn.execute_batch("CREATE TABLE t(x TEXT); INSERT INTO t VALUES ('before')")?;
            Ok(())
        })
        .unwrap();
    assert_eq!(cell(&store), "before");

    let snapshot = dir.path().join("snapshot.sqlite");
    write_snapshot(&snapshot, "after");
    store.discard_idle_handles();
    replace_live(&path, &snapshot);
    assert_eq!(store.bump_generation(), 1);
    assert_eq!(store.generation(), 1);
    assert_eq!(
        cell(&store),
        "after",
        "the same Store must reopen onto the swapped file without a process restart"
    );
}

#[test]
fn the_config_follows_pythons_blank_string_rules() {
    let home = Path::new("/Users/x");
    let configured = RuntimeConfig::resolve(
        Some(" /srv/brain "),
        Some("/srv/static"),
        Some("http://127.0.0.1:4826/"),
        Some(home),
    );
    assert_eq!(configured.data_dir(), Path::new("/srv/brain"));
    assert_eq!(configured.static_dir(), Some(Path::new("/srv/static")));
    assert_eq!(configured.worker_origin(), Some("http://127.0.0.1:4826"));
    assert_eq!(
        configured.graph_db_path(),
        Path::new("/srv/brain/knowledge_graph.sqlite")
    );
    assert_eq!(
        configured.state_file(state_files::USERS),
        Path::new("/srv/brain/users.json")
    );

    let blank = RuntimeConfig::resolve(Some("  "), Some(""), Some("   "), Some(home));
    assert_eq!(blank.data_dir(), Path::new("/Users/x/.ltcai"));
    assert_eq!(blank.static_dir(), None);
    assert_eq!(blank.worker_origin(), None);

    // `_value` semantics: the static dir is not stripped, so whitespace is
    // a path rather than "unset" — mirrored deliberately, not overlooked.
    let spaces = RuntimeConfig::resolve(None, Some("   "), None, Some(home));
    assert_eq!(spaces.static_dir(), Some(Path::new("   ")));
}

#[test]
fn config_builders_replace_what_the_environment_did_not_say() {
    let config = RuntimeConfig::resolve(Some("/srv/brain"), None, None, None)
        .with_pool_sizes(3, 2)
        .with_worker_origin("http://127.0.0.1:9000//");
    assert_eq!(config.reader_pool(), 3);
    assert_eq!(config.writer_pool(), 2);
    assert_eq!(config.worker_origin(), Some("http://127.0.0.1:9000"));
    assert_eq!(
        config.with_worker_origin("  ").worker_origin(),
        None,
        "a blank override clears the origin rather than storing an empty one"
    );
}

#[test]
fn from_env_matches_the_pure_resolver() {
    // No env mutation: whatever this process has, both paths must agree.
    let live = RuntimeConfig::from_env();
    let expected = RuntimeConfig::resolve(
        std::env::var(DATA_DIR_ENV).ok().as_deref(),
        std::env::var(STATIC_DIR_ENV).ok().as_deref(),
        std::env::var(WORKER_ORIGIN_ENV).ok().as_deref(),
        std::env::var_os("HOME").map(PathBuf::from).as_deref(),
    );
    assert_eq!(live, expected);
    assert_eq!(RuntimeConfig::default(), live);
}

#[test]
fn the_ownership_map_is_self_consistent() {
    assert!(!TABLES.is_empty());
    for row in TABLES {
        assert_eq!(row.file, GRAPH_DB, "{} names an unknown file", row.table);
        assert!(!row.written_by.is_empty(), "{} has no evidence", row.table);
    }
    let mut seen: Vec<&str> = TABLES.iter().map(|row| row.table).collect();
    seen.sort_unstable();
    let count = seen.len();
    seen.dedup();
    assert_eq!(seen.len(), count, "a table is listed twice");
}

/// v11.6.0 §W3b: the Brain's tables became Rust's, and the projections did
/// not. The test kept its shape — it is still the one that says which half
/// is which — and only the expected owner of the first half moved.
#[test]
fn the_brain_is_rust_writable_and_the_projections_are_not() {
    for table in [
        "nodes",
        "edges",
        "chunks",
        "nodes_v2",
        "edges_v2",
        "vector_embeddings",
        "vector_jobs",
        "ingestion_provenance",
    ] {
        assert_eq!(
            tables::owner_of(table),
            Some(Owner::RustPlatform),
            "{table} is written by graph_write::GraphWriter since §W3b"
        );
        assert!(tables::rust_may_write(table));
    }
    for view in ["kgv2_nodes", "kgv2_edges", "node_fts"] {
        assert_eq!(tables::owner_of(view), Some(Owner::SharedRead));
        assert!(!tables::rust_may_write(view));
    }
    for platform in [
        "workspace_os_state",
        "workspace_os_meta",
        "conversation_messages",
    ] {
        assert!(tables::rust_may_write(platform), "{platform} is Rust's");
    }
    // An unknown table is refused rather than assumed harmless.
    assert_eq!(tables::owner_of("something_new"), None);
    assert!(!tables::rust_may_write("something_new"));
    // Everything but the three projections: one writer for one file.
    assert_eq!(tables::rust_owned().count(), TABLES.len() - 3);
}

#[test]
fn state_files_carry_the_same_verdicts() {
    assert_eq!(
        state_files::owner_of(state_files::USERS),
        Some(Owner::RustPlatform)
    );
    assert_eq!(
        state_files::owner_of(state_files::KNOWLEDGE_GRAPH_BLOBS),
        Some(Owner::RustPlatform)
    );
    assert_eq!(
        state_files::owner_of(state_files::TELEGRAM_CHATS),
        Some(Owner::Worker),
        "the telegram allowlist is the last thing a Python process writes"
    );
    assert_eq!(state_files::owner_of("nothing.json"), None);
    for row in state_files::STATE_FILES {
        assert!(!row.name.is_empty());
        assert!(!row.written_by.is_empty(), "{} has no evidence", row.name);
    }
    assert_eq!(Owner::RustPlatform.as_str(), "RUST_PLATFORM");
    assert_eq!(Owner::Worker.as_str(), "WORKER");
    assert_eq!(Owner::SharedRead.as_str(), "SHARED_READ");
}
