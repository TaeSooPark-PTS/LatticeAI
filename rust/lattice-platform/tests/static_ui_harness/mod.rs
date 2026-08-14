//! Shared replay machinery for the static/UI parity suites (WP-I4).
//!
//! Everything here exists to make one sentence testable: *the Rust front door
//! answers the byte the Python one answered*. The recording is
//! `rust/fixtures/http/static_ui.json`, written by
//! `scripts/gen_static_fixtures.py` from the live FastAPI app; this module
//! rebuilds the machine that recording was made on — the same static tree, the
//! same invite posture — and serves it over real HTTP, because half the contract
//! is headers and hyper is the thing that writes them.
//!
//! The module lives under a `static_ui_` prefix rather than `tests/common` on
//! purpose: sibling work packages own their own test files in this directory and
//! a shared `common` would be a shared file.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports, unused_variables)]
#![allow(
    clippy::too_many_arguments,
    clippy::unnecessary_sort_by,
    clippy::field_reassign_with_default
)]

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use lattice_platform::static_ui::StaticUiConfig;
use serde_json::Value;
use sha2::{Digest, Sha256};

/// Headers the fixture pins. Anything outside this list (dates, etags) is
/// machine noise; anything inside it is compared **including its absence**.
pub const HEADER_WHITELIST: [&str; 9] = [
    "content-type",
    "content-length",
    "location",
    "cache-control",
    "pragma",
    "expires",
    "content-security-policy",
    "service-worker-allowed",
    "allow",
];

/// The committed recording, parsed once.
pub fn fixture() -> &'static Value {
    static FIXTURE: OnceLock<Value> = OnceLock::new();
    FIXTURE.get_or_init(|| {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("fixtures")
            .join("http")
            .join("static_ui.json");
        let raw =
            std::fs::read(&path).unwrap_or_else(|err| panic!("read {}: {err}", path.display()));
        serde_json::from_slice(&raw).expect("fixture is JSON")
    })
}

/// The cases recorded against one install.
pub fn cases_for(config: &str) -> Vec<&'static Value> {
    fixture()["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter(|case| case["config"] == config)
        .collect()
}

/// Rebuild the recorded static tree for `config` under `root`.
///
/// The bytes come from the fixture, not from `static/`: the real tree is vite
/// output whose asset names change on every build, and a golden that churns with
/// the bundler proves nothing about the server.
pub fn materialise(root: &Path, config: &str) {
    let omit: Vec<&str> = fixture()["configs"][config]["omit"]
        .as_array()
        .expect("omit list")
        .iter()
        .map(|value| value.as_str().expect("path"))
        .collect();
    for (relative, entry) in fixture()["tree"].as_object().expect("tree") {
        if omit.contains(&relative.as_str()) {
            continue;
        }
        let bytes = BASE64
            .decode(entry["b64"].as_str().expect("b64"))
            .expect("base64");
        let target = root.join(relative);
        std::fs::create_dir_all(target.parent().expect("parent")).expect("mkdir");
        std::fs::write(&target, &bytes).expect("write");
    }
}

/// The Rust configuration matching a recorded install.
pub fn config_for(static_dir: PathBuf, config: &str) -> StaticUiConfig {
    let settings = &fixture()["configs"][config];
    let invite = &fixture()["invite"];
    StaticUiConfig {
        static_dir,
        invite_gate_enabled: settings["invite_gate_enabled"].as_bool().expect("flag"),
        invite_code: invite["code"].as_str().expect("code").to_string(),
        invite_cookie_secret: invite["secret"].as_str().expect("secret").to_string(),
        secure_cookies: settings["secure_cookies"].as_bool().expect("flag"),
    }
}

/// A scratch directory under the crate's target dir, emptied first.
pub fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("static_ui")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch");
    dir
}

/// One recorded install, served over loopback HTTP.
pub struct Install {
    /// Where the client should knock.
    pub origin: String,
    /// A client that does **not** follow redirects — the 308s are the contract.
    pub client: reqwest::Client,
    /// The static root this install serves.
    pub static_dir: PathBuf,
}

impl Install {
    /// Materialise `config`, mount the static + redirect routers, and serve.
    pub async fn start(config: &str) -> Self {
        let static_dir = scratch(config);
        materialise(&static_dir, config);
        let router = lattice_platform::static_ui::router(config_for(static_dir.clone(), config))
            // Merging both routers is also the proof that they do not claim the
            // same path: axum panics on a duplicate route.
            .merge(lattice_platform::ui_redirects::router());
        Self::serve(router, static_dir).await
    }

    /// Serve an already-built router.
    pub async fn serve(router: axum::Router, static_dir: PathBuf) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let origin = format!("http://{}", listener.local_addr().expect("addr"));
        tokio::spawn(async move {
            let _ = axum::serve(listener, router).await;
        });
        Self {
            origin,
            client: reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .expect("client"),
            static_dir,
        }
    }

    /// Replay one recorded case and assert every pinned field.
    pub async fn replay(&self, case: &Value) {
        let name = case["name"].as_str().expect("name");
        let method =
            reqwest::Method::from_bytes(case["method"].as_str().expect("method").as_bytes())
                .expect("method");
        let path = case["path"].as_str().expect("path");
        let mut request = self
            .client
            .request(method.clone(), format!("{}{path}", self.origin));
        if let Some(cookies) = case.get("cookies").and_then(Value::as_object) {
            let jar: Vec<String> = cookies
                .iter()
                .map(|(key, value)| format!("{key}={}", value.as_str().expect("cookie")))
                .collect();
            request = request.header("cookie", jar.join("; "));
        }
        let response = request
            .send()
            .await
            .unwrap_or_else(|err| panic!("{name}: {err}"));

        assert_eq!(
            response.status().as_u16(),
            case["status"].as_u64().expect("status") as u16,
            "{name}: status"
        );

        let expected_headers = case["headers"].as_object().expect("headers");
        for key in HEADER_WHITELIST {
            let expected = expected_headers.get(key).and_then(Value::as_str);
            let actual = response
                .headers()
                .get(key)
                .map(|value| value.to_str().expect("ascii header"));
            assert_eq!(actual, expected, "{name}: header {key}");
        }

        if let Some(expected) = case.get("set_cookie") {
            let raw = response
                .headers()
                .get("set-cookie")
                .unwrap_or_else(|| panic!("{name}: no set-cookie"))
                .to_str()
                .expect("ascii");
            assert_eq!(set_cookie_shape(raw), *expected, "{name}: set-cookie shape");
        } else {
            assert!(
                response.headers().get("set-cookie").is_none(),
                "{name}: unexpected set-cookie"
            );
        }

        let body = response.bytes().await.expect("body");
        assert_eq!(
            sha256(&body),
            case["body_sha256"].as_str().expect("digest"),
            "{name}: body digest ({} bytes here, {} recorded)",
            body.len(),
            case["body_bytes"]
        );
    }

    /// Replay every case recorded against this install.
    pub async fn replay_all(&self, config: &str) {
        let cases = cases_for(config);
        assert!(!cases.is_empty(), "no cases for {config}");
        for case in cases {
            self.replay(case).await;
        }
    }
}

/// The generator's `_set_cookie_shape`, so the two can be compared as data.
pub fn set_cookie_shape(raw: &str) -> Value {
    let (head, rest) = raw.split_once(';').unwrap_or((raw, ""));
    let (name, value) = head.split_once('=').unwrap_or((head, ""));
    let attributes: Vec<&str> = rest
        .split(';')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect();
    serde_json::json!({
        "name": name.trim(),
        "value_prefix": value.split('.').next().unwrap_or_default(),
        "value_parts": value.split('.').count(),
        "attributes": attributes,
    })
}

/// Hex sha256, the digest the fixture records bodies by.
pub fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
