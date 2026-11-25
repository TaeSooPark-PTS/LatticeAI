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
    let store = Arc::new(Store::open(&db).unwrap());
    let graph =
        lattice_core::graph_write::GraphWriter::open(Arc::clone(&store), data.join("blobs"))
            .unwrap();
    let conn = rusqlite::Connection::open(&db).unwrap();
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS ingestion_jobs(job_id TEXT PRIMARY KEY, status TEXT, total INT,
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
    let permissions = LocalApprovals::new();
    let state = LocalFilesState::new(auth, Some(store), runtime)
        .with_permissions(Arc::clone(&permissions))
        .with_graph(graph);
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

    let probe = client
        .get(format!("{origin}/local/list?path={corpus_s}"))
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

fn fragment() -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "openapi",
        "knowledge_search.json",
    ]
    .iter()
    .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
}

fn to_openapi(path: &str) -> String {
    path.split('/')
        .map(
            |seg| match seg.strip_prefix(':').or_else(|| seg.strip_prefix('*')) {
                Some(name) => format!("{{{name}}}"),
                None => seg.to_string(),
            },
        )
        .collect::<Vec<_>>()
        .join("/")
}

#[test]
fn mounted_local_and_browser_routes_are_in_the_committed_contract() {
    let spec = fragment();
    let expected: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut ours: Vec<String> = lattice_ingest::local_files_api::MOUNTED
        .iter()
        .chain(lattice_ingest::browser_api::MOUNTED.iter())
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    ours.sort();
    for key in &ours {
        if key.ends_with("/upload/document") || key.ends_with("/folder/prune") {
            continue;
        }
        assert!(
            expected.contains(key),
            "{key} is not in knowledge_search.json"
        );
    }
    assert_eq!(lattice_ingest::local_files_api::MOUNTED.len(), 26);
    assert_eq!(lattice_ingest::browser_api::MOUNTED.len(), 2);
}

#[test]
fn greedy_path_params_do_not_apply_to_this_slice() {
    let spec = fragment();
    let ours: Vec<String> = lattice_ingest::local_files_api::MOUNTED
        .iter()
        .chain(lattice_ingest::browser_api::MOUNTED.iter())
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    for (key, _) in spec["greedy_path_params"].as_object().unwrap() {
        assert!(
            !ours.contains(&key.to_string()),
            "{key} is not this crate's to wildcard"
        );
    }
}

#[tokio::test]
async fn native_obsidian_and_interop_ingest_write_directly_through_graph_writer() {
    let data = tempfile::tempdir().unwrap();
    let (origin, token, permissions, _) = boot(data.path()).await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let post_json = |path: &str, payload: serde_json::Value| {
        let client = client.clone();
        let url = format!("{origin}{path}");
        let cookie = format!("session_token={token}");
        async move {
            let resp = client
                .post(url)
                .header("cookie", cookie)
                .header("content-type", "application/json")
                .body(serde_json::to_vec(&payload).unwrap())
                .send()
                .await
                .unwrap();
            assert_eq!(resp.status().as_u16(), 200);
            let bytes = resp.bytes().await.unwrap();
            serde_json::from_slice::<serde_json::Value>(&bytes).unwrap()
        }
    };

    let vault = tempfile::tempdir().unwrap();
    std::fs::write(
        vault.path().join("Alpha.md"),
        "# Alpha\nLinks to [[Beta|Beta Note]] and #sprint.\n",
    )
    .unwrap();
    std::fs::write(vault.path().join("Beta.md"), "# Beta\nBack to [[Alpha]].\n").unwrap();
    let vault_s = vault.path().to_string_lossy().to_string();

    let probe = post_json(
        "/api/ingestion/obsidian",
        serde_json::json!({"path": vault_s, "dry_run": true}),
    )
    .await;
    let tok = probe["approval_token"].as_str().unwrap();
    assert!(permissions.approve(tok));
    let dry = post_json(
        "/api/ingestion/obsidian",
        serde_json::json!({
            "path": vault_s,
            "dry_run": true,
            "approved": true,
            "approval_token": tok,
        }),
    )
    .await;
    assert_eq!(dry["dry_run"], true);
    assert_eq!(dry["notes_found"], 2);
    assert_eq!(dry["wikilinks_detected"], 2);

    let probe2 = post_json(
        "/api/ingestion/obsidian",
        serde_json::json!({"path": vault_s, "dry_run": false}),
    )
    .await;
    let tok2 = probe2["approval_token"].as_str().unwrap();
    assert!(permissions.approve(tok2));
    let live = post_json(
        "/api/ingestion/obsidian",
        serde_json::json!({
            "path": vault_s,
            "dry_run": false,
            "approved": true,
            "approval_token": tok2,
        }),
    )
    .await;
    assert_eq!(live["ingested"], 2);
    assert_eq!(live["edges_created"], 2);

    let notion_dir = tempfile::tempdir().unwrap();
    std::fs::write(
        notion_dir
            .path()
            .join("Product Spec a1b2c3d4e5f60718293a4b5c6d7e8f90.md"),
        "# Product Spec\nLocal-first Digital Brain.",
    )
    .unwrap();
    let notion_s = notion_dir.path().to_string_lossy().to_string();
    let n_probe = post_json(
        "/api/ingestion/interop",
        serde_json::json!({"source": "notion", "path": notion_s}),
    )
    .await;
    let n_tok = n_probe["approval_token"].as_str().unwrap();
    assert!(permissions.approve(n_tok));
    let n_res = post_json(
        "/api/ingestion/interop",
        serde_json::json!({
            "source": "notion",
            "path": notion_s,
            "approved": true,
            "approval_token": n_tok,
        }),
    )
    .await;
    assert_eq!(n_res["ingested"], 1);

    let mail_dir = tempfile::tempdir().unwrap();
    std::fs::write(
        mail_dir.path().join("note.eml"),
        "Subject: Weekly Sync\nFrom: team@local\nDate: Tue, 24 Nov 2026 10:00:00 +0900\n\nShip v13.7.0.",
    )
    .unwrap();
    std::fs::write(
        mail_dir.path().join("cal.ics"),
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Release Review\nDTSTART:20261124T100000Z\nEND:VEVENT\nEND:VCALENDAR\n",
    )
    .unwrap();
    let mail_s = mail_dir.path().to_string_lossy().to_string();
    let m_probe = post_json(
        "/api/ingestion/interop",
        serde_json::json!({"source": "mail", "path": mail_s}),
    )
    .await;
    let m_tok = m_probe["approval_token"].as_str().unwrap();
    assert!(permissions.approve(m_tok));
    let m_res = post_json(
        "/api/ingestion/interop",
        serde_json::json!({
            "source": "mail",
            "path": mail_s,
            "approved": true,
            "approval_token": m_tok,
        }),
    )
    .await;
    assert_eq!(m_res["ingested"], 2);

    let git_dir = tempfile::tempdir().unwrap();
    let run_git = |args: &[&str]| {
        let status = std::process::Command::new("git")
            .current_dir(git_dir.path())
            .args(args)
            .env("GIT_AUTHOR_NAME", "Lattice Test")
            .env("GIT_AUTHOR_EMAIL", "test@lattice.local")
            .env("GIT_COMMITTER_NAME", "Lattice Test")
            .env("GIT_COMMITTER_EMAIL", "test@lattice.local")
            .status()
            .expect("git binary must be available for git interop test");
        assert!(status.success(), "git {args:?} failed");
    };
    run_git(&["init"]);
    std::fs::write(
        git_dir.path().join("README.md"),
        "# Demo Repo\nInitial commit.\n",
    )
    .unwrap();
    run_git(&["add", "README.md"]);
    run_git(&["commit", "-m", "feat: initialize demo repo"]);
    std::fs::write(
        git_dir.path().join("notes.md"),
        "Architecture notes for v13.7.1.\n",
    )
    .unwrap();
    run_git(&["add", "notes.md"]);
    run_git(&["commit", "-m", "docs: add architecture notes"]);

    let git_s = git_dir.path().to_string_lossy().to_string();
    let g_probe = post_json(
        "/api/ingestion/interop",
        serde_json::json!({"source": "git", "path": git_s, "dry_run": true}),
    )
    .await;
    let g_tok = g_probe["approval_token"].as_str().unwrap();
    assert!(permissions.approve(g_tok));
    let g_dry = post_json(
        "/api/ingestion/interop",
        serde_json::json!({
            "source": "git",
            "path": git_s,
            "dry_run": true,
            "approved": true,
            "approval_token": g_tok,
        }),
    )
    .await;
    assert_eq!(g_dry["dry_run"], true);
    assert_eq!(g_dry["items_found"], 2);

    let g_probe2 = post_json(
        "/api/ingestion/interop",
        serde_json::json!({"source": "git", "path": git_s, "dry_run": false}),
    )
    .await;
    let g_tok2 = g_probe2["approval_token"].as_str().unwrap();
    assert!(permissions.approve(g_tok2));
    let g_res = post_json(
        "/api/ingestion/interop",
        serde_json::json!({
            "source": "git",
            "path": git_s,
            "dry_run": false,
            "approved": true,
            "approval_token": g_tok2,
        }),
    )
    .await;
    assert_eq!(g_res["ingested"], 2);
}

#[tokio::test]
async fn unwired_graph_writer_returns_503_consistently() {
    let data = tempfile::tempdir().unwrap();
    seed_users(data.path());
    let db = data.path().join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).unwrap());
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data.path().to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@lattice.test"));
    let runtime = RuntimeConfig::resolve(
        Some(data.path().to_str().unwrap()),
        None,
        None,
        Some(data.path()),
    );
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
    let origin = format!("http://{addr}");
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let dir = tempfile::tempdir().unwrap();
    let dir_s = dir.path().to_string_lossy().to_string();

    for (endpoint, body) in [
        ("/api/ingestion/folder", serde_json::json!({"path": dir_s})),
        (
            "/api/ingestion/obsidian",
            serde_json::json!({"path": dir_s}),
        ),
        (
            "/api/ingestion/interop",
            serde_json::json!({"source": "notion", "path": dir_s}),
        ),
        (
            "/knowledge-graph/local/index",
            serde_json::json!({"path": dir_s}),
        ),
    ] {
        let probe: serde_json::Value = serde_json::from_slice(
            &client
                .post(format!("{origin}{endpoint}"))
                .header("cookie", format!("session_token={token}"))
                .header("content-type", "application/json")
                .body(serde_json::to_vec(&body).unwrap())
                .send()
                .await
                .unwrap()
                .bytes()
                .await
                .unwrap(),
        )
        .unwrap();
        let tok = probe["approval_token"].as_str().unwrap();
        assert!(permissions.approve(tok));
        let mut approved = body.clone();
        approved["approved"] = serde_json::json!(true);
        approved["approval_token"] = serde_json::json!(tok);
        let resp = client
            .post(format!("{origin}{endpoint}"))
            .header("cookie", format!("session_token={token}"))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&approved).unwrap())
            .send()
            .await
            .unwrap();
        assert_eq!(
            resp.status().as_u16(),
            503,
            "expected 503 when GraphWriter is unwired on {endpoint}"
        );
    }
}
