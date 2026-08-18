//! N5/N6: WAL-consistent backups, honest blob manifests, export counts.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::types::{ChunkPiece, IngestContentRequest};
use lattice_core::graph_write::GraphWriter;
use lattice_platform::portability::{self, PortabilityState};
use serde_json::{json, Value};

struct Install {
    origin: String,
    data: PathBuf,
    graph: GraphWriter,
    _dir: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
}

impl Install {
    async fn start() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let data = dir.path().to_path_buf();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
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
        let db = runtime.graph_db_path();
        let store = Arc::new(Store::open(&db).expect("store"));
        store
            .with_write_conn(|conn| {
                conn.execute_batch("PRAGMA wal_autocheckpoint=0")
                    .map_err(lattice_core::CoreError::from)
            })
            .expect("disable autocheckpoint");
        let graph = GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs"))
            .expect("graph");

        let mut state = PortabilityState::new(auth, runtime);
        state.graph = Some(graph.clone());
        let app = portability::router(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app.into_make_service()).await;
        });
        Self {
            origin: format!("http://{addr}"),
            data,
            graph,
            _dir: dir,
            _handle: handle,
        }
    }

    async fn post(&self, path: &str, body: Value) -> (u16, Value) {
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let response = client
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

    async fn get(&self, path: &str) -> (u16, Value) {
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let response = client
            .get(format!("{}{path}", self.origin))
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
}

fn seed_graph(graph: &GraphWriter, label: &str) -> (u64, u64, u64) {
    graph
        .ingest_content(&IngestContentRequest {
            source_type: "note".into(),
            title: format!("{label} title"),
            text: format!("{label} body that is long enough to stay searchable"),
            chunks: vec![ChunkPiece {
                text: format!("{label} chunk"),
                ..Default::default()
            }],
            ..Default::default()
        })
        .expect("ingest");
    counts(graph.store().path())
}

fn counts(db: &Path) -> (u64, u64, u64) {
    let conn = lattice_core::db::open_read_only(db).expect("ro");
    let one = |table: &str| -> u64 {
        conn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap_or(0) as u64
    };
    (one("nodes"), one("edges"), one("chunks"))
}

fn write_blob(root: &Path, rel: &str, bytes: &[u8]) {
    let path = root.join("knowledge_graph_blobs").join(rel);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("blob dir");
    }
    std::fs::write(path, bytes).expect("blob");
}

fn extract_zip_file(zip_path: &Path, name: &str, dest: &Path) {
    let file = std::fs::File::open(zip_path).expect("zip");
    let mut archive = zip::ZipArchive::new(file).expect("archive");
    let mut entry = archive.by_name(name).expect(name);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).expect("parent");
    }
    let mut out = std::fs::File::create(dest).expect("dest");
    std::io::copy(&mut entry, &mut out).expect("copy");
}

fn zip_names(zip_path: &Path) -> Vec<String> {
    let file = std::fs::File::open(zip_path).expect("zip");
    let archive = zip::ZipArchive::new(file).expect("archive");
    archive.file_names().map(str::to_string).collect()
}

fn wal_path(db: &Path) -> PathBuf {
    PathBuf::from(format!("{}-wal", db.display()))
}

#[tokio::test]
async fn a_wal_backup_captures_uncheckpointed_rows_and_blobs() {
    let install = Install::start().await;
    let db = install.data.join("knowledge_graph.sqlite");
    seed_graph(&install.graph, "alpha");
    seed_graph(&install.graph, "beta");
    let (nodes, edges, chunks) = counts(&db);
    write_blob(&install.data, "ab/memo.bin", b"sidecar-bytes");
    assert!(
        wal_path(&db).exists(),
        "the live store must still have a WAL sidecar so a raw copy would lose rows"
    );

    let (status, body) = install.post("/api/knowledge-graph/backup", json!({})).await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["manifest"]["has_blobs"], json!(true));
    assert_eq!(body["manifest"]["snapshot"], json!("vacuum-into"));
    assert_eq!(body["manifest"]["nodes"], json!(nodes));
    assert_eq!(body["manifest"]["edges"], json!(edges));
    assert_eq!(body["manifest"]["chunks"], json!(chunks));

    let zip_path = PathBuf::from(body["path"].as_str().expect("path"));
    let names = zip_names(&zip_path);
    assert!(
        names.iter().any(|n| n == "knowledge_graph.sqlite"),
        "snapshot db missing: {names:?}"
    );
    assert!(
        names
            .iter()
            .any(|n| n == "knowledge_graph_blobs/ab/memo.bin"),
        "blob missing: {names:?}"
    );

    let extracted = install.data.join("extracted-snapshot.sqlite");
    extract_zip_file(&zip_path, "knowledge_graph.sqlite", &extracted);
    assert_eq!(
        counts(&extracted),
        (nodes, edges, chunks),
        "the snapshot must include WAL-only rows"
    );

    let (health_status, health) = install.get("/api/knowledge-graph/backup-health").await;
    assert_eq!(health_status, 200, "{health}");
    assert_eq!(health["has_blobs"], json!(true));
    assert_eq!(health["latest_nodes"], json!(nodes));
    assert!(health["count"].as_u64().unwrap_or(0) >= 1);

    // Mutate the live store, then restore the snapshot.
    seed_graph(&install.graph, "gamma");
    std::fs::remove_file(install.data.join("knowledge_graph_blobs/ab/memo.bin")).expect("rm blob");
    let (restore_status, restored) = install
        .post(
            "/api/knowledge-graph/restore",
            json!({"path": zip_path.to_string_lossy(), "confirm": true}),
        )
        .await;
    assert_eq!(restore_status, 200, "{restored}");
    assert_eq!(restored["restored"], json!(true));
    assert_eq!(restored["has_blobs"], json!(true));
    assert_eq!(restored["blobs"], json!(1));

    let fresh = counts(&db);
    assert_eq!(
        fresh,
        (nodes, edges, chunks),
        "restore must swap the snapshot"
    );
    assert_eq!(
        std::fs::read(install.data.join("knowledge_graph_blobs/ab/memo.bin")).expect("blob back"),
        b"sidecar-bytes"
    );
}

fn node_titles(store: &Store) -> Vec<String> {
    store
        .with_read_conn(|conn| {
            let mut stmt = conn.prepare("SELECT title FROM nodes ORDER BY title")?;
            let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
            Ok(rows.filter_map(Result::ok).collect())
        })
        .expect("titles")
}

/// The 11.9.0 ops-note: restore swapped the file but long-lived Store
/// handles kept serving pre-restore bytes until the process restarted.
/// The same GraphWriter / Store must see the snapshot on the next query.
#[tokio::test]
async fn restore_is_visible_through_the_same_store_without_a_restart() {
    let install = Install::start().await;
    seed_graph(&install.graph, "alpha");
    let before = node_titles(install.graph.store());
    assert!(
        before.iter().any(|title| title.contains("alpha")),
        "pre-backup seed missing: {before:?}"
    );

    let (status, body) = install.post("/api/knowledge-graph/backup", json!({})).await;
    assert_eq!(status, 200, "{body}");
    let zip_path = PathBuf::from(body["path"].as_str().expect("path"));

    seed_graph(&install.graph, "gamma");
    let mutated = node_titles(install.graph.store());
    assert!(
        mutated.iter().any(|title| title.contains("gamma")),
        "live mutation missing: {mutated:?}"
    );

    let (restore_status, restored) = install
        .post(
            "/api/knowledge-graph/restore",
            json!({"path": zip_path.to_string_lossy(), "confirm": true}),
        )
        .await;
    assert_eq!(restore_status, 200, "{restored}");
    assert_eq!(restored["restored"], json!(true));

    let after = node_titles(install.graph.store());
    assert!(
        after.iter().any(|title| title.contains("alpha")),
        "post-restore query through the same Store must see the snapshot: {after:?}"
    );
    assert!(
        after.iter().all(|title| !title.contains("gamma")),
        "post-restore query must not keep pre-restore rows: {after:?}"
    );
    assert_eq!(after, before);
}

#[tokio::test]
async fn export_carries_nodes_edges_and_chunks() {
    let install = Install::start().await;
    seed_graph(&install.graph, "one");
    seed_graph(&install.graph, "two");
    let expected = counts(&install.data.join("knowledge_graph.sqlite"));
    assert!(expected.0 > 0 && expected.1 > 0 && expected.2 > 0);

    let (status, body) = install.post("/api/knowledge-graph/export", json!({})).await;
    assert_eq!(status, 200, "{body}");
    let header = &body["header"]["counts"];
    assert_eq!(header["nodes"], json!(expected.0));
    assert_eq!(header["edges"], json!(expected.1));
    assert_eq!(header["chunks"], json!(expected.2));
    assert_eq!(
        body["nodes"].as_array().map(Vec::len),
        Some(expected.0 as usize)
    );
    assert_eq!(
        body["edges"].as_array().map(Vec::len),
        Some(expected.1 as usize)
    );
    assert_eq!(
        body["chunks"].as_array().map(Vec::len),
        Some(expected.2 as usize)
    );
    let edge = body["edges"][0].as_object().expect("edge");
    assert!(edge.contains_key("from_node"), "{edge:?}");
    assert!(edge.contains_key("to_node"), "{edge:?}");
    assert!(!edge.contains_key("src"));
}

#[tokio::test]
async fn restore_without_confirm_is_still_refused() {
    let install = Install::start().await;
    let (status, body) = install
        .post(
            "/api/knowledge-graph/restore",
            json!({"path": "/no/such/backup.lattice", "confirm": false}),
        )
        .await;
    assert_eq!(status, 400, "{body}");
    assert_eq!(
        body["detail"],
        json!("Backup archive not found: /no/such/backup.lattice")
    );
}
