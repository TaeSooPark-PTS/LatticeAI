//! The static / SPA-serving family, native (v11.6.0, WP-I4).
//!
//! Port of `latticeai/api/static_routes.py` plus the two `StaticFiles` mounts
//! assembled in `latticeai/runtime/web_runtime.py`. This is the first thing a
//! browser touches and the last thing anyone thinks about: nothing here is
//! interesting until it is missing, and then nothing else works.
//!
//! The contract is almost entirely **headers**, which is why it was captured
//! rather than reasoned about. `scripts/gen_static_fixtures.py` drives the real
//! Python router with a `TestClient` and writes
//! `rust/fixtures/http/static_ui.json`; the tests in
//! `tests/static_ui_parity.rs` replay every case against this module. What that
//! pinned:
//!
//! * `GET /app` returns the SPA shell with the no-store trio
//!   (`Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`,
//!   `Expires: 0`) **and** [`PRODUCTION_CSP`]. The shell is a plain file — vite
//!   output, no templating, no injected values — so serving it is a read, not a
//!   render. Its assets are hashed and are served by the mounts below, which is
//!   why only the shell is no-store.
//! * `/manifest.json` is `application/manifest+json`, `/sw.js` carries
//!   `Service-Worker-Allowed: /` (without it the worker's scope is `/` only by
//!   accident), and `/favicon.ico` falls back to `icons/favicon-32.png`.
//! * Every miss is FastAPI's `{"detail":"Not Found"}`, including inside the
//!   mounts, and every wrong method is `{"detail":"Method Not Allowed"}`. These
//!   are JSON, not the plain text a bare file server sends.
//! * `HEAD` is **not** a free synonym for `GET` here: only `/favicon.ico`
//!   declares it. The rest answer 405, because the Python routes are
//!   `@router.get`.
//!
//! ## What is deliberately *not* here
//!
//! * **No SPA path fallback.** The client router is hash-based
//!   (`/app#/knowledge-graph`), so a deep link never reaches the server as a
//!   path. Python has no `/app/{path}` route and neither does this; adding one
//!   would invent a surface the product does not have.
//! * **`GET /status`** stays with the models family — it reports the loaded
//!   model, which is worker state, not a file.
//! * **The GPU half of `/local/sysinfo`.** CPU and RAM are sampled here; the
//!   unified-memory numbers come from MLX, which lives in the Python worker, so
//!   they are asked for over the seam ([`GpuSource`]). A static-files module
//!   importing an ML runtime was the Python original's one wart; this port does
//!   not inherit it.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, HeaderValue, Response, StatusCode};
use axum::middleware::{from_fn, Next};
use axum::routing::{get, MethodRouter};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tower_http::services::ServeDir;

use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};

use crate::ui_redirects::app_redirect;

/// The Content-Security-Policy every HTML page this family serves carries.
///
/// Verbatim from `static_routes.PRODUCTION_CSP`, including the deliberate
/// absence of `ws:` — the live surfaces are server-sent events read with
/// `fetch`, which `connect-src` already covers.
pub const PRODUCTION_CSP: &str = "default-src 'self'; \
script-src 'self'; \
style-src 'self' 'unsafe-inline'; \
img-src 'self' data: blob: http://127.0.0.1:*; \
font-src 'self' data:; \
connect-src 'self' http://127.0.0.1:*; \
frame-src 'none'; \
object-src 'none'; \
base-uri 'none'; \
form-action 'self'; \
frame-ancestors 'none'";

/// Name of the invite-gate cookie.
pub const INVITE_COOKIE_NAME: &str = "lattice_invite";

/// How long an issued invite claim is good for — one week.
pub const INVITE_COOKIE_TTL_SECONDS: i64 = 60 * 60 * 24 * 7;

/// The wall shown to a visitor without an invitation. Byte-for-byte the Python
/// literal, trailing whitespace and all: it is asserted by digest.
pub const INVITE_DENIED_HTML: &str = r#"
            <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
                <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                    <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                    <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                    <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                    <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">LATTICE AI</div>
                </div>
            </body>
        "#;

/// Where the SPA shell lives under the static root.
const SHELL_RELATIVE: [&str; 2] = ["app", "index.html"];

/// Host load at or below this is `roomy`.
pub const SYSINFO_READINESS_ROOMY_MAX: f64 = 55.0;
/// Host load above `roomy` and at or below this is `tight`; above it, `low`.
pub const SYSINFO_READINESS_TIGHT_MAX: f64 = 80.0;

/// The worker seam that answers with this machine's GPU memory (WP-I6).
pub const WORKER_SYSINFO_PATH: &str = "/worker/sysinfo";

/// How long the host probe waits for `top` / `vm_stat`, as Python waits.
pub const PROBE_TIMEOUT: Duration = Duration::from_secs(4);

/// How long the GPU seam may take. Short on purpose: this route is polled by the
/// System screen *and* during first-run analysis, i.e. while an answer is
/// streaming, and a slow worker must cost a missing GPU number rather than a
/// stalled panel.
pub const GPU_SEAM_TIMEOUT: Duration = Duration::from_secs(4);

// ── configuration ──────────────────────────────────────────────────────────

/// What the static family needs to know about this install.
#[derive(Debug, Clone)]
pub struct StaticUiConfig {
    /// The `static/` root — vite output plus the hand-kept assets beside it.
    pub static_dir: PathBuf,
    /// Whether the invite gate is armed (`LATTICEAI_INVITE_GATE_ENABLED`).
    pub invite_gate_enabled: bool,
    /// The invitation code, if the operator set one.
    pub invite_code: String,
    /// The HMAC key the invite cookie is signed with.
    pub invite_cookie_secret: String,
    /// Whether issued cookies carry `Secure` — true on a public/non-loopback bind.
    pub secure_cookies: bool,
}

impl StaticUiConfig {
    /// A config for `static_dir` with the gate disarmed — the product default.
    pub fn new(static_dir: impl Into<PathBuf>) -> Self {
        Self {
            static_dir: static_dir.into(),
            invite_gate_enabled: false,
            invite_code: String::new(),
            invite_cookie_secret: String::new(),
            secure_cookies: false,
        }
    }

    /// [`Self::new`] plus the three `LATTICEAI_INVITE_*` variables.
    ///
    /// The *persisted* per-install secret (`security_runtime._resolve_invite_gate_secrets`
    /// writes one into the data dir when the operator supplies none) is not read
    /// here: that file belongs to the security runtime, and a static-file module
    /// inventing a credential is exactly the kind of second source of truth this
    /// release exists to remove. A host with the gate armed and no secret in the
    /// environment must pass one in.
    pub fn from_env(static_dir: impl Into<PathBuf>) -> Self {
        let flag = std::env::var("LATTICEAI_INVITE_GATE_ENABLED").unwrap_or_default();
        Self {
            static_dir: static_dir.into(),
            invite_gate_enabled: parse_bool(&flag),
            invite_code: std::env::var("LATTICEAI_INVITE_CODE").unwrap_or_default(),
            invite_cookie_secret: std::env::var("LATTICEAI_INVITE_COOKIE_SECRET")
                .unwrap_or_default(),
            secure_cookies: false,
        }
    }
}

/// Resolve the `static/` root the way Python's `Config.from_env` does.
///
/// ```text
/// first = LATTICEAI_STATIC_DIR  or  {base_dir}/static
/// if first exists → first
/// else if {install_prefix}/static exists → that   # Python: sys.prefix/static
/// else → first   # missing build output stays a 404, not a silent `.`
/// ```
///
/// Setting `LATTICEAI_STATIC_DIR` *replaces* the checkout path; a missing
/// override does not fall through to `{base_dir}/static`, only to the
/// packaged prefix. This module does not re-read the environment: the host
/// already did (`RuntimeConfig::from_env`). Pass what it found.
pub fn resolve_static_dir(
    explicit: Option<&Path>,
    base_dir: &Path,
    install_prefix: Option<&Path>,
) -> PathBuf {
    let first = explicit
        .map(Path::to_path_buf)
        .unwrap_or_else(|| base_dir.join("static"));
    if first.exists() {
        return first;
    }
    if let Some(prefix) = install_prefix {
        let packaged = prefix.join("static");
        if packaged.exists() {
            return packaged;
        }
    }
    first
}

/// `latticeai/core/config.py::_bool` — the truthy set, lowercased and trimmed.
///
/// Python falls back to the *default* for anything it does not recognise, and
/// the gate's default is off, so an unreadable value leaves the gate down here
/// too. That is the same answer, reached the same way — not a shortcut.
fn parse_bool(raw: &str) -> bool {
    matches!(
        raw.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Shared state behind the page handlers.
#[derive(Debug)]
pub struct StaticUiState {
    /// The configuration these handlers answer from.
    pub config: StaticUiConfig,
}

// ── the router ─────────────────────────────────────────────────────────────

/// Every static/UI route: the SPA shell, the two public assets, the two file
/// routes, and the mounts.
///
/// Mount order matters, and not in the usual way: these are *exact* paths, so
/// they cannot shadow each other, but `/static` and `/icons` are prefix mounts
/// and must be merged into a host **before** any catch-all proxy fallthrough —
/// otherwise a missing asset is forwarded to the Python worker, which answers
/// its own 404 and hides the fact that the build output is not there.
pub fn router(config: StaticUiConfig) -> Router {
    let static_dir = config.static_dir.clone();
    let state = Arc::new(StaticUiState { config });
    let pages = Router::new()
        .route("/", get_only(root))
        .route("/account", get_only(account))
        .route("/app", get_only(app_shell))
        .route("/manifest.json", get_only(manifest))
        .route("/sw.js", get_only(service_worker))
        .route(
            "/favicon.ico",
            get(favicon)
                .head(favicon)
                .fallback(|| async { method_not_allowed("GET, HEAD") }),
        )
        .with_state(state);
    pages.merge(assets_router(&static_dir))
}

/// A `@router.get`-shaped route: GET answers, HEAD and everything else are 405
/// with `Allow: GET`, which is what FastAPI does for a GET-only path.
fn get_only<H, T, S>(handler: H) -> MethodRouter<S>
where
    H: axum::handler::Handler<T, S>,
    T: 'static,
    S: Clone + Send + Sync + 'static,
{
    get(handler)
        .head(|| async { method_not_allowed("GET") })
        .fallback(|| async { method_not_allowed("GET") })
}

/// The `/static` and `/icons` mounts, with their responses bent to Starlette's.
///
/// `ServeDir` is close but not identical: it answers an empty-bodied 404/405 and
/// guesses content types from its own table. Both are corrected here so a
/// browser — and the fixture — cannot tell which runtime served the file.
fn assets_router(static_dir: &Path) -> Router {
    let files = ServeDir::new(static_dir).append_index_html_on_directories(false);
    let icons = ServeDir::new(static_dir.join("icons")).append_index_html_on_directories(false);
    Router::new()
        .nest_service("/static", files)
        .nest_service("/icons", icons)
        .layer(from_fn(starlette_shaped_assets))
}

async fn starlette_shaped_assets(request: axum::extract::Request, next: Next) -> Response<Body> {
    let path = request.uri().path().to_owned();
    let response = next.run(request).await;
    match response.status() {
        // Starlette's `StaticFiles` raises `HTTPException(404)`, which FastAPI's
        // handler renders as JSON — the same body as any other missing route.
        StatusCode::NOT_FOUND => json_detail(StatusCode::NOT_FOUND, "Not Found"),
        // Note the absence of `Allow`: the mount is not a route, so FastAPI has
        // no method list to advertise for it.
        StatusCode::METHOD_NOT_ALLOWED => {
            json_detail(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed")
        }
        StatusCode::OK | StatusCode::PARTIAL_CONTENT => {
            let (mut parts, body) = response.into_parts();
            if let Ok(value) = HeaderValue::from_str(asset_content_type(&path)) {
                parts.headers.insert(header::CONTENT_TYPE, value);
            }
            Response::from_parts(parts, body)
        }
        _ => response,
    }
}

// ── page handlers ──────────────────────────────────────────────────────────

/// `GET /` — the login/register entry, or the invitation wall.
async fn root(
    State(state): State<Arc<StaticUiState>>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
) -> Response<Body> {
    let config = &state.config;
    if !config.invite_gate_enabled {
        return app_redirect("account", query.as_deref());
    }
    // A valid claim is enough; note that Python drops the query on this branch
    // (it calls the helper without the request), and so does this.
    if invite_authorized(config, &headers) {
        return app_redirect("account", None);
    }
    let supplied = query
        .as_deref()
        .and_then(|raw| first_query_value(raw, "code"));
    if !config.invite_code.is_empty() {
        if let Some(code) = supplied {
            if constant_time_eq(code.as_bytes(), config.invite_code.as_bytes()) {
                // Redirect rather than render, so the code leaves the URL bar
                // and the browser history with it.
                let mut response = app_redirect("account", None);
                if let Some(cookie) = issue_invite_cookie(config) {
                    response.headers_mut().append(header::SET_COOKIE, cookie);
                }
                return response;
            }
        }
    }
    invite_denied()
}

/// `GET /account` — where logout and manual navigation land.
async fn account(State(state): State<Arc<StaticUiState>>, headers: HeaderMap) -> Response<Body> {
    if !invite_authorized(&state.config, &headers) {
        return invite_denied();
    }
    app_redirect("account", None)
}

/// `GET /app` — the React shell.
async fn app_shell(State(state): State<Arc<StaticUiState>>, headers: HeaderMap) -> Response<Body> {
    if !invite_authorized(&state.config, &headers) {
        return invite_denied();
    }
    let page = state
        .config
        .static_dir
        .join(SHELL_RELATIVE[0])
        .join(SHELL_RELATIVE[1]);
    match tokio::fs::read(&page).await {
        Ok(bytes) => {
            let mut response = file_response(bytes, "text/html; charset=utf-8");
            let headers = response.headers_mut();
            headers.insert(
                header::CACHE_CONTROL,
                HeaderValue::from_static("no-cache, no-store, must-revalidate"),
            );
            headers.insert(header::PRAGMA, HeaderValue::from_static("no-cache"));
            headers.insert(header::EXPIRES, HeaderValue::from_static("0"));
            headers.insert(
                header::CONTENT_SECURITY_POLICY,
                HeaderValue::from_static(PRODUCTION_CSP),
            );
            response
        }
        Err(_) => json_detail(StatusCode::NOT_FOUND, "React shell not found."),
    }
}

/// `GET /manifest.json` — the PWA manifest, with the media type browsers want.
async fn manifest(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    match tokio::fs::read(state.config.static_dir.join("manifest.json")).await {
        Ok(bytes) => file_response(bytes, "application/manifest+json"),
        Err(_) => json_detail(StatusCode::NOT_FOUND, "Not Found"),
    }
}

/// `GET|HEAD /favicon.ico` — the `.ico`, else the 32px png, else nothing.
async fn favicon(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    let root = &state.config.static_dir;
    if let Ok(bytes) = tokio::fs::read(root.join("favicon.ico")).await {
        return file_response(bytes, "image/x-icon");
    }
    if let Ok(bytes) = tokio::fs::read(root.join("icons").join("favicon-32.png")).await {
        return file_response(bytes, "image/png");
    }
    json_detail(StatusCode::NOT_FOUND, "Not Found")
}

/// `GET /sw.js` — the service worker, allowed to claim the whole origin.
async fn service_worker(State(state): State<Arc<StaticUiState>>) -> Response<Body> {
    match tokio::fs::read(state.config.static_dir.join("sw.js")).await {
        Ok(bytes) => {
            let mut response = file_response(bytes, "application/javascript");
            response.headers_mut().insert(
                header::HeaderName::from_static("service-worker-allowed"),
                HeaderValue::from_static("/"),
            );
            response
        }
        Err(_) => json_detail(StatusCode::NOT_FOUND, "Not Found"),
    }
}

// ── response helpers ───────────────────────────────────────────────────────

/// FastAPI's error body: `{"detail":"…"}`, compact, `application/json`.
pub fn json_detail(status: StatusCode, detail: &str) -> Response<Body> {
    let body = serde_json::to_vec(&json!({ "detail": detail }))
        .unwrap_or_else(|_| b"{\"detail\":\"Internal Server Error\"}".to_vec());
    let mut response = Response::new(Body::from(body));
    *response.status_mut() = status;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    response
}

/// FastAPI's 405, with the `Allow` header the route declares.
///
/// The body is written even when the refused method is `HEAD`: hyper drops it on
/// the wire and keeps the `Content-Length` that describes it, which is exactly
/// what Starlette does (a `HEAD /manifest.json` is `Content-Length: 31` with no
/// bytes). Emptying it here instead would report a length of zero and quietly
/// disagree with the recording.
pub fn method_not_allowed(allow: &'static str) -> Response<Body> {
    let mut response = json_detail(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed");
    response
        .headers_mut()
        .insert(header::ALLOW, HeaderValue::from_static(allow));
    response
}

/// A 200 carrying file bytes and an explicit content type.
fn file_response(bytes: Vec<u8>, content_type: &'static str) -> Response<Body> {
    let mut response = Response::new(Body::from(bytes));
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}

/// The invitation wall: 403, HTML, no cache directives (Python sets none).
fn invite_denied() -> Response<Body> {
    let mut response = Response::new(Body::from(INVITE_DENIED_HTML));
    *response.status_mut() = StatusCode::FORBIDDEN;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/html; charset=utf-8"),
    );
    response
}

/// Starlette's `mimetypes` answer for a served file, charset rule included.
///
/// Two entries are **machine-dependent** in Python and pinned here to the
/// answer a developer box and the release builder give (CPython also reads
/// `/etc/apache2/mime.types`): `.ico` is `image/x-icon` — matching the explicit
/// media type the `/favicon.ico` route hard-codes, so the same bytes do not
/// change type depending on which door they came through — and `.xml` is
/// `application/xml`. The fixture records both answers.
pub fn asset_content_type(path: &str) -> &'static str {
    let extension = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    match extension.as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "txt" => "text/plain; charset=utf-8",
        "md" => "text/markdown; charset=utf-8",
        "json" => "application/json",
        "xml" => "application/xml",
        "wasm" => "application/wasm",
        "pdf" => "application/pdf",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        // Starlette's fallback when `mimetypes` has no answer — `.map` files
        // (vite source maps) take this branch.
        _ => "application/octet-stream",
    }
}

// ── the invite gate ────────────────────────────────────────────────────────

/// Whether this request carries a claim the gate accepts.
///
/// With the gate disarmed every request is authorised, exactly as Python's
/// `invite_authorized` short-circuits.
pub fn invite_authorized(config: &StaticUiConfig, headers: &HeaderMap) -> bool {
    if !config.invite_gate_enabled {
        return true;
    }
    let cookie = cookie_value(headers, INVITE_COOKIE_NAME);
    verify_invite_cookie(
        cookie.as_deref(),
        &config.invite_cookie_secret,
        now_seconds(),
    )
}

/// `Set-Cookie` for a freshly signed claim, in Starlette's attribute order.
fn issue_invite_cookie(config: &StaticUiConfig) -> Option<HeaderValue> {
    let nonce = token_urlsafe(24)?;
    let value = sign_invite_cookie(
        &config.invite_cookie_secret,
        now_seconds() + INVITE_COOKIE_TTL_SECONDS,
        &nonce,
    );
    let mut cookie = format!(
        "{INVITE_COOKIE_NAME}={value}; HttpOnly; Max-Age={INVITE_COOKIE_TTL_SECONDS}; Path=/; SameSite=lax"
    );
    if config.secure_cookies {
        cookie.push_str("; Secure");
    }
    HeaderValue::from_str(&cookie).ok()
}

/// `_sign_invite_cookie` — `v1.<expiry>.<nonce>.<hmac-sha256 hex>`.
///
/// The signed payload is `<expiry>.<nonce>`, so neither half can be moved
/// between cookies, and the nonce means two claims issued in the same second
/// are still different strings.
pub fn sign_invite_cookie(secret: &str, expires_at: i64, nonce: &str) -> String {
    let payload = format!("{expires_at}.{nonce}");
    let signature = hmac_sha256_hex(secret.as_bytes(), payload.as_bytes());
    format!("v1.{payload}.{signature}")
}

/// `_verify_invite_cookie` — version, expiry and signature, trusting no claim.
///
/// Deliberately branch-for-branch with Python, including that expiry is
/// *exclusive* (`expires_at <= now` is dead) and that a malformed value is a
/// refusal rather than an error.
pub fn verify_invite_cookie(value: Option<&str>, secret: &str, now: i64) -> bool {
    let value = match value {
        Some(value) if !value.is_empty() => value,
        _ => return false,
    };
    if secret.is_empty() {
        return false;
    }
    // `value.split(".", 3)` in Python: four fields, the last of which may itself
    // contain dots. A nonce is base64url and cannot, but the parser is the
    // contract, not the alphabet.
    let mut parts = value.splitn(4, '.');
    let (version, raw_expiry, nonce, supplied) =
        match (parts.next(), parts.next(), parts.next(), parts.next()) {
            (Some(version), Some(expiry), Some(nonce), Some(signature)) => {
                (version, expiry, nonce, signature)
            }
            _ => return false,
        };
    let expires_at: i64 = match raw_expiry.parse() {
        Ok(parsed) => parsed,
        Err(_) => return false,
    };
    if version != "v1" || nonce.is_empty() || expires_at <= now {
        return false;
    }
    let expected = hmac_sha256_hex(
        secret.as_bytes(),
        format!("{expires_at}.{nonce}").as_bytes(),
    );
    constant_time_eq(supplied.as_bytes(), expected.as_bytes())
}

/// HMAC-SHA256, hex — `hmac.new(secret, payload, hashlib.sha256).hexdigest()`.
fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    const BLOCK: usize = 64;
    let mut padded = [0u8; BLOCK];
    if key.len() > BLOCK {
        padded[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        padded[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = [0x36u8; BLOCK];
    let mut outer_key = [0x5cu8; BLOCK];
    for index in 0..BLOCK {
        inner_key[index] ^= padded[index];
        outer_key[index] ^= padded[index];
    }
    let inner = Sha256::new()
        .chain_update(inner_key)
        .chain_update(message)
        .finalize();
    let digest = Sha256::new()
        .chain_update(outer_key)
        .chain_update(inner)
        .finalize();
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `secrets.compare_digest` — length-revealing, content-blind.
fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right.iter())
        .fold(0u8, |accumulator, (a, b)| accumulator | (a ^ b))
        == 0
}

/// `secrets.token_urlsafe(bytes)` — random bytes, base64url, unpadded.
fn token_urlsafe(bytes: usize) -> Option<String> {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).ok()?;
    Some(URL_SAFE_NO_PAD.encode(&buffer))
}

/// Starlette's lenient cookie split: last value wins, quotes stripped, a pair
/// without `=` counts as a nameless cookie rather than ending the parse.
///
/// A stricter parser would fail *open* here — the invite gate would stop seeing
/// a claim that a browser is quite happy to send alongside a malformed one.
pub fn cookie_value(headers: &HeaderMap, name: &str) -> Option<String> {
    let mut found = None;
    for header in headers.get_all(header::COOKIE).iter() {
        let raw = match header.to_str() {
            Ok(raw) => raw,
            Err(_) => continue,
        };
        for chunk in raw.split(';') {
            let (key, value) = match chunk.split_once('=') {
                Some((key, value)) => (key.trim(), value.trim()),
                None => ("", chunk.trim()),
            };
            if key == name {
                found = Some(unquote(value));
            }
        }
    }
    found
}

fn unquote(value: &str) -> String {
    if value.len() >= 2 && value.starts_with('"') && value.ends_with('"') {
        value[1..value.len() - 1].to_string()
    } else {
        value.to_string()
    }
}

/// The first value for `key` in a raw query string, percent-decoded.
///
/// Starlette's `QueryParams.get` returns the first occurrence; FastAPI hands
/// that to the handler decoded.
fn first_query_value(query: &str, key: &str) -> Option<String> {
    query.split('&').find_map(|pair| {
        let (name, value) = pair.split_once('=')?;
        (percent_decode(name) == key).then(|| percent_decode(value))
    })
}

/// Form-decoding as a query string carries it: `+` is a space, `%XX` is a byte.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                match u8::from_str_radix(&raw[index + 1..index + 3], 16) {
                    Ok(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    Err(_) => {
                        out.push(b'%');
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs() as i64)
        .unwrap_or_default()
}

// ── /local/sysinfo ─────────────────────────────────────────────────────────

/// The GPU half of the host probe, as the worker reports it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GpuReading {
    /// Unified memory held by the ML runtime, in GiB.
    pub gpu_mem_gb: f64,
    /// The same as a percentage of the machine's memory.
    pub gpu_mem_pct: f64,
}

/// A future returning what the machine's GPU is holding, or nothing.
pub type GpuFuture<'a> = Pin<Box<dyn std::future::Future<Output = Option<GpuReading>> + Send + 'a>>;

/// Where the GPU numbers come from.
///
/// The seam exists because MLX is Python-only: `api/static_routes.py` imports
/// `mlx.core` purely for these two numbers, which is why a static-files module
/// carried an ML dependency. Here the numbers are asked for
/// ([`WORKER_SYSINFO_PATH`], added by WP-I6) and their absence is the same
/// non-event it is in Python, where the import failure is swallowed and the
/// fields stay zero.
pub trait GpuSource: Send + Sync + 'static {
    /// Read the current GPU memory, or `None` if this machine cannot say.
    fn read(&self) -> GpuFuture<'_>;
}

/// A machine with no ML runtime to ask: the fields stay zero.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoGpu;

impl GpuSource for NoGpu {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async { None })
    }
}

/// The worker seam: `GET {origin}/worker/sysinfo`.
#[derive(Debug, Clone)]
pub struct WorkerGpuSource {
    client: WorkerSeamClient,
}

impl WorkerGpuSource {
    /// A source pointed at the worker this host supervises.
    ///
    /// Builds its own HTTP client; prefer [`Self::with_client`] from a host that
    /// already has a seam client, so the loopback connection pool is shared and
    /// any credential the seam needs is configured in exactly one place.
    pub fn new(origin: impl AsRef<str>) -> Result<Self, WorkerSeamError> {
        Ok(Self {
            client: WorkerSeamClient::new(origin)?.with_timeout(GPU_SEAM_TIMEOUT),
        })
    }

    /// A source over a seam client the caller already configured.
    pub fn with_client(client: WorkerSeamClient) -> Self {
        Self { client }
    }
}

impl GpuSource for WorkerGpuSource {
    fn read(&self) -> GpuFuture<'_> {
        Box::pin(async move {
            // A worker that is down, refusing, or answering something else is
            // "cannot say" — the same non-event as MLX failing to import in
            // Python, which `quiet()` swallows. This route reports host load and
            // must not become a second place the product looks unhealthy.
            let payload = self.client.get_json(WORKER_SYSINFO_PATH).await.ok()?;
            gpu_from_worker_payload(&payload)
        })
    }
}

/// Read the GPU numbers out of WP-I6's `GET /worker/sysinfo` body.
///
/// Shipped schema (`latticeai/api/worker_seams.py::probe_gpu_memory`):
///
/// ```json
/// {"mlx_available": bool, "gpu_mem_gb": num, "gpu_mem_pct": num,
///  "total_bytes": int, "detail": str | null}
/// ```
///
/// Only the two numbers this route reports are taken. `mlx_available`,
/// `total_bytes` and `detail` stay worker-side: `/local/sysinfo` has never
/// exposed them, and inventing keys here would be a client-visible change.
/// A payload missing either number is "cannot say", not zero, so a seam that
/// regresses shows up as an absent GPU rather than an idle one. `false` plus
/// zeros (no MLX on this machine) still parses — those zeros are the reading.
pub fn gpu_from_worker_payload(payload: &Value) -> Option<GpuReading> {
    Some(GpuReading {
        gpu_mem_gb: payload.get("gpu_mem_gb")?.as_f64()?,
        gpu_mem_pct: payload.get("gpu_mem_pct")?.as_f64()?,
    })
}

/// What `/local/sysinfo` needs: somewhere to ask about the GPU.
pub struct SysinfoState {
    /// The GPU seam.
    pub gpu: Arc<dyn GpuSource>,
}

impl std::fmt::Debug for SysinfoState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("SysinfoState { gpu: <seam> }")
    }
}

/// `GET /local/sysinfo`, on its own so it can be mounted behind the user gate.
///
/// The Python route calls `require_user`; this router does not, because
/// authentication in this release is a layer (`lattice-auth`, WP-I2) rather than
/// a line at the top of every handler. Mounting it bare is a difference from
/// Python, and a host that does so is publishing its CPU and RAM load to
/// anything that can reach the port.
pub fn sysinfo_router(state: Arc<SysinfoState>) -> Router {
    Router::new()
        .route("/local/sysinfo", get_only(local_sysinfo))
        .with_state(state)
}

async fn local_sysinfo(State(state): State<Arc<SysinfoState>>) -> Response<Body> {
    let payload = probe_host_capacity(state.gpu.as_ref()).await;
    let body = serde_json::to_vec(&payload).unwrap_or_else(|_| b"{}".to_vec());
    let mut response = Response::new(Body::from(body));
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    response
}

/// `_probe_host_capacity` — CPU and RAM from this machine, GPU from the seam.
///
/// The Python original wraps all three samples in one `try`, so a failure while
/// reading CPU means RAM and GPU are never read and the payload carries an
/// `error` string with zeros beside it. That sequencing is reproduced rather
/// than improved: a caller reading `cpu_pct: 0.0` next to `error` knows it was
/// not sampled, whereas a partially-filled payload would look like an idle box.
pub async fn probe_host_capacity(gpu: &dyn GpuSource) -> Value {
    let mut cpu_pct = 0.0;
    let mut ram_pct = 0.0;
    let mut gpu_mem_gb = 0.0;
    let mut gpu_mem_pct = 0.0;
    let mut error: Option<String> = None;

    match capture_stdout("top", &["-l", "1", "-n", "0"]).await {
        Ok(output) => cpu_pct = parse_cpu_percent(&output).unwrap_or(0.0),
        Err(message) => error = Some(message),
    }
    if error.is_none() {
        match capture_stdout("vm_stat", &[]).await {
            Ok(output) => ram_pct = parse_ram_percent(&output),
            Err(message) => error = Some(message),
        }
    }
    if error.is_none() {
        if let Some(reading) = gpu.read().await {
            gpu_mem_gb = reading.gpu_mem_gb;
            gpu_mem_pct = reading.gpu_mem_pct;
        }
    }

    let mut payload = Map::new();
    payload.insert("cpu_pct".into(), json!(cpu_pct));
    payload.insert("ram_pct".into(), json!(ram_pct));
    payload.insert("gpu_mem_pct".into(), json!(gpu_mem_pct));
    payload.insert("gpu_mem_gb".into(), json!(gpu_mem_gb));
    if let Some(message) = error {
        payload.insert("error".into(), json!(message));
    }
    payload.insert(
        "readiness".into(),
        json!(host_capacity_readiness(cpu_pct, ram_pct, gpu_mem_pct)),
    );
    Value::Object(payload)
}

async fn capture_stdout(program: &str, args: &[&str]) -> Result<String, String> {
    let mut command = tokio::process::Command::new(program);
    command.args(args);
    let run = command.output();
    match tokio::time::timeout(PROBE_TIMEOUT, run).await {
        Ok(Ok(output)) => Ok(String::from_utf8_lossy(&output.stdout).into_owned()),
        Ok(Err(err)) => Err(err.to_string()),
        Err(_) => Err(format!(
            "Command '{program}' timed out after {} seconds",
            PROBE_TIMEOUT.as_secs()
        )),
    }
}

/// `top -l 1 -n 0`'s user+sys percentage, rounded as CPython rounds.
///
/// `None` when no line carries the pair — the Python original simply leaves the
/// field at its default there, which is not the same as reading zero load, but
/// is what the product has always reported.
pub fn parse_cpu_percent(top_output: &str) -> Option<f64> {
    let mut latest = None;
    for line in top_output.lines() {
        if !line.contains("CPU usage") {
            continue;
        }
        // The Python regex is `([\d.]+)% user.*?([\d.]+)% sys`: the first number
        // before "% user", then the first before "% sys" *after* it.
        let Some(user_at) = line.find("% user") else {
            continue;
        };
        let Some(user) = number_before(line, user_at) else {
            continue;
        };
        let tail_from = user_at + "% user".len();
        let Some(sys_at) = line[tail_from..].find("% sys").map(|at| at + tail_from) else {
            continue;
        };
        let Some(sys) = number_before(line, sys_at) else {
            continue;
        };
        latest = Some(lattice_core::pytext::round_to(user + sys, 1));
    }
    latest
}

/// The `[\d.]+` run ending at `end`, parsed as a float.
fn number_before(line: &str, end: usize) -> Option<f64> {
    let bytes = line.as_bytes();
    let mut start = end;
    while start > 0 && (bytes[start - 1].is_ascii_digit() || bytes[start - 1] == b'.') {
        start -= 1;
    }
    if start == end {
        return None;
    }
    line[start..end].parse().ok()
}

/// `vm_stat`'s used-pages percentage, rounded as CPython rounds.
///
/// The five counters Python sums are the whole of its idea of "memory in use";
/// anything else `vm_stat` prints (page-ins, faults) is not memory and is
/// ignored. A duplicate line overwrites, exactly as the Python dict does.
pub fn parse_ram_percent(vm_stat_output: &str) -> f64 {
    const KEYS: [&str; 5] = [
        "Pages free",
        "Pages active",
        "Pages inactive",
        "Pages wired down",
        "Pages occupied by compressor",
    ];
    let mut pages: BTreeMap<&str, u64> = BTreeMap::new();
    for line in vm_stat_output.lines() {
        for key in KEYS {
            if line.starts_with(key) {
                if let Some(count) = first_integer(line) {
                    pages.insert(key, count);
                }
            }
        }
    }
    let total: u64 = pages.values().sum();
    if total == 0 {
        return 0.0;
    }
    let used = total - pages.get("Pages free").copied().unwrap_or(0);
    lattice_core::pytext::round_to(used as f64 / total as f64 * 100.0, 1)
}

fn first_integer(line: &str) -> Option<u64> {
    let bytes = line.as_bytes();
    let start = bytes.iter().position(u8::is_ascii_digit)?;
    let end = bytes[start..]
        .iter()
        .position(|byte| !byte.is_ascii_digit())
        .map(|offset| start + offset)
        .unwrap_or(bytes.len());
    line[start..end].parse().ok()
}

/// `host_capacity_readiness` — one plain-language bucket for three numbers.
///
/// The heaviest of the three decides, so a machine that is fine on CPU and out
/// of memory is not described as roomy. The thresholds live here, and only here,
/// so basic-mode copy and advanced-mode numbers cannot disagree.
pub fn host_capacity_readiness(cpu_pct: f64, ram_pct: f64, gpu_mem_pct: f64) -> &'static str {
    let load = cpu_pct.max(ram_pct).max(gpu_mem_pct);
    if load <= SYSINFO_READINESS_ROOMY_MAX {
        "roomy"
    } else if load <= SYSINFO_READINESS_TIGHT_MAX {
        "tight"
    } else {
        "low"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gated(secret: &str) -> StaticUiConfig {
        StaticUiConfig {
            static_dir: PathBuf::from("/nonexistent"),
            invite_gate_enabled: true,
            invite_code: "CODE".into(),
            invite_cookie_secret: secret.into(),
            secure_cookies: false,
        }
    }

    #[test]
    fn a_signed_cookie_round_trips() {
        let value = sign_invite_cookie("secret", 4_000_604_800, "nonce");
        assert!(verify_invite_cookie(Some(&value), "secret", 4_000_000_000));
        assert!(!verify_invite_cookie(Some(&value), "other", 4_000_000_000));
        assert!(!verify_invite_cookie(Some(&value), "secret", 4_000_604_800));
    }

    #[test]
    fn the_gate_opens_for_everyone_when_it_is_not_armed() {
        let mut config = gated("secret");
        config.invite_gate_enabled = false;
        assert!(invite_authorized(&config, &HeaderMap::new()));
    }

    #[test]
    fn a_malformed_neighbour_cookie_does_not_hide_the_claim() {
        let value = sign_invite_cookie("secret", 4_000_604_800, "nonce");
        let mut headers = HeaderMap::new();
        headers.insert(
            header::COOKIE,
            HeaderValue::from_str(&format!("broken; {INVITE_COOKIE_NAME}={value}"))
                .expect("header"),
        );
        assert!(invite_authorized(&gated("secret"), &headers));
    }

    #[test]
    fn the_last_value_for_a_name_wins_as_starlette_reads_it() {
        let value = sign_invite_cookie("secret", 4_000_604_800, "nonce");
        let mut headers = HeaderMap::new();
        headers.insert(
            header::COOKIE,
            HeaderValue::from_str(&format!(
                "{INVITE_COOKIE_NAME}=stale; {INVITE_COOKIE_NAME}={value}"
            ))
            .expect("header"),
        );
        assert!(invite_authorized(&gated("secret"), &headers));
    }

    #[test]
    fn an_empty_secret_verifies_nothing() {
        let value = sign_invite_cookie("", 4_000_604_800, "nonce");
        assert!(!verify_invite_cookie(Some(&value), "", 4_000_000_000));
    }

    #[test]
    fn query_values_are_decoded_like_a_form() {
        assert_eq!(
            first_query_value("code=a%20b&x=1", "code").as_deref(),
            Some("a b")
        );
        assert_eq!(
            first_query_value("code=a+b", "code").as_deref(),
            Some("a b")
        );
        assert_eq!(
            first_query_value("x=1&code=first&code=second", "code").as_deref(),
            Some("first")
        );
        assert_eq!(first_query_value("nope", "code"), None);
    }

    #[test]
    fn the_worker_payload_is_the_i6_schema() {
        let shipped = json!({
            "mlx_available": true,
            "gpu_mem_gb": 2.5,
            "gpu_mem_pct": 15.6,
            "total_bytes": 17_179_869_184u64,
            "detail": null,
        });
        let expected = GpuReading {
            gpu_mem_gb: 2.5,
            gpu_mem_pct: 15.6,
        };
        assert_eq!(gpu_from_worker_payload(&shipped), Some(expected));
        // No MLX: I6 still answers 200 with zeros and a reason. Those zeros
        // are the reading, not a missing seam.
        let absent = json!({
            "mlx_available": false,
            "gpu_mem_gb": 0.0,
            "gpu_mem_pct": 0.0,
            "total_bytes": 0,
            "detail": "No module named 'mlx'",
        });
        assert_eq!(
            gpu_from_worker_payload(&absent),
            Some(GpuReading {
                gpu_mem_gb: 0.0,
                gpu_mem_pct: 0.0
            })
        );
        assert_eq!(gpu_from_worker_payload(&json!({"cpu_pct": 1.0})), None);
        // Nested guesses from before I6 shipped are not a contract.
        assert_eq!(
            gpu_from_worker_payload(&json!({"gpu": {"mem_gb": 2.5, "mem_pct": 15.6}})),
            None
        );
    }

    #[test]
    fn static_dir_resolution_prefers_the_first_existing_directory() {
        let root =
            std::env::temp_dir().join(format!("lattice-static-ui-resolve-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("checkout/static")).expect("checkout");
        std::fs::create_dir_all(root.join("prefix/static")).expect("prefix");
        std::fs::create_dir_all(root.join("override")).expect("override");

        assert_eq!(
            resolve_static_dir(
                Some(&root.join("override")),
                &root.join("checkout"),
                Some(&root.join("prefix")),
            ),
            root.join("override")
        );
        assert_eq!(
            resolve_static_dir(None, &root.join("checkout"), Some(&root.join("prefix"))),
            root.join("checkout/static")
        );
        assert_eq!(
            resolve_static_dir(None, &root.join("missing"), Some(&root.join("prefix"))),
            root.join("prefix/static")
        );
        // An override replaces the checkout path; a miss falls to the prefix
        // only — Python never walks back to `{base_dir}/static`.
        assert_eq!(
            resolve_static_dir(
                Some(&root.join("gone")),
                &root.join("checkout"),
                Some(&root.join("prefix")),
            ),
            root.join("prefix/static")
        );
        // Nothing on disk: keep the operator's path so the 404 is honest.
        assert_eq!(
            resolve_static_dir(
                Some(&root.join("gone")),
                &root.join("missing"),
                Some(&root.join("also-missing")),
            ),
            root.join("gone")
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_token_is_thirty_two_urlsafe_characters() {
        let token = token_urlsafe(24).expect("randomness");
        assert_eq!(token.len(), 32);
        assert!(token
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
    }

    #[test]
    fn the_content_type_table_covers_what_the_product_ships() {
        assert_eq!(
            asset_content_type("/static/app/assets/x.js"),
            "text/javascript; charset=utf-8"
        );
        assert_eq!(
            asset_content_type("/static/app/INDEX.HTML"),
            "text/html; charset=utf-8"
        );
        assert_eq!(
            asset_content_type("/static/vendor/icons/t.woff2"),
            "font/woff2"
        );
        assert_eq!(
            asset_content_type("/static/app/assets/x.js.map"),
            "application/octet-stream"
        );
        assert_eq!(
            asset_content_type("/static/README"),
            "application/octet-stream"
        );
    }
}
