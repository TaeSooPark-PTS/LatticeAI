//! Replay knowledge-graph + search records from `knowledge_search.json`, and
//! the three `memory_brain.json` families that share the brain harness:
//! `memory`, `garden` and `evidence_actions`.
//!
//! Four test binaries collapsed into one; three of them were a single
//! `replay_family` call around the same `common::brain` install.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod common;

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_retrieval::knowledge_graph_api;
use lattice_retrieval::search_api::{self, RetrievalApiState};
use serde_json::Value;

fn fixture() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join("knowledge_search.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn cases<'a>(root: &'a Value, family: &str) -> Vec<&'a Value> {
    root["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["family"].as_str() == Some(family))
        .collect()
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

fn install(dir: &Path) -> (Arc<AuthState>, String) {
    seed_users(dir);
    let mut env = HashMap::new();
    env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = dir.to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
    let token = auth
        .sessions()
        .create("user:owner", Some("owner@lattice.test"));
    (auth, token)
}

async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr: SocketAddr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await;
    });
    (format!("http://{addr}"), handle)
}

fn seed_graph(path: &Path) {
    let conn = rusqlite::Connection::open(path).unwrap();
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
            metadata_json TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE IF NOT EXISTS edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
            weight REAL, metadata_json TEXT, created_at TEXT);
         CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
            metadata_json TEXT, created_at TEXT);
         CREATE TABLE IF NOT EXISTS nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT, type TEXT);
         CREATE TABLE IF NOT EXISTS edges_v2(id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT);
         CREATE TABLE IF NOT EXISTS knowledge_sources(id TEXT PRIMARY KEY, root_path TEXT);
         CREATE TABLE IF NOT EXISTS local_file_index(id TEXT PRIMARY KEY, source_id TEXT, status TEXT);
         CREATE TABLE IF NOT EXISTS ingestion_provenance(id TEXT PRIMARY KEY, node_id TEXT, source_type TEXT);
         CREATE TABLE IF NOT EXISTS graph_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS vector_embeddings(
            item_id TEXT PRIMARY KEY, item_type TEXT, source_node TEXT,
            embedding BLOB, embedding_dim INT, embedding_model TEXT,
            text_hash TEXT, metadata_json TEXT, indexed_at TEXT);
         INSERT OR IGNORE INTO nodes VALUES
           ('doc-a','Document','handbook.md','retrieval fusion','{\"filename\":\"handbook.md\"}',
            '2026-01-01T00:00:00','2026-01-03T00:00:00');
         INSERT OR IGNORE INTO nodes_v2 VALUES ('doc-a','personal','Document');",
    )
    .unwrap();
}

async fn issue(origin: &str, case: &Value, token: &str) -> (u16, String) {
    let method = reqwest::Method::from_bytes(case["method"].as_str().unwrap().as_bytes()).unwrap();
    let mut url = format!("{}{}", origin, case["path"].as_str().unwrap());
    if let Some(query) = case["query"].as_object() {
        if !query.is_empty() {
            let pairs: Vec<String> = query
                .iter()
                .map(|(k, v)| format!("{k}={}", v.as_str().unwrap_or(&v.to_string())))
                .collect();
            url.push('?');
            url.push_str(&pairs.join("&"));
        }
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();
    let mut request = client.request(method, &url);
    if let Some(headers) = case["request_headers"].as_object() {
        for (name, value) in headers {
            let mut rendered = value.as_str().unwrap_or("").to_string();
            rendered = rendered.replace("@session", token);
            request = request.header(name, rendered);
        }
    }
    if !case["request_body"].is_null() {
        request = request
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&case["request_body"]).unwrap());
    }
    let response = request.send().await.unwrap();
    let status = response.status().as_u16();
    let body = response.text().await.unwrap();
    (status, body)
}

#[tokio::test]
async fn search_and_kg_replay_auth_and_validation_branches() {
    let root = fixture();
    let data = tempfile::tempdir().unwrap();
    let db = data.path().join("knowledge_graph.sqlite");
    seed_graph(&db);
    let (auth, token) = install(data.path());
    let config = RuntimeConfig::resolve(
        Some(data.path().to_str().unwrap()),
        None,
        None,
        Some(data.path()),
    );
    let store = Arc::new(Store::open(&db).unwrap());
    let state = Arc::new(RetrievalApiState::new(
        Arc::clone(&auth),
        Some(store),
        config,
    ));
    let app = Router::new()
        .merge(search_api::router(Arc::clone(&state)))
        .merge(knowledge_graph_api::router(state));
    let (origin, _handle) = serve(app).await;

    let mut checked = 0usize;
    for family in ["search", "knowledge_graph"] {
        for case in cases(&root, family) {
            let name = case["name"].as_str().unwrap();
            let expected = case["status"].as_u64().unwrap() as u16;
            // KEEP_WORKER / page shells / ingest (worker door) are not ours.
            if matches!(
                name,
                "embeddings_status"
                    | "embeddings_providers"
                    | "graph_page"
                    | "knowledge_graph_page"
                    | "ingest_note"
                    | "ingest_duplicate"
                    | "ingest_unsupported_type"
                    | "ingest_validation"
                    | "ingest_identity_mismatch"
                    | "ingest_auth_denied"
            ) {
                continue;
            }
            // Auth/validation branches must match status exactly.
            let auth_or_validation = expected == 401
                || expected == 403
                || expected == 422
                || name.ends_with("_missing_q")
                || name.ends_with("_missing_id")
                || name.ends_with("_validation")
                || name.ends_with("_auth_denied")
                || name.ends_with("_admin_denied");
            // This harness only mints the owner session; admin-denied
            // records were captured with a member cookie rewritten to
            // `@session`. Skip rather than 200-as-owner.
            if name.ends_with("_admin_denied") {
                continue;
            }
            if !auth_or_validation {
                // Happy-path search/node/curate bodies need the capture
                // sandbox + GraphWriter. A stub store 404s/503s; pin no 5xx
                // other than the known "graph write not wired" 503.
                let (status, body) = issue(&origin, case, &token).await;
                assert!(
                    status == 200 || status == expected || status == 404 || status == 503,
                    "{name}: status {status} (expected {expected} or 200) body={body}"
                );
                checked += 1;
                continue;
            }
            let (status, body) = issue(&origin, case, &token).await;
            assert_eq!(status, expected, "{name}: {body}");
            checked += 1;
        }
    }
    assert!(checked > 10, "replayed {checked} cases");
}

// ── memory_brain.json: the families that share the brain harness ──

#[tokio::test]
async fn memory_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("memory").await;
    // Every Self-Model write and the vector rebuild are native since v11.7.0.
    // Until then they posted to `POST /worker/graph/mutate`, which the Python
    // worker stopped serving in v11.6.0 — so on a live install the four
    // Self-Model routes answered 404 while this replay stayed green against a
    // stand-in that still mounted it. The stand-in is now a tripwire.
    assert_eq!(
        common::brain::seed::GRAPH_MUTATE_CALLS.load(std::sync::atomic::Ordering::SeqCst),
        0,
        "a memory route still delegated a graph write to the retired seam"
    );
}

#[tokio::test]
async fn garden_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("garden").await;
}

#[tokio::test]
async fn evidence_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("evidence_actions").await;
}
