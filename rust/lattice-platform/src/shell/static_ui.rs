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

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{header, HeaderValue, Response, StatusCode};
use axum::middleware::{from_fn, Next};
use axum::routing::{get, MethodRouter};
use axum::Router;
use tower_http::services::ServeDir;

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
pub(crate) const SHELL_RELATIVE: [&str; 2] = ["app", "index.html"];

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

mod http;
mod invite;
mod pages;
mod sysinfo;

use pages::{account, app_shell, favicon, manifest, root, service_worker};

pub use http::{asset_content_type, json_detail, method_not_allowed};
pub(crate) use invite::first_query_value;
pub use invite::{cookie_value, invite_authorized, sign_invite_cookie, verify_invite_cookie};
pub use sysinfo::{
    gpu_from_worker_payload, host_capacity_readiness, parse_cpu_percent, parse_ram_percent,
    probe_host_capacity, sysinfo_router, GpuFuture, GpuReading, GpuSource, NoGpu, SysinfoState,
    WorkerGpuSource,
};

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
pub(crate) fn get_only<H, T, S>(handler: H) -> MethodRouter<S>
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

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderMap;
    use serde_json::json;

    use invite::token_urlsafe;

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
