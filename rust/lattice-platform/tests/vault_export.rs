use std::collections::{BTreeMap, HashMap};
use std::io::{Cursor, Read};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::password::hash_password;
use lattice_auth::users::ensure_user_identity;
use lattice_auth::{stable_user_id, AuthConfig, AuthState, Clock, Users};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_platform::knowledge::portability::{self, PortabilityState};
use serde_json::{json, Map, Value};

const ADMIN: &str = "admin@lattice.test";
const MEMBER: &str = "member@lattice.test";

struct Install {
    origin: String,
    cookie: Option<String>,
    graph: GraphWriter,
    _dir: tempfile::TempDir,
}

fn account(email: &str, role: &str) -> Map<String, Value> {
    let mut record = Map::new();
    record.insert(
        "password".into(),
        json!(hash_password("Fixture0pass1").expect("hash")),
    );
    record.insert("name".into(), json!(email));
    record.insert("role".into(), json!(role));
    record.insert("disabled".into(), json!(false));
    ensure_user_identity(email, &mut record);
    record
}

impl Install {
    async fn start(require_auth: bool) -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let data = dir.path().to_path_buf();
        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_ENABLE_GRAPH".into(), "1".into());
        if require_auth {
            env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        }
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.clone();
        let auth = AuthState::with_clock(config, Clock::system());
        let cookie = require_auth.then(|| {
            let mut users = Users::new();
            users.insert(ADMIN, account(ADMIN, "admin"));
            users.insert(MEMBER, account(MEMBER, "user"));
            auth.users().save(&users);
            let token = auth.sessions().create(&stable_user_id(ADMIN), Some(ADMIN));
            format!("session_token={token}")
        });
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
            let _ = axum::serve(listener, portability::router(state).into_make_service()).await;
        });
        Self {
            origin: format!("http://{addr}"),
            cookie,
            graph,
            _dir: dir,
        }
    }

    async fn vault(&self, body: Value) -> (u16, BTreeMap<String, String>, String) {
        let mut request = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(30))
            .build()
            .expect("client")
            .post(format!("{}/api/knowledge-graph/export-vault", self.origin))
            .header("content-type", "application/json");
        if let Some(cookie) = &self.cookie {
            request = request.header("cookie", cookie);
        }
        let response = request
            .body(serde_json::to_vec(&body).expect("encode"))
            .send()
            .await
            .expect("request");
        let status = response.status().as_u16();
        let withheld = response
            .headers()
            .get("x-lattice-vault-withheld")
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .to_string();
        let bytes = response.bytes().await.expect("body");
        let mut files = BTreeMap::new();
        if status == 200 {
            let mut archive = zip::ZipArchive::new(Cursor::new(bytes.to_vec())).expect("zip");
            for index in 0..archive.len() {
                let mut entry = archive.by_index(index).expect("entry");
                let name = entry.name().expect("name").into_owned();
                let mut text = String::new();
                entry.read_to_string(&mut text).expect("utf-8");
                files.insert(name, text);
            }
        }
        (status, files, withheld)
    }

    fn execute(&self, sql: &str) {
        self.graph
            .store()
            .with_write_txn(|txn| Ok(txn.execute_batch(sql)?))
            .expect("execute");
    }

    fn rows(&self, sql: &str) -> Vec<Vec<Option<String>>> {
        self.graph
            .store()
            .with_read_conn(|conn| {
                let mut stmt = conn.prepare(sql)?;
                let width = stmt.column_count();
                let rows = stmt.query_map([], |row| {
                    (0..width)
                        .map(|i| row.get::<_, Option<String>>(i))
                        .collect::<Result<Vec<_>, _>>()
                })?;
                Ok(rows.collect::<Result<Vec<_>, _>>()?)
            })
            .expect("rows")
    }

    fn node(&self, id: &str, kind: &str, title: &str, metadata: Value, owner: Option<&str>) {
        let meta = metadata.to_string().replace('\'', "''");
        let owner = owner.map_or("NULL".to_string(), |o| format!("'{o}'"));
        self.execute(&format!(
            "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at) \
             VALUES ('{id}', '{kind}', '{title}', '', '{meta}', '{{}}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z'); \
             INSERT INTO nodes_v2 (id, type, legacy_type, label, attrs, owner_id, workspace_id, created_at, updated_at) \
             VALUES ('{id}', upper('{kind}'), '{kind}', '{title}', '{meta}', {owner}, 'personal', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z');"
        ));
    }

    fn edge(&self, id: &str, from: &str, to: &str) {
        self.execute(&format!(
            "INSERT INTO edges (id, from_node, to_node, type, weight, metadata_json, created_at) \
             VALUES ('{id}', '{from}', '{to}', 'RELATED_TO', 1.0, '{{}}', '2026-11-15T00:00:00Z')"
        ));
    }
}

fn targets(text: &str) -> Vec<String> {
    text.split("[[")
        .skip(1)
        .filter_map(|rest| rest.split_once("]]"))
        .map(|(inner, _)| {
            inner
                .split(['|', '#'])
                .next()
                .unwrap_or_default()
                .trim()
                .to_lowercase()
        })
        .collect()
}

fn personal() -> Value {
    json!({"workspace_id": "personal"})
}

#[tokio::test]
async fn a_vault_never_carries_foreign_or_blocked_knowledge() {
    let source = Install::start(true).await;
    source.node("idea", "Concept", "회의: 아이디어", personal(), None);
    source.node(
        "doc",
        "Document",
        "Plan",
        json!({"workspace_id": "personal", "source_uri": "/home/me/plan.md", "owner": ADMIN}),
        Some(ADMIN),
    );
    source.node(
        "their-doc",
        "Document",
        "Their diary",
        json!({"workspace_id": "personal", "owner": MEMBER}),
        Some(MEMBER),
    );
    source.node(
        "their-chat",
        "Concept",
        "Their derived",
        json!({"workspace_id": "personal", "user_email": MEMBER, "source_node": "message:1"}),
        Some(MEMBER),
    );
    source.node(
        "hidden",
        "Document",
        "Local only",
        json!({"workspace_id": "personal", "local_only": true}),
        None,
    );
    source.node("key", "ApiKey", "Provider key", personal(), None);
    source.node("elsewhere", "Document", "Org plan", personal(), None);
    source.execute(
        "UPDATE nodes SET metadata_json = '{\"workspace_id\":\"org-1\"}' WHERE id = 'elsewhere'",
    );
    source.edge("e1", "doc", "idea");
    source.edge("e2", "doc", "their-doc");
    source.execute(
        "INSERT INTO chunks (id, source_node, text, metadata_json, created_at) \
         VALUES ('chunk-1', 'doc', 'Ship it. api_key: supersecretvalue123', '{\"index\":0}', '2026-11-15T00:00:00Z')",
    );

    let (status, _, _) = source.vault(json!({})).await;
    assert_eq!(status, 400);
    let (status, files, withheld) = source.vault(personal()).await;
    assert_eq!(status, 200);
    assert_eq!(withheld, "2");
    let names: Vec<&str> = files.keys().map(String::as_str).collect();
    assert_eq!(names, ["Plan.md", "_export.json", "회의 아이디어.md"]);
    let plan = &files["Plan.md"];
    assert!(plan.starts_with("---\nid: \"doc\"\n"), "{plan}");
    assert!(plan.contains("source_uri: \"/home/me/plan.md\""));
    assert!(plan.contains("workspace: \"personal\""));
    assert!(plan.contains("valid_from: \"2026-11-15T00:00:00Z\""));
    assert!(plan.contains("Ship it."));
    assert!(!plan.contains("supersecretvalue123"));
    assert!(plan.contains("[[회의 아이디어|회의: 아이디어]]"));
    assert!(!plan.contains("Their diary"));
    assert_eq!(targets(plan), ["회의 아이디어"]);
}

#[tokio::test]
async fn superseded_notes_move_to_history_and_links_still_resolve() {
    let source = Install::start(false).await;
    source.node("old", "Decision", "Pricing", personal(), None);
    source.node("new", "Decision", "pricing", personal(), None);
    source.node("traversal", "Concept", "../../escape", personal(), None);
    source.execute(
        "UPDATE nodes_v2 SET superseded_by = 'new', valid_to = '2026-11-16T00:00:00Z' WHERE id = 'old'",
    );
    source.edge("e1", "traversal", "old");
    source.edge("e2", "new", "traversal");
    let before = source.rows("SELECT id, updated_at FROM nodes ORDER BY id");

    let (status, files, _) = source.vault(json!({})).await;
    assert_eq!(status, 200);
    assert_eq!(
        source.rows("SELECT id, updated_at FROM nodes ORDER BY id"),
        before
    );
    let stems: Vec<String> = files
        .keys()
        .filter_map(|name| name.strip_suffix(".md"))
        .map(|name| name.rsplit('/').next().unwrap_or(name).to_lowercase())
        .collect();
    assert!(files.keys().all(|name| !name.contains("..")));
    assert!(files.contains_key("escape.md"));
    let (history, text) = files
        .iter()
        .find(|(name, _)| name.starts_with("_history/"))
        .expect("history note");
    assert!(history.to_lowercase().starts_with("_history/pricing"));
    assert!(text.contains("superseded_by: \"new\""));
    assert!(text.contains("valid_to: \"2026-11-16T00:00:00Z\""));
    let links: Vec<String> = files.values().flat_map(|text| targets(text)).collect();
    assert_eq!(links.len(), 3);
    assert!(links.iter().all(|link| stems.contains(link)), "{links:?}");
}
