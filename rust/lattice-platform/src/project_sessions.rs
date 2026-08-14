//! Project-session CRUD — native port of `latticeai/api/project_sessions.py`.
//!
//! Storage is the same on-disk contract Python serves today: one JSON file per
//! session under `<data_dir>/project_sessions/`. A live install migrates in
//! place.

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
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, put};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use lattice_auth::response::json_response;
use lattice_auth::{atomic, AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;
use lattice_core::messages;
use serde_json::{json, Value};

/// Mounted (method, path) pairs — the OpenAPI contract assertion table.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/:session_id"),
    ("PATCH", "/api/projects/:session_id"),
    ("PUT", "/api/projects/:session_id/todos"),
    ("DELETE", "/api/projects/:session_id"),
];

const SESSION_ID_CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
const PROJECT_STATUSES: &[&str] = &["active", "archived"];
const MAX_TODOS: usize = 100;

/// What the router needs: auth + the data dir that holds `project_sessions/`.
#[derive(Clone)]
pub struct ProjectSessionsState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
}

impl ProjectSessionsState {
    pub fn new(auth: Arc<AuthState>, config: RuntimeConfig) -> Self {
        Self {
            auth,
            config: Arc::new(config),
        }
    }

    fn root(&self) -> PathBuf {
        self.config.state_file(state_files::PROJECT_SESSIONS)
    }
}

/// Build the project-session router.
pub fn router(state: ProjectSessionsState) -> Router {
    Router::new()
        .route("/api/projects", get(list_projects).post(create_project))
        .route(
            "/api/projects/:session_id",
            get(get_project)
                .patch(update_project)
                .delete(delete_project),
        )
        .route("/api/projects/:session_id/todos", put(set_todos))
        .with_state(state)
}

#[derive(Debug, Default)]
struct ListQuery {
    status: String,
}

impl ListQuery {
    fn from_raw(raw: Option<&str>) -> Self {
        let status = query_value(raw, "status").unwrap_or_else(|| "active".into());
        Self { status }
    }
}

async fn list_projects(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    Query(raw): Query<std::collections::HashMap<String, String>>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let query = raw
        .get("status")
        .cloned()
        .unwrap_or_else(|| "active".into());
    let _ = ListQuery::from_raw(Some(&format!("status={query}")));
    let store = ProjectStore::new(state.root());
    let user = email_or_none(&identity.email);
    let workspace = workspace_of(&headers);
    json_ok(store.list(user, workspace.as_deref(), &query))
}

async fn create_project(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let parsed = match parse_create(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let store = ProjectStore::new(state.root());
    json_ok(store.create(
        &parsed.0,
        &parsed.1,
        email_or_none(&identity.email),
        workspace_of(&headers),
    ))
}

async fn get_project(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    AxumPath(session_id): AxumPath<String>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let store = ProjectStore::new(state.root());
    match store.get(
        &session_id,
        email_or_none(&identity.email),
        workspace_of(&headers).as_deref(),
    ) {
        Some(record) => json_ok(record),
        None => not_found(&headers),
    }
}

async fn update_project(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    AxumPath(session_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let parsed = match parse_update(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let store = ProjectStore::new(state.root());
    match store.update(
        &session_id,
        parsed.0.as_deref(),
        parsed.1.as_deref(),
        parsed.2.as_deref(),
        email_or_none(&identity.email),
        workspace_of(&headers).as_deref(),
    ) {
        Some(record) => json_ok(record),
        None => not_found(&headers),
    }
}

async fn set_todos(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    AxumPath(session_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let todos = match parse_todos(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let store = ProjectStore::new(state.root());
    match store.set_todos(
        &session_id,
        &todos,
        email_or_none(&identity.email),
        workspace_of(&headers).as_deref(),
    ) {
        Some(record) => json_ok(record),
        None => not_found(&headers),
    }
}

async fn delete_project(
    State(state): State<ProjectSessionsState>,
    headers: HeaderMap,
    AxumPath(session_id): AxumPath<String>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let store = ProjectStore::new(state.root());
    if store.delete(
        &session_id,
        email_or_none(&identity.email),
        workspace_of(&headers).as_deref(),
    ) {
        let mut map = OrderedMap::new();
        map.insert("status", json!("deleted"));
        map.insert("id", json!(session_id));
        json_ok(map)
    } else {
        not_found(&headers)
    }
}

fn not_found(headers: &HeaderMap) -> Response {
    let lang = messages::resolve_language_from_headers(header_pairs(headers));
    let err = messages::http_error(404, "project.not_found", lang, &[]);
    let (status, body) = err.into_response_parts();
    json_status(
        StatusCode::from_u16(status).unwrap_or(StatusCode::NOT_FOUND),
        &body,
    )
}

fn header_pairs(headers: &HeaderMap) -> impl Iterator<Item = (&str, &str)> {
    headers
        .iter()
        .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v)))
}

fn email_or_none(email: &str) -> Option<&str> {
    if email.is_empty() {
        None
    } else {
        Some(email)
    }
}

/// Authenticated callers default to the personal workspace, matching the
/// product's workspace gate when no `X-Workspace-Id` is sent.
fn workspace_of(headers: &HeaderMap) -> Option<String> {
    lattice_auth::workspace_scope_from_request(headers, None).or_else(|| Some("personal".into()))
}

fn parse_create(bytes: &[u8]) -> Result<(String, String), Response> {
    let object = parse_object(bytes)?;
    match object.get("title") {
        None => Err(missing_fields(&object, &["title"])),
        Some(Value::String(title)) if title.is_empty() => Err(string_too_short("title", "")),
        Some(Value::String(title)) if title.chars().count() > 200 => {
            Err(string_too_long("title", title, 200))
        }
        Some(Value::String(title)) => {
            let goal = object
                .get("goal")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            Ok((title.clone(), goal))
        }
        Some(other) => Err(string_type("title", other)),
    }
}

fn parse_update(
    bytes: &[u8],
) -> Result<(Option<String>, Option<String>, Option<String>), Response> {
    let object = parse_object(bytes)?;
    let title = match object.get("title") {
        None => None,
        Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => return Err(string_type("title", other)),
    };
    let goal = match object.get("goal") {
        None => None,
        Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => return Err(string_type("goal", other)),
    };
    let status = match object.get("status") {
        None => None,
        Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => return Err(string_type("status", other)),
    };
    Ok((title, goal, status))
}

fn parse_todos(bytes: &[u8]) -> Result<Vec<Value>, Response> {
    let object = parse_object(bytes)?;
    match object.get("todos") {
        None => Ok(Vec::new()),
        Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(items)) => Ok(items.clone()),
        Some(other) => Err(list_type("todos", other)),
    }
}

fn parse_object(bytes: &[u8]) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Err(missing_body());
    }
    let parsed: Value = serde_json::from_slice(bytes).map_err(|error| {
        fastapi_errors(&[problem(
            "json_invalid",
            json!(["body", 0]),
            "JSON decode error",
            json!({}),
            Some(json!({ "error": error.to_string() })),
        )])
    })?;
    parsed.as_object().cloned().ok_or_else(|| {
        fastapi_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
            None,
        )])
    })
}

struct ProjectStore {
    root: PathBuf,
}

impl ProjectStore {
    fn new(root: PathBuf) -> Self {
        Self { root }
    }

    fn path(&self, session_id: &str) -> Option<PathBuf> {
        if !valid_session_id(session_id) {
            return None;
        }
        Some(self.root.join(format!("{session_id}.json")))
    }

    fn load(&self, session_id: &str) -> Option<OrderedMap> {
        let path = self.path(session_id)?;
        let text = std::fs::read_to_string(path).ok()?;
        serde_json::from_str::<OrderedMap>(&text).ok()
    }

    fn visible(record: &OrderedMap, user_email: Option<&str>, workspace_id: Option<&str>) -> bool {
        if let Some(email) = user_email {
            if record.get("user_email").and_then(Value::as_str) != Some(email) {
                return false;
            }
        }
        if let Some(ws) = workspace_id {
            if record.get("workspace_id").and_then(Value::as_str) != Some(ws) {
                return false;
            }
        }
        true
    }

    fn get(
        &self,
        session_id: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Option<OrderedMap> {
        let record = self.load(session_id)?;
        if Self::visible(&record, user_email, workspace_id) {
            Some(record)
        } else {
            None
        }
    }

    fn list(
        &self,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        status: &str,
    ) -> OrderedMap {
        let mut sessions: Vec<OrderedMap> = Vec::new();
        if self.root.exists() {
            if let Ok(entries) = std::fs::read_dir(&self.root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) != Some("json") {
                        continue;
                    }
                    let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
                        continue;
                    };
                    let Some(record) = self.load(stem) else {
                        continue;
                    };
                    if !Self::visible(&record, user_email, workspace_id) {
                        continue;
                    }
                    if status != "all"
                        && record.get("status").and_then(Value::as_str) != Some(status)
                    {
                        continue;
                    }
                    sessions.push(record);
                }
            }
        }
        sessions.sort_by(|a, b| {
            let left = a.get("updated_at").and_then(Value::as_str).unwrap_or("");
            let right = b.get("updated_at").and_then(Value::as_str).unwrap_or("");
            right.cmp(left)
        });
        let count = sessions.len();
        let mut map = OrderedMap::new();
        map.insert("projects", json!(sessions));
        map.insert("count", json!(count));
        map
    }

    fn save(&self, mut record: OrderedMap) -> OrderedMap {
        record.insert("updated_at", json!(now_iso()));
        let id = record
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if let Some(path) = self.path(&id) {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if let Ok(text) = serde_json::to_string(&record) {
                atomic::write_text(&path, &text);
            }
        }
        record
    }

    fn create(
        &self,
        title: &str,
        goal: &str,
        user_email: Option<&str>,
        workspace_id: Option<String>,
    ) -> OrderedMap {
        let session_id = token_urlsafe(12);
        let stamp = now_iso();
        let mut record = OrderedMap::new();
        record.insert("id", json!(session_id));
        record.insert(
            "title",
            json!(clean(title, 200).unwrap_or_else(|| "프로젝트".into())),
        );
        record.insert("goal", json!(clean(goal, 2000).unwrap_or_default()));
        record.insert("status", json!("active"));
        record.insert("user_email", json!(user_email));
        record.insert("workspace_id", json!(workspace_id));
        record.insert("created_at", json!(stamp));
        record.insert("updated_at", json!(stamp));
        record.insert("files", json!([]));
        record.insert("todos", json!([]));
        record.insert("runs", json!([]));
        record.insert("last_verification", Value::Null);
        self.save(record)
    }

    fn update(
        &self,
        session_id: &str,
        title: Option<&str>,
        goal: Option<&str>,
        status: Option<&str>,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Option<OrderedMap> {
        let mut record = self.get(session_id, user_email, workspace_id)?;
        if let Some(title) = title {
            if let Some(cleaned) = clean(title, 200) {
                record.insert("title", json!(cleaned));
            }
        }
        if let Some(goal) = goal {
            record.insert("goal", json!(clean(goal, 2000).unwrap_or_default()));
        }
        if let Some(status) = status {
            if PROJECT_STATUSES.contains(&status) {
                record.insert("status", json!(status));
            }
        }
        Some(self.save(record))
    }

    fn delete(
        &self,
        session_id: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> bool {
        if self.get(session_id, user_email, workspace_id).is_none() {
            return false;
        }
        match self.path(session_id) {
            Some(path) if path.exists() => std::fs::remove_file(path).is_ok(),
            _ => false,
        }
    }

    fn set_todos(
        &self,
        session_id: &str,
        todos: &[Value],
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Option<OrderedMap> {
        let mut record = self.get(session_id, user_email, workspace_id)?;
        let mut normalized = Vec::new();
        for raw in todos.iter().take(MAX_TODOS) {
            let (text, done) = if let Some(obj) = raw.as_object() {
                (
                    clean(obj.get("text").and_then(Value::as_str).unwrap_or(""), 300),
                    obj.get("done").and_then(Value::as_bool).unwrap_or(false),
                )
            } else {
                (clean(raw.as_str().unwrap_or(&raw.to_string()), 300), false)
            };
            if let Some(text) = text {
                let mut item = OrderedMap::new();
                item.insert("text", json!(text));
                item.insert("done", json!(done));
                normalized.push(item);
            }
        }
        record.insert("todos", json!(normalized));
        Some(self.save(record))
    }
}

fn valid_session_id(session_id: &str) -> bool {
    let len = session_id.len();
    (8..=64).contains(&len) && session_id.bytes().all(|b| SESSION_ID_CHARS.contains(&b))
}

fn clean(value: &str, limit: usize) -> Option<String> {
    let trimmed: String = value.trim().chars().take(limit).collect();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

fn token_urlsafe(nbytes: usize) -> String {
    let mut buf = vec![0u8; nbytes];
    let _ = getrandom::fill(&mut buf);
    URL_SAFE_NO_PAD.encode(buf)
}

fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    civil_utc(secs)
}

fn civil_utc(secs: u64) -> String {
    const SECS_PER_DAY: u64 = 86_400;
    let days = (secs / SECS_PER_DAY) as i64;
    let rem = secs % SECS_PER_DAY;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

/// Howard Hinnant's civil-from-days, days since 1970-01-01.
fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    (year as i32, m as u32, d as u32)
}

fn query_value(raw: Option<&str>, key: &str) -> Option<String> {
    raw.and_then(|q| {
        q.split('&').find_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            (k == key).then(|| v.to_string())
        })
    })
}

pub(crate) fn json_ok(body: impl serde::Serialize) -> Response {
    let text = serde_json::to_string(&body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &text, None)
}

pub(crate) fn json_status(status: StatusCode, body: &Value) -> Response {
    let text = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(status, &text, None)
}

pub(crate) fn detail(status: StatusCode, message: &str) -> Response {
    let mut map = OrderedMap::new();
    map.insert("detail", json!(message));
    let text = serde_json::to_string(&map).unwrap_or_else(|_| "{}".into());
    json_response(status, &text, None)
}

pub(crate) fn missing_body() -> Response {
    fastapi_errors(&[problem(
        "missing",
        json!(["body"]),
        "Field required",
        Value::Null,
        None,
    )])
}

pub(crate) fn missing_fields(input: &serde_json::Map<String, Value>, names: &[&str]) -> Response {
    let problems: Vec<OrderedMap> = names
        .iter()
        .map(|name| {
            problem(
                "missing",
                json!(["body", name]),
                "Field required",
                Value::Object(input.clone()),
                None,
            )
        })
        .collect();
    fastapi_errors(&problems)
}

fn string_too_short(name: &str, input: &str) -> Response {
    fastapi_errors(&[problem(
        "string_too_short",
        json!(["body", name]),
        "String should have at least 1 character",
        json!(input),
        Some(json!({ "min_length": 1 })),
    )])
}

fn string_too_long(name: &str, input: &str, max_length: usize) -> Response {
    fastapi_errors(&[problem(
        "string_too_long",
        json!(["body", name]),
        &format!("String should have at most {max_length} characters"),
        json!(input),
        Some(json!({ "max_length": max_length })),
    )])
}

fn string_type(name: &str, input: &Value) -> Response {
    fastapi_errors(&[problem(
        "string_type",
        json!(["body", name]),
        "Input should be a valid string",
        input.clone(),
        None,
    )])
}

fn list_type(name: &str, input: &Value) -> Response {
    fastapi_errors(&[problem(
        "list_type",
        json!(["body", name]),
        "Input should be a valid list",
        input.clone(),
        None,
    )])
}

pub(crate) fn problem(
    kind: &str,
    loc: Value,
    msg: &str,
    input: Value,
    ctx: Option<Value>,
) -> OrderedMap {
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

pub(crate) fn fastapi_errors(problems: &[OrderedMap]) -> Response {
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

pub(crate) fn parse_json_object(bytes: &[u8]) -> Result<serde_json::Map<String, Value>, Response> {
    if bytes.is_empty() {
        return Err(missing_body());
    }
    let parsed: Value = serde_json::from_slice(bytes).map_err(|error| {
        fastapi_errors(&[problem(
            "json_invalid",
            json!(["body", 0]),
            "JSON decode error",
            json!({}),
            Some(json!({ "error": error.to_string() })),
        )])
    })?;
    parsed.as_object().cloned().ok_or_else(|| {
        fastapi_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
            None,
        )])
    })
}

pub(crate) fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language_from_headers(
        headers
            .iter()
            .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v))),
    )
}

pub(crate) fn message_detail(status: u16, id: &str, headers: &HeaderMap) -> Response {
    let err = messages::http_error(status, id, language_of(headers), &[]);
    let (code, body) = err.into_response_parts();
    json_status(
        StatusCode::from_u16(code).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
    )
}

#[allow(dead_code)]
pub(crate) fn exports_dir(config: &RuntimeConfig) -> PathBuf {
    config.state_file(state_files::WORKSPACE_EXPORTS)
}

#[allow(dead_code)]
pub(crate) fn now_iso_utc() -> String {
    now_iso()
}

#[allow(dead_code)]
pub(crate) fn atomic_json(path: &Path, value: &impl serde::Serialize) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(value) {
        atomic::write_text(path, &text);
    }
}
