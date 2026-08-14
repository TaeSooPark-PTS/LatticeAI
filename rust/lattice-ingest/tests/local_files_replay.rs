//! Replay local_files + local_knowledge auth, probe, and approval-dance cases.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_ingest::local_files_api::{self, LocalApprovals, LocalFilesState};
use serde_json::Value;

fn fixture() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join("knowledge_search.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn seed_corpus(root: &Path) -> PathBuf {
    let corpus = root.join("corpus");
    std::fs::create_dir_all(&corpus).unwrap();
    std::fs::write(corpus.join("onboarding.txt"), "welcome to lattice\n").unwrap();
    std::fs::write(corpus.join("ranking-notes.md"), "alpha fusion notes\n").unwrap();
    std::fs::write(corpus.join("회의록.md"), "회의 기록\n").unwrap();
    corpus
}

fn seed_users(dir: &Path) {
    let email = "owner@lattice.test";
    let mut owner = OrderedMap::new();
    owner.insert("password", serde_json::json!("x"));
    owner.insert("name", serde_json::json!("owner"));
    owner.insert("nickname", serde_json::json!("owner"));
    owner.insert("role", serde_json::json!("admin"));
    owner.insert("disabled", serde_json::json!(false));
    owner.insert("id", serde_json::json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", serde_json::json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

async fn boot(data: &Path) -> (String, String, Arc<LocalApprovals>, PathBuf) {
    seed_users(data);
    let corpus = seed_corpus(data);
    let db = data.join("knowledge_graph.sqlite");
    let conn = rusqlite::Connection::open(&db).unwrap();
    conn.execute_batch(
        "CREATE TABLE knowledge_sources(id TEXT PRIMARY KEY, root_path TEXT, os_type TEXT,
            drive_id TEXT, label TEXT, status TEXT, include_ocr INT, watch_enabled INT,
            consent_json TEXT, created_at TEXT, updated_at TEXT, last_scanned_at TEXT);
         CREATE TABLE local_file_index(id TEXT PRIMARY KEY, source_id TEXT, status TEXT,
            relative_path TEXT, error_message TEXT, last_scanned_at TEXT);
         CREATE TABLE ingestion_jobs(job_id TEXT PRIMARY KEY, status TEXT, total INT,
            processed INT, failed INT, errors_json TEXT, created_at TEXT, updated_at TEXT,
            items_json TEXT, done_indices_json TEXT);
         INSERT INTO ingestion_jobs VALUES
           ('bg_ingest_0001','completed',4,4,0,'[]','2026-01-01T00:00:00','2026-01-01T00:00:01','[]','[]');",
    )
    .unwrap();
    drop(conn);

    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@lattice.test"));

    let runtime = RuntimeConfig::resolve(Some(data.to_str().unwrap()), None, None, Some(data));
    let store = Arc::new(Store::open(&db).unwrap());
    let permissions = LocalApprovals::new();
    let state =
        LocalFilesState::new(auth, Some(store), runtime).with_permissions(Arc::clone(&permissions));
    let app = local_files_api::router(Arc::new(state));

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    (format!("http://{addr}"), token, permissions, corpus)
}

#[tokio::test]
async fn local_files_replay_auth_probe_and_approval_dance() {
    let data = tempfile::tempdir().unwrap();
    let (origin, token, permissions, corpus) = boot(data.path()).await;
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();
    let corpus_s = corpus.to_string_lossy().into_owned();

    // Auth denial.
    let denied = client
        .post(format!("{origin}/local/list"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({"path": corpus_s})).unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(denied.status().as_u16(), 401);
    let body: Value = json_of(denied).await;
    assert_eq!(
        body["detail"].as_str().unwrap(),
        "로컬 파일 접근은 로그인 세션이 필요합니다."
    );

    // GET /local/list always probes.
    let probe = client
        .get(format!("{origin}/local/list"))
        .query(&[("path", corpus_s.as_str())])
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(probe.status().as_u16(), 200);
    let body: Value = json_of(probe).await;
    assert_eq!(body["permission_required"], true);
    assert_eq!(body["action"], "list");
    assert_eq!(body["expires_in"], 300);
    assert!(body["approval_token"].as_str().unwrap().len() > 8);

    // POST probe, then approve, then list.
    let minted = client
        .post(format!("{origin}/local/list"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({"path": corpus_s})).unwrap())
        .send()
        .await
        .unwrap();
    let minted: Value = json_of(minted).await;
    let approval = minted["approval_token"].as_str().unwrap().to_string();
    assert!(permissions.approve(&approval));
    let listed = client
        .post(format!("{origin}/local/list"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&serde_json::json!({
                "path": corpus_s,
                "approved": true,
                "approval_token": approval
            }))
            .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(listed.status().as_u16(), 200);
    let body: Value = json_of(listed).await;
    assert_eq!(body["status"], "ok");
    let names: Vec<&str> = body["result"]["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["name"].as_str().unwrap())
        .collect();
    assert!(names.contains(&"onboarding.txt"));
    assert!(names.contains(&"ranking-notes.md"));
    assert!(names.contains(&"회의록.md"));

    // Unapproved / missing token.
    let bad = client
        .post(format!("{origin}/local/list"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&serde_json::json!({
                "path": corpus_s,
                "approved": true,
                "approval_token": "not-a-token"
            }))
            .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(bad.status().as_u16(), 403);
    let missing = client
        .post(format!("{origin}/local/list"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&serde_json::json!({"path": corpus_s, "approved": true})).unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status().as_u16(), 403);

    // Jobs read.
    let jobs = client
        .get(format!("{origin}/api/ingestion/jobs"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(jobs.status().as_u16(), 200);
    let missing_job = client
        .get(format!("{origin}/api/ingestion/jobs/nope"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(missing_job.status().as_u16(), 404);

    // Local knowledge reads.
    let sources = client
        .get(format!("{origin}/knowledge-graph/local/sources"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(sources.status().as_u16(), 200);
    let health = client
        .get(format!("{origin}/knowledge-graph/local/health"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(health.status().as_u16(), 200);
    let roots = client
        .get(format!("{origin}/knowledge-graph/local/roots"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(roots.status().as_u16(), 200);

    // Folder ingest without path.
    let empty = client
        .post(format!("{origin}/api/ingestion/folder"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(
                &serde_json::json!({"path": "", "approved": true, "approval_token": "x"}),
            )
            .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(empty.status().as_u16(), 400);

    // Interop unknown source.
    let interop = client
        .post(format!("{origin}/api/ingestion/interop"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&serde_json::json!({"source": "dropbox", "path": corpus_s}))
                .unwrap(),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(interop.status().as_u16(), 400);

    // Fixture inventory: we claim these families, minus KEEP multimodal.
    let root = fixture();
    let mut seen = 0;
    for case in root["cases"].as_array().unwrap() {
        if matches!(
            case["family"].as_str(),
            Some("local_files") | Some("local_knowledge")
        ) {
            seen += 1;
        }
    }
    assert!(
        seen >= 40,
        "expected the captured local families, got {seen}"
    );
}

#[tokio::test]
async fn local_agent_status_is_probed_not_hardcoded() {
    let data = tempfile::tempdir().unwrap();
    let (origin, token, _, _) = boot(data.path()).await;
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();
    let response = client
        .get(format!("{origin}/api/local-agent/status"))
        .header("cookie", format!("session_token={token}"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status().as_u16(), 200);
    let body: Value = json_of(response).await;
    assert_eq!(body["agent"]["id"], "lattice-local-runtime");
    assert!(body["handshake"]["ok"].as_bool().unwrap());
    assert!(body["filesystem_access"].as_bool().unwrap());
}

async fn json_of(response: reqwest::Response) -> Value {
    let text = response.text().await.unwrap();
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("not JSON ({err}): {text}"))
}
