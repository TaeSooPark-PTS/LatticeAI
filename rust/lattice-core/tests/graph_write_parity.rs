//! Python↔Rust write-path parity: replay the scenario, compare the store.
//!
//! `scripts/gen_graph_write_goldens.py` drove the **real** Python
//! `KnowledgeGraphStore` through a 32-step battery — fresh store, text and file
//! ingests including a dedup hit, a multi-chunk document, a workspace-scoped
//! ingest, chat turns, an event, direct upserts, provenance, promotions parked
//! then applied and rejected, both curations, a sensitivity flip, vector writes
//! and three rebuilds, an import (merge, dry-run and replace), source watch
//! toggling and removal, and a document-tree delete — dumping every table after
//! every step.
//!
//! This test replays the identical scenario through [`GraphWriter`] and
//! compares three things:
//!
//! 1. **every op's return value**, step by step;
//! 2. **a per-table digest of the whole store after every step**, so a
//!    divergence names the table *and* the step that caused it;
//! 3. **two full dumps** — the richest state the battery reaches, and the final
//!    one — with the embedding BLOBs as raw hex, which is the byte-level proof
//!    that both engines wrote the same vectors.
//!
//! The clock is the only thing pinned: the generator recorded what `_now()`,
//! `time.time()`, `os.getpid()` and `perf_counter()` answered, and
//! [`FrozenClock`] hands the replay the same readings. Nothing else is stubbed
//! — the schema, the ids, the hashes, the projection and the embeddings are all
//! produced by the engine under test.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_core::db::Store;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_core::graph_write::dump::{digest_dump, dump_store, Blobs};
use lattice_core::graph_write::types::{
    CurateNoiseRequest, CurateRequest, EdgeSpec, ImportRequest, IngestContentRequest,
    IngestEventRequest, IngestFileRequest, IngestMessageRequest, IngestionRecord, NodeSpec,
    RebuildRequest,
};
use lattice_core::graph_write::{FrozenClock, GraphWriter};
use serde_json::{json, Map, Value};

const WORK_PLACEHOLDER: &str = "{{WORK}}";

fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("graph_write")
}

fn load(name: &str) -> Value {
    let path = fixture_dir().join(name);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("cannot read {}: {err}", path.display()));
    serde_json::from_str(&text)
        .unwrap_or_else(|err| panic!("cannot parse {}: {err}", path.display()))
}

/// `serde_json::from_value` with the step number in the failure.
fn parse<T: serde::de::DeserializeOwned>(step: u64, op: &str, args: &Value) -> T {
    serde_json::from_value(args.clone())
        .unwrap_or_else(|err| panic!("step {step} ({op}): cannot read args: {err}"))
}

fn optional_ids(args: &Value) -> Option<Vec<String>> {
    match args.get("ids") {
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .map(|item| item.as_str().unwrap_or_default().to_string())
                .collect(),
        ),
        _ => None,
    }
}

#[test]
fn the_rust_write_engine_reproduces_the_python_store_row_for_row() {
    // `schema.EMBED_DIM` is stamped into `kg_meta`; the generator pinned it.
    std::env::set_var("LATTICEAI_EMBED_DIM", "1024");
    std::env::remove_var("LATTICEAI_GRAPH_PROMOTION_REVIEW");

    let scenario = load("scenario.json");
    let snapshots = load("snapshots.json");
    let full_dumps = load("final_store.json");
    let manifest = &scenario["manifest"];

    let work = tempfile::tempdir().expect("temp dir");
    let root = work.path().to_path_buf();
    let uploads = root.join("uploads");
    std::fs::create_dir_all(&uploads).expect("uploads dir");
    let substitutions = vec![(
        root.to_string_lossy().to_string(),
        WORK_PLACEHOLDER.to_string(),
    )];

    let clock = Arc::new(FrozenClock::new(
        manifest["frozen_now"].as_str().expect("frozen_now"),
        manifest["vector_op_time_sequence"]
            .as_array()
            .expect("vector_op_time_sequence")
            .iter()
            .map(|value| value.as_f64().expect("a float"))
            .collect(),
        manifest["frozen_pid"].as_u64().expect("frozen_pid") as u32,
        manifest["frozen_perf_counter"]
            .as_f64()
            .expect("frozen_perf_counter"),
    ));
    let store =
        Arc::new(Store::open(&root.join("knowledge_graph.sqlite")).expect("open the store"));
    // 384 is `LATTICEAI_VECTOR_DIM`'s pinned value; passing it explicitly keeps
    // the replay independent of the environment the suite happens to run in.
    let writer = GraphWriter::with_parts(
        Arc::clone(&store),
        root.join("knowledge_graph_blobs"),
        LocalEmbeddingModel::new(384),
        clock,
    )
    .expect("open the writer");

    let snapshots = snapshots["snapshots"].as_array().expect("snapshots");
    assert_snapshot(&store, &substitutions, &snapshots[0], 0, "bootstrap");

    let mut checkpoints_seen = 0usize;
    let steps = scenario["steps"].as_array().expect("steps");
    assert_eq!(
        steps.len() + 1,
        snapshots.len(),
        "one snapshot per step, plus the bootstrap"
    );
    for (index, step) in steps.iter().enumerate() {
        let number = step["n"].as_u64().expect("step number");
        let op = step["op"].as_str().expect("step op");
        let args = &step["args"];
        let expected = &step["result"];
        let actual = run_step(&writer, &uploads, number, op, args);
        let actual = rewrite_paths(&actual, &substitutions);
        assert_eq!(
            &actual, expected,
            "step {number} ({op}): the return value diverged"
        );
        assert_snapshot(&store, &substitutions, &snapshots[index + 1], number, op);
        // The byte-level checkpoints, compared at the step they were taken at
        // rather than at whichever step the battery happens to end on.
        for (name, checkpoint) in full_dumps.as_object().expect("full dumps") {
            if checkpoint["after_step"].as_u64() != Some(number) {
                continue;
            }
            let actual = store
                .with_read_conn(|conn| dump_store(conn, Blobs::Hex, &substitutions))
                .expect("dump the store");
            assert_eq!(
                &actual, &checkpoint["dump"],
                "the `{name}` full dump (after step {number}) diverged"
            );
            checkpoints_seen += 1;
        }
    }
    assert_eq!(
        checkpoints_seen,
        full_dumps.as_object().expect("full dumps").len(),
        "every recorded full dump must be compared"
    );
}

fn assert_snapshot(
    store: &Arc<Store>,
    substitutions: &[(String, String)],
    expected: &Value,
    number: u64,
    op: &str,
) {
    let dump = store
        .with_read_conn(|conn| dump_store(conn, Blobs::Digest, substitutions))
        .expect("dump the store");
    let actual = digest_dump(&dump);
    let expected_tables = expected["tables"].as_object().expect("expected tables");
    let actual_tables = actual["tables"].as_object().expect("actual tables");
    for (table, expected_digest) in expected_tables {
        let actual_digest = actual_tables
            .get(table)
            .unwrap_or_else(|| panic!("step {number} ({op}): table {table} missing from the dump"));
        assert_eq!(
            actual_digest, expected_digest,
            "step {number} ({op}): table `{table}` diverged"
        );
    }
    assert_eq!(
        actual["user_version"], expected["user_version"],
        "step {number} ({op}): user_version diverged"
    );
}

fn run_step(writer: &GraphWriter, uploads: &Path, number: u64, op: &str, args: &Value) -> Value {
    match op {
        "ingest_content" => {
            let request: IngestContentRequest = parse(number, op, args);
            writer
                .ingest_content(&request)
                .expect("ingest_content")
                .to_json()
        }
        "ingest_file" => {
            let filename = args["filename"].as_str().expect("filename");
            let body = args["body"].as_str().expect("body");
            let path = uploads.join(filename);
            std::fs::write(&path, body).expect("write the upload");
            let mut object = args.as_object().cloned().expect("file args");
            object.remove("filename");
            object.remove("body");
            object.insert("path".into(), json!(path.to_string_lossy()));
            let request: IngestFileRequest = parse(number, op, &Value::Object(object));
            writer.ingest_file(&request).expect("ingest_file").to_json()
        }
        "ingest_message" => {
            let request: IngestMessageRequest = parse(number, op, args);
            writer
                .ingest_message(&request)
                .expect("ingest_message")
                .to_json_brief()
        }
        "ingest_event" => {
            let request: IngestEventRequest = parse(number, op, args);
            writer
                .ingest_event(&request)
                .expect("ingest_event")
                .to_json_brief()
        }
        "record_ingestion" => {
            let record: IngestionRecord = parse(number, op, args);
            writer
                .record_ingestion(&record)
                .expect("record_ingestion")
                .to_json()
        }
        "upsert_nodes" => {
            let nodes: Vec<NodeSpec> = parse(number, op, &args["nodes"]);
            json!(writer.upsert_nodes(&nodes).expect("upsert_nodes"))
        }
        "upsert_edges" => {
            let edges: Vec<EdgeSpec> = parse(number, op, &args["edges"]);
            json!(writer.upsert_edges(&edges).expect("upsert_edges"))
        }
        "curate" => {
            let request: CurateRequest = parse(number, op, args);
            writer.curate(&request).expect("curate")
        }
        "apply_promotions" => writer
            .apply_promotions(optional_ids(args).as_deref())
            .expect("apply_promotions"),
        "reject_promotions" => writer
            .reject_promotions(optional_ids(args).as_deref())
            .expect("reject_promotions"),
        "curate_noise" => {
            let request: CurateNoiseRequest = parse(number, op, args);
            writer.curate_noise(&request).expect("curate_noise")
        }
        "set_node_sensitivity" => writer
            .set_node_sensitivity(
                args["node_id"].as_str().expect("node_id"),
                args["local_only"].as_bool().expect("local_only"),
                args["reason"].as_str(),
            )
            .expect("set_node_sensitivity"),
        "delete_document_tree" => writer
            .delete_document_tree(args["node_id"].as_str().expect("node_id"))
            .expect("delete_document_tree"),
        "set_local_source_watch" => writer
            .set_local_source_watch(
                args["source_id"].as_str().expect("source_id"),
                args["enabled"].as_bool().expect("enabled"),
            )
            .expect("set_local_source_watch"),
        "remove_local_source" => writer
            .remove_local_source(args["source_id"].as_str().expect("source_id"))
            .expect("remove_local_source"),
        "import_graph_data" => {
            let request: ImportRequest = parse(number, op, args);
            writer
                .import_graph_data(&request)
                .expect("import_graph_data")
                .to_json()
        }
        "write_vectors" => writer
            .write_vectors(args["node_id"].as_str().expect("node_id"))
            .to_json(),
        "rebuild_vector_index" => {
            let request: RebuildRequest = parse(number, op, args);
            writer
                .rebuild_vector_index(&request)
                .expect("rebuild_vector_index")
                .to_json()
        }
        other => panic!("step {number}: unknown op `{other}`"),
    }
}

/// The run root out of every string, the way the generator wrote it out.
fn rewrite_paths(value: &Value, substitutions: &[(String, String)]) -> Value {
    match value {
        Value::String(text) => {
            let mut text = text.clone();
            for (from, to) in substitutions {
                if !from.is_empty() {
                    text = text.replace(from.as_str(), to);
                }
            }
            Value::String(text)
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| rewrite_paths(item, substitutions))
                .collect(),
        ),
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                out.insert(key.clone(), rewrite_paths(item, substitutions));
            }
            Value::Object(out)
        }
        scalar => scalar.clone(),
    }
}
