//! N7: folder ingest accepts the trusted local owner and actually runs.

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
    _handle: tokio::task::JoinHandle<()>,
}

impl Install {
    async fn start(data: &Path) -> Self {
        let folder = data.join("notes");
        std::fs::create_dir_all(&folder).expect("folder");
        std::fs::write(
            folder.join("standup.md"),
            "ship the backup snapshot today\n",
        )
        .expect("note");

        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.to_path_buf();
        assert!(
            !config.require_auth && !config.externally_reachable,
            "this install must be trusted_local_owner"
        );
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
}

#[tokio::test]
async fn trusted_local_owner_can_ingest_a_folder() {
    let data = tempfile::tempdir().expect("tempdir");
    let install = Install::start(data.path()).await;
    let folder = data.path().join("notes");
    let folder_s = folder.to_string_lossy().to_string();

    let (probe_status, probe) = install
        .post(
            "/api/ingestion/folder",
            json!({"path": folder_s, "approved": false}),
        )
        .await;
    assert_eq!(probe_status, 200, "{probe}");
    assert_eq!(probe["permission_required"], json!(true));
    let token = probe["approval_token"].as_str().expect("token").to_string();
    assert!(
        install.permissions.approve(&token),
        "the probe token must be approvable"
    );

    let (status, body) = install
        .post(
            "/api/ingestion/folder",
            json!({
                "path": folder_s,
                "approved": true,
                "approval_token": token,
                "background": false
            }),
        )
        .await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["status"], json!("completed"));
    assert!(body["ingested"].as_u64().unwrap_or(0) >= 1, "{body}");
    assert!(
        body["job_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("bg_ingest_")),
        "{body}"
    );

    let conn = lattice_core::db::open_read_only(&install.db).expect("ro");
    let found: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE summary LIKE ?",
            ["%ship the backup snapshot today%"],
            |row| row.get(0),
        )
        .expect("count");
    assert!(found >= 1, "the ingested note must land in the Brain");
}
