use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::http::{HeaderMap, HeaderName, HeaderValue};
use axum::Router;
use lattice_auth::{
    requested_workspace, AuthConfig, AuthState, Clock, Identity, OrderedMap, AUTH_PATHS,
};
use serde_json::{json, Value};

const CAPTURED_HEADERS: [&str; 3] = ["content-type", "set-cookie", "retry-after"];

fn fixture() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join("auth_security.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("fixture is valid JSON")
}

fn mask_cookie(value: &str) -> String {
    let mut out = value.to_string();
    if let Some(rest) = out.strip_prefix("session_token=") {
        let end = rest.find([';', '"']).unwrap_or(rest.len());
        if end > 0 {
            out = format!("session_token=@token{}", &rest[end..]);
        }
    }
    if let Some(start) = out.find("expires=") {
        let tail = out[start + "expires=".len()..].to_string();
        let end = tail.find(';').unwrap_or(tail.len());
        if end > 0 {
            out = format!("{}expires=@date{}", &out[..start], &tail[end..]);
        }
    }
    out
}

fn install(profile: &Value, dir: &Path) -> Arc<AuthState> {
    for (name, seed) in [
        ("users.json", "seed_users"),
        ("sessions.json", "seed_sessions"),
    ] {
        let payload = profile.get(seed).cloned().unwrap_or_else(|| json!({}));
        let ordered: OrderedMap = serde_json::from_value(payload).expect("seed is a JSON object");
        std::fs::write(
            dir.join(name),
            lattice_auth::pyjson::dumps_indent2(&ordered).expect("render seed"),
        )
        .expect("write seed");
    }

    let mut env: HashMap<String, String> = profile["env"]
        .as_object()
        .expect("profile env")
        .iter()
        .map(|(key, value)| (key.clone(), value.as_str().unwrap_or_default().to_string()))
        .collect();
    env.insert(
        "LATTICEAI_DATA_DIR".into(),
        dir.to_string_lossy().into_owned(),
    );
    let mut config = AuthConfig::from_map(&env, None);
    config.data_dir = dir.to_path_buf();

    let created_at = profile["session_created_at"].as_f64().unwrap_or(0.0);
    AuthState::with_clock(config, Clock::frozen(created_at + 1.0))
}

fn header_map(headers: &Value) -> HeaderMap {
    let mut map = HeaderMap::new();
    if let Some(object) = headers.as_object() {
        for (name, value) in object {
            let (Ok(name), Ok(value)) = (
                HeaderName::from_bytes(name.as_bytes()),
                HeaderValue::from_str(value.as_str().unwrap_or_default()),
            ) else {
                panic!("unusable fixture header {name}");
            };
            map.insert(name, value);
        }
    }
    map
}

async fn serve(app: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind loopback");
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

struct Answer {
    status: u16,
    headers: HashMap<String, String>,
    body: String,
}

async fn issue(origin: &str, case: &Value) -> Answer {
    let request = &case["request"];
    let method =
        reqwest::Method::from_bytes(request["method"].as_str().expect("method").as_bytes())
            .expect("valid method");
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(20))
        .build()
        .expect("client");

    let mut builder = client.request(
        method,
        format!("{origin}{}", request["path"].as_str().expect("path")),
    );
    builder = builder.header("host", "127.0.0.1:4825");
    if let Some(object) = request["headers"].as_object() {
        for (name, value) in object {
            builder = builder.header(name.as_str(), value.as_str().unwrap_or_default());
        }
    }
    if let Some(body) = request["body"].as_str() {
        builder = builder.body(body.to_string());
    }
    let response = builder.send().await.expect("request");

    let status = response.status().as_u16();
    let mut headers = HashMap::new();
    for name in CAPTURED_HEADERS {
        if let Some(value) = response.headers().get(name) {
            let text = value.to_str().unwrap_or_default().to_string();
            headers.insert(
                name.to_string(),
                if name == "set-cookie" {
                    mask_cookie(&text)
                } else {
                    text
                },
            );
        }
    }
    Answer {
        status,
        headers,
        body: response.text().await.expect("body"),
    }
}

async fn run_route(case: &Value, state: Arc<AuthState>) -> Answer {
    let (origin, handle) = serve(lattice_auth::router_with_csrf(state)).await;
    let warmup = case["warmup"].as_u64().unwrap_or(0);
    for _ in 0..warmup {
        issue(&origin, case).await;
    }
    let answer = issue(&origin, case).await;
    handle.abort();
    answer
}

fn probe_body(identity: &Identity) -> String {
    let mut map = OrderedMap::new();
    map.insert("email", json!(identity.email));
    map.insert("role", json!(identity.role));
    serde_json::to_string(&map).expect("render probe")
}

async fn refusal(response: axum::response::Response) -> Answer {
    let status = response.status().as_u16();
    let mut headers = HashMap::new();
    for name in CAPTURED_HEADERS {
        if name == "content-type" {
            continue;
        }
        if let Some(value) = response.headers().get(name) {
            headers.insert(
                name.to_string(),
                value.to_str().unwrap_or_default().to_string(),
            );
        }
    }
    let bytes = axum::body::to_bytes(response.into_body(), 65_536)
        .await
        .expect("guard body");
    Answer {
        status,
        headers,
        body: String::from_utf8(bytes.to_vec()).expect("utf-8 body"),
    }
}

fn ok_answer(body: String) -> Answer {
    Answer {
        status: 200,
        headers: HashMap::new(),
        body,
    }
}

async fn run_guard(case: &Value, state: Arc<AuthState>) -> Answer {
    let request = &case["request"];
    let headers = header_map(&request["headers"]);
    let query = request["query"].as_str().filter(|text| !text.is_empty());
    let body_workspace = request["body_workspace"].as_str();

    match case["guard"].as_str().expect("guard name") {
        "require_user" => match state.require_user(&headers) {
            Ok(identity) => ok_answer(probe_body(&identity)),
            Err(response) => refusal(response).await,
        },
        "require_admin" => match state.require_admin(&headers) {
            Ok(identity) => ok_answer(probe_body(&identity)),
            Err(response) => refusal(response).await,
        },
        "requested_workspace" => match requested_workspace(&headers, query, body_workspace) {
            Ok(scope) => {
                let mut map = OrderedMap::new();
                map.insert("workspace", json!(scope));
                ok_answer(serde_json::to_string(&map).expect("render probe"))
            }
            Err(response) => refusal(response).await,
        },
        "enforce_rate_limit" => {
            let repeat = case["repeat"].as_u64().unwrap_or(1);
            for _ in 0..repeat {
                if let Err(response) = state.enforce_rate_limit("a@b.com", "agent") {
                    return refusal(response).await;
                }
            }
            let mut map = OrderedMap::new();
            map.insert("ok", json!(true));
            ok_answer(serde_json::to_string(&map).expect("render probe"))
        }
        other => panic!("unknown guard {other}"),
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn every_recorded_case_replays_byte_for_byte() {
    let fixture = fixture();
    let cases = fixture["cases"].as_array().expect("cases");
    assert!(
        cases.len() >= 50,
        "the recording lost cases: {}",
        cases.len()
    );

    let mut checked = 0usize;
    for case in cases {
        let name = case["name"].as_str().expect("case name");
        let profile_name = case["profile"].as_str().expect("profile name");
        let profile = &fixture["profiles"][profile_name];
        let dir = tempfile::tempdir().expect("tempdir");
        let state = install(profile, dir.path());

        let answer = match case["kind"].as_str().expect("kind") {
            "route" => run_route(case, state).await,
            "guard" => run_guard(case, state).await,
            other => panic!("unknown case kind {other}"),
        };

        let expected = &case["response"];
        assert_eq!(
            answer.status,
            expected["status"].as_u64().expect("status") as u16,
            "{name}: status (body was {})",
            answer.body
        );
        assert_eq!(
            answer.body,
            expected["body"].as_str().expect("body"),
            "{name}: body"
        );
        for (header, value) in expected["headers"].as_object().expect("headers") {
            assert_eq!(
                answer.headers.get(header.as_str()).map(String::as_str),
                value.as_str(),
                "{name}: header {header}"
            );
        }
        checked += 1;
    }
    assert_eq!(checked, cases.len());
}

#[test]
fn every_recorded_route_is_one_this_crate_declares() {
    let fixture = fixture();
    for case in fixture["cases"].as_array().expect("cases") {
        if case["kind"] != "route" {
            continue;
        }
        let path = case["request"]["path"].as_str().expect("path");
        assert!(
            AUTH_PATHS.contains(&path),
            "{path} is served by the fixture but missing from AUTH_PATHS"
        );
    }
}

#[test]
fn the_fixture_states_what_it_masks() {
    let fixture = fixture();
    let masks = fixture["masks"].as_object().expect("masks");
    assert!(masks.contains_key("@token"));
    assert!(masks.contains_key("@date"));
    for case in fixture["cases"].as_array().expect("cases") {
        let body = case["response"]["body"].as_str().unwrap_or_default();
        for mask in masks.keys() {
            assert!(
                !body.contains(mask.as_str()),
                "{}: {mask} appears in a body",
                case["name"]
            );
        }
    }
}

#[test]
fn masking_is_symmetric_with_the_generator() {
    assert_eq!(
        mask_cookie("session_token=abc-123; HttpOnly; Max-Age=86400; Path=/; SameSite=lax"),
        "session_token=@token; HttpOnly; Max-Age=86400; Path=/; SameSite=lax"
    );
    assert_eq!(
        mask_cookie(
            "session_token=\"\"; expires=Thu, 06 Aug 2026 07:06:40 GMT; HttpOnly; Max-Age=0"
        ),
        "session_token=\"\"; expires=@date; HttpOnly; Max-Age=0"
    );
    assert_eq!(mask_cookie("other=1; Path=/"), "other=1; Path=/");
}
