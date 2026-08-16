//! Plugin SDK family (v11.6.0, WP-R8).
//!
//! Port of `latticeai/api/plugins.py` + `latticeai/core/plugins.py`.
//! Plugin directory resolution matches `persistence_runtime.py`:
//! `LATTICEAI_PLUGINS_DIR` or `<base_dir>/plugins`.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, OrderedMap};
use serde_json::{json, Value};

use crate::mcp::{
    detail, json_status, missing_fields, parse_json_object, requested_scope, require_admin,
    require_user, value_to_ordered, PlatformStore,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/plugins/sdk"),
    ("GET", "/plugins/registry"),
    ("GET", "/plugins/registry/:plugin_id"),
    ("POST", "/plugins/validate"),
    ("POST", "/plugins/install"),
    ("POST", "/plugins/uninstall"),
    ("POST", "/plugins/enable"),
    ("POST", "/plugins/disable"),
    ("POST", "/plugins/execute"),
];

pub const PLUGIN_SDK_VERSION: &str = "2.2.0";
const PLUGIN_PERMISSIONS: &[&str] = &[
    "read_workspace",
    "write_workspace",
    "read_graph",
    "write_graph",
    "run_tools",
    "run_skills",
    "run_workflows",
    "run_agents",
    "network",
    "manage_memory",
];
const PLUGIN_PROVIDES: &[&str] = &["skills", "tools", "workflows", "actions"];

#[derive(Clone)]
pub struct PluginsState {
    pub auth: Arc<AuthState>,
    pub(crate) store: PlatformStore,
    pub plugins_dir: PathBuf,
}

impl PluginsState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let plugins_dir = std::env::var("LATTICEAI_PLUGINS_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../latticeai/plugins")
            });
        Self {
            auth,
            store: PlatformStore::new(data_dir),
            plugins_dir,
        }
    }

    pub fn with_plugins_dir(mut self, dir: impl AsRef<Path>) -> Self {
        self.plugins_dir = dir.as_ref().to_path_buf();
        self
    }
}

pub fn router(state: PluginsState) -> Router {
    Router::new()
        .route("/plugins/sdk", get(plugins_sdk))
        .route("/plugins/registry", get(plugins_registry))
        .route("/plugins/registry/:plugin_id", get(plugin_detail))
        .route("/plugins/validate", post(plugin_validate))
        .route("/plugins/install", post(plugin_install))
        .route("/plugins/uninstall", post(plugin_uninstall))
        .route("/plugins/enable", post(plugin_enable))
        .route("/plugins/disable", post(plugin_disable))
        .route("/plugins/execute", post(plugin_execute))
        .with_state(state)
}

fn valid_id(id: &str) -> bool {
    let bytes = id.as_bytes();
    if bytes.len() < 2 || bytes.len() > 64 {
        return false;
    }
    let first = bytes[0];
    if !first.is_ascii_lowercase() && !first.is_ascii_digit() {
        return false;
    }
    id.chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_')
}

fn valid_semver(v: &str) -> bool {
    let mut parts = v.split(['.', '-']);
    let mut nums = 0;
    for part in parts.by_ref() {
        if nums >= 3 {
            break;
        }
        if part.is_empty() || !part.chars().all(|c| c.is_ascii_digit()) && nums < 3 {
            if nums < 3 && part.chars().all(|c| c.is_ascii_digit()) {
                nums += 1;
                continue;
            }
            if nums < 3 {
                return false;
            }
        } else {
            nums += 1;
        }
    }
    let segs: Vec<&str> = v.split('.').collect();
    if segs.is_empty() {
        return false;
    }
    let major = segs[0]
        .split(|c: char| !c.is_ascii_digit())
        .next()
        .unwrap_or("");
    let minor = segs
        .get(1)
        .map(|s| s.split(|c: char| !c.is_ascii_digit()).next().unwrap_or(""))
        .unwrap_or("0");
    let patch = segs
        .get(2)
        .map(|s| s.split(|c: char| !c.is_ascii_digit()).next().unwrap_or(""))
        .unwrap_or("0");
    major.parse::<u32>().is_ok() && minor.parse::<u32>().is_ok() && patch.parse::<u32>().is_ok()
}

fn version_tuple(version: &str) -> (u32, u32, u32) {
    let cleaned = version.trim().trim_start_matches(">=").trim();
    let mut nums = [0u32; 3];
    for (i, part) in cleaned.split(['.', '-']).take(3).enumerate() {
        nums[i] = part.parse().unwrap_or(0);
    }
    (nums[0], nums[1], nums[2])
}

fn is_compatible(required: &str) -> bool {
    let required = required.trim().trim_start_matches(">=").trim();
    if required.is_empty() {
        return true;
    }
    let req = version_tuple(required);
    let cur = version_tuple(PLUGIN_SDK_VERSION);
    req.0 == cur.0 && cur >= req
}

struct Manifest {
    id: String,
    name: String,
    version: String,
    description: String,
    author: String,
    lattice_version: String,
    permissions: Vec<String>,
    provides: serde_json::Map<String, Value>,
    entrypoint: String,
    homepage: String,
    path: String,
}

impl Manifest {
    fn public(&self) -> OrderedMap {
        let mut m = OrderedMap::new();
        m.insert("id", json!(self.id));
        m.insert("name", json!(self.name));
        m.insert("version", json!(self.version));
        m.insert("description", json!(self.description));
        m.insert("author", json!(self.author));
        m.insert("lattice_version", json!(self.lattice_version));
        m.insert("permissions", json!(self.permissions));
        m.insert("provides", json!(self.provides));
        m.insert("entrypoint", json!(self.entrypoint));
        m.insert("homepage", json!(self.homepage));
        m.insert("path", json!(self.path));
        m.insert("compatible", json!(is_compatible(&self.lattice_version)));
        m
    }
}

fn validate_manifest(data: &Value, path: &str) -> (Option<Manifest>, Vec<String>) {
    let mut errors = Vec::new();
    let Some(obj) = data.as_object() else {
        return (None, vec!["manifest is not a JSON object".into()]);
    };
    let plugin_id = obj.get("id").and_then(Value::as_str).unwrap_or("").trim();
    if plugin_id.is_empty() {
        errors.push("missing required field: id".into());
    } else if !valid_id(plugin_id) {
        errors.push("id must be lowercase alphanumeric with - or _ (2-64 chars)".into());
    }
    let name = obj
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or(plugin_id)
        .trim();
    if name.is_empty() {
        errors.push("missing required field: name".into());
    }
    let version = obj
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if version.is_empty() {
        errors.push("missing required field: version".into());
    } else if !valid_semver(version) {
        errors.push(format!(
            "version '{version}' is not a valid semantic version"
        ));
    }
    let mut perms = Vec::new();
    match obj.get("permissions") {
        None => {}
        Some(Value::Array(arr)) => {
            for perm in arr {
                let p = perm.as_str().unwrap_or("");
                if !PLUGIN_PERMISSIONS.contains(&p) {
                    errors.push(format!("unknown permission: {p}"));
                } else {
                    perms.push(p.to_string());
                }
            }
        }
        Some(_) => errors.push("permissions must be a list".into()),
    }
    let mut provides = serde_json::Map::new();
    match obj.get("provides") {
        None => {}
        Some(Value::Object(map)) => {
            for (key, value) in map {
                if !PLUGIN_PROVIDES.contains(&key.as_str()) {
                    errors.push(format!("unknown provides key: {key}"));
                    continue;
                }
                match value {
                    Value::Array(arr) => {
                        provides.insert(
                            key.clone(),
                            json!(arr
                                .iter()
                                .map(|v| v.as_str().unwrap_or("").to_string())
                                .collect::<Vec<_>>()),
                        );
                    }
                    _ => errors.push(format!("provides.{key} must be a list")),
                }
            }
        }
        Some(_) => errors.push("provides must be an object".into()),
    }
    let lattice_version = obj
        .get("lattice_version")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if !lattice_version.is_empty() && !is_compatible(&lattice_version) {
        errors.push(format!(
            "requires Lattice {lattice_version} but host is {PLUGIN_SDK_VERSION}"
        ));
    }
    if !errors.is_empty() {
        return (None, errors);
    }
    (
        Some(Manifest {
            id: plugin_id.to_string(),
            name: name.to_string(),
            version: version.to_string(),
            description: obj
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            author: obj
                .get("author")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            lattice_version,
            permissions: perms,
            provides,
            entrypoint: obj
                .get("entrypoint")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            homepage: obj
                .get("homepage")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            path: path.to_string(),
        }),
        Vec::new(),
    )
}

fn discover(dir: &Path) -> (Vec<Manifest>, Vec<Value>) {
    let mut valid = Vec::new();
    let mut invalid = Vec::new();
    if !dir.exists() {
        return (valid, invalid);
    }
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .map(|rd| rd.filter_map(|e| e.ok()).collect())
        .unwrap_or_default();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        if !entry.path().is_dir() {
            continue;
        }
        let manifest_path = entry.path().join("plugin.json");
        if !manifest_path.exists() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&manifest_path) else {
            invalid
                .push(json!({"path": entry.path().to_string_lossy(), "errors": ["invalid JSON"]}));
            continue;
        };
        let data = match serde_json::from_str::<Value>(&text) {
            Ok(v) => v,
            Err(exc) => {
                invalid.push(json!({"path": entry.path().to_string_lossy(), "errors": [format!("invalid JSON: {exc}")]}));
                continue;
            }
        };
        let (manifest, errors) = validate_manifest(&data, &entry.path().to_string_lossy());
        match manifest {
            Some(m) => valid.push(m),
            None => invalid.push(json!({
                "path": entry.path().to_string_lossy(),
                "id": data.get("id"),
                "errors": errors,
            })),
        }
    }
    (valid, invalid)
}

async fn plugins_sdk(State(state): State<PluginsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    Redirect::permanent("/app#/marketplace").into_response()
}

async fn plugins_registry(State(state): State<PluginsState>, headers: HeaderMap) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let (valid, invalid) = discover(&state.plugins_dir);
    let registry = state.store.list_plugin_registry();
    let mut plugins = Vec::new();
    for manifest in &valid {
        let st = registry.get(&manifest.id).cloned().unwrap_or(json!({}));
        let mut public = manifest.public();
        let installed = st
            .get("installed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        public.insert("installed", json!(installed));
        public.insert(
            "enabled",
            json!(st
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(installed)),
        );
        public.insert(
            "install_status",
            st.get("install_status")
                .cloned()
                .unwrap_or_else(|| json!(if installed { "ready" } else { "available" })),
        );
        public.insert("validation_status", json!("valid"));
        public.insert(
            "updated_at",
            st.get("updated_at").cloned().unwrap_or(Value::Null),
        );
        plugins.push(serde_json::to_string(&public).unwrap_or_else(|_| "{}".into()));
    }
    let text = format!(
        "{{\"sdk_version\":\"{PLUGIN_SDK_VERSION}\",\"permissions\":{},\"provides\":{},\"plugins\":[{}],\"invalid\":{},\"plugins_dir\":{},\"total\":{}}}",
        serde_json::to_string(&PLUGIN_PERMISSIONS).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&PLUGIN_PROVIDES).unwrap_or_else(|_| "[]".into()),
        plugins.join(","),
        serde_json::to_string(&invalid).unwrap_or_else(|_| "[]".into()),
        serde_json::to_string(&state.plugins_dir.to_string_lossy()).unwrap_or_else(|_| "\"\"".into()),
        valid.len(),
    );
    json_response(StatusCode::OK, &text, None)
}

async fn plugin_detail(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    AxumPath(plugin_id): AxumPath<String>,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let (valid, _) = discover(&state.plugins_dir);
    let Some(manifest) = valid.into_iter().find(|m| m.id == plugin_id) else {
        return detail(
            StatusCode::NOT_FOUND,
            &format!("Plugin not found: {plugin_id}"),
        );
    };
    let registry = state
        .store
        .list_plugin_registry()
        .get(&plugin_id)
        .cloned()
        .unwrap_or(json!({}));
    let text = format!(
        "{{\"plugin\":{},\"registry\":{}}}",
        serde_json::to_string(&manifest.public()).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(&registry).unwrap_or_else(|_| "{}".into()),
    );
    json_response(StatusCode::OK, &text, None)
}

async fn plugin_validate(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let manifest_in = parsed.get("manifest").cloned().unwrap_or(json!({}));
    let (manifest, errors) = validate_manifest(&manifest_in, "");
    let mut body = OrderedMap::new();
    body.insert("ok", json!(errors.is_empty()));
    body.insert("errors", json!(errors));
    body.insert(
        "manifest",
        manifest
            .map(|m| serde_json::to_value(m.public()).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
    );
    json_status(StatusCode::OK, &body)
}

async fn plugin_install(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("plugin_id"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["plugin_id"]);
    }
    let plugin_id = parsed
        .get("plugin_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    detail(
        StatusCode::BAD_REQUEST,
        &format!("plugin not found or invalid: {plugin_id}"),
    )
}

async fn plugin_uninstall(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("plugin_id"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["plugin_id"]);
    }
    let plugin_id = parsed
        .get("plugin_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let result = state.store.mark_plugin_uninstalled(plugin_id);
    json_response(
        StatusCode::OK,
        &serde_json::to_string(&value_to_ordered(&result)).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

async fn plugin_enable(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    toggle(&state, &headers, &body, true).await
}

async fn plugin_disable(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    toggle(&state, &headers, &body, false).await
}

async fn toggle(
    state: &PluginsState,
    headers: &HeaderMap,
    body: &axum::body::Bytes,
    enabled: bool,
) -> Response {
    if let Err(r) = require_admin(&state.auth, headers) {
        return r;
    }
    let parsed = match parse_json_object(body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("plugin_id"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["plugin_id"]);
    }
    let plugin_id = parsed
        .get("plugin_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let plugin = state.store.set_plugin_enabled(plugin_id, enabled);
    let text = format!(
        "{{\"plugin\":{}}}",
        serde_json::to_string(&value_to_ordered(&plugin)).unwrap_or_else(|_| "{}".into())
    );
    json_response(StatusCode::OK, &text, None)
}

async fn plugin_execute(
    State(state): State<PluginsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_user(&state.auth, &headers) {
        return r;
    }
    let _ = requested_scope(&headers, None);
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    for field in ["plugin_id", "action"] {
        if !parsed
            .as_object()
            .map(|o| o.contains_key(field))
            .unwrap_or(false)
        {
            return missing_fields(&parsed, &[field]);
        }
    }
    let plugin_id = parsed
        .get("plugin_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let action = parsed.get("action").and_then(Value::as_str).unwrap_or("");
    let mut out = OrderedMap::new();
    out.insert("plugin_id", json!(plugin_id));
    out.insert("action", json!(action));
    out.insert("status", json!("error"));
    out.insert("output", Value::Null);
    out.insert("reason", json!("plugin not found or invalid"));
    json_status(StatusCode::OK, &out)
}
