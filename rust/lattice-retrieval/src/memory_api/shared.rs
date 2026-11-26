use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Arc;

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::{AuthState, Identity, OrderedMap, ScopeMode};
use lattice_core::db::{RuntimeConfig, Store};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use lattice_core::CoreError;

use super::graph_native;
use super::self_model_write;
use rusqlite::Connection;
use serde::Serialize;
use serde_json::Value;

use super::wsos;

pub const MAX_BODY_BYTES: usize = 4 * 1024 * 1024;

pub type NowFn = Arc<dyn Fn() -> String + Send + Sync>;

pub type UtcNowFn = Arc<dyn Fn() -> f64 + Send + Sync>;

fn default_brain_dir() -> PathBuf {
    for name in ["LATTICEAI_OBSIDIAN_VAULT_DIR", "LATTICEAI_BRAIN_DIR"] {
        if let Some(value) = std::env::var(name).ok().filter(|v| !v.is_empty()) {
            return PathBuf::from(value);
        }
    }
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default()
        .join(".ltcai-brain")
}

#[derive(Clone)]
pub struct BrainState {
    auth: Arc<AuthState>,
    config: RuntimeConfig,
    store: Arc<Store>,
    graph: Option<GraphWriter>,
    seam: Option<WorkerSeamClient>,
    enable_graph: bool,
    now: NowFn,
    now_utc: UtcNowFn,
    active_model: Option<NowFn>,
    brain_dir: PathBuf,
    synthesis_pending: Arc<AtomicI64>,
}

impl std::fmt::Debug for BrainState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BrainState")
            .field("data_dir", &self.config.data_dir())
            .field("enable_graph", &self.enable_graph)
            .field("seam", &self.seam.as_ref().map(WorkerSeamClient::origin))
            .finish()
    }
}

impl BrainState {
    pub fn new(auth: Arc<AuthState>, config: RuntimeConfig, store: Arc<Store>) -> Self {
        Self {
            auth,
            config,
            store,
            graph: None,
            seam: None,
            enable_graph: true,
            now: Arc::new(now_iso),
            now_utc: Arc::new(crate::brain_api::sampling::now_utc_secs),
            active_model: None,
            brain_dir: default_brain_dir(),
            synthesis_pending: Arc::new(AtomicI64::new(0)),
        }
    }

    pub fn with_brain_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.brain_dir = dir.into();
        self
    }

    pub fn with_synthesis_pending(self, pending: i64) -> Self {
        self.synthesis_pending.store(pending, Ordering::Relaxed);
        self
    }

    pub fn brain_dir(&self) -> &Path {
        &self.brain_dir
    }

    pub fn synthesis_trigger(&self) -> (i64, i64, i64) {
        let threshold = 25;
        let pending = self.synthesis_pending.load(Ordering::Relaxed);
        (threshold, pending, threshold - pending)
    }

    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        crate::vector_hnsw::bind_worker_origin(seam.origin());
        self.seam = Some(seam);
        self
    }

    pub fn graph(&self) -> Option<&GraphWriter> {
        self.graph.as_ref()
    }

    pub fn with_graph_enabled(mut self, enabled: bool) -> Self {
        self.enable_graph = enabled;
        self
    }

    pub fn with_clock(mut self, now: NowFn) -> Self {
        self.now = now;
        self
    }

    pub fn with_active_model(mut self, active_model: NowFn) -> Self {
        self.active_model = Some(active_model);
        self
    }

    pub fn active_model(&self) -> String {
        self.active_model
            .as_ref()
            .map(|getter| getter())
            .unwrap_or_default()
    }

    pub fn now(&self) -> String {
        (self.now)()
    }

    pub fn with_utc_clock(mut self, now_utc: UtcNowFn) -> Self {
        self.now_utc = now_utc;
        self
    }

    pub fn now_utc(&self) -> f64 {
        (self.now_utc)()
    }

    pub fn data_dir(&self) -> &Path {
        self.config.data_dir()
    }

    pub fn graph_enabled(&self) -> bool {
        self.enable_graph
    }

    pub fn auth(&self) -> &Arc<AuthState> {
        &self.auth
    }

    pub fn store(&self) -> &Arc<Store> {
        &self.store
    }

    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    pub fn require_user(&self, headers: &HeaderMap) -> Result<Identity, Response> {
        self.auth.require_user(headers)
    }

    pub fn gate_read(
        &self,
        headers: &HeaderMap,
        query: Option<&str>,
        user: &str,
    ) -> Result<Option<String>, Response> {
        self.gate(headers, query, user, ScopeMode::Read)
    }

    pub fn gate_write(
        &self,
        headers: &HeaderMap,
        query: Option<&str>,
        user: &str,
    ) -> Result<Option<String>, Response> {
        self.gate(headers, query, user, ScopeMode::Write)
    }

    fn gate(
        &self,
        headers: &HeaderMap,
        query: Option<&str>,
        user: &str,
        mode: ScopeMode,
    ) -> Result<Option<String>, Response> {
        let resolver = wsos::Resolver::new(&self.store, self.data_dir());
        lattice_auth::resolve_workspace_scope(
            headers,
            query,
            None,
            user,
            Some(&resolver),
            mode,
            false,
        )
    }

    pub fn graph_viewer(&self, user: &str) -> Option<String> {
        self.auth.graph_viewer(user)
    }

    pub fn scoped_workspace(
        &self,
        workspace_id: Option<&str>,
        user: &str,
    ) -> Option<BTreeSet<String>> {
        let viewer = self.graph_viewer(user);
        match workspace_id.filter(|id| !id.is_empty()) {
            Some(id) => lattice_core::workspace_scope(Some(id), viewer.as_deref()),
            None => viewer.and_then(|v| self.allowed_workspaces(&v)),
        }
    }

    pub fn allowed_workspaces(&self, user: &str) -> Option<BTreeSet<String>> {
        let state = wsos::load(&self.store, self.data_dir());
        let identity = if user.is_empty() { None } else { Some(user) };
        let mut allowed = BTreeSet::new();
        for id in wsos::workspace_ids(&state) {
            if wsos::has_permission(&state, &id, identity, "read") {
                allowed.insert(id);
            }
        }
        if allowed.is_empty() && identity.is_none() {
            return None;
        }
        Some(lattice_core::with_viewer(
            allowed,
            self.graph_viewer(user).as_deref(),
        ))
    }

    pub async fn read<T, F>(&self, work: F) -> Result<T, Response>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> Result<T, CoreError> + Send + 'static,
    {
        self.store.read(work).await.map_err(store_failed)
    }

    pub async fn mutate(&self, op: &str, args: Value) -> Result<Value, Response> {
        self.mutate_detailed(op, args)
            .await
            .map_err(SeamRefusal::into_response)
    }

    pub async fn mutate_detailed(&self, op: &str, args: Value) -> Result<Value, SeamRefusal> {
        let Some(graph) = self.graph.clone() else {
            return Err(SeamRefusal {
                status: 503,
                detail: WRITE_ENGINE_UNCONFIGURED.to_string(),
            });
        };
        if graph_native::is_writer_op(op) {
            let op = op.to_string();
            return tokio::task::spawn_blocking(move || graph_native::dispatch(&graph, &op, &args))
                .await
                .map_err(|error| SeamRefusal {
                    status: 500,
                    detail: error.to_string(),
                })?
                .map_err(|error| SeamRefusal {
                    status: graph_native::status_for(&error),
                    detail: error.to_string(),
                });
        }
        self_model_write::dispatch(self, &graph, op, args).await
    }
}

pub const WRITE_ENGINE_UNCONFIGURED: &str =
    "the knowledge-graph write engine is not configured on this host";

#[derive(Debug, Clone)]
pub struct SeamRefusal {
    pub status: u16,
    pub detail: String,
}

impl SeamRefusal {
    pub fn into_response(self) -> Response {
        detail_response(self.status, &self.detail)
    }
}

pub fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    local_iso(secs).unwrap_or_else(|| "1970-01-01T00:00:00".to_string())
}

fn local_iso(utc_secs: i64) -> Option<String> {
    lattice_core::graph_write::clock::local_iso_for(utc_secs)
}

pub fn lang_of(headers: &HeaderMap) -> &'static str {
    lattice_core::messages::resolve_language_from_headers(
        headers
            .iter()
            .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v))),
    )
}

pub fn ok_json<T: Serialize>(body: &T) -> Response {
    match serde_json::to_string(body) {
        Ok(text) => lattice_auth::response::json_response(StatusCode::OK, &text, None),
        Err(error) => detail_response(500, &format!("could not render the answer: {error}")),
    }
}

pub fn detail_response(status: u16, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", Value::String(detail.to_string()));
    let text = serde_json::to_string(&body).unwrap_or_else(|_| "{\"detail\":\"\"}".to_string());
    lattice_auth::response::json_response(status_of(status), &text, None)
}

pub fn message_response(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    detail_response(status, &lattice_core::messages::text(id, lang, args))
}

pub fn validation_response(kind: &str, loc: Value, msg: &str, input: Value) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", Value::String(kind.to_string()));
    entry.insert("loc", loc);
    entry.insert("msg", Value::String(msg.to_string()));
    entry.insert("input", input);
    let mut body = OrderedMap::new();
    body.insert("detail", serde_json::json!([entry]));
    let text = serde_json::to_string(&body).unwrap_or_default();
    lattice_auth::response::json_response(StatusCode::UNPROCESSABLE_ENTITY, &text, None)
}

pub fn missing_query(field: &str) -> Response {
    validation_response(
        "missing",
        serde_json::json!(["query", field]),
        "Field required",
        Value::Null,
    )
}

pub fn missing_body(field: &str, input: &Value) -> Response {
    validation_response(
        "missing",
        serde_json::json!(["body", field]),
        "Field required",
        input.clone(),
    )
}

fn store_failed(error: CoreError) -> Response {
    detail_response(500, &format!("knowledge store read failed: {error}"))
}

fn status_of(status: u16) -> StatusCode {
    StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR)
}

#[path = "request_parse.rs"]
mod request_parse;
pub use request_parse::*;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_language_comes_off_the_headers_the_catalog_names() {
        let mut headers = HeaderMap::new();
        assert_eq!(lang_of(&headers), "ko");
        headers.insert("x-lattice-language", "en".parse().expect("header"));
        assert_eq!(lang_of(&headers), "en");
    }
}
