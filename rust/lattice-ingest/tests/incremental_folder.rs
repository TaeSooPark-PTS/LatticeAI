//! Unchanged files skip parse/extract/embed; the job report says so.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_ingest::local_files_api::{self, LocalApprovals, LocalFilesState};
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

    async fn ingest(&self) -> (u16, Value) {
        let folder_s = self.folder.to_string_lossy().to_string();
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let probe = client
            .post(format!("{}/api/ingestion/folder", self.origin))
            .header("content-type", "application/json")
            .body(
                serde_json::to_vec(&json!({"path": folder_s, "approved": false})).expect("encode"),
            )
            .send()
            .await
            .expect("probe");
        let probe: Value =
            serde_json::from_str(&probe.text().await.unwrap_or_default()).unwrap_or(json!({}));
        let token = probe["approval_token"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        assert!(self.permissions.approve(&token), "approvable");
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
                .expect("encode"),
            )
            .send()
            .await
            .expect("ingest");
        let status = response.status().as_u16();
        let text = response.text().await.unwrap_or_default();
        (
            status,
            serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text})),
        )
    }
}

#[tokio::test]
async fn a_second_pass_over_an_unchanged_folder_skips_every_file() {
    let data = tempfile::tempdir().expect("tempdir");
    let install = Install::start(data.path()).await;

    let (status, first) = install.ingest().await;
    assert_eq!(status, 200, "{first}");
    assert_eq!(first["status"], json!("completed"), "{first}");
    assert_eq!(first["ingested"], json!(2), "{first}");
    assert_eq!(first["skipped_unchanged"], json!(0), "{first}");
    assert_eq!(first["processed"], json!(2), "{first}");

    let (status, second) = install.ingest().await;
    assert_eq!(status, 200, "{second}");
    assert_eq!(second["status"], json!("completed"), "{second}");
    assert_eq!(second["ingested"], json!(0), "{second}");
    assert_eq!(second["processed"], json!(0), "{second}");
    assert_eq!(second["skipped_unchanged"], json!(2), "{second}");
    assert_eq!(second["reingested"], json!(0), "{second}");
    assert_eq!(second["scanned"], json!(2), "{second}");
    let redo =
        second["processed"].as_f64().unwrap_or(1.0) / first["processed"].as_f64().unwrap_or(1.0);
    assert!(redo <= 0.05, "redo ratio {redo} from {second}");

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let nodes: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Document'",
            [],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(nodes, 2, "a skip must not grow a second pair of nodes");
}

#[tokio::test]
async fn a_changed_file_is_reingested_and_a_deleted_file_is_only_reported() {
    let data = tempfile::tempdir().expect("tempdir");
    let install = Install::start(data.path()).await;
    let (_, first) = install.ingest().await;
    assert_eq!(first["ingested"], json!(2));

    std::fs::write(install.folder.join("a.md"), "alpha note one, revised\n").expect("edit");
    std::fs::remove_file(install.folder.join("b.md")).expect("delete");

    let (status, second) = install.ingest().await;
    assert_eq!(status, 200, "{second}");
    assert_eq!(second["reingested"], json!(1), "{second}");
    assert_eq!(second["skipped_unchanged"], json!(0), "{second}");
    let deleted = second["deleted"].as_array().cloned().unwrap_or_default();
    assert_eq!(deleted.len(), 1, "{second}");
    assert!(
        deleted[0].as_str().unwrap_or_default().ends_with("b.md"),
        "{deleted:?}"
    );

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let docs: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE type='Document'",
            [],
            |row| row.get(0),
        )
        .expect("count");
    assert!(
        docs >= 2,
        "deletion is report-only; the old node stays ({docs})"
    );
}
