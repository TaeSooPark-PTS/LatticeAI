use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::graph_write::{GraphWriter, SystemClock};
use serde_json::Value;

fn is_platform_conversation_object(name: &str) -> bool {
    name == "conversation_messages"
        || name.starts_with("idx_conv_messages_")
        || name.starts_with("sqlite_autoindex_conversation_messages_")
}

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
        if is_platform_conversation_object(name) {
            continue;
        }
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
fn a_fresh_rust_install_creates_the_conversation_table_the_chat_writer_uses() {
    let work = tempfile::tempdir().expect("temp dir");
    let (store, _writer) = open_writer(&work.path().join("knowledge_graph.sqlite"));
    store
        .with_read_conn(|conn| {
            let sql: String = conn.query_row(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversation_messages'",
                [],
                |row| row.get(0),
            )?;
            assert!(
                sql.contains("message_hash TEXT NOT NULL UNIQUE"),
                "the writer unique key must be present: {sql}"
            );
            assert!(sql.contains("workspace_id TEXT"), "{sql}");
            assert!(sql.contains("organization_id TEXT"), "{sql}");
            let indexes: i64 = conn.query_row(
                "SELECT COUNT(*) FROM sqlite_master \
                 WHERE type='index' AND tbl_name='conversation_messages' \
                   AND name LIKE 'idx_conv_messages_%'",
                [],
                |row| row.get(0),
            )?;
            assert_eq!(indexes, 4, "conv/time/user/workspace indexes");
            conn.execute_batch("SELECT 1 FROM conversation_messages")?;
            Ok(())
        })
        .expect("conversation_messages must exist after bootstrap");
}

#[test]
fn opening_a_python_built_brain_migrates_nothing() {
    std::env::set_var("LATTICEAI_EMBED_DIM", "1024");
    let work = tempfile::tempdir().expect("temp dir");
    let path = work.path().join("knowledge_graph.sqlite");
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

#[test]
fn delete_node_cascades_part_of_edges_across_v1_and_v2() {
    use lattice_core::graph_write::types::{EdgeSpec, NodeSpec};
    use serde_json::Map;

    let work = tempfile::tempdir().expect("temp dir");
    let path = work.path().join("knowledge_graph.sqlite");
    let (store, writer) = open_writer(&path);

    let root = NodeSpec {
        id: "self:root".into(),
        node_type: "Self".into(),
        title: "Root".into(),
        summary: "Root".into(),
        metadata: Map::new(),
        raw: Map::new(),
        owner: None,
        workspace_id: None,
        visibility: None,
    };
    let fact = NodeSpec {
        id: "self:preference:1".into(),
        node_type: "Preference".into(),
        title: "Dark mode".into(),
        summary: "Dark mode".into(),
        metadata: Map::new(),
        raw: Map::new(),
        owner: None,
        workspace_id: None,
        visibility: None,
    };
    writer.upsert_nodes(&[root, fact]).expect("upsert nodes");
    writer
        .upsert_edges(&[EdgeSpec {
            from_node: "self:preference:1".into(),
            to_node: "self:root".into(),
            edge_type: "PART_OF".into(),
            weight: 1.0,
            metadata: Map::new(),
            legacy_label: None,
        }])
        .expect("upsert PART_OF edge");

    assert!(writer
        .delete_node("self:preference:1")
        .expect("delete_node"));

    store
        .with_read_conn(|conn| {
            let v1_edges: i64 = conn.query_row(
                "SELECT COUNT(*) FROM edges WHERE from_node='self:preference:1' OR to_node='self:preference:1'",
                [],
                |r| r.get(0),
            )?;
            let v2_edges: i64 = conn.query_row(
                "SELECT COUNT(*) FROM edges_v2 WHERE source='self:preference:1' OR target='self:preference:1'",
                [],
                |r| r.get(0),
            )?;
            assert_eq!(v1_edges, 0, "PART_OF edge must be deleted from edges");
            assert_eq!(v2_edges, 0, "PART_OF edge must be deleted from edges_v2");
            Ok(())
        })
        .expect("verify PART_OF cascade");
}
