//! The vault-watch bridge, end to end over real sockets.
//!
//! `bridge_wired: false` was the honest answer for two releases: detection had
//! been testable since v11.5.0 (`crate::watch`) and the native note write since
//! v11.7.0 (`crate::worker::NoteIngestor`), but nothing joined them. This suite
//! is the proof that they are joined, and it asks the three questions that
//! matter:
//!
//! 1. **Does a change in a watched folder reach the Brain?** Not "did a scan
//!    run" — did a `Document` node with the note's provenance appear in SQLite.
//! 2. **Did it get there natively?** The stand-in worker mounts
//!    `POST /knowledge-graph/ingest` *and answers 200*, so a delegation would
//!    look like a success from the client side. It must be requested zero times.
//! 3. **Does enabling a watch re-ingest what was already there?** It must not:
//!    `enable()` is consent, and the baseline is taken at that moment.
//!
//! The watched vault is a tempdir of its own, never the data directory — a
//! scanner pointed at the data directory would ingest `users.json`.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::routing::post;
use axum::{Json, Router};
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_ingest::local_files_api::{self, LocalApprovals, LocalFilesState};
use serde_json::{json, Value};

/// A one-page PDF. Magic `%PDF` only — the stand-in worker mocks the parse.
const TINY_PDF: &[u8] = b"%PDF-1.1\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Size 2>>\n%%EOF\n";

#[derive(Clone, Default)]
struct SeamLog {
    paths: Arc<Mutex<Vec<String>>>,
}

impl SeamLog {
    fn hit(&self, path: &str) {
        self.paths.lock().expect("lock").push(path.to_string());
    }

    fn count(&self, path: &str) -> usize {
        self.paths
            .lock()
            .expect("lock")
            .iter()
            .filter(|seen| *seen == path)
            .count()
    }
}

/// The compute seams the enrich chain uses, plus the retired write door as a
/// tripwire that answers 200 so "nobody asked" has to be asserted.
async fn stand_in_worker(seams: SeamLog) -> String {
    let app = Router::new()
        .route(
            "/worker/extract",
            post({
                let seams = seams.clone();
                move |Json(_): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/extract");
                        Json(json!({"concepts": [], "triples": [], "semantic": []}))
                    }
                }
            }),
        )
        .route(
            "/worker/embed",
            post({
                let seams = seams.clone();
                move |Json(body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/embed");
                        let model = lattice_core::embeddings::LocalEmbeddingModel::from_env();
                        let vectors: Vec<Vec<f64>> = body["texts"]
                            .as_array()
                            .cloned()
                            .unwrap_or_default()
                            .iter()
                            .map(|text| model.embed(text.as_str().unwrap_or("")))
                            .collect();
                        Json(json!({
                            "vectors": vectors,
                            "dim": model.dim(),
                            "model_id": model.model_id(),
                        }))
                    }
                }
            }),
        )
        .route(
            "/worker/parse",
            post({
                let seams = seams.clone();
                move |Json(body): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/worker/parse");
                        Json(json!({
                            "filename": body["filename"],
                            "content": "분기 보고서 본문",
                            "pages": 1,
                        }))
                    }
                }
            }),
        )
        .route(
            "/knowledge-graph/ingest",
            post({
                let seams = seams.clone();
                move |Json(_): Json<Value>| {
                    let seams = seams.clone();
                    async move {
                        seams.hit("/knowledge-graph/ingest");
                        Json(json!({"status": "ok", "node_id": "webdoc:should-not-happen"}))
                    }
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind worker");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

fn seed_users(dir: &Path) {
    let email = "owner@lattice.test";
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("id", json!(lattice_auth::stable_user_id(email)));
    owner.insert("email", json!(email));
    let mut users = OrderedMap::new();
    users.insert(email, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

struct Install {
    origin: String,
    token: String,
    permissions: Arc<LocalApprovals>,
    state: Arc<LocalFilesState>,
    graph: GraphWriter,
    seams: SeamLog,
}

async fn boot(data: &Path) -> Install {
    seed_users(data);
    let seams = SeamLog::default();
    let worker_origin = stand_in_worker(seams.clone()).await;

    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
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

    let db = data.join("knowledge_graph.sqlite");
    let store = Arc::new(Store::open(&db).expect("store"));
    let graph =
        GraphWriter::open(Arc::clone(&store), data.join("knowledge_graph_blobs")).expect("writer");
    let runtime = RuntimeConfig::resolve(Some(data.to_str().unwrap()), None, None, Some(data));
    let permissions = LocalApprovals::new();
    let state = Arc::new(
        LocalFilesState::new(auth, Some(store), runtime)
            .with_graph(graph.clone())
            .with_seam(WorkerSeamClient::new(&worker_origin).expect("seam"))
            .with_permissions(Arc::clone(&permissions)),
    );
    let app = local_files_api::router(Arc::clone(&state));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr: SocketAddr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    Install {
        origin: format!("http://{addr}"),
        token,
        permissions,
        state,
        graph,
        seams,
    }
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .expect("client")
}

impl Install {
    async fn get_watch(&self) -> Value {
        let response = client()
            .get(format!("{}/api/ingestion/watch", self.origin))
            .header("cookie", format!("session_token={}", self.token))
            .send()
            .await
            .expect("watch status");
        assert_eq!(response.status().as_u16(), 200);
        json_of(response).await
    }

    /// The probe → approve → enable dance the route requires.
    async fn enable(&self, vault: &Path) -> Value {
        let path = vault.to_string_lossy().into_owned();
        let probe = client()
            .post(format!("{}/api/ingestion/watch", self.origin))
            .header("cookie", format!("session_token={}", self.token))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&json!({"path": path, "kind": "folder"})).unwrap())
            .send()
            .await
            .expect("probe");
        let probe: Value = json_of(probe).await;
        let approval = probe["approval_token"].as_str().expect("token").to_string();
        assert!(self.permissions.approve(&approval));
        let response = client()
            .post(format!("{}/api/ingestion/watch", self.origin))
            .header("cookie", format!("session_token={}", self.token))
            .header("content-type", "application/json")
            .body(
                serde_json::to_vec(&json!({
                    "path": path,
                    "recursive": true,
                    "kind": "folder",
                    "workspace_id": "personal",
                    "approved": true,
                    "approval_token": approval,
                }))
                .unwrap(),
            )
            .send()
            .await
            .expect("enable");
        assert_eq!(response.status().as_u16(), 200);
        json_of(response).await
    }

    fn documents(&self) -> Vec<(String, String)> {
        self.graph
            .store()
            .with_read_conn(|conn| {
                let mut statement = conn
                    .prepare("SELECT title, metadata_json FROM nodes WHERE type='Document' ORDER BY title")
                    .expect("prepare");
                let rows: Vec<(String, String)> = statement
                    .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
                    .expect("rows")
                    .filter_map(Result::ok)
                    .collect();
                Ok(rows)
            })
            .expect("read")
    }

    fn count(&self, sql: &str) -> i64 {
        self.graph
            .store()
            .with_read_conn(|conn| Ok(conn.query_row(sql, [], |row| row.get(0)).unwrap_or(0)))
            .expect("read")
    }
}

async fn json_of(response: reqwest::Response) -> Value {
    let text = response.text().await.expect("body");
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("not JSON ({err}): {text}"))
}

#[tokio::test]
async fn a_watched_vault_delivers_notes_into_the_brain_and_never_asks_the_worker_to_write() {
    let data = tempfile::tempdir().expect("data");
    // The vault is its own directory. Pointed at `data`, a scanner would find
    // `users.json` and cheerfully ingest the password file.
    let vault = tempfile::tempdir().expect("vault");
    std::fs::write(vault.path().join("onboarding.md"), "welcome to lattice\n").unwrap();
    std::fs::write(vault.path().join("회의록.md"), "회의 기록\n").unwrap();
    let install = boot(data.path()).await;

    // Before anything is enabled the bridge says so honestly.
    let idle = install.get_watch().await;
    assert_eq!(idle["enabled_count"], 0);
    assert_eq!(idle["polling"], false);
    assert_eq!(
        idle["vault_watch"]["bridge_wired"], true,
        "the poller delivers through NoteIngestor; this said false while nothing did"
    );

    // ── enable is consent, not ingestion ───────────────────────────────────
    let enabled = install.enable(vault.path()).await;
    assert_eq!(enabled["status"], "ok");
    assert_eq!(enabled["already_watching"], false);
    let watch = &enabled["watch"];
    assert_eq!(watch["owner"], "owner@lattice.test");
    assert_eq!(watch["workspace_id"], "personal");
    assert_eq!(watch["kind"], "folder");
    assert_eq!(watch["enabled"], true);
    assert_eq!(
        watch["tracked_files"], 2,
        "the baseline covers what is there"
    );
    assert_eq!(watch["last_scan_at"], Value::Null);
    assert_eq!(watch["last_result"], Value::Null);
    assert_eq!(watch["last_errors"], json!([]));
    assert!(watch["id"]
        .as_str()
        .unwrap_or_default()
        .starts_with("watch_"));
    assert!(watch["created_at"].as_str().is_some());
    assert_eq!(
        install.count("SELECT COUNT(*) FROM nodes"),
        0,
        "enabling a watch must never re-ingest a folder that was already there"
    );

    let after = install.get_watch().await;
    assert_eq!(after["enabled_count"], 1);
    assert_eq!(after["polling"], true, "a poller is live for this install");
    assert_eq!(after["watches"].as_array().map(Vec::len), Some(1));

    // ── a change flows ─────────────────────────────────────────────────────
    std::fs::write(
        vault.path().join("decisions.md"),
        "# 결정\n\n리트리벌 가중치를 0.7로 올린다.\n",
    )
    .unwrap();
    std::fs::write(vault.path().join("onboarding.md"), "welcome, revised\n").unwrap();
    std::fs::write(vault.path().join("report.pdf"), TINY_PDF).unwrap();

    let report = local_files_api::scan_watches(&install.state).await;
    assert_eq!(report["status"], "ok");
    let scan = &report["watches"][0];
    assert_eq!(scan["status"], "ok", "{report}");
    assert_eq!(
        scan["ingested"], 3,
        "one new, one changed, one PDF: {report}"
    );
    assert_eq!(scan["failed"], 0);
    assert_eq!(scan["removed"], 0);

    // …natively. The tripwire answers 200, so this has to be asserted.
    assert_eq!(
        install.seams.count("/knowledge-graph/ingest"),
        0,
        "the watch write is native; a request here is the v11.6.0 strand returning"
    );
    // The binary half is F-ING's parse seam, composed with rather than bypassed.
    assert_eq!(
        install.seams.count("/worker/parse"),
        1,
        "the PDF was parsed"
    );
    assert!(install.seams.count("/worker/extract") >= 3);
    assert!(install.seams.count("/worker/embed") >= 3);

    let documents = install.documents();
    let titles: Vec<&str> = documents.iter().map(|(title, _)| title.as_str()).collect();
    assert!(titles.contains(&"decisions.md"), "{titles:?}");
    assert!(titles.contains(&"onboarding.md"), "{titles:?}");
    assert!(titles.contains(&"report.pdf"), "{titles:?}");
    assert!(
        !titles.contains(&"회의록.md"),
        "the unchanged file stayed out: {titles:?}"
    );

    let (_, metadata) = documents
        .iter()
        .find(|(title, _)| title == "decisions.md")
        .expect("the new note");
    let metadata: Value = serde_json::from_str(metadata).expect("node metadata");
    assert_eq!(metadata["source_type"], "note");
    assert_eq!(metadata["owner"], "owner@lattice.test");
    assert_eq!(metadata["workspace_id"], "personal");
    assert_eq!(metadata["folder_watch"], true);
    assert_eq!(metadata["detected_by"], "lattice-ingest");
    assert_eq!(metadata["relative_path"], "decisions.md");
    assert!(metadata["watch_id"]
        .as_str()
        .unwrap_or_default()
        .starts_with("watch_"));

    // The PDF carries the parsed text, not its bytes.
    let (_, pdf_metadata) = documents
        .iter()
        .find(|(title, _)| title == "report.pdf")
        .expect("the parsed pdf");
    assert!(pdf_metadata.contains("report.pdf"));
    let pdf_summary: String = install
        .graph
        .store()
        .with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT summary FROM nodes WHERE type='Document' AND title='report.pdf'",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or_default())
        })
        .expect("read");
    assert_eq!(pdf_summary, "분기 보고서 본문");

    assert_eq!(
        install.count("SELECT COUNT(*) FROM ingestion_provenance WHERE source_type='note'"),
        3,
        "every watched note records where it came from"
    );
    assert!(
        install.count("SELECT COUNT(*) FROM chunks") >= 3,
        "the shared enrich chain chunked them for retrieval"
    );

    // ── the status route now reports the scan ──────────────────────────────
    let scanned = install.get_watch().await;
    let entry = &scanned["watches"][0];
    assert!(entry["last_scan_at"].as_str().is_some());
    assert_eq!(entry["last_result"]["ingested"], 3);
    assert_eq!(entry["last_result"]["status"], "ok");
    assert_eq!(entry["tracked_files"], 4);

    // ── a second scan with nothing new is a no-op, not a re-ingest ─────────
    let again = local_files_api::scan_watches(&install.state).await;
    assert_eq!(again["watches"][0]["ingested"], 0, "{again}");
    assert_eq!(install.documents().len(), documents.len());

    // ── disable stops the poller and forgets the snapshot ──────────────────
    let removed = client()
        .delete(format!(
            "{}/api/ingestion/watch?path={}",
            install.origin,
            urlencode(&vault.path().to_string_lossy())
        ))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("disable");
    assert_eq!(removed.status().as_u16(), 200);
    let removed: Value = json_of(removed).await;
    assert_eq!(removed["status"], "ok");
    assert_eq!(
        removed["watch"]["path"],
        vault.path().to_string_lossy().as_ref()
    );

    let idle = install.get_watch().await;
    assert_eq!(idle["enabled_count"], 0);
    assert_eq!(
        idle["polling"], false,
        "the poller stopped with the last watch"
    );
    assert_eq!(
        local_files_api::scan_watches(&install.state).await["watches"]
            .as_array()
            .map(Vec::len),
        Some(0)
    );
}

#[tokio::test]
async fn enabling_the_same_folder_twice_is_one_watch() {
    let data = tempfile::tempdir().expect("data");
    let vault = tempfile::tempdir().expect("vault");
    std::fs::write(vault.path().join("a.md"), "본문\n").unwrap();
    let install = boot(data.path()).await;

    let first = install.enable(vault.path()).await;
    assert_eq!(first["already_watching"], false);
    let second = install.enable(vault.path()).await;
    assert_eq!(second["already_watching"], true);
    assert_eq!(second["watch"]["id"], first["watch"]["id"]);
    assert_eq!(install.get_watch().await["enabled_count"], 1);
}

#[tokio::test]
async fn a_vault_that_vanishes_is_a_counted_failure_not_a_dead_poller() {
    let data = tempfile::tempdir().expect("data");
    let vault = tempfile::tempdir().expect("vault");
    std::fs::write(vault.path().join("a.md"), "본문\n").unwrap();
    let install = boot(data.path()).await;
    install.enable(vault.path()).await;
    std::fs::remove_dir_all(vault.path()).unwrap();

    let report = local_files_api::scan_watches(&install.state).await;
    assert_eq!(report["status"], "ok", "the pass completed");
    assert_eq!(report["watches"][0]["status"], "failed", "{report}");
    assert!(report["watches"][0]["detail"].as_str().is_some());
    // …and the failure is visible on the status route rather than swallowed.
    let entry = &install.get_watch().await["watches"][0];
    assert_eq!(entry["last_result"]["status"], "failed");
}

#[tokio::test]
async fn a_deleted_watched_file_is_pruned_from_the_graph() {
    let data = tempfile::tempdir().expect("data");
    let vault = tempfile::tempdir().expect("vault");
    let install = boot(data.path()).await;
    // Enable is consent, not ingest: files added after the baseline are the
    // ones this scan delivers, and the one we then delete is the prune case.
    install.enable(vault.path()).await;
    std::fs::write(vault.path().join("keep.md"), "남는다\n").unwrap();
    std::fs::write(vault.path().join("gone.md"), "사라진다\n").unwrap();

    let first = local_files_api::scan_watches(&install.state).await;
    assert_eq!(first["watches"][0]["ingested"], 2, "{first}");
    assert_eq!(install.documents().len(), 2);

    std::fs::remove_file(vault.path().join("gone.md")).unwrap();
    let second = local_files_api::scan_watches(&install.state).await;
    let scan = &second["watches"][0];
    assert_eq!(scan["removed"], 1, "{second}");
    assert_eq!(scan["status"], "ok", "{second}");
    let titles: Vec<String> = install
        .documents()
        .into_iter()
        .map(|(title, _)| title)
        .collect();
    assert!(titles.contains(&"keep.md".to_string()), "{titles:?}");
    assert!(
        !titles.contains(&"gone.md".to_string()),
        "the vanished file must leave the graph: {titles:?}"
    );
}

/// Percent-encode a filesystem path for a query string. Small on purpose.
fn urlencode(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}
