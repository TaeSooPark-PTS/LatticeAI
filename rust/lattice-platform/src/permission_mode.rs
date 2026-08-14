//! Permission-mode dial — port of `latticeai/api/permission_mode.py`.
//!
//! Three routes, one JSON file (`permission_mode.json`). The write is the same
//! atomic replace Python uses (`<path>.tmp` → rename, mode 0600): a torn
//! write of this file is how v9.9.8 lost a scope and then applied the wrong
//! autonomy level to the next agent turn.
//!
//! Scope precedence is workspace → user → process default. `set_mode` resolves
//! the previous value from the already-loaded document (it must not re-enter
//! the store lock — that deadlock was the other v9.9.8 bug).

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
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use lattice_auth::atomic;
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::{dumps_indent2, OrderedMap};
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity};
use lattice_core::db::tables::state_files;
use serde::Deserialize;
use serde_json::{json, Value};

/// Routes this family mounts. Compared to `models_misc.json` by the contract test.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/permission-mode"),
    ("POST", "/api/permission-mode"),
    ("GET", "/api/permission-mode/catalog"),
];

const BYPASS_ACK: &str = "bypass mode requires acknowledge_risk=true \
(YOLO inside the agent workspace; circuit breakers still apply)";

/// What the family needs to serve the dial.
#[derive(Clone)]
pub struct PermissionModeState {
    auth: Arc<AuthState>,
    store: Arc<ModeStore>,
}

impl PermissionModeState {
    /// Point the dial at `data_dir/permission_mode.json`.
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        Self::with_default(auth, data_dir, "strict")
    }

    /// Same, with an explicit process-level default (env `LATTICEAI_PERMISSION_MODE`).
    pub fn with_default(
        auth: Arc<AuthState>,
        data_dir: impl AsRef<Path>,
        default_mode: &str,
    ) -> Self {
        let path = data_dir.as_ref().join(state_files::PERMISSION_MODE);
        Self {
            auth,
            store: Arc::new(ModeStore::open(path, default_mode)),
        }
    }

    /// On-disk path this instance writes.
    pub fn path(&self) -> &Path {
        &self.store.path
    }
}

/// Router factory. The integrator merges this into the gateway.
pub fn router(state: PermissionModeState) -> Router {
    Router::new()
        .route("/api/permission-mode", get(get_mode).post(set_mode))
        .route("/api/permission-mode/catalog", get(get_catalog))
        .with_state(state)
}

// ── catalog / contract (latticeai.core.permission_mode) ──────────────────────

/// Parse user/API/env input; unknown → strict.
pub fn normalize_mode(value: &str) -> &'static str {
    match value.trim().to_ascii_lowercase().as_str() {
        "strict" | "default" | "manual" => "strict",
        "trusted" | "acceptedits" | "accept_edits" | "workspace" => "trusted",
        "bypass"
        | "bypasspermissions"
        | "bypass_permissions"
        | "yolo"
        | "dangerously-skip-permissions" => "bypass",
        _ => "strict",
    }
}

fn catalog_entries() -> Vec<OrderedMap> {
    vec![
        catalog_row(
            "strict",
            "Strict",
            "엄격",
            "Reads auto; writes and exec need approval or review proposals.",
            "읽기는 자동, 쓰기·실행은 승인 또는 변경 제안.",
            "low",
            false,
            None,
            None,
        ),
        catalog_row(
            "trusted",
            "Trusted",
            "신뢰",
            "Workspace writes and knowledge reads auto-run; exec/desktop control still gated.",
            "워크스페이스 쓰기·지식 읽기 자동. 실행·데스크톱 제어는 승인 필요.",
            "medium",
            false,
            None,
            None,
        ),
        catalog_row(
            "bypass",
            "Bypass",
            "바이패스",
            "YOLO inside the agent workspace. Hard circuit breakers still apply.",
            "에이전트 워크스페이스 안에서 전부 자동. 하드 차단만 남음.",
            "high",
            true,
            Some(
                "Bypass skips routine approval prompts. Destructive system paths, \
root/home wipes, and blocked prefixes remain denied.",
            ),
            Some(
                "바이패스는 일상 승인 프롬프트를 건너뜁니다. 시스템 경로 파괴, \
루트/홈 삭제, 차단 접두사는 계속 거부됩니다.",
            ),
        ),
    ]
}

#[allow(clippy::too_many_arguments)]
fn catalog_row(
    id: &str,
    label: &str,
    label_ko: &str,
    summary: &str,
    summary_ko: &str,
    risk: &str,
    requires_ack: bool,
    warning: Option<&str>,
    warning_ko: Option<&str>,
) -> OrderedMap {
    let mut row = OrderedMap::new();
    row.insert("id", json!(id));
    row.insert("label", json!(label));
    row.insert("label_ko", json!(label_ko));
    row.insert("summary", json!(summary));
    row.insert("summary_ko", json!(summary_ko));
    row.insert("risk", json!(risk));
    row.insert("requires_ack", json!(requires_ack));
    if let Some(warning) = warning {
        row.insert("warning", json!(warning));
    }
    if let Some(warning_ko) = warning_ko {
        row.insert("warning_ko", json!(warning_ko));
    }
    row
}

fn entry_for(mode: &str) -> OrderedMap {
    catalog_entries()
        .into_iter()
        .find(|row| row.get("id").and_then(Value::as_str) == Some(mode))
        .unwrap_or_else(|| catalog_entries().remove(0))
}

fn mode_contract(mode: &str, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
    let mode = normalize_mode(mode);
    let entry = entry_for(mode);
    let mut body = OrderedMap::new();
    body.insert("mode", json!(mode));
    body.insert(
        "label",
        entry.get("label").cloned().unwrap_or(json!("Strict")),
    );
    body.insert(
        "label_ko",
        entry.get("label_ko").cloned().unwrap_or(json!("엄격")),
    );
    body.insert("risk", entry.get("risk").cloned().unwrap_or(json!("low")));
    body.insert(
        "requires_ack",
        entry.get("requires_ack").cloned().unwrap_or(json!(false)),
    );
    body.insert("proposal_first", json!(mode == "strict"));
    body.insert(
        "workspace_writes_auto",
        json!(mode == "trusted" || mode == "bypass"),
    );
    body.insert(
        "knowledge_reads_auto",
        json!(mode == "trusted" || mode == "bypass"),
    );
    body.insert("exec_auto", json!(mode == "bypass"));
    body.insert(
        "computer_observation_auto",
        json!(mode == "trusted" || mode == "bypass"),
    );
    body.insert("computer_control_auto", json!(mode == "bypass"));
    body.insert("circuit_breakers", json!(true));
    let catalog: Vec<Value> = catalog_entries()
        .into_iter()
        .map(|row| serde_json::to_value(row).unwrap_or(json!({})))
        .collect();
    body.insert("catalog", json!(catalog));
    let mut scope = OrderedMap::new();
    scope.insert("user_email", json!(user_email));
    scope.insert("workspace_id", json!(workspace_id));
    body.insert(
        "scope",
        serde_json::to_value(scope).unwrap_or(json!({"user_email": null, "workspace_id": null})),
    );
    body
}

// ── store ────────────────────────────────────────────────────────────────────

struct ModeStore {
    path: PathBuf,
    default_mode: String,
    lock: Mutex<()>,
}

impl ModeStore {
    fn open(path: PathBuf, default_mode: &str) -> Self {
        Self {
            path,
            default_mode: normalize_mode(default_mode).to_string(),
            lock: Mutex::new(()),
        }
    }

    fn empty(&self) -> OrderedMap {
        let mut data = OrderedMap::new();
        data.insert("default", json!(self.default_mode));
        data.insert("users", json!({}));
        data.insert("workspaces", json!({}));
        data
    }

    fn read(&self) -> OrderedMap {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return self.empty();
        };
        let Ok(data) = serde_json::from_str::<OrderedMap>(&text) else {
            return self.empty();
        };
        if data.get("default").is_none()
            && data.get("users").is_none()
            && data.get("workspaces").is_none()
        {
            // A non-object already failed Deserialize. An object missing every
            // bucket is still a document — fill the holes like Python's setdefault.
        }
        let mut data = data;
        if data.get("default").is_none() {
            data.insert("default", json!(self.default_mode));
        }
        if data.get("users").is_none() {
            data.insert("users", json!({}));
        }
        if data.get("workspaces").is_none() {
            data.insert("workspaces", json!({}));
        }
        data
    }

    fn write(&self, data: &OrderedMap) {
        let Ok(text) = dumps_indent2(data) else {
            return;
        };
        atomic::write_text(&self.path, &text);
    }

    fn resolve_from(
        &self,
        data: &OrderedMap,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> String {
        if let Some(workspace_id) = workspace_id.filter(|value| !value.is_empty()) {
            if let Some(mode) = data
                .get("workspaces")
                .and_then(Value::as_object)
                .and_then(|map| map.get(workspace_id))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        if let Some(user_email) = user_email.filter(|value| !value.is_empty()) {
            let key = user_email.to_ascii_lowercase();
            if let Some(mode) = data
                .get("users")
                .and_then(Value::as_object)
                .and_then(|map| map.get(&key))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        let fallback = data
            .get("default")
            .and_then(Value::as_str)
            .unwrap_or(self.default_mode.as_str());
        normalize_mode(fallback).to_string()
    }

    fn get(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
        let _guard = self.lock.lock().expect("permission_mode lock");
        let data = self.read();
        drop(_guard);
        let mode = self.resolve_from(&data, user_email, workspace_id);
        mode_contract(&mode, user_email, workspace_id)
    }

    fn set_mode(
        &self,
        mode: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        acknowledge_risk: bool,
    ) -> Result<OrderedMap, String> {
        let mode = normalize_mode(mode);
        if mode == "bypass" && !acknowledge_risk {
            return Err(BYPASS_ACK.to_string());
        }
        let _guard = self.lock.lock().expect("permission_mode lock");
        let mut data = self.read();
        // Lock-free helper on purpose: calling get() here would re-enter.
        let _previous = self.resolve_from(&data, user_email, workspace_id);
        if let Some(workspace_id) = workspace_id.filter(|value| !value.is_empty()) {
            let mut workspaces = data
                .get("workspaces")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            workspaces.insert(workspace_id.to_string(), json!(mode));
            data.insert("workspaces", Value::Object(workspaces));
        } else if let Some(user_email) = user_email.filter(|value| !value.is_empty()) {
            let mut users = data
                .get("users")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            users.insert(user_email.to_ascii_lowercase(), json!(mode));
            data.insert("users", Value::Object(users));
        } else {
            data.insert("default", json!(mode));
        }
        self.write(&data);
        drop(_guard);
        Ok(self.get(user_email, workspace_id))
    }
}

// ── handlers ─────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct GetQuery {
    workspace_id: Option<String>,
}

fn require_user(state: &PermissionModeState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn header_workspace(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-workspace-id")
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn ok(body: &OrderedMap) -> Response {
    let rendered = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &rendered, None)
}

async fn get_mode(
    State(state): State<PermissionModeState>,
    headers: HeaderMap,
    Query(query): Query<GetQuery>,
) -> Result<Response, Response> {
    let user = require_user(&state, &headers)?;
    let scope = query
        .workspace_id
        .filter(|value| !value.is_empty())
        .or_else(|| header_workspace(&headers));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    Ok(ok(&state.store.get(user_email, scope.as_deref())))
}

async fn get_catalog(
    State(state): State<PermissionModeState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let modes: Vec<Value> = catalog_entries()
        .into_iter()
        .map(|row| serde_json::to_value(row).unwrap_or(json!({})))
        .collect();
    let mut body = OrderedMap::new();
    body.insert("modes", json!(modes));
    Ok(ok(&body))
}

async fn set_mode(
    State(state): State<PermissionModeState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let parsed = parse_set_body(&body)?;
    let user = require_user(&state, &headers)?;
    let scope = parsed
        .workspace_id
        .filter(|value| !value.is_empty())
        .or_else(|| header_workspace(&headers));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    match state.store.set_mode(
        &parsed.mode,
        user_email,
        scope.as_deref(),
        parsed.acknowledge_risk,
    ) {
        Ok(contract) => Ok(ok(&contract)),
        Err(detail) => Err(detail_error(StatusCode::BAD_REQUEST, &detail)),
    }
}

struct SetBody {
    mode: String,
    workspace_id: Option<String>,
    acknowledge_risk: bool,
}

fn parse_set_body(bytes: &[u8]) -> Result<SetBody, Response> {
    // FastAPI/pydantic validation shape, field-declaration order.
    let parsed: Value = match serde_json::from_slice(bytes) {
        Ok(value) => value,
        Err(error) => {
            return Err(validation_errors(&[problem(
                "json_invalid",
                json!(["body", 0]),
                "JSON decode error",
                json!({}),
                Some(json!({ "error": error.to_string() })),
            )]))
        }
    };
    let Some(object) = parsed.as_object() else {
        return Err(validation_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
            None,
        )]));
    };

    let mut problems: Vec<OrderedMap> = Vec::new();
    let mode = match object.get("mode") {
        None => {
            problems.push(problem(
                "missing",
                json!(["body", "mode"]),
                "Field required",
                parsed.clone(),
                None,
            ));
            String::new()
        }
        Some(Value::String(text)) => text.clone(),
        Some(other) => {
            problems.push(problem(
                "string_type",
                json!(["body", "mode"]),
                "Input should be a valid string",
                other.clone(),
                None,
            ));
            String::new()
        }
    };
    let workspace_id = match object.get("workspace_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) => Some(text.clone()),
        Some(other) => {
            problems.push(problem(
                "string_type",
                json!(["body", "workspace_id"]),
                "Input should be a valid string",
                other.clone(),
                None,
            ));
            None
        }
    };
    let acknowledge_risk = match object.get("acknowledge_risk") {
        None | Some(Value::Null) => false,
        Some(Value::Bool(flag)) => *flag,
        Some(other) => {
            problems.push(problem(
                "bool_type",
                json!(["body", "acknowledge_risk"]),
                "Input should be a valid boolean",
                other.clone(),
                None,
            ));
            false
        }
    };
    if !problems.is_empty() {
        return Err(validation_errors(&problems));
    }
    Ok(SetBody {
        mode,
        workspace_id,
        acknowledge_risk,
    })
}

fn problem(kind: &str, loc: Value, msg: &str, input: Value, ctx: Option<Value>) -> OrderedMap {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!(kind));
    entry.insert("loc", loc);
    entry.insert("msg", json!(msg));
    entry.insert("input", input);
    if let Some(ctx) = ctx {
        entry.insert("ctx", ctx);
    }
    entry
}

fn validation_errors(problems: &[OrderedMap]) -> Response {
    let rendered: Vec<String> = problems
        .iter()
        .filter_map(|entry| serde_json::to_string(entry).ok())
        .collect();
    json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        &format!("{{\"detail\":[{}]}}", rendered.join(",")),
        None,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_input_is_strict() {
        assert_eq!(normalize_mode("nonsense"), "strict");
        assert_eq!(normalize_mode(""), "strict");
        assert_eq!(normalize_mode("  TRUSTED  "), "trusted");
        assert_eq!(normalize_mode("yolo"), "bypass");
    }

    #[test]
    fn catalog_lists_the_three_dials_in_order() {
        let ids: Vec<_> = catalog_entries()
            .iter()
            .map(|row| row.get("id").and_then(Value::as_str).unwrap().to_string())
            .collect();
        assert_eq!(ids, ["strict", "trusted", "bypass"]);
    }
}
