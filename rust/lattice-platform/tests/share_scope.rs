use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use lattice_auth::password::hash_password;
use lattice_auth::users::ensure_user_identity;
use lattice_auth::{stable_user_id, AuthConfig, AuthState, Clock, Users};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_platform::knowledge::network::DeviceIdentity;
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

    async fn post(&self, path: &str, body: Value) -> (u16, Value) {
        let mut request = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(30))
            .build()
            .expect("client")
            .post(format!("{}{path}", self.origin))
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
        let text = response.text().await.unwrap_or_default();
        (
            status,
            serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text})),
        )
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

    fn count(&self, sql: &str) -> i64 {
        self.graph
            .store()
            .with_read_conn(|conn| Ok(conn.query_row(sql, [], |row| row.get(0))?))
            .expect("count")
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

fn ids(bundle: &Value) -> Vec<String> {
    let mut out: Vec<String> = bundle["nodes"]
        .as_array()
        .expect("nodes")
        .iter()
        .map(|node| node["id"].as_str().unwrap_or_default().to_string())
        .collect();
    out.sort();
    out
}

#[tokio::test]
async fn a_share_export_never_carries_another_users_conversations() {
    let source = Install::start(true).await;
    let mine = json!({"workspace_id": "personal", "user_email": ADMIN});
    let theirs = json!({"workspace_id": "personal", "user_email": MEMBER});
    source.node(
        "idea",
        "Concept",
        "Shared idea",
        json!({"workspace_id": "personal"}),
        None,
    );
    source.node("my-msg", "Message", "Mine", mine, Some(ADMIN));
    source.node(
        "their-msg",
        "Message",
        "Theirs",
        theirs.clone(),
        Some(MEMBER),
    );
    source.node(
        "their-chat",
        "Chat",
        "Their chat",
        theirs.clone(),
        Some(MEMBER),
    );
    source.execute(&format!(
        "INSERT INTO nodes (id, type, title, summary, metadata_json, raw_json, created_at, updated_at) \
         VALUES ('legacy-msg', 'Message', 'Legacy theirs', '', '{}', '{{}}', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')",
        theirs
    ));
    source.edge("e1", "idea", "their-msg");
    source.edge("e2", "idea", "my-msg");

    let (status, reply) = source
        .post(
            "/api/knowledge-graph/share/export",
            json!({"node_types": ["Message", "Chat", "Concept"]}),
        )
        .await;
    assert_eq!(status, 400, "workspace_id is mandatory with auth: {reply}");

    let (status, reply) = source
        .post(
            "/api/knowledge-graph/share/export",
            json!({"node_types": ["Message", "Chat", "Concept"], "workspace_id": "personal"}),
        )
        .await;
    assert_eq!(status, 200, "{reply}");
    assert_eq!(ids(&reply["bundle"]), vec!["idea", "my-msg"]);

    let (status, reply) = source
        .post(
            "/api/knowledge-graph/share/export",
            json!({"node_ids": ["idea"], "include_neighbors": true, "workspace_id": "personal"}),
        )
        .await;
    assert_eq!(status, 200, "{reply}");
    assert_eq!(ids(&reply["bundle"]), vec!["idea", "my-msg"]);
    assert_eq!(reply["bundle"]["edges"].as_array().map(Vec::len), Some(1));
}

fn signed(identity: &DeviceIdentity, mut body: Value) -> Value {
    let canonical = json!({
        "header": body["header"].clone(),
        "nodes": body["nodes"].clone(),
        "edges": body["edges"].clone(),
        "chunks": body["chunks"].clone(),
        "knowledge_sources": body["knowledge_sources"].clone(),
        "provenance": body["provenance"].clone(),
    });
    let signature = identity.sign(&serde_json::to_vec(&canonical).expect("encode"));
    body["signature"] = json!({
        "algorithm": "ed25519",
        "public_key": identity.public_key_b64(),
        "fingerprint": identity.fingerprint(),
        "signature": signature,
    });
    body
}

fn crafted_bundle() -> Value {
    let foreign = json!({
        "workspace_id": "team",
        "owner": "victim@x",
        "user_email": "victim@x",
        "superseded_by": "doc-1",
    })
    .to_string();
    let node = |id: &str, kind: &str, title: &str| {
        json!({
            "id": id, "type": kind, "title": title, "summary": "",
            "metadata_json": foreign, "raw_json": "{}",
            "created_at": "2026-11-15T00:00:00Z", "updated_at": "2026-11-15T00:00:00Z",
        })
    };
    json!({
        "header": {"format": "latticeai.kg.subgraph", "format_version": 1},
        "nodes": [
            node("doc-1", "Document", "Hijacked"),
            node("shared-1", "Concept", "Shared concept"),
            node("msg-1", "Message", "Injected message"),
            node("secret-1", "Secret", "Injected secret"),
        ],
        "edges": [
            {"id": "x1", "from_node": "shared-1", "to_node": "doc-1", "type": "RELATED_TO",
             "weight": 1.0, "metadata_json": "{}", "created_at": "2026-11-15T00:00:00Z"},
        ],
        "chunks": [],
        "knowledge_sources": [
            {"id": "s1", "root_path": "/tmp/attacker-root", "os_type": "macos", "watch_enabled": 1},
        ],
        "provenance": [
            {"id": "p1", "node_id": "shared-1", "source_type": "note", "pipeline": "share",
             "owner": "victim@x", "workspace_id": "team",
             "permissions_json": "{\"admin\":true}", "metadata_json": "{}"},
        ],
    })
}

#[tokio::test]
async fn accepting_a_share_never_overwrites_or_reassigns_local_knowledge() {
    let target = Install::start(false).await;
    target.node(
        "doc-1",
        "Document",
        "Local truth",
        json!({"workspace_id": "personal"}),
        None,
    );
    let identity = DeviceIdentity::load_or_create(&target._dir.path().join("sender.key"));

    let (status, reply) = target
        .post(
            "/api/knowledge-graph/share/import",
            json!({"artifact": crafted_bundle()}),
        )
        .await;
    assert_eq!(status, 400, "unsigned bundles are refused: {reply}");

    let (status, staged) = target
        .post(
            "/api/knowledge-graph/share/import",
            json!({"artifact": signed(&identity, crafted_bundle())}),
        )
        .await;
    assert_eq!(status, 200, "{staged}");
    assert_eq!(staged["sender_trusted"], json!(false));
    let proposal = staged["proposal_id"].as_str().expect("proposal");
    let (status, accepted) = target
        .post(
            &format!("/api/knowledge-graph/share/proposals/{proposal}/accept"),
            json!({}),
        )
        .await;
    assert_eq!(status, 200, "{accepted}");
    assert_eq!(accepted["import"]["nodes"], json!(1));
    assert_eq!(accepted["import"]["skipped_existing"], json!(1));
    assert_eq!(accepted["import"]["skipped_unsafe"], json!(2));
    assert_eq!(accepted["import"]["ignored_knowledge_sources"], json!(1));
    assert_eq!(accepted["import"]["edges"], json!(0));

    assert_eq!(
        target.rows("SELECT title FROM nodes WHERE id = 'doc-1'"),
        vec![vec![Some("Local truth".to_string())]]
    );
    assert_eq!(
        target.count("SELECT COUNT(*) FROM nodes WHERE id IN ('msg-1', 'secret-1')"),
        0
    );
    assert_eq!(target.count("SELECT COUNT(*) FROM knowledge_sources"), 0);
    assert_eq!(target.count("SELECT COUNT(*) FROM edges"), 0);
    assert_eq!(
        target.rows(
            "SELECT json_extract(n.metadata_json, '$.workspace_id'), \
                    json_extract(n.metadata_json, '$.owner'), \
                    json_extract(n.metadata_json, '$.user_email'), \
                    json_extract(n.metadata_json, '$.superseded_by'), \
                    v.workspace_id, v.owner_id, v.superseded_by \
             FROM nodes n JOIN nodes_v2 v ON v.id = n.id WHERE n.id = 'shared-1'"
        ),
        vec![vec![
            Some("personal".to_string()),
            None,
            None,
            None,
            Some("personal".to_string()),
            None,
            None
        ]]
    );
    assert_eq!(
        target.rows(
            "SELECT owner, workspace_id, permissions_json FROM ingestion_provenance WHERE id = 'p1'"
        ),
        vec![vec![
            None,
            Some("personal".to_string()),
            Some("{}".to_string())
        ]]
    );

    let (status, again) = target
        .post(
            &format!("/api/knowledge-graph/share/proposals/{proposal}/accept"),
            json!({}),
        )
        .await;
    assert_eq!(status, 409, "{again}");
}
