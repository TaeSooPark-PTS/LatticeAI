//! Local permission request and approval routes.
//!
//! Port of `latticeai/api/permissions.py`. In-memory approvals plus
//! `<data_dir>/permission_queue.json`. Discord notify is best-effort and
//! skipped when no webhook/bot is configured.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::atomic;
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::dumps_indent2;
use lattice_auth::response::json_response;
use lattice_auth::AuthState;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("POST", "/permissions/approve/:token"),
    ("POST", "/permissions/deny/:token"),
    ("GET", "/permissions/pending"),
    ("GET", "/permissions/status/:token"),
];

const ACTION_LABELS: &[(&str, &str)] = &[
    ("list", "폴더 목록 보기"),
    ("read", "파일 읽기"),
    ("write", "파일 쓰기"),
];

const TTL_SECONDS: f64 = 5.0 * 60.0;

/// A minted local-files token the `/permissions/*` routes can redeem.
///
/// Folder ingest (and `/local/{list,read,write}`) keep their table in
/// `lattice-ingest::LocalApprovals`. The product UI approves those tokens
/// here, so this process holds one extra table rather than two that cannot
/// see each other.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalTokenSnapshot {
    /// Canonical path the probe bound.
    pub path: String,
    /// `list` / `read` / `write`.
    pub action: String,
    /// Owner identity (empty for the trusted local owner).
    pub user_email: String,
    /// First eight characters of the raw token — the Act inbox key.
    pub token_hint: String,
}

/// Status of a local-files token, including expiry.
#[derive(Debug, Clone, PartialEq)]
pub struct LocalTokenStatus {
    /// Snapshot fields the status/deny responses echo.
    pub snapshot: LocalTokenSnapshot,
    /// Whether the token has already been approved.
    pub approved: bool,
    /// Unix seconds; the router compares this with now.
    pub expires_at: f64,
}

/// One pending row for `/permissions/pending`.
#[derive(Debug, Clone, PartialEq)]
pub struct LocalTokenPending {
    /// Act-inbox key (the 8-char hint).
    pub hint: String,
    /// Canonical path.
    pub path: String,
    /// `list` / `read` / `write`.
    pub action: String,
    /// Owner identity.
    pub user_email: String,
    /// Already approved or still waiting.
    pub approved: bool,
    /// Remaining TTL, seconds.
    pub expires_in: i64,
}

/// The local-files approval table, as the permissions router sees it.
pub trait LocalTokenTable: Send + Sync {
    /// Mark the token approved. `None` if it is missing or expired.
    fn approve_record(&self, token: &str) -> Option<LocalTokenSnapshot>;
    /// Drop the token. `None` if it was never minted (or already gone).
    fn deny_record(&self, token: &str) -> Option<LocalTokenSnapshot>;
    /// Look up without mutating. Expired records are still returned.
    fn status_of(&self, token: &str) -> Option<LocalTokenStatus>;
    /// Non-expired records, for the Act inbox.
    fn pending_records(&self) -> Vec<LocalTokenPending>;
}

/// Shared permission state used by local-file and knowledge routers.
#[derive(Clone)]
pub struct PermissionGateway {
    approvals: Arc<Mutex<HashMap<String, Map<String, Value>>>>,
    queue_file: PathBuf,
    queue_lock: Arc<Mutex<()>>,
    monitor_secret: Option<String>,
}

impl PermissionGateway {
    /// Open the gateway rooted at `data_dir`.
    pub fn open(data_dir: &Path) -> Self {
        Self {
            approvals: Arc::new(Mutex::new(HashMap::new())),
            queue_file: data_dir.join(lattice_core::db::tables::state_files::PERMISSION_QUEUE),
            queue_lock: Arc::new(Mutex::new(())),
            monitor_secret: std::env::var("LATTICEAI_PERMISSION_SECRET")
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty()),
        }
    }

    /// SHA-256 hex of a token, matching Python `sha256_hex`.
    pub fn token_hash(token: &str) -> String {
        sha256_hex(token)
    }

    /// First eight characters of the token, or empty.
    pub fn token_hint(token: &str) -> String {
        token.chars().take(8).collect()
    }

    /// `normalize_local_path_for_approval`.
    pub fn normalize_path(path: &str) -> String {
        let expanded = expanduser(path);
        std::fs::canonicalize(&expanded)
            .unwrap_or(expanded)
            .to_string_lossy()
            .into_owned()
    }

    /// Seed an approval the way `local_permission_response` does.
    pub fn request(&self, path: &str, action: &str, user_email: &str, content: &str) -> Value {
        let normalized = Self::normalize_path(path);
        let token = token_urlsafe(24);
        let mut record = Map::new();
        record.insert("path".into(), json!(normalized));
        record.insert("action".into(), json!(action));
        record.insert("user_email".into(), json!(user_email));
        record.insert("expires_at".into(), json!(now_secs() + TTL_SECONDS));
        record.insert("approved".into(), json!(false));
        if action == "write" {
            record.insert("content_hash".into(), json!(sha256_hex(content)));
        }
        let hint = Self::token_hint(&token);
        record.insert("token_hint".into(), json!(hint));
        let key = Self::token_hash(&token);
        self.approvals
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .insert(key.clone(), record.clone());
        self.queue_write(&key, &record);
        let label = action_label(action);
        json!({
            "permission_required": true,
            "path": path,
            "action": action,
            "action_label": label,
            "approval_token": token,
            "expires_in": TTL_SECONDS as i64,
            "message": format!("AI가 '{path}' 에 대한 {label} 권한을 요청합니다."),
            "check_status_url": format!("/permissions/status/{token}"),
        })
    }

    fn queue_write(&self, key: &str, record: &Map<String, Value>) {
        let _guard = self.queue_lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut queue = self.read_queue();
        let mut stored = record.clone();
        stored.insert(
            "token_hint".into(),
            record
                .get("token_hint")
                .cloned()
                .unwrap_or_else(|| json!("")),
        );
        stored.insert("notified".into(), json!(false));
        queue.insert(key.to_string(), Value::Object(stored));
        self.write_queue(&queue);
    }

    fn queue_remove(&self, key: &str) {
        let _guard = self.queue_lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut queue = self.read_queue();
        queue.remove(key);
        self.write_queue(&queue);
    }

    fn read_queue(&self) -> Map<String, Value> {
        let Ok(text) = std::fs::read_to_string(&self.queue_file) else {
            return Map::new();
        };
        match serde_json::from_str::<Value>(&text) {
            Ok(Value::Object(map)) => map,
            _ => Map::new(),
        }
    }

    fn write_queue(&self, queue: &Map<String, Value>) {
        if let Ok(text) = dumps_indent2(&Value::Object(queue.clone())) {
            atomic::write_text(&self.queue_file, &text);
        }
    }

    /// Resolve a full token or its 8-char hint.
    pub fn resolve_key(&self, token_or_hint: &str) -> Result<String, Response> {
        let direct = Self::token_hash(token_or_hint);
        let approvals = self
            .approvals
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if approvals.contains_key(&direct) {
            return Ok(direct);
        }
        let matches: Vec<String> = approvals
            .iter()
            .filter(|(_, record)| {
                token_or_hint.chars().count() == 8
                    && record.get("token_hint").and_then(Value::as_str) == Some(token_or_hint)
            })
            .map(|(key, _)| key.clone())
            .collect();
        if matches.len() > 1 {
            return Err(detail_error(
                StatusCode::CONFLICT,
                "요청 ID가 중복되었습니다. 관리자 목록을 새로고침하세요.",
            ));
        }
        Ok(matches.into_iter().next().unwrap_or(direct))
    }

    fn check_auth(&self, auth: &AuthState, headers: &HeaderMap) -> Result<(), Response> {
        if let Some(secret) = &self.monitor_secret {
            if let Some(header) = headers.get("authorization").and_then(|v| v.to_str().ok()) {
                if header == format!("Bearer {secret}") {
                    return Ok(());
                }
            }
        }
        auth.require_admin(headers).map(|_| ())
    }

    fn require_status_owner(&self, token: &str, user_email: &str) -> Result<(), Response> {
        let key = Self::token_hash(token);
        let approvals = self
            .approvals
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if let Some(record) = approvals.get(&key) {
            if record.get("user_email").and_then(Value::as_str) != Some(user_email) {
                return Err(detail_error(
                    StatusCode::FORBIDDEN,
                    "다른 사용자의 승인 상태는 조회할 수 없습니다.",
                ));
            }
        }
        Ok(())
    }
}

fn refuse_other_owner(record_email: &str, user_email: &str) -> Result<(), Response> {
    if record_email != user_email {
        return Err(detail_error(
            StatusCode::FORBIDDEN,
            "다른 사용자의 승인 상태는 조회할 수 없습니다.",
        ));
    }
    Ok(())
}

fn action_label(action: &str) -> &str {
    ACTION_LABELS
        .iter()
        .find(|(name, _)| *name == action)
        .map(|(_, label)| *label)
        .unwrap_or(action)
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

fn sha256_hex(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn token_urlsafe(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    let _ = getrandom::fill(&mut bytes);
    base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, &bytes)
}

fn expanduser(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = home_dir() {
            return home.join(rest);
        }
    } else if path == "~" {
        if let Some(home) = home_dir() {
            return home;
        }
    }
    PathBuf::from(path)
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

/// State the permissions router needs.
#[derive(Clone)]
pub struct PermissionsState {
    /// Process-wide auth.
    pub auth: Arc<AuthState>,
    /// Shared gateway (also handed to local-file families).
    pub gateway: PermissionGateway,
    /// Folder-ingest / `/local/*` tokens minted in `LocalApprovals`.
    pub local_tokens: Option<Arc<dyn LocalTokenTable>>,
}

impl PermissionsState {
    /// Build from auth + data dir.
    pub fn new(auth: Arc<AuthState>, data_dir: &Path) -> Self {
        Self {
            auth,
            gateway: PermissionGateway::open(data_dir),
            local_tokens: None,
        }
    }

    /// Redeem `LocalApprovals` tokens through this router.
    pub fn with_local_tokens(mut self, table: Arc<dyn LocalTokenTable>) -> Self {
        self.local_tokens = Some(table);
        self
    }
}

impl axum::extract::FromRef<PermissionsState> for Arc<AuthState> {
    fn from_ref(state: &PermissionsState) -> Self {
        Arc::clone(&state.auth)
    }
}

/// Router factory.
pub fn router(state: PermissionsState) -> Router {
    Router::new()
        .route("/permissions/pending", get(pending))
        .route("/permissions/approve/:token", post(approve))
        .route("/permissions/deny/:token", post(deny))
        .route("/permissions/status/:token", get(status))
        .with_state(state)
}

fn ok(value: &Value) -> Response {
    json_response(
        StatusCode::OK,
        &serde_json::to_string(value).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

async fn pending(State(state): State<PermissionsState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    let now = now_secs();
    let approvals = state
        .gateway
        .approvals
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let mut result = Map::new();
    for (token_hash, rec) in approvals.iter() {
        let expires_at = rec.get("expires_at").and_then(Value::as_f64).unwrap_or(0.0);
        if expires_at < now {
            continue;
        }
        let hint = rec
            .get("token_hint")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| token_hash.chars().take(8).collect());
        let action = rec.get("action").and_then(Value::as_str).unwrap_or("");
        result.insert(
            hint,
            json!({
                "path": rec.get("path"),
                "action": rec.get("action"),
                "action_label": action_label(action),
                "user_email": rec.get("user_email"),
                "approved": rec.get("approved").and_then(Value::as_bool).unwrap_or(false),
                "expires_in": (expires_at - now).round() as i64,
            }),
        );
    }
    if let Some(table) = &state.local_tokens {
        for item in table.pending_records() {
            if result.contains_key(&item.hint) {
                continue;
            }
            result.insert(
                item.hint,
                json!({
                    "path": item.path,
                    "action": item.action,
                    "action_label": action_label(&item.action),
                    "user_email": item.user_email,
                    "approved": item.approved,
                    "expires_in": item.expires_in,
                }),
            );
        }
    }
    let count = result.len();
    ok(&json!({"pending": Value::Object(result), "count": count}))
}

async fn approve(
    State(state): State<PermissionsState>,
    headers: HeaderMap,
    AxumPath(token): AxumPath<String>,
) -> Response {
    if let Err(refusal) = state.gateway.check_auth(&state.auth, &headers) {
        return refusal;
    }
    let key = match state.gateway.resolve_key(&token) {
        Ok(key) => key,
        Err(refusal) => return refusal,
    };
    let mut approvals = state
        .gateway
        .approvals
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let Some(record) = approvals.get_mut(&key) else {
        drop(approvals);
        if let Some(table) = &state.local_tokens {
            if let Some(snap) = table.approve_record(&token) {
                return ok(&json!({
                    "ok": true,
                    "token_hint": snap.token_hint,
                    "path": snap.path,
                    "action": snap.action,
                    "user_email": snap.user_email,
                }));
            }
        }
        return detail_error(StatusCode::NOT_FOUND, "토큰이 없거나 만료되었습니다.");
    };
    if record
        .get("expires_at")
        .and_then(Value::as_f64)
        .unwrap_or(0.0)
        < now_secs()
    {
        approvals.remove(&key);
        drop(approvals);
        state.gateway.queue_remove(&key);
        return detail_error(StatusCode::GONE, "토큰이 만료되었습니다.");
    }
    record.insert("approved".into(), json!(true));
    let path = record.get("path").cloned();
    let action = record.get("action").cloned();
    let user_email = record.get("user_email").cloned();
    drop(approvals);
    state.gateway.queue_remove(&key);
    ok(&json!({
        "ok": true,
        "token_hint": PermissionGateway::token_hint(&token),
        "path": path,
        "action": action,
        "user_email": user_email,
    }))
}

async fn deny(
    State(state): State<PermissionsState>,
    headers: HeaderMap,
    AxumPath(token): AxumPath<String>,
) -> Response {
    if let Err(refusal) = state.gateway.check_auth(&state.auth, &headers) {
        return refusal;
    }
    let key = match state.gateway.resolve_key(&token) {
        Ok(key) => key,
        Err(refusal) => return refusal,
    };
    let record = state
        .gateway
        .approvals
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .remove(&key);
    state.gateway.queue_remove(&key);
    let Some(record) = record else {
        if let Some(table) = &state.local_tokens {
            if let Some(snap) = table.deny_record(&token) {
                return ok(&json!({
                    "ok": true,
                    "denied": true,
                    "token_hint": snap.token_hint,
                    "path": snap.path,
                    "action": snap.action,
                }));
            }
        }
        return detail_error(StatusCode::NOT_FOUND, "토큰이 없거나 이미 처리되었습니다.");
    };
    ok(&json!({
        "ok": true,
        "denied": true,
        "token_hint": PermissionGateway::token_hint(&token),
        "path": record.get("path"),
        "action": record.get("action"),
    }))
}

async fn status(
    State(state): State<PermissionsState>,
    headers: HeaderMap,
    AxumPath(token): AxumPath<String>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = state.gateway.require_status_owner(&token, &identity.email) {
        return refusal;
    }
    let now = now_secs();
    let key = PermissionGateway::token_hash(&token);
    let approvals = state
        .gateway
        .approvals
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let Some(record) = approvals.get(&key) else {
        drop(approvals);
        if let Some(table) = &state.local_tokens {
            if let Some(status) = table.status_of(&token) {
                if let Err(refusal) =
                    refuse_other_owner(&status.snapshot.user_email, &identity.email)
                {
                    return refusal;
                }
                if status.expires_at < now {
                    return ok(&json!({
                        "status": "expired",
                        "token_hint": status.snapshot.token_hint,
                    }));
                }
                if status.approved {
                    return ok(&json!({
                        "status": "approved",
                        "token_hint": status.snapshot.token_hint,
                    }));
                }
                return ok(&json!({
                    "status": "pending",
                    "token_hint": status.snapshot.token_hint,
                    "expires_in": (status.expires_at - now).round() as i64,
                }));
            }
        }
        return ok(&json!({
            "status": "denied_or_expired",
            "token_hint": PermissionGateway::token_hint(&token),
        }));
    };
    let expires_at = record
        .get("expires_at")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    if expires_at < now {
        return ok(&json!({
            "status": "expired",
            "token_hint": PermissionGateway::token_hint(&token),
        }));
    }
    if record
        .get("approved")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return ok(&json!({
            "status": "approved",
            "token_hint": PermissionGateway::token_hint(&token),
        }));
    }
    ok(&json!({
        "status": "pending",
        "token_hint": PermissionGateway::token_hint(&token),
        "expires_in": (expires_at - now).round() as i64,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    struct FakeLocal {
        tokens: Mutex<HashMap<String, LocalTokenStatus>>,
    }

    impl FakeLocal {
        fn with(token: &str, email: &str) -> Arc<Self> {
            let hint = token.chars().take(8).collect::<String>();
            let status = LocalTokenStatus {
                snapshot: LocalTokenSnapshot {
                    path: "/tmp/notes".into(),
                    action: "read".into(),
                    user_email: email.into(),
                    token_hint: hint.clone(),
                },
                approved: false,
                expires_at: now_secs() + 300.0,
            };
            let mut tokens = HashMap::new();
            tokens.insert(token.to_string(), status.clone());
            tokens.insert(hint, status);
            Arc::new(Self {
                tokens: Mutex::new(tokens),
            })
        }
    }

    impl LocalTokenTable for FakeLocal {
        fn approve_record(&self, token: &str) -> Option<LocalTokenSnapshot> {
            let mut tokens = self.tokens.lock().expect("lock");
            let status = tokens.get_mut(token)?;
            status.approved = true;
            Some(status.snapshot.clone())
        }

        fn deny_record(&self, token: &str) -> Option<LocalTokenSnapshot> {
            self.tokens
                .lock()
                .expect("lock")
                .remove(token)
                .map(|status| status.snapshot)
        }

        fn status_of(&self, token: &str) -> Option<LocalTokenStatus> {
            self.tokens.lock().expect("lock").get(token).cloned()
        }

        fn pending_records(&self) -> Vec<LocalTokenPending> {
            self.tokens
                .lock()
                .expect("lock")
                .values()
                .filter(|status| status.expires_at >= now_secs())
                .map(|status| LocalTokenPending {
                    hint: status.snapshot.token_hint.clone(),
                    path: status.snapshot.path.clone(),
                    action: status.snapshot.action.clone(),
                    user_email: status.snapshot.user_email.clone(),
                    approved: status.approved,
                    expires_in: (status.expires_at - now_secs()).round() as i64,
                })
                .collect()
        }
    }

    #[test]
    fn extra_table_redeems_a_token_the_gateway_never_saw() {
        let table = FakeLocal::with("folder-token-aaaaaaaa", "");
        assert!(table.approve_record("folder-token-aaaaaaaa").is_some());
        assert!(table.status_of("folder-token-aaaaaaaa").unwrap().approved);
        assert_eq!(table.pending_records()[0].path, "/tmp/notes");
        assert!(table.deny_record("folder-token-aaaaaaaa").is_some());
        assert!(table.status_of("folder-token-aaaaaaaa").is_none());
    }
}
