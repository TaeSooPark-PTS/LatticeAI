#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]
#![allow(clippy::field_reassign_with_default, clippy::unnecessary_sort_by)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::RawQuery;
use axum::http::HeaderMap;
use axum::routing::get;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_platform::invitations::{self, InvitationsState};
use lattice_platform::permissions::{self, PermissionGateway, PermissionsState};
use lattice_platform::ui_redirects;
use lattice_platform::workspace::{
    self, GraphReads, GraphSeam, WorkspaceDeps, WorkspaceProviders, WorkspaceState,
};
use serde_json::{json, Value};

use super::match_util::*;
use super::seed::*;
use super::*;

/// One running install: auth + workspace + invitations + permissions.
pub struct Install {
    pub origin: String,
    pub data_dir: PathBuf,
    pub owner_token: String,
    pub member_token: String,
    pub symbols: HashMap<String, String>,
    pub permissions: PermissionGateway,
    _data: tempfile::TempDir,
    _handle: tokio::task::JoinHandle<()>,
}

impl Install {
    pub async fn start() -> Self {
        let data = tempfile::tempdir().expect("data dir");
        let data_dir = data.path().to_path_buf();
        std::env::set_var("HOME", &data_dir);
        seed_users(&data_dir);
        seed_chat(&data_dir);
        seed_audit(&data_dir);
        let notes = data_dir.join("fixture-notes.txt");
        let second = data_dir.join("fixture-second.txt");
        let _ = std::fs::write(&notes, "notes");
        let _ = std::fs::write(&second, "second");

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

        let mut providers = WorkspaceProviders::default();
        providers.settings = Arc::new(|| {
            json!({
                "mode": "local",
                "host": "127.0.0.1",
                "port": 4825,
                "require_auth": true,
                "enable_graph": true,
                "allow_local_models": false,
                "static_dir": "/repo/static",
                "data_dir": "",
            })
        });
        providers.graph_reads = Some(Arc::new(FixtureGraph::new()));
        providers.watcher_status = Some(Arc::new(
            || json!({"available": true, "error": "", "debounce_seconds": 5.0, "active": {}}),
        ));
        providers.skills_marketplace = Arc::new(|| {
            Ok(vec![json!({
                "plugin": "fixture-remote",
                "skill": "fixture-remote-skill",
                "category": "development",
                "description": "Canned remote skill used only by the fixture capture.",
                "skill_md_url": "https://example.test/fixture-remote-skill/SKILL.md",
                "homepage": "https://example.test/fixture-remote-skill",
                "license": "Apache-2.0",
                "author": "LatticeAI",
            })])
        });
        providers.skills_dir = Some(data_dir.join("skills"));
        let _ = std::fs::create_dir_all(data_dir.join("skills"));
        providers.scan_environment = Some(Arc::new(|| {
            json!({
                "os": "darwin", "os_version": "test", "chip": "test", "cpu": "test",
                "gpu": "", "cuda": false, "wsl": false, "ram_gb": 16, "disk_free_gb": 64,
                "tools": {}, "components": {}, "path": [], "mlx": false, "api_keys": {},
            })
        }));
        providers.local_sysinfo = Some(Arc::new(|| {
            json!({
                "cpu_pct": 1.0, "ram_pct": 2.0, "gpu_mem_pct": 0.0, "gpu_mem_gb": 0.0,
                "readiness": "ok",
            })
        }));
        providers.model_recommendations = Some(Arc::new(|_| {
            (
                json!({
                    "components": [], "engines": [], "models": [], "mcps": [], "summary": {}
                }),
                json!({
                    "engine": "local_mlx", "engine_available": false, "apple_silicon": true,
                    "ram_gb": 16, "counts": {}, "top_pick": null, "families": {}, "models": [],
                }),
            )
        }));
        let deps = WorkspaceDeps {
            seam: GraphSeam::Stub(Arc::new(|op, args| {
                let source_id = args
                    .get("source_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                // No local sources are seeded; every watch/remove is the
                // unknown-source branch the fixture captured (uncaught 500).
                if matches!(op, "set_local_source_watch" | "remove_local_source") {
                    return Err(format!("knowledge source not found: {source_id}"));
                }
                match op {
                    "set_local_source_watch" => Ok(json!({
                        "source_id": source_id,
                        "watch_enabled": args.get("enabled"),
                    })),
                    "remove_local_source" => Ok(json!({"removed": 0, "source_id": source_id})),
                    "ingest_event" => Ok(json!({"node_id": "n1"})),
                    "import_graph_data" => Ok(json!({"imported": 0})),
                    other => Err(format!("unknown op {other}")),
                }
            })),
            providers,
        };

        let workspace = WorkspaceState::new(Arc::clone(&auth), &data_dir).with_deps(deps);
        let invitations = InvitationsState::from_workspace(&workspace);
        let permissions = PermissionsState::new(Arc::clone(&auth), &data_dir);
        let gateway = permissions.gateway.clone();
        let auth_for_pages = Arc::clone(&auth);

        let app = Router::new()
            .merge(workspace::router(workspace))
            .merge(invitations::router(invitations))
            .merge(permissions::router(permissions))
            .route(
                "/workspace",
                get({
                    let auth = Arc::clone(&auth_for_pages);
                    move |headers: HeaderMap, RawQuery(query): RawQuery| {
                        let auth = Arc::clone(&auth);
                        async move {
                            if let Err(refusal) = auth.require_user(&headers) {
                                return refusal;
                            }
                            ui_redirects::app_redirect("workspace-admin", query.as_deref())
                        }
                    }
                }),
            )
            .route(
                "/onboarding",
                get({
                    let auth = Arc::clone(&auth_for_pages);
                    move |headers: HeaderMap, RawQuery(query): RawQuery| {
                        let auth = Arc::clone(&auth);
                        async move {
                            if let Err(refusal) = auth.require_user(&headers) {
                                return refusal;
                            }
                            ui_redirects::app_redirect("workspace-admin", query.as_deref())
                        }
                    }
                }),
            );

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
            data_dir,
            owner_token,
            member_token,
            symbols: HashMap::new(),
            permissions: gateway,
            _data: data,
            _handle: handle,
        };
        install.seed_via_http().await;
        install.seed_approvals();
        install
    }

    fn seed_approvals(&mut self) {
        let first = self.permissions.request(
            &self.data_dir.join("fixture-notes.txt").to_string_lossy(),
            "read",
            "owner@lattice.test",
            "",
        );
        let second = self.permissions.request(
            &self.data_dir.join("fixture-second.txt").to_string_lossy(),
            "read",
            "owner@lattice.test",
            "",
        );
        if let Some(token) = first.get("approval_token").and_then(Value::as_str) {
            self.symbols
                .insert("$approval_token".into(), token.to_string());
            self.symbols
                .insert("$approval_hint".into(), token.chars().take(8).collect());
        }
        if let Some(token) = second.get("approval_token").and_then(Value::as_str) {
            self.symbols
                .insert("$approval_token_b".into(), token.to_string());
            self.symbols
                .insert("$approval_hint_b".into(), token.chars().take(8).collect());
        }
    }

    async fn seed_via_http(&mut self) {
        let org_a = json!({"name": "Fixture Team", "settings": {"tier": "team"}});
        let a = self
            .issue("POST", "/workspace/orgs", Some(&org_a), "session:owner")
            .await;
        assert_eq!(a.status, 200, "seed org a: {}", a.body);
        // Same-second created_at ties sort by insertion in Python and by
        // BTreeMap key here; a one-second gap makes created_at the tie-break.
        tokio::time::sleep(Duration::from_secs(1)).await;

        let org_b = json!({"name": "Archive Target", "settings": {}});
        let b = self
            .issue("POST", "/workspace/orgs", Some(&org_b), "session:owner")
            .await;
        assert_eq!(b.status, 200, "seed org b: {}", b.body);

        let member_id = lattice_auth::stable_user_id("member@lattice.test");
        let add = json!({"user_id": member_id, "role": "member"});
        let added = self
            .issue(
                "POST",
                "/workspace/orgs/org-Fixture-Team/members",
                Some(&add),
                "session:owner",
            )
            .await;
        assert_eq!(added.status, 200, "seed member: {}", added.body);

        for (symbol, name) in [
            ("$snapshot_a", "Fixture snapshot A"),
            ("$snapshot_b", "Fixture snapshot B"),
        ] {
            let body = json!({"name": name});
            let answer = self
                .issue("POST", "/workspace/snapshots", Some(&body), "session:owner")
                .await;
            assert_eq!(answer.status, 200, "seed {symbol}: {}", answer.body);
            let parsed: Value = serde_json::from_str(&answer.body).expect("snapshot");
            if let Some(id) = parsed["snapshot"]["id"].as_str() {
                self.symbols.insert(symbol.to_string(), id.to_string());
            }
        }

        let memory = json!({
            "kind": "decisions",
            "content": "결정: 하이브리드 검색의 알파 융합을 유지한다.",
            "tags": ["decision", "retrieval"]
        });
        let mem = self
            .issue(
                "POST",
                "/workspace/memories",
                Some(&memory),
                "session:owner",
            )
            .await;
        assert_eq!(mem.status, 200, "seed memory: {}", mem.body);
        let parsed: Value = serde_json::from_str(&mem.body).expect("memory");
        if let Some(id) = parsed["memory"]["id"].as_str() {
            self.symbols.insert("$memory_id".into(), id.to_string());
        }

        let workflow = json!({
            "name": "Fixture workflow",
            "steps": [{"action": "note", "detail": "첫 단계"}],
            "metadata": {"origin": "fixture"}
        });
        let wf = self
            .issue(
                "POST",
                "/workspace/workflows",
                Some(&workflow),
                "session:owner",
            )
            .await;
        assert_eq!(wf.status, 200, "seed workflow: {}", wf.body);
        let parsed: Value = serde_json::from_str(&wf.body).expect("workflow");
        if let Some(id) = parsed["workflow"]["id"].as_str() {
            self.symbols.insert("$workflow_id".into(), id.to_string());
        }

        let run = json!({
            "agent_id": "agent:executor",
            "status": "ok",
            "input": "요약해줘",
            "output": "요약했습니다.",
            "timeline": [],
            "relationships": []
        });
        let run_answer = self
            .issue(
                "POST",
                "/workspace/agents/runs",
                Some(&run),
                "session:owner",
            )
            .await;
        assert_eq!(run_answer.status, 200, "seed run: {}", run_answer.body);
        let parsed: Value = serde_json::from_str(&run_answer.body).expect("run");
        if let Some(id) = parsed["run"]["id"].as_str() {
            self.symbols.insert("$agent_run_id".into(), id.to_string());
        }

        let invite_a = json!({
            "email": "invitee@lattice.test",
            "workspace_id": "org-Fixture-Team",
            "role": "member",
            "expires_hours": 168
        });
        let ia = self
            .issue("POST", "/invitations", Some(&invite_a), "session:owner")
            .await;
        assert_eq!(ia.status, 200, "seed invite a: {}", ia.body);
        let parsed: Value = serde_json::from_str(&ia.body).expect("invite a");
        if let Some(id) = parsed["invitation"]["id"].as_str() {
            self.symbols.insert("$invitation_id".into(), id.to_string());
        }
        if let Some(token) = parsed["invitation"]["token"].as_str() {
            self.symbols
                .insert("$invitation_token".into(), token.to_string());
        }

        let invite_b = json!({
            "email": "member@lattice.test",
            "role": "viewer",
            "expires_hours": 24
        });
        let ib = self
            .issue("POST", "/invitations", Some(&invite_b), "session:owner")
            .await;
        assert_eq!(ib.status, 200, "seed invite b: {}", ib.body);
        let parsed: Value = serde_json::from_str(&ib.body).expect("invite b");
        if let Some(token) = parsed["invitation"]["token"].as_str() {
            self.symbols
                .insert("$acceptable_invitation_token".into(), token.to_string());
        }

        let vscode = json!({
            "status": "connected",
            "index_status": "ok",
            "workspace_folder": "/workspace/fixture",
            "extension_version": env!("CARGO_PKG_VERSION"),
            "active_file": "src/main.rs",
            "detail": ""
        });
        let vs = self
            .issue(
                "POST",
                "/workspace/vscode/status",
                Some(&vscode),
                "session:owner",
            )
            .await;
        assert_eq!(vs.status, 200, "seed vscode: {}", vs.body);
    }

    pub fn bind(&self, text: &str) -> String {
        let mut out = text.to_string();
        let mut pairs: Vec<_> = self.symbols.iter().collect();
        pairs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
        for (symbol, value) in pairs {
            out = out.replace(symbol, value);
        }
        out.replace("<DATA_DIR>", &self.data_dir.to_string_lossy())
            .replace("<HOME>", &self.data_dir.to_string_lossy())
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
            .redirect(reqwest::redirect::Policy::none())
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
                .body(self.bind(&serde_json::to_string(body).expect("body")));
        }
        let response = builder.send().await.expect("request");
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let location = response
            .headers()
            .get("location")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        Answer {
            status,
            content_type,
            location,
            body: response.text().await.expect("body"),
        }
    }

    pub async fn replay(&self, case: &Value) -> Result<(), String> {
        let name = case["name"].as_str().unwrap_or("");
        let branch = case["branch"].as_str().unwrap_or("");
        if SKIP.iter().any(|(n, b)| *n == name && *b == branch) {
            return Ok(());
        }
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
        let extra_headers: HashMap<String, String> = case["request_headers"]
            .as_object()
            .map(|map| {
                map.iter()
                    .filter(|(k, _)| {
                        let key = k.to_ascii_lowercase();
                        key != "cookie" && key != "origin" && key != "content-type"
                    })
                    .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let request_body = case.get("request_body").cloned().unwrap_or(Value::Null);
        let body = match &request_body {
            Value::Null => None,
            other => {
                let mut cloned = other.clone();
                substitute_tokens(&mut cloned, &self.symbols);
                Some(cloned)
            }
        };
        let answer = self
            .issue_with_headers(method, &path, body.as_ref(), session, &extra_headers)
            .await;
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
        if expected_status == 308 {
            let expected_loc = case["response_headers"]
                .get("location")
                .and_then(Value::as_str)
                .unwrap_or("");
            if !expected_loc.is_empty() && answer.location != expected_loc {
                return Err(format!(
                    "{} {} {}: location '{}' != '{}'",
                    case["family"], case["name"], case["branch"], answer.location, expected_loc
                ));
            }
            return Ok(());
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
        if expected.is_null() {
            return Ok(());
        }
        let actual: Value =
            serde_json::from_str(&answer.body).unwrap_or(json!({"@raw": answer.body}));
        let mut expected = expected;
        substitute_symbols(&mut expected, &self.symbols);
        rewrite_path_tokens(&mut expected, &self.data_dir);
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

    async fn issue_with_headers(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        session: &str,
        extra: &HashMap<String, String>,
    ) -> Answer {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let method = reqwest::Method::from_bytes(method.as_bytes()).expect("method");
        let url = format!("{}{}", self.origin, path);
        let mut builder = client.request(method, url);
        builder = builder.header("host", "127.0.0.1:4825");
        builder = builder.header("origin", "http://127.0.0.1:4825");
        for (name, value) in extra {
            builder = builder.header(name.as_str(), self.bind(value));
        }
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
                .body(self.bind(&serde_json::to_string(body).expect("body")));
        }
        let response = builder.send().await.expect("request");
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let location = response
            .headers()
            .get("location")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        Answer {
            status,
            content_type,
            location,
            body: response.text().await.expect("body"),
        }
    }

    pub async fn replay_family(&self, doc: &Value, family: &str) {
        let cases = cases_for(doc, family);
        assert!(!cases.is_empty(), "no cases for {family}");
        let mut failures = Vec::new();
        for case in &cases {
            if let Err(error) = self.replay(case).await {
                failures.push(error);
            }
        }
        if !failures.is_empty() {
            panic!(
                "{} fixture failures for {family}:\n{}",
                failures.len(),
                failures.join("\n\n")
            );
        }
    }
}
