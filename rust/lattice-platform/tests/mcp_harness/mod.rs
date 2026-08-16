//! Shared replay harness for WP-R8 families.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use lattice_agent::sandbox::Workspace;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::{AuthConfig, AuthState, Clock};
use lattice_platform::agent_registry::{self, AgentRegistryState};
use lattice_platform::agents::{self, AgentsState};
use lattice_platform::marketplace::{self, MarketplaceState};
use lattice_platform::mcp::{self, McpState};
use lattice_platform::plugins::{self, PluginsState};
use lattice_platform::tools::{self, ToolsState};
use serde_json::{json, Value};

const OWNER_EMAIL: &str = "owner@lattice.test";
const MEMBER_EMAIL: &str = "member@lattice.test";

pub fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../fixtures")
}

pub fn load_http(name: &str) -> Value {
    let path = fixtures_dir().join("http").join(name);
    serde_json::from_str(&std::fs::read_to_string(&path).expect("fixture")).expect("json")
}

pub fn load_openapi(name: &str) -> Value {
    let path = fixtures_dir().join("openapi").join(name);
    serde_json::from_str(&std::fs::read_to_string(&path).expect("fragment")).expect("json")
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

pub struct Install {
    pub origin: String,
    pub data_dir: PathBuf,
    pub agent_root: PathBuf,
    pub brain_dir: PathBuf,
    pub skills_dir: PathBuf,
    pub handle: tokio::task::JoinHandle<()>,
    pub tokens: HashMap<String, String>,
    _tmp: tempfile::TempDir,
}

impl Install {
    pub async fn start() -> Self {
        let tmp = tempfile::tempdir().expect("tempdir");
        let data_dir = tmp.path().join("data");
        let agent_root = tmp.path().join("agent");
        let brain_dir = tmp.path().join("brain");
        let skills_dir = tmp.path().join("skills");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::create_dir_all(&agent_root).unwrap();
        std::fs::create_dir_all(&brain_dir).unwrap();
        std::fs::create_dir_all(&skills_dir).unwrap();
        write_users(&data_dir);

        let mut env = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            data_dir.to_string_lossy().into_owned(),
        );
        env.insert("LATTICEAI_MODE".into(), "local".into());
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert("LATTICEAI_OPEN_REGISTRATION".into(), "1".into());
        env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
        env.insert("LATTICEAI_INVITE_GATE_ENABLED".into(), "0".into());
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = data_dir.clone();
        let auth = AuthState::with_clock(config, Clock::frozen(1_700_000_000.0));
        // HTTP register+login used to drop Set-Cookie (issue() only kept
        // content-type/location), so every later call went out anonymous and
        // 401'd. Seed the accounts on disk and mint sessions the same way the
        // other platform harnesses do.
        let owner_token = auth.sessions().create(OWNER_EMAIL, Some(OWNER_EMAIL));
        let member_token = auth.sessions().create(MEMBER_EMAIL, Some(MEMBER_EMAIL));

        let workspace = Workspace::new(&agent_root).expect("workspace");
        let tools = ToolsState::new(Arc::clone(&auth), workspace, &brain_dir);
        let mcp = McpState::new(Arc::clone(&auth), &data_dir)
            .with_tools(tools.clone())
            .with_skills_dir(&skills_dir);
        let market = MarketplaceState::new(Arc::clone(&auth), &data_dir);
        let plugins = PluginsState::new(Arc::clone(&auth), &data_dir);
        let registry = AgentRegistryState::new(Arc::clone(&auth), &data_dir);
        let agents = AgentsState::new(Arc::clone(&auth), &data_dir);

        let app = lattice_auth::router_with_csrf(Arc::clone(&auth))
            .merge(mcp::router(mcp))
            .merge(marketplace::router(market))
            .merge(plugins::router(plugins))
            .merge(agent_registry::router(registry))
            .merge(agents::router(agents))
            .merge(tools::router(tools));

        let (origin, handle) = serve(app).await;
        let mut tokens = HashMap::new();
        tokens.insert("session:owner".into(), owner_token);
        tokens.insert("session:member".into(), member_token);
        let mut install = Self {
            origin,
            data_dir,
            agent_root,
            brain_dir,
            skills_dir,
            handle,
            tokens,
            _tmp: tmp,
        };
        install.bootstrap().await;
        install
    }

    async fn bootstrap(&mut self) {
        // tools_misc.json captures list_dir *after* the approve/drift
        // seeding steps in gen_http_fixtures_ecosystem.py.
        for (rel, content) in [
            ("fixture-note.md", "Fixture original line.\n"),
            (
                "fixture-apply.md",
                "Apply me as reviewed.\nPlus the approved edit.\n",
            ),
            ("fixture-reject.md", "Reject me.\n"),
            ("fixture-conflict.md", "Conflict drifted.\n"),
            ("subdir/nested.txt", "nested\n"),
        ] {
            let _ = self
                .issue(
                    "POST",
                    "/tools/write_file",
                    Some("session:owner"),
                    Some(json!({"path": rel, "content": content})),
                )
                .await;
        }
        let _ = self
            .issue(
                "POST",
                "/agents/api/registry",
                Some("session:owner"),
                Some(json!({
                    "name": "Fixture Reviewer",
                    "type": "custom",
                    "description": "A canned agent.",
                    "capabilities": ["review"],
                    "config": {"note": "fixture"},
                    "version": "1.0.0"
                })),
            )
            .await;
        let _ = self
            .issue(
                "POST",
                "/mcp/custom",
                Some("session:owner"),
                Some(json!({
                    "name": "Fixture Custom MCP",
                    "package": "npx fixture-mcp",
                    "description": "Canned custom MCP.",
                    "category": "custom"
                })),
            )
            .await;
    }

    pub async fn issue(
        &self,
        method: &str,
        path: &str,
        session: Option<&str>,
        body: Option<Value>,
    ) -> Answer {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(20))
            .build()
            .expect("client");
        let method = reqwest::Method::from_bytes(method.as_bytes()).expect("method");
        let mut builder = client
            .request(method, format!("{}{path}", self.origin))
            .header("host", "127.0.0.1:4825")
            .header("origin", "http://127.0.0.1:4825");
        if let Some(session) = session {
            if session == "session:invalid" {
                builder = builder.header("cookie", "session_token=not-a-real-session-token");
            } else if let Some(token) = self.tokens.get(session) {
                builder = builder.header("cookie", format!("session_token={token}"));
            }
        }
        if let Some(body) = body {
            builder = builder
                .header("content-type", "application/json")
                .body(body.to_string());
        }
        let response = builder.send().await.expect("request");
        let status = response.status().as_u16();
        let mut headers = HashMap::new();
        for name in ["content-type", "content-disposition", "location"] {
            if let Some(value) = response.headers().get(name) {
                headers.insert(
                    name.to_string(),
                    value.to_str().unwrap_or_default().to_string(),
                );
            }
        }
        if let Some(value) = response.headers().get("set-cookie") {
            headers.insert(
                "set-cookie".to_string(),
                value.to_str().unwrap_or_default().to_string(),
            );
        }
        let bytes = response.bytes().await.expect("bytes");
        Answer {
            status,
            headers,
            body: String::from_utf8_lossy(&bytes).into_owned(),
            bytes: bytes.to_vec(),
        }
    }
}

impl Drop for Install {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

pub struct Answer {
    pub status: u16,
    pub headers: HashMap<String, String>,
    pub body: String,
    pub bytes: Vec<u8>,
}

async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
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
    (format!("http://{addr}"), handle)
}

pub fn substitute_path(path: &str, symbols: &HashMap<String, String>) -> String {
    let mut out = path.to_string();
    for (k, v) in symbols {
        out = out.replace(k, v);
    }
    out
}

pub fn cookie_session(headers: &Value) -> Option<String> {
    headers
        .get("cookie")
        .and_then(Value::as_str)
        .map(str::to_string)
}

pub fn iso_like(value: &str) -> bool {
    value.contains('T') && value.len() >= 19
}

pub fn match_value(expected: &Value, actual: &Value, symbols: &HashMap<String, String>) -> bool {
    match expected {
        Value::String(s) if s == "@any" => true,
        Value::String(s) if s == "@ts" => actual.as_str().map(iso_like).unwrap_or(false),
        Value::String(s) if s == "@version" => actual.as_str().is_some(),
        Value::String(s) if s == "@id" || s == "@uuid" => actual.as_str().is_some(),
        Value::String(s) if s.starts_with('$') => {
            if let Some(bound) = symbols.get(s) {
                actual.as_str() == Some(bound.as_str())
            } else {
                actual.as_str().is_some()
            }
        }
        Value::String(s)
            if s.contains("<AGENT_ROOT>")
                || s.contains("<DATA_DIR>")
                || s.contains("<SANDBOX>")
                || s.contains("<REPO>")
                || s.contains("<HOME>") =>
        {
            actual.as_str().is_some()
        }
        Value::Object(exp) => {
            if exp.contains_key("@binary") {
                return true;
            }
            let Some(act) = actual.as_object() else {
                return false;
            };
            exp.iter().all(|(k, v)| {
                act.get(k)
                    .map(|a| match_value(v, a, symbols))
                    .unwrap_or(false)
            })
        }
        Value::Array(exp) => {
            let Some(act) = actual.as_array() else {
                return false;
            };
            exp.len() == act.len()
                && exp
                    .iter()
                    .zip(act.iter())
                    .all(|(e, a)| match_value(e, a, symbols))
        }
        other => other == actual,
    }
}

pub fn query_string(query: &Value) -> String {
    let Some(obj) = query.as_object() else {
        return String::new();
    };
    if obj.is_empty() {
        return String::new();
    }
    let parts: Vec<String> = obj
        .iter()
        .map(|(k, v)| {
            let val = match v {
                Value::String(s) => s.clone(),
                other => other.to_string().trim_matches('"').to_string(),
            };
            format!("{k}={}", urlencoding_lite(&val))
        })
        .collect();
    format!("?{}", parts.join("&"))
}

fn urlencoding_lite(value: &str) -> String {
    let mut out = String::new();
    for byte in value.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char);
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn write_users(dir: &Path) {
    let mut owner = OrderedMap::new();
    owner.insert("id", json!("user:fa484c1e-1b5a-50ef-9c18-b73ca89368d7"));
    owner.insert("email", json!(OWNER_EMAIL));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    let mut member = OrderedMap::new();
    member.insert("id", json!("user:15669c19-6f17-5633-a2a6-a664d0d032c4"));
    member.insert("email", json!(MEMBER_EMAIL));
    member.insert("name", json!("Fixture Member"));
    member.insert("nickname", json!("member"));
    member.insert("role", json!("user"));
    member.insert("disabled", json!(false));
    let mut users = OrderedMap::new();
    users.insert(OWNER_EMAIL, serde_json::to_value(owner).unwrap());
    users.insert(MEMBER_EMAIL, serde_json::to_value(member).unwrap());
    let text = lattice_auth::pyjson::dumps_indent2(&users).expect("users");
    std::fs::write(dir.join("users.json"), text).expect("write users");
}

#[allow(dead_code)]
pub fn _path_unused(_p: &Path) {}
