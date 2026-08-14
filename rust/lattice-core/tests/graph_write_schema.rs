//! Schema ownership: a fresh Rust install and a live Python Brain agree.
//!
//! Two claims, and they are the same claim seen from either end.
//!
//! * A **fresh Rust-only install** must create a schema a Python build would
//!   accept. `CREATE TABLE IF NOT EXISTS` never upgrades an existing table, so
//!   the only thing that makes that true is the DDL text itself matching — down
//!   to the implicit indexes SQLite derives from `PRIMARY KEY` and `UNIQUE`,
//!   which is why the comparison is against `sqlite_master` rather than against
//!   a hand-written list of column names.
//! * An **existing Python-created database** must open and be written with no
//!   migration at all: same objects afterwards, same rows, same version stamps.
//!   `rust/fixtures/parity_store.sqlite` is a real Brain built by the Python
//!   write path, which is what makes the second test evidence rather than a
//!   restatement of the first.

use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::graph_write::{GraphWriter, SystemClock};
use serde_json::Value;

fn fixture(name: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join(name)
}

fn schema_master(store: &Store) -> Value {
    store
        .with_read_conn(|conn| {
            let mut statement = conn.prepare(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name",
            )?;
            let rows = statement.query_map([], |row| {
                Ok(serde_json::json!({
                    "type": row.get::<_, String>(0)?,
                    "name": row.get::<_, String>(1)?,
                    "tbl_name": row.get::<_, String>(2)?,
                    "sql": row.get::<_, Option<String>>(3)?,
                }))
            })?;
            let objects: Vec<Value> = rows.collect::<Result<Vec<_>, _>>()?;
            let user_version: i64 = conn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
            Ok(serde_json::json!({"objects": objects, "user_version": user_version}))
        })
        .expect("read sqlite_master")
}

fn open_writer(path: &std::path::Path) -> (Arc<Store>, GraphWriter) {
    let store = Arc::new(Store::open(path).expect("open the store"));
    let writer = GraphWriter::with_parts(
        Arc::clone(&store),
        path.parent().unwrap().join("knowledge_graph_blobs"),
        LocalEmbeddingModel::new(384),
        Arc::new(SystemClock),
    )
    .expect("open the writer");
    (store, writer)
}

#[test]
fn a_fresh_rust_install_creates_the_schema_python_emits() {
    std::env::set_var("LATTICEAI_EMBED_DIM", "1024");
    let expected: Value = serde_json::from_str(
        &std::fs::read_to_string(fixture("graph_write/schema_master.json"))
            .expect("read the schema fixture"),
    )
    .expect("parse the schema fixture");

    let work = tempfile::tempdir().expect("temp dir");
    let (store, _writer) = open_writer(&work.path().join("knowledge_graph.sqlite"));
    let actual = schema_master(&store);

    // Object by object, so a divergence names the object rather than printing
    // sixty-seven of them.
    let expected_objects = expected["objects"].as_array().expect("expected objects");
    let actual_objects = actual["objects"].as_array().expect("actual objects");
    for object in expected_objects {
        let name = object["name"].as_str().expect("a name");
        let found = actual_objects
            .iter()
            .find(|candidate| candidate["name"] == object["name"])
            .unwrap_or_else(|| panic!("the fresh Rust store is missing `{name}`"));
        assert_eq!(found, object, "`{name}` diverged from Python's DDL");
    }
    for object in actual_objects {
        let name = object["name"].as_str().expect("a name");
        assert!(
            expected_objects
                .iter()
                .any(|candidate| candidate["name"] == object["name"]),
            "the fresh Rust store created `{name}`, which Python does not"
        );
    }
    assert_eq!(
        actual["user_version"], expected["user_version"],
        "the db format stamp diverged"
    );
}

#[test]
fn opening_a_python_built_brain_migrates_nothing() {
    std::env::set_var("LATTICEAI_EMBED_DIM", "1024");
    let work = tempfile::tempdir().expect("temp dir");
    let path = work.path().join("knowledge_graph.sqlite");
    // A copy, so the committed fixture is never the thing under test.
    std::fs::copy(fixture("parity_store.sqlite"), &path).expect("copy the fixture store");

    let before = {
        let store = Store::open(&path).expect("open the store");
        (schema_master(&store), row_counts(&store))
    };
    let (store, writer) = open_writer(&path);
    let after = (schema_master(&store), row_counts(&store));

    assert_eq!(
        after.0, before.0,
        "opening a Python-built Brain changed the schema"
    );
    assert_eq!(
        after.1, before.1,
        "opening a Python-built Brain changed the data"
    );

    // …and it is writable afterwards, which is the point of not migrating.
    let outcome = writer
        .set_node_sensitivity("dec:fusion-alpha", true, Some("a Rust write"))
        .expect("set_node_sensitivity");
    assert_eq!(outcome["ok"], Value::Bool(true));
    assert_eq!(
        schema_master(&store).get("objects"),
        before.0.get("objects"),
        "a Rust write changed the schema"
    );
}

fn row_counts(store: &Store) -> Vec<(String, i64)> {
    store
        .with_read_conn(|conn| {
            let mut counts = Vec::new();
            for table in [
                "nodes",
                "edges",
                "chunks",
                "nodes_v2",
                "edges_v2",
                "vector_embeddings",
                "ingestion_provenance",
                "graph_meta",
                "kg_meta",
            ] {
                let count: i64 = conn
                    .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                        row.get(0)
                    })
                    .unwrap_or(-1);
                counts.push((table.to_string(), count));
            }
            Ok(counts)
        })
        .expect("count rows")
}
