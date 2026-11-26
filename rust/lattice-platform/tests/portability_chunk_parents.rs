use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::chunk_parents::{
    chunk_parents_exist, export_chunk_parents, PARENT_INDEX_FIELD,
};
use lattice_core::graph_write::types::{ChunkParentPiece, ChunkPiece, IngestContentRequest};
use lattice_core::graph_write::GraphWriter;
use lattice_platform::knowledge::portability::{self, PortabilityState};
use serde_json::{json, Map, Value};

struct Install {
    origin: String,
    data: PathBuf,
    graph: GraphWriter,
    _dir: tempfile::TempDir,
}

impl Install {
    async fn start() -> Self {
        std::env::set_var("LATTICEAI_BRAIN_NETWORK", "1");
        let dir = tempfile::tempdir().expect("tempdir");
        let data = dir.path().to_path_buf();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_ENABLE_GRAPH".into(), "1".into());
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.clone();
        let auth = AuthState::with_clock(config, Clock::system());
        let runtime = RuntimeConfig::resolve(
            Some(&data.to_string_lossy()),
            None,
            None,
            Some(data.as_path()),
        );
        let store = Arc::new(Store::open(&runtime.graph_db_path()).expect("store"));
        let graph =
            GraphWriter::open(store, data.join("knowledge_graph_blobs")).expect("graph writer");
        let mut state = PortabilityState::new(auth, runtime);
        state.graph = Some(graph.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        tokio::spawn(async move {
            let app = portability::router(state);
            let _ = axum::serve(listener, app.into_make_service()).await;
        });
        Self {
            origin: format!("http://{addr}"),
            data,
            graph,
            _dir: dir,
        }
    }

    async fn post(&self, path: &str, body: Value) -> (u16, Value) {
        let response = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(30))
            .build()
            .expect("client")
            .post(format!("{}{path}", self.origin))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&body).expect("encode"))
            .send()
            .await
            .expect("request");
        let status = response.status().as_u16();
        let text = response.text().await.unwrap_or_default();
        (
            status,
            serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text})),
        )
    }

    async fn ok(&self, path: &str, body: Value) -> Value {
        let (status, reply) = self.post(path, body).await;
        assert_eq!(status, 200, "{path}: {reply}");
        reply
    }

    fn parents(&self) -> Vec<Value> {
        self.graph
            .store()
            .with_read_conn(export_chunk_parents)
            .expect("parents")
    }

    fn has_parent_table(&self) -> bool {
        self.graph
            .store()
            .with_read_conn(|conn| Ok(chunk_parents_exist(conn)))
            .expect("probe")
    }

    fn linked_chunks(&self) -> i64 {
        self.count(
            "SELECT COUNT(*) FROM chunks WHERE json_extract(metadata_json, '$.parent_id') \
             IN (SELECT id FROM chunk_parents)",
        )
    }

    fn count(&self, sql: &str) -> i64 {
        self.graph
            .store()
            .with_read_conn(|conn| Ok(conn.query_row(sql, [], |row| row.get(0))?))
            .expect("count")
    }

    fn chunk_hashes(&self) -> Vec<String> {
        self.graph
            .store()
            .with_read_conn(|conn| {
                let mut stmt = conn.prepare(
                    "SELECT text_hash FROM vector_embeddings WHERE item_type='chunk' \
                     ORDER BY item_id",
                )?;
                let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
                Ok(rows.collect::<Result<Vec<_>, _>>()?)
            })
            .expect("hashes")
    }

    fn seed(&self, with_parents: bool) -> String {
        let child = |text: &str| {
            let mut fields = Map::new();
            fields.insert("context_prefix".into(), json!("Document: guide"));
            if with_parents {
                fields.insert(PARENT_INDEX_FIELD.into(), json!(0));
            }
            ChunkPiece {
                text: text.into(),
                fields,
                embedding: None,
            }
        };
        let parents = if with_parents {
            vec![ChunkParentPiece {
                text: "alpha section beta section".into(),
                fields: Map::new(),
            }]
        } else {
            Vec::new()
        };
        self.graph
            .ingest_content(&IngestContentRequest {
                source_type: "note".into(),
                title: "guide".into(),
                text: "alpha section beta section".into(),
                workspace_id: Some("personal".into()),
                chunks: vec![child("alpha section"), child("beta section")],
                parents,
                ..Default::default()
            })
            .expect("ingest")
            .node_id
    }
}

fn assert_restored(source: &Install, target: &Install) {
    assert_eq!(target.parents(), source.parents());
    assert_eq!(target.linked_chunks(), 2);
    assert_eq!(target.chunk_hashes(), source.chunk_hashes());
}

#[tokio::test]
async fn the_json_export_round_trips_parent_sections_and_their_links() {
    let source = Install::start().await;
    source.seed(true);
    let artifact = source.ok("/api/knowledge-graph/export", json!({})).await;
    assert_eq!(artifact["header"]["counts"]["chunk_parents"], json!(1));
    assert_eq!(artifact["chunk_parents"].as_array().map(Vec::len), Some(1));

    for mode in ["merge", "replace"] {
        let target = Install::start().await;
        let outcome = target
            .ok(
                "/api/knowledge-graph/import",
                json!({"artifact": artifact, "mode": mode}),
            )
            .await;
        assert_eq!(outcome["chunk_parents"], json!(1), "{outcome}");
        assert_restored(&source, &target);
    }
}

#[tokio::test]
async fn a_store_without_the_parent_table_exports_and_old_artifacts_import() {
    let source = Install::start().await;
    source.seed(false);
    assert!(!source.has_parent_table());
    let artifact = source.ok("/api/knowledge-graph/export", json!({})).await;
    assert!(artifact.get("chunk_parents").is_none());
    assert!(artifact["header"]["counts"].get("chunk_parents").is_none());
    assert_eq!(artifact["chunks"].as_array().map(Vec::len), Some(2));

    let target = Install::start().await;
    let outcome = target
        .ok(
            "/api/knowledge-graph/import",
            json!({"artifact": artifact, "mode": "replace"}),
        )
        .await;
    assert!(outcome.get("chunk_parents").is_none(), "{outcome}");
    assert!(!target.has_parent_table());
    assert_eq!(target.count("SELECT COUNT(*) FROM chunks"), 2);

    let replaced = Install::start().await;
    replaced.seed(true);
    replaced
        .ok(
            "/api/knowledge-graph/import",
            json!({"artifact": artifact, "mode": "replace"}),
        )
        .await;
    assert!(replaced.parents().is_empty());
}

#[tokio::test]
async fn the_backup_zip_restores_parent_sections() {
    let source = Install::start().await;
    source.seed(true);
    let backup = source.ok("/api/knowledge-graph/backup", json!({})).await;
    let parents = source.parents();
    let target = Install::start().await;
    target
        .ok(
            "/api/knowledge-graph/restore",
            json!({"path": backup["path"], "confirm": true}),
        )
        .await;
    assert_eq!(target.parents(), parents);
    assert_eq!(target.linked_chunks(), 2);
}

#[tokio::test]
async fn the_encrypted_archive_carries_parents_through_replace_and_merge() {
    let source = Install::start().await;
    source.seed(true);
    let path = source.data.join("full.latticebrain");
    let created = source
        .ok(
            "/api/knowledge-graph/archive",
            json!({"passphrase": "correct-horse", "path": path.to_string_lossy()}),
        )
        .await;
    assert_eq!(created["counts"]["chunk_parents"], json!(1), "{created}");

    for mode in ["replace", "merge"] {
        let target = Install::start().await;
        target
            .ok(
                "/api/knowledge-graph/archive/import",
                json!({
                    "path": path.to_string_lossy(),
                    "passphrase": "correct-horse",
                    "mode": mode,
                }),
            )
            .await;
        assert_eq!(target.parents(), source.parents(), "{mode}");
        assert_eq!(target.linked_chunks(), 2, "{mode}");
    }
}

#[tokio::test]
async fn an_archive_of_a_store_without_the_parent_table_still_restores() {
    let source = Install::start().await;
    source.seed(false);
    let path = source.data.join("legacy.latticebrain");
    let created = source
        .ok(
            "/api/knowledge-graph/archive",
            json!({"passphrase": "correct-horse", "path": path.to_string_lossy()}),
        )
        .await;
    assert!(created["counts"].get("chunk_parents").is_none());
    for mode in ["replace", "merge"] {
        let target = Install::start().await;
        target
            .ok(
                "/api/knowledge-graph/archive/import",
                json!({
                    "path": path.to_string_lossy(),
                    "passphrase": "correct-horse",
                    "mode": mode,
                }),
            )
            .await;
        assert_eq!(target.count("SELECT COUNT(*) FROM chunks"), 2, "{mode}");
        assert!(!target.has_parent_table(), "{mode}");
    }
}

#[tokio::test]
async fn a_shared_subgraph_signs_stages_and_merges_parent_sections() {
    let source = Install::start().await;
    let node_id = source.seed(true);
    let shared = source
        .ok(
            "/api/knowledge-graph/share/export",
            json!({"node_ids": [node_id], "include_neighbors": true}),
        )
        .await;
    assert_eq!(shared["signature_verified"], json!(true));
    let bundle = shared["bundle"].clone();
    assert_eq!(bundle["chunk_parents"].as_array().map(Vec::len), Some(1));

    let target = Install::start().await;
    let mut tampered = bundle.clone();
    tampered["chunk_parents"][0]["text"] = json!("injected");
    let mut unsigned = bundle.clone();
    unsigned
        .as_object_mut()
        .expect("bundle")
        .remove("parents_signature");
    for forged in [tampered, unsigned] {
        let (status, reply) = target
            .post(
                "/api/knowledge-graph/share/import",
                json!({"artifact": forged}),
            )
            .await;
        assert_eq!(status, 400, "{reply}");
    }

    let staged = target
        .ok(
            "/api/knowledge-graph/share/import",
            json!({"artifact": bundle}),
        )
        .await;
    let proposal = staged["proposal_id"].as_str().expect("proposal");
    target
        .ok(
            &format!("/api/knowledge-graph/share/proposals/{proposal}/accept"),
            json!({}),
        )
        .await;
    assert_eq!(target.parents(), source.parents());
    assert_eq!(target.linked_chunks(), 2);

    let mut stripped = bundle;
    let object = stripped.as_object_mut().expect("bundle");
    object.remove("chunk_parents");
    object.remove("parents_signature");
    let legacy = Install::start().await;
    let reply = legacy
        .ok(
            "/api/knowledge-graph/share/import",
            json!({"artifact": stripped}),
        )
        .await;
    assert_eq!(reply["signature_verified"], json!(true));
}
