use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

pub(crate) mod enrich;
mod folder;
pub mod http;
pub(crate) mod ingest;
mod interop;
mod jobs_store;
mod knowledge;
mod local;
pub(crate) mod prune;
pub(crate) mod upload_parse;
mod watch_bridge;

pub use watch_bridge::{resume_watches, scan_watches};

pub use http::{detail, language, ok, FieldSpec, Kind, Model, Query};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/local-agent/status"),
    ("GET", "/local/list"),
    ("POST", "/local/list"),
    ("POST", "/local/read"),
    ("GET", "/local/serve"),
    ("POST", "/local/write"),
    ("GET", "/api/ingestion/jobs"),
    ("GET", "/api/ingestion/jobs/:job_id"),
    ("POST", "/api/ingestion/jobs/:job_id/resume"),
    ("POST", "/api/ingestion/folder"),
    ("POST", "/api/ingestion/folder/prune"),
    ("POST", "/api/ingestion/obsidian"),
    ("GET", "/api/ingestion/interop"),
    ("POST", "/api/ingestion/interop"),
    ("GET", "/api/ingestion/watch"),
    ("POST", "/api/ingestion/watch"),
    ("DELETE", "/api/ingestion/watch"),
    ("GET", "/knowledge-graph/local/roots"),
    ("GET", "/knowledge-graph/local/sources"),
    ("GET", "/knowledge-graph/local/health"),
    ("GET", "/knowledge-graph/local/watch/status"),
    ("POST", "/knowledge-graph/local/watch/stop"),
    ("POST", "/knowledge-graph/local/tree"),
    ("POST", "/knowledge-graph/local/audit"),
    ("POST", "/knowledge-graph/local/index"),
    ("POST", "/upload/document"),
];

pub const APPROVAL_TTL_SECS: u64 = 5 * 60;

pub const WRITE_BLOCKED_PREFIXES: &[&str] = &[
    "/etc/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/System/",
    "/private/etc/",
    "/Library/LaunchDaemons/",
    "/Library/LaunchAgents/",
];

const ACTION_LABELS: &[(&str, &str)] = &[
    ("list", "폴더 목록 보기"),
    ("read", "파일 읽기"),
    ("write", "파일 쓰기"),
];

#[derive(Debug)]
pub struct LocalApprovals {
    inner: Mutex<HashMap<String, Approval>>,
}

#[derive(Debug, Clone)]
struct Approval {
    path: String,
    action: String,
    user_email: String,
    expires_at: f64,
    approved: bool,
    content_hash: Option<String>,
    token_hint: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ApprovalView {
    pub path: String,
    pub action: String,
    pub user_email: String,
    pub approved: bool,
    pub expires_at: f64,
    pub token_hint: String,
}

impl Approval {
    fn view(&self) -> ApprovalView {
        ApprovalView {
            path: self.path.clone(),
            action: self.action.clone(),
            user_email: self.user_email.clone(),
            approved: self.approved,
            expires_at: self.expires_at,
            token_hint: self.token_hint.clone(),
        }
    }
}

impl Default for LocalApprovals {
    fn default() -> Self {
        Self {
            inner: Mutex::new(HashMap::new()),
        }
    }
}

impl LocalApprovals {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn normalize(path: &str) -> String {
        let expanded = expand_user(path);
        std::fs::canonicalize(&expanded)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or(expanded)
    }

    pub fn probe(&self, path: &str, action: &str, user_email: &str, content: &str) -> Value {
        let normalized = Self::normalize(path);
        let token = token_urlsafe(24);
        let token_hint = token.chars().take(8).collect::<String>();
        let record = Approval {
            path: normalized,
            action: action.to_string(),
            user_email: user_email.to_string(),
            expires_at: now_secs() + APPROVAL_TTL_SECS as f64,
            approved: false,
            content_hash: (action == "write").then(|| sha256_hex(content.as_bytes())),
            token_hint,
        };
        if let Ok(mut map) = self.inner.lock() {
            map.insert(sha256_hex(token.as_bytes()), record);
        }
        let label = action_label(action);
        let mut body = OrderedMap::new();
        body.insert("permission_required", json!(true));
        body.insert("path", json!(path));
        body.insert("action", json!(action));
        body.insert("action_label", json!(label));
        body.insert("approval_token", json!(token));
        body.insert("expires_in", json!(APPROVAL_TTL_SECS));
        body.insert(
            "message",
            json!(format!("AI가 '{path}' 에 대한 {label} 권한을 요청합니다.")),
        );
        body.insert(
            "check_status_url",
            json!(format!("/permissions/status/{token}")),
        );
        serde_json::to_value(body).unwrap_or(Value::Null)
    }

    pub fn approve(&self, token: &str) -> bool {
        self.approve_record(token).is_some()
    }

    pub fn approve_record(&self, token: &str) -> Option<ApprovalView> {
        let now = now_secs();
        let Ok(mut map) = self.inner.lock() else {
            return None;
        };
        map.retain(|_, record| record.expires_at >= now);
        let key = resolve_token(&map, token)?;
        let record = map.get_mut(&key)?;
        record.approved = true;
        Some(record.view())
    }

    pub fn deny_record(&self, token: &str) -> Option<ApprovalView> {
        let Ok(mut map) = self.inner.lock() else {
            return None;
        };
        let key = resolve_token(&map, token)?;
        map.remove(&key).map(|record| record.view())
    }

    pub fn status_of(&self, token: &str) -> Option<ApprovalView> {
        let Ok(map) = self.inner.lock() else {
            return None;
        };
        let key = resolve_token(&map, token)?;
        map.get(&key).map(Approval::view)
    }

    pub fn pending_records(&self) -> Vec<ApprovalView> {
        let now = now_secs();
        let Ok(map) = self.inner.lock() else {
            return Vec::new();
        };
        map.values()
            .filter(|record| record.expires_at >= now)
            .map(Approval::view)
            .collect()
    }

    pub fn require(
        &self,
        token: Option<&str>,
        path: &str,
        action: &str,
        user_email: &str,
        content: &str,
    ) -> Result<(), Response> {
        let Some(token) = token.filter(|value| !value.is_empty()) else {
            return Err(detail(403, "파일 접근 승인 토큰이 필요합니다."));
        };
        let normalized = Self::normalize(path);
        if action == "write" {
            ensure_write_allowed(&normalized)?;
        }
        let now = now_secs();
        let key = sha256_hex(token.as_bytes());
        let Ok(mut map) = self.inner.lock() else {
            return Err(detail(
                403,
                "파일 접근 승인이 만료되었거나 유효하지 않습니다.",
            ));
        };
        map.retain(|_, record| record.expires_at >= now);
        let Some(record) = map.get(&key) else {
            return Err(detail(
                403,
                "파일 접근 승인이 만료되었거나 유효하지 않습니다.",
            ));
        };
        if !record.approved {
            return Err(detail(
                403,
                "파일 접근이 아직 승인되지 않았습니다. Discord 또는 UI에서 승인해주세요.",
            ));
        }
        if record.user_email != user_email {
            return Err(detail(
                403,
                "다른 사용자의 파일 접근 승인은 사용할 수 없습니다.",
            ));
        }
        if record.path != normalized || record.action != action {
            return Err(detail(403, "파일 접근 승인 범위가 일치하지 않습니다."));
        }
        if action == "write"
            && record.content_hash.as_deref() != Some(sha256_hex(content.as_bytes()).as_str())
        {
            return Err(detail(403, "승인된 파일 내용과 요청 내용이 다릅니다."));
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct LocalFilesState {
    auth: Arc<AuthState>,
    store: Option<Arc<Store>>,
    config: RuntimeConfig,
    graph: Option<GraphWriter>,
    seam: Option<WorkerSeamClient>,
    permissions: Arc<LocalApprovals>,
    agent_root: PathBuf,
    version: String,
}

impl std::fmt::Debug for LocalFilesState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LocalFilesState")
            .field("graph_enabled", &self.store.is_some())
            .field("seam", &self.seam.as_ref().map(WorkerSeamClient::origin))
            .finish()
    }
}

impl LocalFilesState {
    pub fn new(auth: Arc<AuthState>, store: Option<Arc<Store>>, config: RuntimeConfig) -> Self {
        Self {
            auth,
            store,
            config,
            graph: None,
            seam: None,
            permissions: LocalApprovals::new(),
            agent_root: PathBuf::from(
                std::env::var("LATTICEAI_AGENT_ROOT").unwrap_or_else(|_| "agent_workspace".into()),
            ),
            version: env!("CARGO_PKG_VERSION").to_string(),
        }
    }

    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }

    pub fn graph(&self) -> Option<&GraphWriter> {
        self.graph.as_ref()
    }

    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    pub fn with_permissions(mut self, permissions: Arc<LocalApprovals>) -> Self {
        self.permissions = permissions;
        self
    }

    pub fn with_version(mut self, version: impl Into<String>) -> Self {
        self.version = version.into();
        self
    }

    pub fn permissions(&self) -> &Arc<LocalApprovals> {
        &self.permissions
    }

    pub fn require_user(&self, headers: &HeaderMap) -> Result<String, Response> {
        Ok(self.auth.require_user(headers)?.email)
    }

    pub fn require_local_user(&self, headers: &HeaderMap) -> Result<String, Response> {
        match self.auth.get_current_user(headers) {
            Some(email) if !email.is_empty() => Ok(email),
            _ => Err(detail(401, "로컬 파일 접근은 로그인 세션이 필요합니다.")),
        }
    }

    pub fn require_graph(&self, lang: &str) -> Result<&Arc<Store>, Response> {
        self.store
            .as_ref()
            .ok_or_else(|| http::http_error(503, "capture.ingestion_disabled", lang))
    }
}

pub fn router(state: Arc<LocalFilesState>) -> Router {
    resume_watches(&state);
    Router::new()
        .route("/api/local-agent/status", get(local::agent_status))
        .route("/local/list", get(local::list_get).post(local::list_post))
        .route("/local/read", post(local::read_post))
        .route("/local/serve", get(local::serve))
        .route("/local/write", post(local::write_post))
        .route("/api/ingestion/jobs", get(ingest::jobs))
        .route("/api/ingestion/jobs/{job_id}", get(ingest::job_detail))
        .route(
            "/api/ingestion/jobs/{job_id}/resume",
            post(ingest::job_resume),
        )
        .route("/api/ingestion/folder", post(ingest::folder))
        .route("/api/ingestion/folder/prune", post(ingest::folder_prune))
        .route("/api/ingestion/obsidian", post(ingest::obsidian))
        .route(
            "/api/ingestion/interop",
            get(ingest::interop_status).post(ingest::interop_ingest),
        )
        .route(
            "/api/ingestion/watch",
            get(ingest::watch_status)
                .post(ingest::watch_enable)
                .delete(ingest::watch_disable),
        )
        .route("/knowledge-graph/local/roots", get(knowledge::roots))
        .route("/knowledge-graph/local/sources", get(knowledge::sources))
        .route("/knowledge-graph/local/health", get(knowledge::health))
        .route(
            "/knowledge-graph/local/watch/status",
            get(knowledge::watch_status),
        )
        .route(
            "/knowledge-graph/local/watch/stop",
            post(knowledge::watch_stop),
        )
        .route("/knowledge-graph/local/tree", post(knowledge::tree))
        .route("/knowledge-graph/local/audit", post(knowledge::audit))
        .route("/knowledge-graph/local/index", post(knowledge::index))
        .route("/upload/document", post(ingest::upload_document))
        .with_state(state)
}

pub(crate) fn action_label(action: &str) -> &str {
    ACTION_LABELS
        .iter()
        .find(|(name, _)| *name == action)
        .map(|(_, label)| *label)
        .unwrap_or(action)
}

pub(crate) fn expand_user(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = home_dir() {
            return Path::new(&home).join(rest).to_string_lossy().into_owned();
        }
    } else if path == "~" {
        if let Some(home) = home_dir() {
            return home;
        }
    }
    path.to_string()
}

fn home_dir() -> Option<String> {
    std::env::var("HOME")
        .ok()
        .or_else(|| std::env::var("USERPROFILE").ok())
}

pub(crate) fn ensure_write_allowed(normalized: &str) -> Result<(), Response> {
    let slash = normalized.replace('\\', "/");
    for prefix in WRITE_BLOCKED_PREFIXES {
        let trimmed = prefix.trim_end_matches('/');
        if slash == trimmed || slash.starts_with(prefix) {
            return Err(detail(403, &format!("쓰기 금지 경로입니다: {prefix}")));
        }
    }
    Ok(())
}

fn resolve_token(map: &HashMap<String, Approval>, token: &str) -> Option<String> {
    let direct = sha256_hex(token.as_bytes());
    if map.contains_key(&direct) {
        return Some(direct);
    }
    if token.chars().count() != 8 {
        return None;
    }
    let matches: Vec<String> = map
        .iter()
        .filter(|(_, record)| record.token_hint == token)
        .map(|(key, _)| key.clone())
        .collect();
    (matches.len() == 1).then(|| matches.into_iter().next().expect("len == 1"))
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    lattice_core::sha256_bytes(bytes)
}

pub(crate) fn token_urlsafe(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    if let Ok(mut file) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = file.read_exact(&mut bytes);
    } else {
        let seed = now_secs().to_bits();
        for (index, slot) in bytes.iter_mut().enumerate() {
            *slot = seed.to_le_bytes()[index % 8] ^ (index as u8).wrapping_mul(31);
        }
    }
    base64url(&bytes)
}

fn base64url(bytes: &[u8]) -> String {
    const TABLE: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::new();
    let mut index = 0;
    while index < bytes.len() {
        let b0 = bytes[index];
        let b1 = bytes.get(index + 1).copied().unwrap_or(0);
        let b2 = bytes.get(index + 2).copied().unwrap_or(0);
        let n = ((b0 as u32) << 16) | ((b1 as u32) << 8) | (b2 as u32);
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        if index + 1 < bytes.len() {
            out.push(TABLE[((n >> 6) & 63) as usize] as char);
        }
        if index + 2 < bytes.len() {
            out.push(TABLE[(n & 63) as usize] as char);
        }
        index += 3;
    }
    out
}

pub(crate) fn naive_now() -> String {
    let secs = now_secs().floor() as i64;
    let days = secs.div_euclid(86_400);
    let time = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if m <= 2 { y + 1 } else { y };
    format!(
        "{year:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}",
        time / 3600,
        (time % 3600) / 60,
        time % 60
    )
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub(crate) fn tool_ok(agent_root: &Path, result: Value) -> Response {
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("workspace", json!(agent_root.display().to_string()));
    body.insert("result", result);
    ok(&serde_json::to_value(body).unwrap_or(Value::Null))
}

pub(crate) fn tool_error(detail_text: &str) -> Response {
    detail(400, detail_text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::StatusCode;

    #[test]
    fn the_route_table_includes_native_upload() {
        assert_eq!(MOUNTED.len(), 26);
        assert!(MOUNTED.iter().any(|(_, path)| *path == "/upload/document"));
    }

    #[test]
    fn an_unapproved_token_is_refused() {
        let table = LocalApprovals::new();
        let err = table
            .require(Some("not-a-token"), "/tmp", "list", "a@b", "")
            .unwrap_err();
        assert_eq!(err.status(), StatusCode::FORBIDDEN);
    }

    #[test]
    fn a_minted_then_approved_token_passes() {
        let table = LocalApprovals::new();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_string_lossy().into_owned();
        let probe = table.probe(&path, "list", "a@b.c", "");
        let token = probe["approval_token"].as_str().unwrap().to_string();
        assert!(table.approve(&token));
        table
            .require(Some(&token), &path, "list", "a@b.c", "")
            .unwrap();
    }

    #[test]
    fn the_eight_char_hint_redeems_the_same_token() {
        let table = LocalApprovals::new();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_string_lossy().into_owned();
        let probe = table.probe(&path, "read", "", "");
        let token = probe["approval_token"].as_str().unwrap().to_string();
        let hint: String = token.chars().take(8).collect();
        let view = table.approve_record(&hint).expect("hint redeems");
        assert_eq!(view.action, "read");
        assert_eq!(view.user_email, "");
        assert!(view.approved);
        table
            .require(Some(&token), &path, "read", "", "")
            .expect("full token still works after a hint approve");
        assert_eq!(table.pending_records().len(), 1);
        assert!(table.deny_record(&token).is_some());
        assert!(table.status_of(&token).is_none());
    }

    #[test]
    fn write_blocked_prefixes_match_python() {
        assert!(ensure_write_allowed("/etc/passwd").is_err());
        assert!(ensure_write_allowed("/usr/bin/env").is_err());
        assert!(ensure_write_allowed("/tmp/ok").is_ok());
    }
}
