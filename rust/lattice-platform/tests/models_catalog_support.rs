//! Shared install / replay helpers for the R10 families.
//!
//! This file is pulled in via `#[path]` from the family test crates. Cargo
//! also sees it as an empty integration target; that is intentional.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::{AuthConfig, AuthState};
use serde_json::{json, Value};

pub const OWNER_EMAIL: &str = "owner@lattice.test";
pub const MEMBER_EMAIL: &str = "member@lattice.test";

pub struct Install {
    pub dir: tempfile::TempDir,
    pub auth: Arc<AuthState>,
    pub owner_token: String,
    pub member_token: String,
    pub invalid_token: String,
}

impl Install {
    pub fn start() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        write_users(dir.path());
        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "1".into());
        env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
        env.insert("LATTICEAI_PORT".into(), "4825".into());
        env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
        env.insert("LATTICEAI_MODE".into(), "local".into());
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            dir.path().to_string_lossy().into_owned(),
        );
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = dir.path().to_path_buf();
        let auth = AuthState::new(config);
        let owner_token = auth.sessions().create(OWNER_EMAIL, Some(OWNER_EMAIL));
        let member_token = auth.sessions().create(MEMBER_EMAIL, Some(MEMBER_EMAIL));
        Self {
            dir,
            auth,
            owner_token,
            member_token,
            invalid_token: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA".into(),
        }
    }

    pub fn data_dir(&self) -> &Path {
        self.dir.path()
    }

    pub fn cookie(&self, symbol: &str) -> Option<String> {
        match symbol {
            "session:owner" => Some(format!("session_token={}", self.owner_token)),
            "session:member" => Some(format!("session_token={}", self.member_token)),
            "session:invalid" => Some(format!("session_token={}", self.invalid_token)),
            "absent" | "" => None,
            other if other.starts_with("session_token=") => Some(other.to_string()),
            _ => None,
        }
    }
}

fn write_users(dir: &Path) {
    let mut owner = OrderedMap::new();
    owner.insert("id", json!("user:fa484c1e-1b5a-50ef-9c18-b73ca89368d7"));
    owner.insert("email", json!(OWNER_EMAIL));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    let mut member = OrderedMap::new();
    member.insert("id", json!("user:15669c19-6f17-5633-a2a6-a664d0d032c4"));
    member.insert("email", json!(MEMBER_EMAIL));
    member.insert("name", json!("Fixture Member"));
    member.insert("nickname", json!("member"));
    member.insert("role", json!("user"));
    let mut users = OrderedMap::new();
    users.insert(OWNER_EMAIL, serde_json::to_value(owner).unwrap());
    users.insert(MEMBER_EMAIL, serde_json::to_value(member).unwrap());
    let text = lattice_auth::pyjson::dumps_indent2(&users).expect("users");
    std::fs::write(dir.join("users.json"), text).expect("write users");
}

pub fn fixture(name: &str) -> Value {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "http", name]
        .iter()
        .collect();
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("fixture json")
}

pub fn family_records<'a>(root: &'a Value, family: &str) -> Vec<&'a Value> {
    root["fixtures"]
        .as_array()
        .expect("fixtures")
        .iter()
        .filter(|row| row["family"].as_str() == Some(family))
        .collect()
}

pub async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
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

pub struct Answer {
    pub status: u16,
    pub content_type: Option<String>,
    pub location: Option<String>,
    pub body: String,
}

pub async fn issue(
    origin: &str,
    method: &str,
    path: &str,
    query: &Value,
    headers: &Value,
    body: &Value,
    install: &Install,
) -> Answer {
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(20))
        .build()
        .expect("client");
    let mut url = format!("{origin}{path}");
    if let Some(object) = query.as_object() {
        if !object.is_empty() {
            let pairs: Vec<String> = object
                .iter()
                .map(|(key, value)| {
                    let rendered = match value {
                        Value::String(text) => text.clone(),
                        other => other.to_string().trim_matches('"').to_string(),
                    };
                    format!("{}={}", key, urlencoding_lite(&rendered))
                })
                .collect();
            url.push('?');
            url.push_str(&pairs.join("&"));
        }
    }
    let method = reqwest::Method::from_bytes(method.as_bytes()).expect("method");
    let mut builder = client.request(method, &url);
    builder = builder.header("host", "127.0.0.1:4825");
    if let Some(object) = headers.as_object() {
        for (name, value) in object {
            let text = value.as_str().unwrap_or("");
            if name.eq_ignore_ascii_case("cookie") {
                if let Some(cookie) = install.cookie(text) {
                    builder = builder.header("cookie", cookie);
                }
                continue;
            }
            builder = builder.header(name.as_str(), text);
        }
    }
    if !body.is_null() {
        builder = builder
            .header("content-type", "application/json")
            .body(serde_json::to_string(body).expect("body"));
    }
    let response = builder.send().await.expect("send");
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let location = response
        .headers()
        .get("location")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let body = response.text().await.expect("text");
    Answer {
        status,
        content_type,
        location,
        body,
    }
}

fn urlencoding_lite(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

pub fn substitute_path(path: &str, symbols: &HashMap<String, String>) -> String {
    let mut out = path.to_string();
    for (key, value) in symbols {
        out = out.replace(key, value);
    }
    out
}

pub fn substitute_value(value: &Value, symbols: &HashMap<String, String>) -> Value {
    match value {
        Value::String(text) => {
            if let Some(bound) = symbols.get(text.as_str()) {
                json!(bound)
            } else {
                let mut out = text.clone();
                for (key, replacement) in symbols {
                    out = out.replace(key, replacement);
                }
                json!(out)
            }
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| substitute_value(item, symbols))
                .collect(),
        ),
        Value::Object(map) => {
            let mut out = serde_json::Map::new();
            for (key, child) in map {
                out.insert(key.clone(), substitute_value(child, symbols));
            }
            Value::Object(out)
        }
        other => other.clone(),
    }
}

pub fn assert_matches(expected: &Value, actual: &Value, trail: &str) {
    match expected {
        Value::String(token) if token == "@any" => {}
        Value::String(token) if token == "@ts" => {
            assert!(
                actual.as_str().is_some_and(looks_like_ts),
                "{trail}: expected @ts, got {actual}"
            );
        }
        Value::String(token) if token == "@id" || token == "@uuid" => {
            assert!(
                actual.as_str().is_some_and(|text| !text.is_empty()),
                "{trail}: expected {token}, got {actual}"
            );
        }
        Value::String(token) if token == "@version" => {
            assert!(actual.as_str().is_some(), "{trail}: expected @version");
        }
        Value::Object(expected_map) => {
            if expected_map.len() == 1 && expected_map.contains_key("@text") {
                let want = expected_map["@text"].as_str().unwrap_or("");
                let got = if let Some(text) = actual.as_str() {
                    text.to_string()
                } else if let Some(text) = actual.get("@text").and_then(Value::as_str) {
                    text.to_string()
                } else {
                    actual.to_string()
                };
                assert_eq!(got, want, "{trail}: text body");
                return;
            }
            let Some(actual_map) = actual.as_object() else {
                panic!("{trail}: expected object, got {actual}");
            };
            for (key, child) in expected_map {
                assert!(
                    actual_map.contains_key(key),
                    "{trail}.{key}: missing (actual keys {:?})",
                    actual_map.keys().collect::<Vec<_>>()
                );
                assert_matches(child, &actual_map[key], &format!("{trail}.{key}"));
            }
        }
        Value::Array(expected_items) => {
            let Some(actual_items) = actual.as_array() else {
                panic!("{trail}: expected array, got {actual}");
            };
            assert_eq!(
                actual_items.len(),
                expected_items.len(),
                "{trail}: array length"
            );
            for (index, child) in expected_items.iter().enumerate() {
                assert_matches(child, &actual_items[index], &format!("{trail}[{index}]"));
            }
        }
        other => {
            assert_eq!(actual, other, "{trail}");
        }
    }
}

fn looks_like_ts(value: &str) -> bool {
    value.len() >= 10 && value.as_bytes().get(4) == Some(&b'-')
}

pub fn parse_body(raw: &str) -> Value {
    serde_json::from_str(raw).unwrap_or_else(|_| json!({ "@text": raw }))
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

pub fn openapi_fragment(name: &str) -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "openapi",
        name,
    ]
    .iter()
    .collect();
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("openapi fragment")
}
