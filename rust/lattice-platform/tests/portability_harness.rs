//! Shared replay helpers for WP-R9 fixture tests.
//!
//! Included via `#[path]` from the per-family integration tests. Cargo also
//! compiles this file as its own crate; it has no `#[test]` items.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::http::{HeaderMap, StatusCode};
use axum::middleware::from_fn_with_state;
use axum::Router;
use lattice_auth::password::hash_password;
use lattice_auth::users::ensure_user_identity;
use lattice_auth::{csrf_guard, stable_user_id, AuthConfig, AuthState, Clock, Users};
use lattice_core::db::RuntimeConfig;
use lattice_platform::computer_use::{self, ComputerUseState};
use lattice_platform::network::{self, NetworkState};
use lattice_platform::network_boundary::{self, NetworkBoundaryState};
use lattice_platform::portability::{self, PortabilityState};
use lattice_platform::project_sessions::{self, ProjectSessionsState};
use lattice_platform::realtime::{self, RealtimeState};
use lattice_platform::voice;
use serde_json::{json, Value};

pub const CAPTURED_HEADERS: [&str; 4] = [
    "content-type",
    "cache-control",
    "x-accel-buffering",
    "location",
];

pub fn load_fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join(name);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).expect("fixture json")
}

pub fn openapi_fragment(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("openapi")
        .join(name);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).expect("openapi fragment")
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

pub fn family_cases<'a>(fixture: &'a Value, family: &str) -> Vec<&'a Value> {
    fixture["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["family"].as_str() == Some(family))
        .collect()
}

pub struct Install {
    pub dir: tempfile::TempDir,
    pub origin: String,
    pub handle: tokio::task::JoinHandle<()>,
    pub owner_cookie: String,
    pub member_cookie: String,
    pub data_dir: PathBuf,
    pub agent_root: PathBuf,
}

impl Install {
    pub async fn start() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let data = dir.path().to_path_buf();
        let agent_root = data.join("agent_workspace");
        let _ = std::fs::create_dir_all(&agent_root);
        let _ = std::fs::create_dir_all(data.join("project_sessions"));
        let _ = std::fs::create_dir_all(data.join("workspace_exports"));

        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert("LATTICEAI_OPEN_REGISTRATION".into(), "1".into());
        env.insert("LATTICEAI_MODE".into(), "local".into());
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
        env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
        env.insert("LATTICEAI_ENABLE_GRAPH".into(), "1".into());
        env.insert("LATTICEAI_VECTOR_DIM".into(), "384".into());
        env.insert(
            "LATTICEAI_AGENT_ROOT".into(),
            agent_root.to_string_lossy().into_owned(),
        );
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data.clone();
        let auth = AuthState::with_clock(config, Clock::system());

        let mut users = Users::new();
        users.insert(
            "owner@lattice.test",
            account(
                "owner@lattice.test",
                "Fixture0wner1",
                "Fixture Owner",
                "owner",
                "admin",
            ),
        );
        users.insert(
            "member@lattice.test",
            account(
                "member@lattice.test",
                "Fixture0member1",
                "Fixture Member",
                "member",
                "user",
            ),
        );
        auth.users().save(&users);
        let owner_token = auth.sessions().create(
            &stable_user_id("owner@lattice.test"),
            Some("owner@lattice.test"),
        );
        let member_token = auth.sessions().create(
            &stable_user_id("member@lattice.test"),
            Some("member@lattice.test"),
        );

        let data_str = data.to_string_lossy().into_owned();
        let runtime = RuntimeConfig::resolve(Some(&data_str), None, None, Some(data.as_path()));
        let _ = runtime.open_store();

        let app = Router::new()
            .merge(lattice_auth::router(Arc::clone(&auth)))
            .merge(project_sessions::router(ProjectSessionsState::new(
                Arc::clone(&auth),
                runtime.clone(),
            )))
            .merge(network::router(NetworkState::new(
                Arc::clone(&auth),
                runtime.clone(),
                None,
            )))
            .merge(network_boundary::router(NetworkBoundaryState::new(
                Arc::clone(&auth),
                runtime.clone(),
                None,
            )))
            .merge(realtime::router(RealtimeState::new(Arc::clone(&auth))))
            .merge(computer_use::router(ComputerUseState::new(
                Arc::clone(&auth),
                None,
                agent_root.clone(),
            )))
            .merge(portability::router(PortabilityState::new(
                Arc::clone(&auth),
                runtime,
                None,
            )))
            .merge(voice::router())
            .layer(from_fn_with_state(Arc::clone(&auth), csrf_guard));

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

        Self {
            dir,
            origin: format!("http://{addr}"),
            handle,
            owner_cookie: format!("session_token={owner_token}"),
            member_cookie: format!("session_token={member_token}"),
            data_dir: data,
            agent_root,
        }
    }

    pub fn cookie(&self, token: &str) -> Option<String> {
        match token {
            "session:owner" => Some(self.owner_cookie.clone()),
            "session:member" => Some(self.member_cookie.clone()),
            "session:invalid" => {
                Some("session_token=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into())
            }
            "absent" | "" => None,
            other => Some(other.to_string()),
        }
    }

    pub async fn issue(
        &self,
        method: &str,
        path: &str,
        cookie: Option<&str>,
        extra_headers: &HeaderMap,
        body: Option<&[u8]>,
    ) -> (u16, HashMap<String, String>, String) {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(15))
            .build()
            .expect("client");
        let method = reqwest::Method::from_bytes(method.as_bytes()).expect("method");
        let mut builder = client
            .request(method, format!("{}{path}", self.origin))
            .header("host", "127.0.0.1:4825")
            .header("origin", "http://127.0.0.1:4825");
        if let Some(cookie) = cookie {
            builder = builder.header("cookie", cookie);
        }
        for (name, value) in extra_headers.iter() {
            if name.as_str() == "cookie" || name.as_str() == "origin" || name.as_str() == "host" {
                continue;
            }
            builder = builder.header(name, value);
        }
        if let Some(body) = body {
            builder = builder
                .header("content-type", "application/json")
                .body(body.to_vec());
        }
        let response = builder.send().await.expect("request");
        let status = response.status().as_u16();
        let mut headers = HashMap::new();
        for name in CAPTURED_HEADERS {
            if let Some(value) = response.headers().get(name) {
                headers.insert(
                    name.to_string(),
                    value.to_str().unwrap_or_default().to_string(),
                );
            }
        }
        let text = response.text().await.unwrap_or_default();
        (status, headers, text)
    }
}

impl Drop for Install {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

fn account(
    email: &str,
    password: &str,
    name: &str,
    nickname: &str,
    role: &str,
) -> serde_json::Map<String, Value> {
    let mut record = serde_json::Map::new();
    record.insert("password".into(), json!(hash_password(password)));
    record.insert("name".into(), json!(name));
    record.insert("nickname".into(), json!(nickname));
    record.insert("role".into(), json!(role));
    record.insert("disabled".into(), json!(false));
    ensure_user_identity(email, &mut record);
    record
}

pub fn substitute_symbols(
    text: &str,
    symbols: &HashMap<String, String>,
    data_dir: &Path,
    agent_root: &Path,
) -> String {
    let mut out = text.to_string();
    for (k, v) in symbols {
        out = out.replace(k, v);
    }
    out = out.replace("<DATA_DIR>", &data_dir.to_string_lossy());
    out = out.replace("<AGENT_ROOT>", &agent_root.to_string_lossy());
    out
}

pub fn values_match(expected: &Value, actual: &Value) -> bool {
    match expected {
        Value::String(token)
            if token == "@any"
                || token == "@ts"
                || token == "@id"
                || token == "@uuid"
                || token == "@version"
                || token == "@hostname" =>
        {
            true
        }
        Value::String(token) if token.starts_with('$') => actual.is_string() || actual.is_number(),
        Value::Object(exp) => {
            let Some(act) = actual.as_object() else {
                return false;
            };
            exp.iter()
                .all(|(k, v)| act.get(k).map(|got| values_match(v, got)).unwrap_or(false))
        }
        Value::Array(exp) => {
            let Some(act) = actual.as_array() else {
                return false;
            };
            if exp.len() != act.len() {
                return false;
            }
            exp.iter().zip(act).all(|(e, a)| values_match(e, a))
        }
        other => other == actual,
    }
}

pub fn is_keep_or_static_gap(case: &Value) -> bool {
    let family = case["family"].as_str().unwrap_or("");
    let path = case["path"].as_str().unwrap_or("");
    family == "voice_capture.py" || path == "/activity"
}

/// Graph-dependent or process-state bodies we only pin status + content-type.
pub fn partial_body(case: &Value) -> bool {
    let name = case["name"].as_str().unwrap_or("");
    let branch = case["branch"].as_str().unwrap_or("");
    matches!(
        (name, branch),
        ("portability_status", "happy")
            | ("brain_storage", "happy")
            | ("export_graph", "happy")
            | ("export_graph_file", "happy")
            | ("backup_graph", "happy")
            | ("migrate_postgres", "error_dry_run")
            | ("realtime_feed", "happy")
            // Presence list is process-state (a prior join + 29 feed events
            // from the capture session). Pin status only.
            | ("realtime_presence", "happy")
            | ("preview_cloud_context", "happy")
            | ("preview_cloud_context", "happy_top_k_clamped")
    )
}

pub async fn replay_family(install: &Install, fixture: &Value, family: &str) {
    let mut symbols = HashMap::new();
    replay_family_with(install, fixture, family, &mut symbols).await;
}

pub async fn replay_family_with(
    install: &Install,
    fixture: &Value,
    family: &str,
    symbols: &mut HashMap<String, String>,
) {
    for case in family_cases(fixture, family) {
        if is_keep_or_static_gap(case) {
            continue;
        }
        replay_one(install, case, symbols).await;
    }
}

pub async fn replay_one(install: &Install, case: &Value, symbols: &mut HashMap<String, String>) {
    let method = case["method"].as_str().unwrap_or("GET");
    let mut path = case["path"].as_str().unwrap_or("/").to_string();
    path = substitute_symbols(&path, symbols, &install.data_dir, &install.agent_root);
    let cookie_alias = case["request_headers"]
        .get("cookie")
        .and_then(Value::as_str)
        .unwrap_or("absent");
    let cookie = install.cookie(cookie_alias);
    let mut extra = HeaderMap::new();
    if let Some(obj) = case["request_headers"].as_object() {
        for (name, value) in obj {
            if name == "cookie" || name == "origin" {
                continue;
            }
            if let Some(v) = value.as_str() {
                if let (Ok(n), Ok(hv)) = (
                    axum::http::HeaderName::from_bytes(name.as_bytes()),
                    axum::http::HeaderValue::from_str(v),
                ) {
                    extra.insert(n, hv);
                }
            }
        }
    }
    let body_bytes = case.get("request_body").and_then(|b| {
        if b.is_null() {
            None
        } else {
            Some(serde_json::to_vec(b).unwrap_or_default())
        }
    });
    let (status, headers, text) = install
        .issue(
            method,
            &path,
            cookie.as_deref(),
            &extra,
            body_bytes.as_deref(),
        )
        .await;
    assert_eq!(
        status,
        case["status"].as_u64().unwrap_or(0) as u16,
        "{} {} {} wanted {} body={text}",
        case["name"],
        method,
        path,
        case["status"]
    );
    if let Some(expected_ct) = case["response_headers"]
        .get("content-type")
        .and_then(Value::as_str)
    {
        let got = headers.get("content-type").cloned().unwrap_or_default();
        if expected_ct.starts_with("text/event-stream") {
            assert!(
                got.starts_with("text/event-stream"),
                "{} content-type {got}",
                case["name"]
            );
        } else {
            assert_eq!(got, expected_ct, "{} content-type", case["name"]);
        }
    }
    if let Some(expected_loc) = case["response_headers"]
        .get("location")
        .and_then(Value::as_str)
    {
        assert_eq!(
            headers.get("location").map(String::as_str),
            Some(expected_loc),
            "{} location",
            case["name"]
        );
    }
    if case["response_body"].is_null() {
        return;
    }
    if partial_body(case) {
        if let Ok(actual) = serde_json::from_str::<Value>(&text) {
            assert!(
                actual.is_object() || actual.is_array(),
                "{} envelope",
                case["name"]
            );
        }
        return;
    }
    let expected = case["response_body"].clone();
    let expected = replace_placeholders(expected, symbols, &install.data_dir, &install.agent_root);
    if let Ok(actual) = serde_json::from_str::<Value>(&text) {
        bind_symbols(&expected, &actual, symbols);
        assert!(
            values_match(&expected, &actual),
            "{} body mismatch\nexpected={}\nactual={}",
            case["name"],
            serde_json::to_string_pretty(&expected).unwrap_or_default(),
            serde_json::to_string_pretty(&actual).unwrap_or_default()
        );
    } else {
        panic!("{} non-json body: {text}", case["name"]);
    }
}

fn replace_placeholders(
    value: Value,
    symbols: &HashMap<String, String>,
    data_dir: &Path,
    agent_root: &Path,
) -> Value {
    match value {
        Value::String(s) => Value::String(substitute_symbols(&s, symbols, data_dir, agent_root)),
        Value::Array(items) => Value::Array(
            items
                .into_iter()
                .map(|v| replace_placeholders(v, symbols, data_dir, agent_root))
                .collect(),
        ),
        Value::Object(map) => {
            let mut out = serde_json::Map::new();
            for (k, v) in map {
                out.insert(k, replace_placeholders(v, symbols, data_dir, agent_root));
            }
            Value::Object(out)
        }
        other => other,
    }
}

fn bind_symbols(expected: &Value, actual: &Value, symbols: &mut HashMap<String, String>) {
    match (expected, actual) {
        (Value::String(token), Value::String(got)) if token.starts_with('$') => {
            symbols.entry(token.clone()).or_insert_with(|| got.clone());
        }
        (Value::Object(e), Value::Object(a)) => {
            for (k, v) in e {
                if let Some(got) = a.get(k) {
                    bind_symbols(v, got, symbols);
                }
            }
        }
        (Value::Array(e), Value::Array(a)) => {
            for (ev, av) in e.iter().zip(a) {
                bind_symbols(ev, av, symbols);
            }
        }
        _ => {}
    }
}

pub fn assert_mounted_matches_fragment(
    mounted: &[(&str, &str)],
    fragment: &Value,
    filter: impl Fn(&str) -> bool,
) {
    let expected: Vec<String> = fragment["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .filter(|op| filter(op))
        .collect();
    let mut actual: Vec<String> = mounted
        .iter()
        .map(|(m, p)| format!("{m} {}", to_openapi(p)))
        .collect();
    let mut expected = expected;
    expected.sort();
    actual.sort();
    assert_eq!(actual, expected);
}
