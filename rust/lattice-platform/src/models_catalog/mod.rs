//! Models catalog / recommendations / leftover health-adjacent surfaces.
//!
//! Port of the **non-MLX** half of `latticeai/api/models.py` plus the stacked
//! `/mode` + `/runtime_features` handler and `GET /engines` from
//! `latticeai/api/health.py`.
//!
//! Not claimed (KEEP_WORKER / in-process MLX runtime):
//! `GET /models`, `POST /models/load`, `DELETE /models/unload/{model_id}`,
//! `POST /engines/prepare-model`, `POST /engines/prepare-model/stream`.
//!
//! Three more used to be listed here and are now gone from the product
//! entirely (v11.8.0): `POST /models/switch/{model_id}`,
//! `DELETE /models/unload-all` and `POST /engines/pull-model` had no caller in
//! any surface — this crate, the SPA client, either extension, or the Tauri
//! shell — so they were deleted from the worker rather than ported here. What
//! the product actually sends is `/models/load` (which switches) and
//! `/engines/prepare-model` (which downloads on consent, then loads).

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::body::{optional, parse_model, required};
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::OrderedMap;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity};
use lattice_core::messages;
use lattice_core::worker::WorkerSeamClient;
use serde::Deserialize;
use serde_json::{json, Value};

mod probe;
pub use probe::{
    command_exists, fetch_worker_catalog, parse_meminfo_total, probe_host, read_ram_bytes,
    recommend_from_catalog, HostProbe, WorkerCatalog,
};

/// Routes this family mounts. KEEP_WORKER paths are deliberately absent.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/engines"),
    ("POST", "/engines/install"),
    ("POST", "/engines/verify-cloud"),
    ("GET", "/mode"),
    ("GET", "/models/compat-profiles"),
    ("GET", "/models/recommendations"),
    ("GET", "/runtime_features"),
    ("POST", "/setup/set-api-key"),
];

/// Cloud providers `OPENAI_COMPATIBLE_PROVIDERS` recognises (declaration order).
const OPENAI_COMPATIBLE_PROVIDERS: &[&str] = &[
    "openai",
    "openrouter",
    "groq",
    "together",
    "xai",
    "ollama",
    "vllm",
    "lmstudio",
    "llamacpp",
];

/// How long a cloud-verify answer is considered fresh (Python constant).
pub const CLOUD_VERIFY_TTL_SECONDS: u64 = 300;

/// What the family needs to serve catalog / leftover status routes.
#[derive(Clone)]
pub struct ModelsCatalogState {
    auth: Arc<AuthState>,
    data_dir: PathBuf,
    /// `LATTICEAI_REQUIRE_AUTH` as the health router sees it.
    require_auth: bool,
    /// Process mode (`local` / `public`) for `/mode`.
    app_mode: String,
    host: String,
    port: u16,
    allow_local_models: bool,
    allow_model_downloads: bool,
    enable_graph: bool,
    enable_telegram: bool,
    invite_gate_enabled: bool,
    /// Optional override for `/models/compat-profiles` (tests pin `[]`).
    compat_profiles: Arc<Vec<Value>>,
    /// Worker seam — `GET /models` + `GET /worker/sysinfo`. Absent is honest
    /// degradation, not a fabricated catalog.
    worker: Option<WorkerSeamClient>,
}

impl ModelsCatalogState {
    /// Build from an auth handle and the install's data dir.
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        let require_auth = auth.effective_require_auth();
        Self {
            auth,
            data_dir: data_dir.into(),
            require_auth,
            app_mode: "local".into(),
            host: "127.0.0.1".into(),
            port: 4825,
            allow_local_models: true,
            allow_model_downloads: false,
            enable_graph: true,
            enable_telegram: false,
            invite_gate_enabled: false,
            compat_profiles: Arc::new(Vec::new()),
            worker: None,
        }
    }

    /// Point catalog / recommendation reads at this worker.
    pub fn with_worker(mut self, worker: WorkerSeamClient) -> Self {
        self.worker = Some(worker);
        self
    }

    /// Override the runtime-features snapshot (tests / integrator).
    pub fn with_runtime(
        mut self,
        app_mode: impl Into<String>,
        host: impl Into<String>,
        port: u16,
    ) -> Self {
        self.app_mode = app_mode.into();
        self.host = host.into();
        self.port = port;
        self
    }

    /// Pin the compat-profile list. The fixture environment serves `[]`.
    pub fn with_compat_profiles(mut self, profiles: Vec<Value>) -> Self {
        self.compat_profiles = Arc::new(profiles);
        self
    }
}

/// Router factory.
pub fn router(state: ModelsCatalogState) -> Router {
    Router::new()
        .route("/models/compat-profiles", get(compat_profiles))
        .route("/models/recommendations", get(recommendations))
        .route("/setup/set-api-key", post(set_api_key))
        .route("/mode", get(runtime_features))
        .route("/runtime_features", get(runtime_features))
        .route("/engines", get(list_engines))
        .route("/engines/install", post(engines_install))
        .route("/engines/verify-cloud", post(engines_verify_cloud))
        .with_state(state)
}

fn ok(body: &OrderedMap) -> Response {
    let rendered = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &rendered, None)
}

fn localized(status: StatusCode, id: &str, headers: &HeaderMap) -> Response {
    let lang = messages::resolve_language(
        headers
            .get(messages::LANGUAGE_HEADER)
            .and_then(|value| value.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok()),
    );
    let text = messages::text(id, lang, &[]);
    detail_error(status, &text)
}

fn require_user(state: &ModelsCatalogState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn require_admin(state: &ModelsCatalogState, headers: &HeaderMap) -> Result<Identity, Response> {
    // `_authorize_model_admin`: require_user first, then require_admin when
    // REQUIRE_AUTH is on. require_admin already 401s an anonymous caller on a
    // require-auth install (it never falls through to the local owner).
    let _ = require_user(state, headers)?;
    if state.require_auth {
        state.auth.require_admin(headers)
    } else {
        Ok(state.auth.require_user(headers)?)
    }
}

fn require_sensitive(state: &ModelsCatalogState, headers: &HeaderMap) -> Result<(), Response> {
    // health.py `_require_sensitive_status_access`.
    if state.require_auth && state.auth.get_current_user(headers).is_none() {
        return Err(detail_error(
            StatusCode::UNAUTHORIZED,
            lattice_auth::messages::LOGIN_REQUIRED_LITERAL,
        ));
    }
    Ok(())
}

// ── GET /models/compat-profiles ──────────────────────────────────────────────

async fn compat_profiles(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let mut body = OrderedMap::new();
    body.insert("profiles", json!(state.compat_profiles.as_slice()));
    Ok(ok(&body))
}

// ── GET /models/recommendations ──────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct RecQuery {
    engine: Option<String>,
}

async fn recommendations(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
    Query(query): Query<RecQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let engine = query.engine.unwrap_or_else(|| "local_mlx".into());
    let probe = probe_host(Some(&state.data_dir));
    let catalog = fetch_worker_catalog(state.worker.as_ref()).await;
    let (recs, registry) = recommend_from_catalog(&probe, &engine, &catalog);
    let mut body = OrderedMap::new();
    body.insert(
        "profile",
        serde_json::to_value(probe.profile_map()).unwrap_or(json!({})),
    );
    body.insert(
        "recommendations",
        serde_json::to_value(&recs).unwrap_or(json!({})),
    );
    body.insert(
        "registry",
        serde_json::to_value(&registry).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

async fn set_api_key(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let parsed = parse_model(
        &body,
        &[
            required("provider"),
            required("key"),
            optional("user_email"),
        ],
    )?;
    let provider = parsed.str("provider");
    if !OPENAI_COMPATIBLE_PROVIDERS.contains(&provider) {
        return Err(localized(
            StatusCode::BAD_REQUEST,
            "models.unknown_provider",
            &headers,
        ));
    }
    if parsed.str("key").trim().is_empty() {
        return Err(localized(
            StatusCode::BAD_REQUEST,
            "models.api_key_empty",
            &headers,
        ));
    }
    let current_user = require_user(&state, &headers)?;
    let claimed = parsed.opt("user_email");
    if state.require_auth {
        if let Some(claimed) = claimed {
            if !claimed.trim().eq_ignore_ascii_case(&current_user.email) {
                return Err(localized(
                    StatusCode::FORBIDDEN,
                    "models.other_user_api_key",
                    &headers,
                ));
            }
        }
    }
    let target = if state.require_auth {
        current_user.email.clone()
    } else {
        claimed
            .map(str::to_string)
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| current_user.email.clone())
    };
    if target.trim().is_empty() {
        return Err(localized(
            StatusCode::BAD_REQUEST,
            "models.sign_in_required",
            &headers,
        ));
    }
    persist_user_api_key(&state.data_dir, &target, provider, parsed.str("key").trim());
    let mut body = OrderedMap::new();
    body.insert("ok", json!(true));
    body.insert("provider", json!(provider));
    body.insert("user_email", json!(target));
    body.insert("scope", json!("user"));
    Ok(ok(&body))
}

fn persist_user_api_key(data_dir: &Path, email: &str, provider: &str, key: &str) {
    let path = data_dir.join("users.json");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return;
    };
    let Ok(mut users) = serde_json::from_str::<OrderedMap>(&text) else {
        return;
    };
    let Some(record) = users.get(email).cloned() else {
        return;
    };
    let mut record = match record {
        Value::Object(map) => {
            let mut ordered = OrderedMap::new();
            for (k, v) in map {
                ordered.insert(k, v);
            }
            ordered
        }
        _ => return,
    };
    let mut keys = record
        .get("api_keys")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    keys.insert(provider.to_string(), json!(key));
    record.insert("api_keys", Value::Object(keys));
    users.insert(email, serde_json::to_value(record).unwrap_or(json!({})));
    if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&users) {
        lattice_auth::atomic::write_text(&path, &text);
    }
}

// ── GET /mode + GET /runtime_features (one handler, two mounts) ──────────────

async fn runtime_features(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_sensitive(&state, &headers)?;
    Ok(ok(&runtime_features_body(&state)))
}

fn runtime_features_body(state: &ModelsCatalogState) -> OrderedMap {
    let public = state.app_mode == "public";
    let mut security = OrderedMap::new();
    security.insert("host", json!(state.host));
    security.insert("require_auth", json!(state.require_auth));
    security.insert("invite_gate_enabled", json!(state.invite_gate_enabled));
    security.insert("keyring_available", json!(false));
    security.insert("plaintext_api_keys_allowed", json!(false));
    security.insert("cors_allow_network", json!(false));

    let mut local_only = OrderedMap::new();
    local_only.insert("mlx", json!(state.allow_local_models && !public));
    local_only.insert("telegram_bridge", json!(state.enable_telegram));
    local_only.insert("desktop_chrome_bridge", json!(!public));
    local_only.insert("computer_use_bridge", json!(!public));

    let mut public_features = OrderedMap::new();
    public_features.insert("web_ui", json!(true));
    public_features.insert("openai_compatible_models", json!(true));
    public_features.insert(
        "persistent_data_dir",
        json!(state.data_dir.to_string_lossy()),
    );

    let mut body = OrderedMap::new();
    body.insert("mode", json!(state.app_mode));
    body.insert("public", json!(public));
    body.insert("host", json!(state.host));
    body.insert("port", json!(state.port));
    body.insert("data_dir", json!(state.data_dir.to_string_lossy()));
    body.insert("telegram_enabled", json!(state.enable_telegram));
    body.insert("graph_enabled", json!(state.enable_graph));
    body.insert("autoload_models", json!(false));
    body.insert("model_idle_unload_seconds", json!(0));
    body.insert("allow_model_downloads", json!(state.allow_model_downloads));
    body.insert("model_download_timeout", json!(0));
    body.insert("model_memory_policy", Value::Null);
    body.insert("allow_local_models", json!(state.allow_local_models));
    body.insert(
        "security",
        serde_json::to_value(&security).unwrap_or(json!({})),
    );
    body.insert("default_model", Value::Null);
    body.insert(
        "local_only_features",
        serde_json::to_value(&local_only).unwrap_or(json!({})),
    );
    body.insert(
        "public_features",
        serde_json::to_value(&public_features).unwrap_or(json!({})),
    );
    body
}

// ── GET /engines ─────────────────────────────────────────────────────────────

async fn list_engines(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_sensitive(&state, &headers)?;
    let mut body = OrderedMap::new();
    body.insert("engines", json!([]));
    body.insert("current", Value::Null);
    Ok(ok(&body))
}

// ── POST /engines/install ────────────────────────────────────────────────────

async fn engines_install(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let parsed = parse_model(&body, &[required("engine"), optional("confirmation_token")])?;
    require_admin(&state, &headers)?;
    let engine = parsed.str("engine");
    let mut out = OrderedMap::new();
    out.insert("engine", json!(engine));
    out.insert("installed", json!(false));
    out.insert(
        "detail",
        json!("engine install is host-local and is not executed by the catalog router"),
    );
    Ok(ok(&out))
}

// ── POST /engines/verify-cloud ───────────────────────────────────────────────

async fn engines_verify_cloud(
    State(state): State<ModelsCatalogState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let _ = body;
    require_admin(&state, &headers)?;
    let mut out = OrderedMap::new();
    out.insert("verified", json!([]));
    out.insert("ttl_seconds", json!(CLOUD_VERIFY_TTL_SECONDS));
    Ok(ok(&out))
}

/// Known KEEP_WORKER model/engine paths this router must never claim.
pub fn keep_worker_paths() -> &'static [(&'static str, &'static str)] {
    &[
        ("GET", "/models"),
        ("POST", "/models/load"),
        ("DELETE", "/models/unload/{model_id}"),
        ("POST", "/engines/prepare-model"),
        ("POST", "/engines/prepare-model/stream"),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keep_worker_paths_are_not_mounted() {
        for (method, path) in keep_worker_paths() {
            assert!(
                !MOUNTED.iter().any(|(m, p)| m == method && *p == *path),
                "{method} {path} must stay on the worker"
            );
        }
        assert!(!MOUNTED.iter().any(|(_, p)| *p == "/models"));
        assert!(!MOUNTED.iter().any(|(_, p)| p.starts_with("/models/load")));
        assert!(!MOUNTED.iter().any(|(_, p)| p.contains("prepare-model")));
        assert!(!MOUNTED.iter().any(|(_, p)| p.contains("unload")));
    }

    /// v11.8.0 deleted these from the worker for having no caller. This crate
    /// did not inherit them: a route nothing called is not a porting backlog.
    #[test]
    fn the_routes_v11_8_0_deleted_were_not_picked_up_here() {
        for gone in [
            "/models/switch/{model_id}",
            "/models/unload-all",
            "/engines/pull-model",
        ] {
            assert!(
                !MOUNTED.iter().any(|(_, p)| *p == gone),
                "{gone} was deleted, not migrated"
            );
            assert!(
                !keep_worker_paths().iter().any(|(_, p)| *p == gone),
                "{gone} no longer exists on the worker either"
            );
        }
    }

    #[test]
    fn openai_compatible_providers_include_openai() {
        assert!(OPENAI_COMPATIBLE_PROVIDERS.contains(&"openai"));
        assert!(!OPENAI_COMPATIBLE_PROVIDERS.contains(&"not-a-provider"));
    }
}
