//! Consent prune: dry-run matches apply, only the deleted file's subgraph goes.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_ingest::local_files_api::{self, LocalApprovals, LocalFilesState};
use rusqlite::Connection;
use serde_json::{json, Value};

struct Install {
    origin: String,
    permissions: Arc<LocalApprovals>,
    db: PathBuf,
    folder: PathBuf,
    _handle: tokio::task::JoinHandle<()>,
}

impl Install {
    async fn start(data: &Path) -> Self {
        let folder = data.join("notes");
        std::fs::create_dir_all(&folder).expect("folder");
        std::fs::write(folder.join("a.md"), "alpha note one\n").expect("a");
        std::fs::write(folder.join("b.md"), "beta note two\n").expect("b");

        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4826".into());
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.to_path_buf();
        let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
        let runtime = RuntimeConfig::resolve(Some(&data.to_string_lossy()), None, None, Some(data));
        let db = runtime.graph_db_path();
        let store = Arc::new(Store::open(&db).expect("store"));
        let graph = GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs"))
            .expect("graph");
        let permissions = LocalApprovals::new();
        let state = LocalFilesState::new(auth, Some(store), runtime)
            .with_graph(graph)
            .with_permissions(Arc::clone(&permissions));
        let app = local_files_api::router(Arc::new(state));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app.into_make_service()).await;
        });
        Self {
            origin: format!("http://{addr}"),
            permissions,
            db,
            folder,
            _handle: handle,
        }
    }

    async fn ingest(&self) -> Value {
        let folder_s = self.folder.to_string_lossy().to_string();
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let probe = client
            .post(format!("{}/api/ingestion/folder", self.origin))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&json!({"path": folder_s, "approved": false})).unwrap())
            .send()
            .await
            .expect("probe");
        let probe: Value = serde_json::from_str(&probe.text().await.unwrap_or_default()).unwrap();
        let token = probe["approval_token"].as_str().unwrap().to_string();
        assert!(self.permissions.approve(&token));
        let response = client
            .post(format!("{}/api/ingestion/folder", self.origin))
            .header("content-type", "application/json")
            .body(
                serde_json::to_vec(&json!({
                    "path": folder_s,
                    "approved": true,
                    "approval_token": token,
                    "background": false
                }))
                .unwrap(),
            )
            .send()
            .await
            .expect("ingest");
        serde_json::from_str(&response.text().await.unwrap_or_default()).unwrap()
    }

    async fn prune(&self, confirm: bool) -> (u16, Value) {
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let response = client
            .post(format!("{}/api/ingestion/folder/prune", self.origin))
            .header("content-type", "application/json")
            .body(
                serde_json::to_vec(&json!({
                    "path": self.folder.to_string_lossy(),
                    "confirm": confirm
                }))
                .unwrap(),
            )
            .send()
            .await
            .expect("prune");
        let status = response.status().as_u16();
        let body = serde_json::from_str(&response.text().await.unwrap_or_default()).unwrap();
        (status, body)
    }
}

fn docs(conn: &Connection) -> Vec<String> {
    let mut stmt = conn
        .prepare("SELECT title FROM nodes WHERE type='Document' ORDER BY title")
        .unwrap();
    stmt.query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .flatten()
        .collect()
}

fn dangling_edges(conn: &Connection) -> i64 {
    conn.query_row(
        "SELECT COUNT(*) FROM edges e \
         WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.from_node) \
            OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.to_node)",
        [],
        |row| row.get(0),
    )
    .unwrap()
}

#[tokio::test]
async fn a_dry_run_matches_the_confirmed_removal_and_leaves_the_other_file() {
    let data = tempfile::tempdir().expect("tempdir");
    let install = Install::start(data.path()).await;
    let first = install.ingest().await;
    assert_eq!(first["ingested"], json!(2), "{first}");

    std::fs::remove_file(install.folder.join("b.md")).expect("delete b");
    let report = install.ingest().await;
    assert_eq!(report["deleted"].as_array().unwrap().len(), 1, "{report}");

    let (status, preview) = install.prune(false).await;
    assert_eq!(status, 200, "{preview}");
    assert_eq!(preview["dry_run"], json!(true), "{preview}");
    assert_eq!(preview["files"].as_array().unwrap().len(), 1, "{preview}");
    let would = preview["would_remove"].clone();
    assert!(would["nodes"].as_i64().unwrap() >= 1, "{preview}");

    let before = lattice_core::db::open_read_only(&install.db).expect("ro");
    let titles_before = docs(&before);
    assert_eq!(titles_before.len(), 2, "{titles_before:?}");
    drop(before);

    let (status, applied) = install.prune(true).await;
    assert_eq!(status, 200, "{applied}");
    assert_eq!(applied["dry_run"], json!(false), "{applied}");
    assert_eq!(applied["would_remove"], would, "{applied}");
    assert!(
        applied["removed"]["nodes"].as_i64().unwrap() >= 1,
        "{applied}"
    );

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let titles = docs(&conn);
    assert_eq!(titles, vec!["a.md".to_string()], "{titles:?}");
    assert_eq!(dangling_edges(&conn), 0, "PART_OF must not dangle");

    let leftover_chunks: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM chunks c \
             WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = c.source_node)",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(leftover_chunks, 0);

    let prune_events: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM ingestion_provenance WHERE source_type='prune'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(prune_events, 1);
}

#[tokio::test]
async fn prune_without_deleted_files_is_a_zero_report() {
    let data = tempfile::tempdir().expect("tempdir");
    let install = Install::start(data.path()).await;
    let _ = install.ingest().await;
    let (status, preview) = install.prune(false).await;
    assert_eq!(status, 200, "{preview}");
    assert_eq!(preview["files"], json!([]));
    assert_eq!(preview["would_remove"]["nodes"], json!(0));
}
