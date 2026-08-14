//! Shared replay harness for the R7 governance families.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports, unused_variables)]
#![allow(
    clippy::too_many_arguments,
    clippy::unnecessary_sort_by,
    clippy::field_reassign_with_default
)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::post;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_core::worker::WorkerSeamClient;
use lattice_platform::automation;
use lattice_platform::change_proposals;
use lattice_platform::hooks::{self, HooksState};
use lattice_platform::review_queue::{self, GovernanceState};
use serde_json::{json, Value};

pub const FIXTURE: &str = "review_proposals.json";

pub fn fixture() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join(FIXTURE);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("fixture is valid JSON")
}

pub fn cases_for(family: &str) -> Vec<Value> {
    fixture()["fixtures"]
        .as_array()
        .expect("fixtures")
        .iter()
        .filter(|case| case["family"].as_str() == Some(family))
        .cloned()
        .collect()
}

pub fn fragment() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("openapi")
        .join("review_proposals.json");
    let text = std::fs::read_to_string(&path).expect("openapi fragment");
    serde_json::from_str(&text).expect("fragment json")
}

pub fn to_openapi(path: &str) -> String {
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

/// One running install: auth + four family routers + a FakeWorker.
pub struct Install {
    pub origin: String,
    pub worker_origin: String,
    pub data_dir: PathBuf,
    pub agent_root: PathBuf,
    pub owner_token: String,
    pub member_token: String,
    /// The store the four routers read — and the one the agent loop stages
    /// into, since it implements `lattice_agent::proposals::ProposalStore`.
    pub gov: GovernanceState,
    pub symbols: HashMap<String, String>,
    hooks: HooksState,
    _data: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
    _worker: tokio::task::JoinHandle<()>,
}

impl Install {
    pub async fn start() -> Self {
        let data = tempfile::tempdir().expect("data dir");
        let data_dir = data.path().to_path_buf();
        let agent_root = data_dir.join("agent_workspace");
        std::fs::create_dir_all(&agent_root).expect("agent root");

        seed_users(&data_dir);

        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
        env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data_dir.to_string_lossy().into_owned(),
        );
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data_dir.clone();
        let auth = AuthState::with_clock(config, Clock::frozen(1_786_000_000.0));
        let owner_token = auth
            .sessions()
            .create("user:owner", Some("owner@lattice.test"));
        let member_token = auth
            .sessions()
            .create("user:member", Some("member@lattice.test"));

        let worker_origin = spawn_fake_worker(agent_root.clone()).await;
        let worker = WorkerSeamClient::new(&worker_origin).expect("worker client");
        let gov = GovernanceState::open(
            Arc::clone(&auth),
            data_dir.clone(),
            agent_root.clone(),
            Some(worker),
        );

        // Seed files the proposals apply against.
        write_file(
            &agent_root.join("fixture-note.md"),
            "Fixture original line.\n",
        );
        write_file(
            &agent_root.join("fixture-apply.md"),
            "Apply me as reviewed.\n",
        );
        write_file(&agent_root.join("fixture-reject.md"), "Reject me.\n");
        write_file(&agent_root.join("fixture-conflict.md"), "Conflict base.\n");
        write_file(&agent_root.join("subdir/nested.txt"), "nested\n");

        gov.seed_recipe_workflow("workflow-fixture-auto");
        gov.seed_plain_workflow("workflow-fixture-plain");

        let hooks = HooksState::new(gov.clone());
        let hooks_for_seed = hooks.clone();
        let app = Router::new()
            .merge(review_queue::router(gov.clone()))
            .merge(change_proposals::router(gov.clone()))
            .merge(automation::router(gov.clone()))
            .merge(hooks::router(hooks));

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr: SocketAddr = listener.local_addr().expect("addr");
        let handle = tokio::spawn(async move {
            let _ = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await;
        });

        let mut install = Self {
            origin: format!("http://{addr}"),
            worker_origin,
            data_dir,
            agent_root,
            owner_token,
            member_token,
            gov: gov.clone(),
            symbols: HashMap::new(),
            hooks: hooks_for_seed,
            _data: data,
            _handle: handle,
            _worker: tokio::spawn(async {}),
        };
        install.symbols.insert(
            "$automation_workflow_id".into(),
            "workflow-fixture-auto".into(),
        );
        install
            .symbols
            .insert("$workflow_id".into(), "workflow-fixture-plain".into());
        install.seed_via_http().await;
        // Drift the conflict file after the proposal is staged.
        write_file(
            &install.agent_root.join("fixture-conflict.md"),
            "Conflict drifted.\n",
        );
        install
    }

    async fn seed_via_http(&mut self) {
        let reviews = [
            (
                "$review_item",
                json!({
                    "title": "Fixture review item",
                    "summary": "A suggestion the Review Center can act on.",
                    "source": "chat_followup",
                    "kind": "suggestion",
                    "payload": {"hint": "fixture"},
                    "provenance": {"origin": "fixture-generator"}
                }),
            ),
            (
                "$review_dismiss",
                json!({
                    "title": "Fixture review to dismiss",
                    "summary": "Will be dismissed.",
                    "source": "workflow_run",
                    "kind": "suggestion"
                }),
            ),
            (
                "$review_snooze",
                json!({
                    "title": "Fixture review to snooze",
                    "summary": "Will be snoozed.",
                    "source": "agent_followup",
                    "kind": "suggestion"
                }),
            ),
            (
                "$review_bulk",
                json!({
                    "title": "Fixture review to bulk-approve",
                    "summary": "Bulk.",
                    "source": "chat_followup",
                    "kind": "suggestion"
                }),
            ),
            (
                "$proposal_apply",
                json!({
                    "title": "파일 수정 제안: fixture-apply.md",
                    "summary": "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.",
                    "source": "change_proposal",
                    "kind": "file_update",
                    "payload": {
                        "path": "fixture-apply.md",
                        "diff": [
                            "--- a/fixture-apply.md",
                            "+++ b/fixture-apply.md",
                            "@@ -1 +1,2 @@",
                            " Apply me as reviewed.",
                            "+Plus the approved edit."
                        ],
                        "new_content": "Apply me as reviewed.\nPlus the approved edit.\n",
                        "tier": "small",
                        "before_bytes": 22,
                        "after_bytes": 46,
                        "base_exists": true,
                        "base_sha256": "c6556d7b4ba2c2b9ffa8b4e3811ad306fc905b60198a8a8bbc37baf90cc92585"
                    },
                    "provenance": {"proposed_by": "fixture", "reason": "seed approve"}
                }),
            ),
            (
                "$proposal_reject",
                json!({
                    "title": "파일 수정 제안: fixture-reject.md",
                    "summary": "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.",
                    "source": "change_proposal",
                    "kind": "file_update",
                    "payload": {
                        "path": "fixture-reject.md",
                        "diff": [
                            "--- a/fixture-reject.md",
                            "+++ b/fixture-reject.md",
                            "@@ -1 +1,2 @@",
                            " Reject me.",
                            "+Would have changed."
                        ],
                        "new_content": "Reject me.\nWould have changed.\n",
                        "tier": "small",
                        "before_bytes": 11,
                        "after_bytes": 31,
                        "base_exists": true,
                        "base_sha256": "03e3fc26cf883e2dd6d05c83de440c6eb9244f51f86c81407031ad01871a4961"
                    },
                    "provenance": {"proposed_by": "fixture", "reason": "seed reject"}
                }),
            ),
            (
                "$proposal_conflict",
                json!({
                    "title": "파일 수정 제안: fixture-conflict.md",
                    "summary": "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.",
                    "source": "change_proposal",
                    "kind": "file_update",
                    "payload": {
                        "path": "fixture-conflict.md",
                        "diff": [
                            "--- a/fixture-conflict.md",
                            "+++ b/fixture-conflict.md",
                            "@@ -1 +1,2 @@",
                            " Conflict base.",
                            "+Staged edit."
                        ],
                        "new_content": "Conflict base.\nStaged edit.\n",
                        "tier": "small",
                        "before_bytes": 15,
                        "after_bytes": 28,
                        "base_exists": true,
                        "base_sha256": "02144a1a31f074d8ddce084a828c44391caca288bd9764dd1b220abd06db95e8"
                    },
                    "provenance": {"proposed_by": "fixture", "reason": "seed conflict"}
                }),
            ),
        ];
        for (symbol, body) in reviews {
            let answer = self
                .issue("POST", "/automation/reviews", Some(&body), "session:owner")
                .await;
            assert_eq!(answer.status, 200, "seed {symbol}: {}", answer.body);
            let parsed: Value = serde_json::from_str(&answer.body).expect("seed body");
            let id = parsed["id"].as_str().expect("id").to_string();
            self.symbols.insert(symbol.to_string(), id);
        }

        let hook_a = json!({
            "name": "Fixture Hook",
            "kind": "pre_tool",
            "description": "A canned hook. Command is empty so /run cannot shell out.",
            "command": "",
            "enabled": true
        });
        let a = self
            .issue(
                "POST",
                "/api/hooks/register",
                Some(&hook_a),
                "session:owner",
            )
            .await;
        assert_eq!(a.status, 200, "seed hook a: {}", a.body);
        let parsed: Value = serde_json::from_str(&a.body).expect("hook a");
        let hook_id = parsed["hook"]["id"].as_str().expect("id").to_string();
        self.symbols.insert("$hook_id".into(), hook_id.clone());

        let hook_b = json!({
            "name": "Fixture Hook Two",
            "kind": "pre_tool",
            "description": "Second canned hook.",
            "command": "",
            "enabled": false
        });
        let b = self
            .issue(
                "POST",
                "/api/hooks/register",
                Some(&hook_b),
                "session:owner",
            )
            .await;
        assert_eq!(b.status, 200, "seed hook b: {}", b.body);
        let parsed: Value = serde_json::from_str(&b.body).expect("hook b");
        let hook_id_b = parsed["hook"]["id"].as_str().expect("id").to_string();
        self.symbols.insert("$hook_id_b".into(), hook_id_b);

        // Recreate the run log the five pre-register write_file calls plus
        // the post-register drift write produced on the Python side.
        self.hooks.hooks.seed_write_file_runs(&hook_id, 5, 1);
    }

    pub fn bind(&self, text: &str) -> String {
        let mut out = text.to_string();
        let mut pairs: Vec<_> = self.symbols.iter().collect();
        pairs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
        for (symbol, value) in pairs {
            out = out.replace(symbol, value);
        }
        out
    }

    pub async fn issue(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        session: &str,
    ) -> Answer {
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let method = reqwest::Method::from_bytes(method.as_bytes()).expect("method");
        let url = format!("{}{}", self.origin, self.bind(path));
        let mut builder = client.request(method, url);
        builder = builder.header("host", "127.0.0.1:4825");
        builder = builder.header("origin", "http://127.0.0.1:4825");
        match session {
            "session:owner" => {
                builder = builder.header("cookie", format!("session_token={}", self.owner_token));
            }
            "session:member" => {
                builder = builder.header("cookie", format!("session_token={}", self.member_token));
            }
            "session:invalid" => {
                builder = builder.header("cookie", "session_token=not-a-real-session-token-value");
            }
            _ => {}
        }
        if let Some(body) = body {
            builder = builder
                .header("content-type", "application/json")
                .body(serde_json::to_string(body).expect("body"));
        }
        let response = builder.send().await.expect("request");
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        Answer {
            status,
            content_type,
            body: response.text().await.expect("body"),
        }
    }

    pub async fn replay(&self, case: &Value) -> Result<(), String> {
        let method = case["method"].as_str().unwrap_or("GET");
        let mut path = case["path"].as_str().unwrap_or("/").to_string();
        path = self.bind(&path);
        if let Some(query) = case.get("query").and_then(Value::as_object) {
            if !query.is_empty() {
                let pairs: Vec<String> = query
                    .iter()
                    .map(|(k, v)| {
                        format!(
                            "{k}={}",
                            v.as_str()
                                .map(str::to_string)
                                .unwrap_or_else(|| v.to_string())
                        )
                    })
                    .collect();
                path = format!("{path}?{}", pairs.join("&"));
            }
        }
        let session = case["request_headers"]
            .get("cookie")
            .and_then(Value::as_str)
            .unwrap_or("absent");
        let request_body = case.get("request_body").cloned().unwrap_or(Value::Null);
        let body = match &request_body {
            Value::Null => None,
            other => {
                let mut cloned = other.clone();
                substitute_tokens(&mut cloned, &self.symbols);
                Some(cloned)
            }
        };
        let answer = self.issue(method, &path, body.as_ref(), session).await;
        let expected_status = case["status"].as_u64().unwrap_or(0) as u16;
        if answer.status != expected_status {
            return Err(format!(
                "{} {} {}: status {} != {} body={}",
                case["family"],
                case["name"],
                case["branch"],
                answer.status,
                expected_status,
                answer.body
            ));
        }
        let expected_ct = case["response_headers"]
            .get("content-type")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !expected_ct.is_empty() && answer.content_type != expected_ct {
            return Err(format!(
                "{} {} {}: content-type '{}' != '{}'",
                case["family"], case["name"], case["branch"], answer.content_type, expected_ct
            ));
        }
        let expected = case.get("response_body").cloned().unwrap_or(Value::Null);
        if let Some(obj) = expected.as_object() {
            if let Some(text) = obj.get("@text").and_then(Value::as_str) {
                if answer.body != text {
                    return Err(format!(
                        "{} {} {}: text body mismatch: {}",
                        case["family"], case["name"], case["branch"], answer.body
                    ));
                }
                return Ok(());
            }
        }
        let actual: Value =
            serde_json::from_str(&answer.body).unwrap_or(json!({"@raw": answer.body}));
        let mut expected = expected;
        substitute_symbols(&mut expected, &self.symbols);
        if !values_match(&expected, &actual) {
            return Err(format!(
                "{} {} {}: body mismatch\nexpected: {}\nactual:   {}",
                case["family"],
                case["name"],
                case["branch"],
                serde_json::to_string_pretty(&expected).unwrap_or_default(),
                serde_json::to_string_pretty(&actual).unwrap_or_default()
            ));
        }
        Ok(())
    }

    pub async fn replay_family(&self, family: &str) {
        let cases = cases_for(family);
        assert!(!cases.is_empty(), "no cases for {family}");
        for case in &cases {
            if let Err(error) = self.replay(case).await {
                panic!("{error}");
            }
        }
    }
}

pub struct Answer {
    pub status: u16,
    pub content_type: String,
    pub body: String,
}

fn seed_users(dir: &Path) {
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert(
        "id",
        json!(lattice_auth::stable_user_id("owner@lattice.test")),
    );
    owner.insert("email", json!("owner@lattice.test"));
    let mut member = OrderedMap::new();
    member.insert("password", json!("x"));
    member.insert("name", json!("Fixture Member"));
    member.insert("nickname", json!("member"));
    member.insert("role", json!("user"));
    member.insert("disabled", json!(false));
    member.insert(
        "id",
        json!(lattice_auth::stable_user_id("member@lattice.test")),
    );
    member.insert("email", json!("member@lattice.test"));
    let mut users = OrderedMap::new();
    users.insert("owner@lattice.test", serde_json::to_value(owner).unwrap());
    users.insert("member@lattice.test", serde_json::to_value(member).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

fn write_file(path: &Path, content: &str) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(path, content).expect("write file");
}

/// The worker as it is after §P1a: `/agent/change-proposal` is gone, because
/// approve-and-apply writes the file here now. What is left is the hook-run
/// dispatch and the graph mutation seam.
async fn spawn_fake_worker(agent_root: PathBuf) -> String {
    async fn tool(Json(_body): Json<Value>) -> axum::response::Response {
        (StatusCode::OK, axum::Json(json!({"result": "ok"}))).into_response()
    }

    let app = Router::new()
        .route("/agent/tool", post(tool))
        .route(
            "/worker/graph/mutate",
            post(|| async {
                axum::Json(json!({"op": "ingest_event", "result": {"node_id": "n1"}}))
            }),
        )
        .with_state(agent_root);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("worker bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app.into_make_service()).await;
    });
    format!("http://{addr}")
}

fn substitute_tokens(value: &mut Value, symbols: &HashMap<String, String>) {
    match value {
        Value::String(text) => {
            let mut out = text.clone();
            if out == "@ts" {
                *text = "2099-01-01T00:00:00+00:00".into();
                return;
            }
            substitute_symbol_text(&mut out, symbols);
            *text = out;
        }
        Value::Array(items) => {
            for item in items {
                substitute_tokens(item, symbols);
            }
        }
        Value::Object(map) => {
            for item in map.values_mut() {
                substitute_tokens(item, symbols);
            }
        }
        _ => {}
    }
}

fn substitute_symbols(value: &mut Value, symbols: &HashMap<String, String>) {
    match value {
        Value::String(text) => {
            let mut out = text.clone();
            substitute_symbol_text(&mut out, symbols);
            *text = out;
        }
        Value::Array(items) => {
            for item in items {
                substitute_symbols(item, symbols);
            }
        }
        Value::Object(map) => {
            for item in map.values_mut() {
                substitute_symbols(item, symbols);
            }
        }
        _ => {}
    }
}

fn substitute_symbol_text(text: &mut String, symbols: &HashMap<String, String>) {
    let mut pairs: Vec<_> = symbols.iter().collect();
    pairs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
    for (symbol, replacement) in pairs {
        *text = text.replace(symbol, replacement);
    }
}

fn values_match(expected: &Value, actual: &Value) -> bool {
    match expected {
        Value::String(token)
            if token == "@any"
                || token == "@ts"
                || token == "@uuid"
                || token == "@id"
                || token == "@version" =>
        {
            true
        }
        Value::String(exp) => actual.as_str() == Some(exp.as_str()),
        Value::Array(exp) => {
            let Some(act) = actual.as_array() else {
                return false;
            };
            if exp.len() != act.len() {
                return false;
            }
            exp.iter().zip(act.iter()).all(|(e, a)| values_match(e, a))
        }
        Value::Object(exp) => {
            let Some(act) = actual.as_object() else {
                return false;
            };
            if exp.len() != act.len() {
                // Allow extra generated_at-only mismatches? No — require same keys
                // unless expected has only a subset we care about. Strict.
                return false;
            }
            exp.iter()
                .all(|(k, ev)| act.get(k).is_some_and(|av| values_match(ev, av)))
        }
        other => other == actual,
    }
}
