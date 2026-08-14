//! Replay `rust/fixtures/http/auth_security.json` against the native crate.
//!
//! The fixture is a recording of the **real** Python application and the
//! **real** runtime guard closures (see `scripts/gen_auth_fixtures.py`). This
//! test is the other half of that contract: for every case it rebuilds the
//! recorded install — same env, same seeded `users.json` and `sessions.json` —
//! and asserts the native answer is byte-identical.
//!
//! Two kinds of case:
//!
//! * `route` — issued over a real loopback socket against
//!   [`lattice_auth::router_with_csrf`], with `ConnectInfo` wired so the peer
//!   is what the CSRF policy and the rate-limit key actually see;
//! * `guard` — calls [`AuthState::require_user`], `require_admin`,
//!   [`lattice_auth::requested_workspace`] or the token bucket directly, and
//!   renders the same tiny probe payload the generator recorded.
//!
//! Masking is symmetric with the generator and covers exactly two values, both
//! documented in the fixture's `masks` block: the freshly issued session token
//! and the logout cookie's live `expires` date.

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

/// Response fields the fixture pins.
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

/// The generator's `mask_cookie`, character for character.
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

/// Rebuild the install one case was recorded against.
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

    // One second after the seeded sessions were minted: live, and below the
    // sliding-refresh threshold, exactly as when the fixture was recorded.
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

// ── route cases ──────────────────────────────────────────────────────────────

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

/// Status, captured headers, and the body — the whole comparable answer.
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
    // The recording ran behind `base_url="http://127.0.0.1:4825"`; the Host
    // header feeds the CSRF same-origin fallback, so it is stated rather than
    // left to the ephemeral test port.
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

// ── guard cases ──────────────────────────────────────────────────────────────

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
            // Guard cases record only what the raised `HTTPException` carried;
            // FastAPI supplies the content type, not the guard.
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

// ── the test ─────────────────────────────────────────────────────────────────

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
    // Both masks are only ever applied to `Set-Cookie`; nothing in a body is
    // masked, so a body comparison is a whole-body comparison.
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
