//! A user hook that fired for a **native tool** is visible at `GET /api/hooks/runs`.
//!
//! v11.6.0 shipped the lifecycle port unwired and said so ("네이티브 도구에
//! 대해서는 아직 사용자 훅이 발화하지 않습니다"). This is the end of that: the
//! sink writes into the same registry the routes read, so one store answers
//! both, and a second `HooksStore::open` over the same directory — the failure
//! this pins against — would show an empty run log here.

#![allow(clippy::all)]

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use lattice_agent::policy::ToolPolicy;
use lattice_agent::sandbox::Workspace;
use lattice_agent::tools::{CallScope, NativeCall, NativeTools, ToolConfig, ToolHost};
use lattice_agent::worker::WorkerClient;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_platform::hooks::{self, HooksState, HooksStore, NativeHookSink};
use lattice_platform::review_queue::GovernanceState;
use serde_json::{json, Value};

const OWNER: &str = "owner@lattice.test";

fn write_users(dir: &std::path::Path) {
    let mut owner = OrderedMap::new();
    owner.insert("id", json!("user:fa484c1e-1b5a-50ef-9c18-b73ca89368d7"));
    owner.insert("email", json!(OWNER));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    let mut users = OrderedMap::new();
    users.insert(OWNER, serde_json::to_value(owner).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

#[tokio::test]
async fn a_hook_fired_by_a_native_tool_shows_up_on_the_runs_route() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let data_dir = tmp.path().join("data");
    let agent_root = tmp.path().join("agent");
    std::fs::create_dir_all(&data_dir).expect("data");
    std::fs::create_dir_all(&agent_root).expect("agent");
    write_users(&data_dir);

    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".to_string(),
        data_dir.to_string_lossy().into_owned(),
    );
    env.insert("LATTICEAI_REQUIRE_AUTH".to_string(), "1".to_string());
    env.insert("LATTICEAI_RATE_LIMIT".to_string(), "0".to_string());
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = data_dir.clone();
    let auth = AuthState::with_clock(config, Clock::frozen(1_700_000_000.0));
    let token = auth.sessions().create(OWNER, Some(OWNER));

    // One registry, two readers — exactly what `OneDoorState` builds.
    let store = HooksStore::open(&data_dir);
    let governance = GovernanceState::open(Arc::clone(&auth), &data_dir, &agent_root, None);
    let app = hooks::router(HooksState::with_store(governance, store.clone()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", listener.local_addr().expect("addr"));
    let server = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(20))
        .build()
        .expect("client");
    let get = |path: String| {
        let client = client.clone();
        let token = token.clone();
        let origin = origin.clone();
        async move {
            let response = client
                .get(format!("{origin}{path}"))
                .header("cookie", format!("session_token={token}"))
                .send()
                .await
                .expect("request");
            let status = response.status().as_u16();
            let body: Value = response.json().await.unwrap_or(Value::Null);
            (status, body)
        }
    };

    // Nothing has run yet.
    let (status, body) = get("/api/hooks/runs".into()).await;
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["total"], json!(0), "{body}");

    // Register a user `pre_tool` hook through the same route a person uses.
    let registered = client
        .post(format!("{origin}/api/hooks/register"))
        .header("cookie", format!("session_token={token}"))
        .header("content-type", "application/json")
        .json(&json!({"name": "Native Watcher", "kind": "pre_tool", "command": ""}))
        .send()
        .await
        .expect("register");
    assert_eq!(registered.status().as_u16(), 200);

    // …and run a native tool with the production sink wired.
    let workspace = Workspace::new(agent_root.join("workspace")).expect("workspace");
    let tools = NativeTools::new(
        workspace.clone(),
        ToolConfig {
            brain_dir: tmp.path().join("brain"),
            role: "owner".into(),
            ..ToolConfig::default()
        },
        WorkerClient::new("http://127.0.0.1:1"),
    )
    .with_hooks(Arc::new(NativeHookSink::new(store.clone(), "agent")));
    let args = json!({"path": "note.md", "content": "hello"});
    let args = args.as_object().expect("object").clone();
    let policy = ToolPolicy::default();
    let scope = CallScope {
        user_email: Some(OWNER.into()),
        workspace_id: None,
    };
    tools
        .execute(NativeCall {
            tool: "write_file",
            args: &args,
            policy: &policy,
            scope: &scope,
        })
        .await;
    assert!(workspace.root().join("note.md").is_file());

    let (status, body) = get("/api/hooks/runs".into()).await;
    assert_eq!(status, 200, "{body}");
    let runs = body["runs"].as_array().cloned().unwrap_or_default();
    assert_eq!(body["total"], json!(2), "{body}");
    let watcher = runs
        .iter()
        .find(|run| run["hook_id"] == json!("user:native-watcher"))
        .expect("the registered hook ran");
    assert_eq!(watcher["target_event"], json!("tool.write_file"));
    assert_eq!(watcher["target_kind"], json!("pre_tool"));
    assert_eq!(watcher["status"], json!("advisory"));

    // The kind filter the UI uses still selects.
    let (_, filtered) = get("/api/hooks/runs?kind=post_tool".into()).await;
    assert_eq!(filtered["total"], json!(1), "{filtered}");

    server.abort();
}
