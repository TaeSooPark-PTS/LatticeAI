//! Shared replay harness for WP-R2 families.
//!
//! Copied from `lattice-auth/tests/auth_security.rs`: real loopback socket +
//! `ConnectInfo`, fixture tokens applied symmetrically.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::too_many_arguments, clippy::unnecessary_sort_by, clippy::field_reassign_with_default)]
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use serde_json::{json, Value};

pub const CAPTURED_HEADERS: [&str; 3] = ["content-type", "content-disposition", "cache-control"];

pub fn load_fixture(name: &str) -> Value {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "http", name]
        .iter()
        .collect();
    let text =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    serde_json::from_str(&text).expect("fixture json")
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
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
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

pub fn seed_users(dir: &Path) {
    let mut owner = OrderedMap::new();
    owner.insert("id", json!("user:fa484c1e-1b5a-50ef-9c18-b73ca89368d7"));
    owner.insert("email", json!("owner@lattice.test"));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert("password", json!("x"));
    let mut member = OrderedMap::new();
    member.insert("id", json!("user:15669c19-6f17-5633-a2a6-a664d0d032c4"));
    member.insert("email", json!("member@lattice.test"));
    member.insert("name", json!("Fixture Member"));
    member.insert("nickname", json!("member"));
    member.insert("role", json!("user"));
    member.insert("disabled", json!(false));
    member.insert("password", json!("x"));
    let mut users = OrderedMap::new();
    users.insert("owner@lattice.test", serde_json::to_value(&owner).unwrap());
    users.insert(
        "member@lattice.test",
        serde_json::to_value(&member).unwrap(),
    );
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).unwrap(),
    )
    .unwrap();
}

pub fn seed_chat_history(dir: &Path) {
    let history = json!([
        {
            "role": "user",
            "content": "배포 키는 sk-fixture1234567890abcdefghij 이고 주민번호는 900101-1234567 이야",
            "timestamp": "2026-08-01T09:00:00+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "conversation_id": "conv-fixture-001",
            "model": "local:test"
        },
        {
            "role": "assistant",
            "content": "민감정보는 저장하지 않겠습니다.",
            "timestamp": "2026-08-01T09:00:05+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "conversation_id": "conv-fixture-001",
            "model": "local:test"
        }
    ]);
    std::fs::write(
        dir.join("chat_history.json"),
        serde_json::to_string_pretty(&history).unwrap(),
    )
    .unwrap();
}

pub fn seed_audit_base(dir: &Path) {
    seed_audit(dir, &base_audit_events());
}

pub fn seed_audit(dir: &Path, events: &Value) {
    std::fs::write(
        dir.join("audit_log.json"),
        serde_json::to_string_pretty(events).unwrap(),
    )
    .unwrap();
}

pub fn base_audit_events() -> Value {
    json!([
        {
            "event_type": "document_upload",
            "timestamp": "2026-08-01T09:05:00+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "filename": "fixture-contract.txt",
            "file_id": "fixture-contract.txt",
            "ext": ".txt",
            "bytes": 128,
            "sensitivity": "high",
            "sensitive_labels": ["secret"],
            "content_preview": "API key: sk-fixture1234567890abcdefghij",
            "extracted_text": "API key: sk-fixture1234567890abcdefghij / 계약 조건"
        }
    ])
}

pub fn eight_audit_events() -> Value {
    json!([
        {
            "event_type": "document_upload",
            "timestamp": "2026-08-01T09:05:00+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "filename": "fixture-contract.txt",
            "file_id": "fixture-contract.txt",
            "ext": ".txt",
            "bytes": 128,
            "sensitivity": "high",
            "sensitive_labels": ["secret"],
            "content_preview": "API key: sk-fixture1234567890abcdefghij",
            "extracted_text": "API key: sk-fixture1234567890abcdefghij / 계약 조건"
        },
        {
            "event_type": "workspace_created",
            "timestamp": "2026-08-01T09:06:00+00:00",
            "user_email": "owner@lattice.test",
            "workspace_id": "org-Fixture-Team"
        },
        {
            "event_type": "workspace_created",
            "timestamp": "2026-08-01T09:06:01+00:00",
            "user_email": "owner@lattice.test",
            "workspace_id": "org-Archive-Target"
        },
        {
            "event_type": "workspace_member_added",
            "timestamp": "2026-08-01T09:06:02+00:00",
            "user_email": "owner@lattice.test",
            "workspace_id": "org-Fixture-Team",
            "role": "member"
        },
        {
            "event_type": "workspace_snapshot",
            "timestamp": "2026-08-01T09:06:03+00:00",
            "user_email": "owner@lattice.test"
        },
        {
            "event_type": "workspace_snapshot",
            "timestamp": "2026-08-01T09:06:04+00:00",
            "user_email": "owner@lattice.test"
        },
        {
            "event_type": "invitation_created",
            "timestamp": "2026-08-01T09:06:05+00:00",
            "user_email": "owner@lattice.test",
            "workspace_id": "org-Fixture-Team",
            "role": "member"
        },
        {
            "event_type": "invitation_created",
            "timestamp": "2026-08-01T09:06:06+00:00",
            "user_email": "owner@lattice.test",
            "workspace_id": null,
            "role": "viewer"
        }
    ])
}

pub fn install_auth(dir: &Path, require_auth: bool) -> (Arc<AuthState>, String, String) {
    seed_users(dir);
    let mut env = HashMap::new();
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.to_string_lossy().into_owned(),
    );
    env.insert("LATTICEAI_HOST".into(), "127.0.0.1".into());
    env.insert("LATTICEAI_PORT".into(), "4825".into());
    env.insert(
        "LATTICEAI_REQUIRE_AUTH".into(),
        if require_auth { "1" } else { "0" }.into(),
    );
    env.insert("LATTICEAI_OPEN_REGISTRATION".into(), "1".into());
    env.insert("LATTICEAI_RATE_LIMIT".into(), "0".into());
    env.insert("LATTICE_TZ".into(), "UTC".into());
    // tz_name() reads the process env, not AuthConfig.
    std::env::set_var("LATTICE_TZ", "UTC");
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = dir.to_path_buf();
    config.require_auth = require_auth;
    let auth = AuthState::with_clock(config, Clock::frozen(1_775_000_000.0));
    let owner = auth.sessions().create(
        "user:fa484c1e-1b5a-50ef-9c18-b73ca89368d7",
        Some("owner@lattice.test"),
    );
    let member = auth.sessions().create(
        "user:15669c19-6f17-5633-a2a6-a664d0d032c4",
        Some("member@lattice.test"),
    );
    (auth, owner, member)
}

pub async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
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

pub struct Answer {
    pub status: u16,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
}

pub async fn issue(
    origin: &str,
    method: &str,
    path: &str,
    query: &Value,
    headers: &Value,
    body: &Value,
    owner: &str,
    member: &str,
) -> Answer {
    let mut url = format!("{origin}{path}");
    if let Some(obj) = query.as_object() {
        if !obj.is_empty() {
            let qs: Vec<String> = obj
                .iter()
                .map(|(k, v)| {
                    format!(
                        "{}={}",
                        k,
                        match v {
                            Value::String(s) => s.clone(),
                            other => other.to_string().trim_matches('"').to_string(),
                        }
                    )
                })
                .collect();
            url.push('?');
            url.push_str(&qs.join("&"));
        }
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(20))
        .build()
        .unwrap();
    let method = reqwest::Method::from_bytes(method.as_bytes()).unwrap();
    let mut builder = client
        .request(method, &url)
        .header("host", "127.0.0.1:4825");
    if let Some(obj) = headers.as_object() {
        for (name, value) in obj {
            let mut text = value.as_str().unwrap_or("").to_string();
            if name.eq_ignore_ascii_case("cookie") {
                text = text
                    .replace("session:owner", &format!("session_token={owner}"))
                    .replace("session:member", &format!("session_token={member}"))
                    .replace("absent", "");
                if text.is_empty() || text == "absent" {
                    continue;
                }
            }
            builder = builder.header(name.as_str(), text);
        }
    }
    match body {
        Value::Null => {}
        Value::String(s) => {
            builder = builder.body(s.clone());
        }
        other => {
            builder = builder
                .header("content-type", "application/json")
                .body(serde_json::to_string(other).unwrap());
        }
    }
    let response = builder.send().await.expect("request");
    let status = response.status().as_u16();
    let mut captured = HashMap::new();
    for name in CAPTURED_HEADERS {
        if let Some(value) = response.headers().get(name) {
            captured.insert(
                name.to_string(),
                value.to_str().unwrap_or_default().to_string(),
            );
        }
    }
    // also grab x-accel-buffering for SSE
    if let Some(value) = response.headers().get("x-accel-buffering") {
        captured.insert(
            "x-accel-buffering".into(),
            value.to_str().unwrap_or_default().to_string(),
        );
    }
    Answer {
        status,
        headers: captured,
        body: response.bytes().await.unwrap().to_vec(),
    }
}

pub fn values_match(expected: &Value, actual: &Value) -> bool {
    match expected {
        Value::String(s) if s == "@any" => true,
        Value::String(s) if s == "@ts" => actual.as_str().map(looks_iso).unwrap_or(false),
        Value::String(s) if s == "@id" => actual.as_str().map(|t| !t.is_empty()).unwrap_or(false),
        Value::String(s) if s == "@uuid" => actual.as_str().map(|t| t.len() >= 8).unwrap_or(false),
        Value::String(s) if s == "@version" => {
            actual.as_str().map(|t| !t.is_empty()).unwrap_or(false)
        }
        Value::String(s) if s == "$device_public_key" || s.starts_with('$') => {
            actual.as_str().map(|t| !t.is_empty()).unwrap_or(false)
        }
        Value::String(s) if s.contains("<DATA_DIR>") => actual.as_str().is_some_and(|got| {
            got.ends_with(s.rsplit("<DATA_DIR>").next().unwrap_or("")) && !got.is_empty()
        }),
        Value::Object(exp) if exp.len() == 1 && exp.contains_key("@text") => {
            let exp_text = exp.get("@text").and_then(Value::as_str).unwrap_or("");
            let act_text = match actual {
                Value::String(s) => s.clone(),
                _ => String::from_utf8_lossy(&[]).into_owned(),
            };
            text_match(exp_text, &act_text)
        }
        Value::Object(exp) if exp.len() == 1 && exp.contains_key("@binary") => {
            // handled by the caller with raw bytes
            true
        }
        Value::Object(exp) => {
            let Some(act) = actual.as_object() else {
                return false;
            };
            for (k, ev) in exp {
                match act.get(k) {
                    Some(av) => {
                        if !values_match(ev, av) {
                            return false;
                        }
                    }
                    None => return false,
                }
            }
            true
        }
        Value::Array(exp) => {
            let Some(act) = actual.as_array() else {
                return false;
            };
            if exp.len() != act.len() {
                return false;
            }
            exp.iter().zip(act.iter()).all(|(e, a)| values_match(e, a))
        }
        other => other == actual,
    }
}

pub fn looks_iso(s: &str) -> bool {
    s.len() >= 10 && s.as_bytes().get(4) == Some(&b'-') && s.as_bytes().get(7) == Some(&b'-')
}

pub fn text_match(expected: &str, actual: &str) -> bool {
    if expected == actual {
        return true;
    }
    let tokens: Vec<&str> = split_tokens(expected);
    let mut rest = actual;
    for tok in tokens {
        if matches!(tok, "@ts" | "@any" | "@id" | "@version") {
            continue;
        }
        if tok.is_empty() {
            continue;
        }
        if let Some(pos) = rest.find(tok) {
            rest = &rest[pos + tok.len()..];
        } else {
            return false;
        }
    }
    true
}

fn split_tokens(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = s;
    while let Some(idx) = rest.find('@') {
        if idx > 0 {
            out.push(&rest[..idx]);
        }
        let tail = &rest[idx..];
        let (tok, next) = if tail.starts_with("@ts") {
            ("@ts", 3)
        } else if tail.starts_with("@any") {
            ("@any", 4)
        } else if tail.starts_with("@id") {
            ("@id", 3)
        } else if tail.starts_with("@version") {
            ("@version", 8)
        } else {
            out.push(&tail[..1]);
            rest = &tail[1..];
            continue;
        };
        out.push(tok);
        rest = &tail[next..];
    }
    if !rest.is_empty() {
        out.push(rest);
    }
    out
}

pub fn assert_case(name: &str, expected: &Value, answer: &Answer) {
    let exp_status = expected["status"].as_u64().unwrap() as u16;
    assert_eq!(
        answer.status,
        exp_status,
        "{name}: status body={}",
        String::from_utf8_lossy(&answer.body)
    );

    if let Some(hdrs) = expected.get("response_headers").and_then(Value::as_object) {
        for (k, v) in hdrs {
            let got = answer.headers.get(k).map(String::as_str).unwrap_or("");
            let want = v.as_str().unwrap_or("");
            if want.contains("@any") {
                assert!(
                    !got.is_empty() || want == "@any",
                    "{name}: header {k} empty"
                );
            } else {
                assert_eq!(got, want, "{name}: header {k}");
            }
        }
    }

    let body = &expected["response_body"];
    if let Some(obj) = body.as_object() {
        if let Some(bin) = obj.get("@binary") {
            let magic = bin
                .get("leading_magic")
                .and_then(Value::as_str)
                .unwrap_or("");
            if !magic.is_empty() {
                let decoded = hex_decode(magic);
                assert!(
                    answer.body.starts_with(&decoded),
                    "{name}: binary magic want {magic} got {:02x?}",
                    &answer.body[..answer.body.len().min(8)]
                );
            }
            return;
        }
        if let Some(text) = obj.get("@text").and_then(Value::as_str) {
            let actual = String::from_utf8_lossy(&answer.body);
            assert!(
                text_match(text, &actual),
                "{name}: text body\nexpected: {text}\nactual:   {actual}"
            );
            return;
        }
    }
    let actual_json: Value = serde_json::from_slice(&answer.body).unwrap_or(Value::Null);
    if body.is_null() {
        return;
    }
    assert!(
        values_match(body, &actual_json),
        "{name}: json body\nexpected: {}\nactual:   {}",
        serde_json::to_string_pretty(body).unwrap(),
        serde_json::to_string_pretty(&actual_json).unwrap()
    );
}

fn hex_decode(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .filter_map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok())
        .collect()
}

pub fn owned_families(family: &str) -> bool {
    matches!(
        family,
        "admin.py" | "security_dashboard.py" | "funnel_metrics.py" | "features.py" | "setup.py"
    )
}
