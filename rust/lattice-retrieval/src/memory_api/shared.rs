//! What every brain route family needs before it can answer anything.
//!
//! One [`BrainState`] carries the four collaborators the Python routers were
//! handed at composition time — the auth guard closures, the workspace read/
//! write gate, the knowledge-graph store, and the engine the graph writes run
//! on — so a handler in any of the six families reaches them the same way. The
//! response helpers below exist for the same reason: FastAPI's
//! refusal bodies are a client contract (`frontend/src/api/base.ts` reads
//! `detail`), and one renderer means they cannot drift between families.
//!
//! Two Python details are load-bearing and reproduced exactly:
//!
//! * **the gate never answers `None`.** `PlatformRuntime.gate_read` calls
//!   `WorkspaceService.resolve_read_scope(None, user)`, which falls back to
//!   `store._active_workspace_id()` — `"personal"` on a fresh install. Handing
//!   the services `None` instead would take the *other* branch in
//!   `MemoryService.manager` (project memories become "everything not
//!   personal") and silently change what the Memory view shows.
//! * **validation runs before the guard.** FastAPI parses the body/query into
//!   the pydantic model first, so an anonymous `POST /api/memory/clear {}` is a
//!   422 about `scope`, not a 401 — `memory_brain.json` case
//!   `clear_auth_denied` pins it.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Arc;

use axum::body::Body;
use axum::http::{HeaderMap, StatusCode, Uri};
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
use serde_json::{Map, Value};

use super::wsos;

/// Longest body any of these routes accepts, matching the retrieval routes.
pub const MAX_BODY_BYTES: usize = 4 * 1024 * 1024;

/// `latticeai.core.timeutil.now_iso`, injectable so a test can freeze it.
pub type NowFn = Arc<dyn Fn() -> String + Send + Sync>;

/// UTC epoch seconds, injectable for the same reason [`NowFn`] is.
///
/// [`NowFn`] freezes the stamps a body *prints*; this freezes the ones it
/// *measures against*. The briefing's health report is the reason it exists:
/// staleness is `now - 45 days` versus each node's `updated_at`, so a fixture
/// whose nodes are all stamped on one day grades "excellent" until that day is
/// 45 days old and then grades "good" — a golden that expires without anyone
/// touching it.
pub type UtcNowFn = Arc<dyn Fn() -> f64 + Send + Sync>;

/// `p_reinforce.BRAIN_DIR` — same env fallbacks, evaluated once at construct.
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

/// Everything the six brain families read, in one clonable handle.
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
    /// The state a gateway builds once and clones into every family router.
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

    /// Override the P-Reinforce vault root (tests pin `HOME` / `LATTICEAI_BRAIN_DIR`).
    pub fn with_brain_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.brain_dir = dir.into();
        self
    }

    /// Seed the in-process synthesis trigger (Python keeps this on the service).
    pub fn with_synthesis_pending(self, pending: i64) -> Self {
        self.synthesis_pending.store(pending, Ordering::Relaxed);
        self
    }

    /// The markdown vault root the garden routes write.
    pub fn brain_dir(&self) -> &Path {
        &self.brain_dir
    }

    /// `SynthesisTrigger.status` for a freshly constructed synthesizer, plus
    /// any pending count the host (or a test) injected.
    pub fn synthesis_trigger(&self) -> (i64, i64, i64) {
        let threshold = 25;
        let pending = self.synthesis_pending.load(Ordering::Relaxed);
        (threshold, pending, threshold - pending)
    }

    /// Native write engine (W3b). Preferred over [`BrainState::with_seam`].
    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    /// The worker seam every graph write is delegated over (plan §2).
    pub fn with_seam(mut self, seam: WorkerSeamClient) -> Self {
        self.seam = Some(seam);
        self
    }

    /// The native writer, when the host wired one.
    pub fn graph(&self) -> Option<&GraphWriter> {
        self.graph.as_ref()
    }

    /// `LATTICEAI_ENABLE_GRAPH`, as the composition root resolved it.
    pub fn with_graph_enabled(mut self, enabled: bool) -> Self {
        self.enable_graph = enabled;
        self
    }

    /// Freeze the clock — the `generated_at` fields are `@ts` in every fixture.
    pub fn with_clock(mut self, now: NowFn) -> Self {
        self.now = now;
        self
    }

    /// `active_model_getter` — which LLM is loaded right now, or `""`.
    ///
    /// Injected rather than read, because only the process that holds the model
    /// knows; the Brain Brief reports it and is deliberately independent of it.
    pub fn with_active_model(mut self, active_model: NowFn) -> Self {
        self.active_model = Some(active_model);
        self
    }

    /// The loaded model's name, or `""` when nothing was injected.
    pub fn active_model(&self) -> String {
        self.active_model
            .as_ref()
            .map(|getter| getter())
            .unwrap_or_default()
    }

    /// `latticeai.core.timeutil.now_iso()`.
    pub fn now(&self) -> String {
        (self.now)()
    }

    /// Freeze the instant the briefing *measures* against.
    ///
    /// Defaults to [`crate::brain_api::sampling::now_utc_secs`], so a host that
    /// does not call this gets the real clock and byte-identical answers.
    pub fn with_utc_clock(mut self, now_utc: UtcNowFn) -> Self {
        self.now_utc = now_utc;
        self
    }

    /// `datetime.now(timezone.utc).timestamp()` — UTC epoch seconds.
    pub fn now_utc(&self) -> f64 {
        (self.now_utc)()
    }

    /// Where `workspace_os.json` and `knowledge_graph.sqlite` live.
    pub fn data_dir(&self) -> &Path {
        self.config.data_dir()
    }

    /// Whether this Brain has a knowledge graph at all.
    pub fn graph_enabled(&self) -> bool {
        self.enable_graph
    }

    /// The shared identity state (sessions, roles, rate limits).
    pub fn auth(&self) -> &Arc<AuthState> {
        &self.auth
    }

    /// The one SQLite file the product has.
    pub fn store(&self) -> &Arc<Store> {
        &self.store
    }

    /// The graph-write seam, when the host wired one.
    pub fn seam(&self) -> Option<&WorkerSeamClient> {
        self.seam.as_ref()
    }

    /// `require_user(request)` — 401 with the catalog's own sentence.
    pub fn require_user(&self, headers: &HeaderMap) -> Result<Identity, Response> {
        self.auth.require_user(headers)
    }

    /// `PlatformRuntime.gate_read` — resolve *and* authorize the read scope.
    pub fn gate_read(
        &self,
        headers: &HeaderMap,
        query: Option<&str>,
        user: &str,
    ) -> Result<Option<String>, Response> {
        self.gate(headers, query, user, ScopeMode::Read)
    }

    /// `PlatformRuntime.gate_write` — the same, gated on `write`.
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

    /// `PlatformRuntime.allowed_scopes(user)` — the set a global index is
    /// filtered against, or `None` for the unscoped local owner.
    pub fn allowed_workspaces(&self, user: &str) -> Option<BTreeSet<String>> {
        let state = wsos::load(&self.store, self.data_dir());
        let identity = if user.is_empty() { None } else { Some(user) };
        let mut allowed = BTreeSet::new();
        for id in wsos::workspace_ids(&state) {
            if wsos::has_permission(&state, &id, identity, "read") {
                allowed.insert(id);
            }
        }
        // `None` is "unscoped local mode" to every consumer, so an
        // authenticated caller never degrades into it (workspace_service.py:87).
        if allowed.is_empty() && identity.is_none() {
            return None;
        }
        Some(allowed)
    }

    /// One blocking read of the graph, off the reactor, rendered as a 500 the
    /// same way the native retrieval routes render theirs.
    pub async fn read<T, F>(&self, work: F) -> Result<T, Response>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> Result<T, CoreError> + Send + 'static,
    {
        self.store.read(work).await.map_err(store_failed)
    }

    /// Run one whitelisted graph mutation on the native write engine.
    ///
    /// Every one of them is native since v11.7.0. The refusal's own status is
    /// kept rather than flattened into a blanket 502, so a caller still sees
    /// `400 op_not_allowed` as a 400.
    pub async fn mutate(&self, op: &str, args: Value) -> Result<Value, Response> {
        self.mutate_detailed(op, args)
            .await
            .map_err(SeamRefusal::into_response)
    }

    /// [`BrainState::mutate`], keeping the refusal's status and reason apart.
    ///
    /// The Self-Model routes have to *read* the reason to pick their own
    /// catalog id (`not_found` is a 404 about the fact, everything else a 400),
    /// and parsing it back out of a rendered body would be a second, lossier
    /// error channel.
    ///
    /// Two families of op meet here and they are written by different code for
    /// a reason: [`graph_native::dispatch`] covers the eleven ops the shared
    /// write engine implements, and [`self_model_write::dispatch`] covers the
    /// five this crate owns (the Self-Model's four writes and the
    /// contradiction stamps), because those write *review items* as well as
    /// nodes and the review queue is not `lattice-core`'s business.
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

/// The one sentence a caller gets when the graph is on but no writer was bound.
///
/// A mis-wired host, not a request problem — and the same wording
/// `knowledge_graph_api::writes` answers with, because it is the same fault.
pub const WRITE_ENGINE_UNCONFIGURED: &str =
    "the knowledge-graph write engine is not configured on this host";

/// What the graph-write refused with.
#[derive(Debug, Clone)]
pub struct SeamRefusal {
    /// The worker's own status, or `502` when the hop itself failed.
    pub status: u16,
    /// The reason, as the worker stated it.
    pub detail: String,
}

impl SeamRefusal {
    /// Render the refusal as the answer the caller receives.
    pub fn into_response(self) -> Response {
        detail_response(self.status, &self.detail)
    }
}

/// `latticeai.core.timeutil.now_iso()` — `datetime.now().isoformat("seconds")`.
///
/// Naive **local** time with no offset, because that is what every stamp this
/// crate compares against was written with. There is no timezone crate in this
/// workspace, so the conversion goes through `localtime_r(3)`, the same route
/// `routes::naive_local_now` already takes for the recency decay.
pub fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    local_iso(secs).unwrap_or_else(|| "1970-01-01T00:00:00".to_string())
}

#[cfg(unix)]
fn local_iso(utc_secs: i64) -> Option<String> {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    if unsafe { libc::localtime_r(&stamp, &mut broken) }.is_null() {
        return None;
    }
    Some(format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        broken.tm_year + 1900,
        broken.tm_mon + 1,
        broken.tm_mday,
        broken.tm_hour,
        broken.tm_min,
        broken.tm_sec,
    ))
}

/// Non-unix hosts have no `localtime_r`; this crate targets macOS and Linux.
#[cfg(not(unix))]
fn local_iso(_utc_secs: i64) -> Option<String> {
    None
}

/// The language the catalog renders a refusal in.
pub fn lang_of(headers: &HeaderMap) -> &'static str {
    lattice_core::messages::resolve_language_from_headers(
        headers
            .iter()
            .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v))),
    )
}

/// A 200 whose body keeps Python's dict order.
pub fn ok_json<T: Serialize>(body: &T) -> Response {
    match serde_json::to_string(body) {
        Ok(text) => lattice_auth::response::json_response(StatusCode::OK, &text, None),
        Err(error) => detail_response(500, &format!("could not render the answer: {error}")),
    }
}

/// `HTTPException(status, detail=...)` as Starlette renders it.
pub fn detail_response(status: u16, detail: &str) -> Response {
    let mut body = OrderedMap::new();
    body.insert("detail", Value::String(detail.to_string()));
    let text = serde_json::to_string(&body).unwrap_or_else(|_| "{\"detail\":\"\"}".to_string());
    lattice_auth::response::json_response(status_of(status), &text, None)
}

/// `http_error(status, id, language, **args)` — the localized refusal.
pub fn message_response(status: u16, id: &str, lang: &str, args: &[(&str, &str)]) -> Response {
    detail_response(status, &lattice_core::messages::text(id, lang, args))
}

/// FastAPI's `{"detail": [{type, loc, msg, input}]}`, one entry.
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

/// A required query parameter that was not sent.
pub fn missing_query(field: &str) -> Response {
    validation_response(
        "missing",
        serde_json::json!(["query", field]),
        "Field required",
        Value::Null,
    )
}

/// A required body field that was not sent; `input` is the whole body.
pub fn missing_body(field: &str, input: &Value) -> Response {
    validation_response(
        "missing",
        serde_json::json!(["body", field]),
        "Field required",
        input.clone(),
    )
}

/// A 500 that names the store failure rather than swallowing it.
fn store_failed(error: CoreError) -> Response {
    detail_response(500, &format!("knowledge store read failed: {error}"))
}

fn status_of(status: u16) -> StatusCode {
    StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR)
}

// ── query + body parsing, with FastAPI's refusal shapes ─────────────────────

/// The parsed query string of one request.
#[derive(Debug, Clone, Default)]
pub struct Query {
    values: Vec<(String, String)>,
}

impl Query {
    /// Parse `?a=1&b=2`; the *last* value wins, as Starlette's `QueryParams` do.
    pub fn from_uri(uri: &Uri) -> Self {
        let mut values = Vec::new();
        if let Some(raw) = uri.query() {
            for pair in raw.split('&').filter(|part| !part.is_empty()) {
                let (key, value) = match pair.split_once('=') {
                    Some((key, value)) => (key, value),
                    None => (pair, ""),
                };
                values.push((percent_decode(key), percent_decode(value)));
            }
        }
        Self { values }
    }

    /// The last value sent for `name`, if any.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.values
            .iter()
            .rev()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `name: str = default` with an optional `max_length` constraint.
    pub fn string(
        &self,
        name: &str,
        default: &str,
        max_length: Option<usize>,
    ) -> Result<String, Response> {
        let value = self.get(name).unwrap_or(default).to_string();
        if let Some(limit) = max_length {
            if value.chars().count() > limit {
                return Err(validation_response(
                    "string_too_long",
                    serde_json::json!(["query", name]),
                    &format!("String should have at most {limit} characters"),
                    Value::String(value),
                ));
            }
        }
        Ok(value)
    }

    /// `name: str` with no default — 422 `missing` when absent.
    pub fn required_string(
        &self,
        name: &str,
        max_length: Option<usize>,
    ) -> Result<String, Response> {
        match self.get(name) {
            None => Err(missing_query(name)),
            Some(_) => self.string(name, "", max_length),
        }
    }

    /// `name: int = default`, optionally bounded by `ge` / `le`.
    pub fn int(
        &self,
        name: &str,
        default: i64,
        ge: Option<i64>,
        le: Option<i64>,
    ) -> Result<i64, Response> {
        let Some(raw) = self.get(name) else {
            return Ok(default);
        };
        let parsed: i64 = raw.trim().parse().map_err(|_| {
            validation_response(
                "int_parsing",
                serde_json::json!(["query", name]),
                "Input should be a valid integer, unable to parse string as an integer",
                Value::String(raw.to_string()),
            )
        })?;
        if let Some(bound) = ge {
            if parsed < bound {
                return Err(validation_response(
                    "greater_than_equal",
                    serde_json::json!(["query", name]),
                    &format!("Input should be greater than or equal to {bound}"),
                    Value::String(raw.to_string()),
                ));
            }
        }
        if let Some(bound) = le {
            if parsed > bound {
                return Err(validation_response(
                    "less_than_equal",
                    serde_json::json!(["query", name]),
                    &format!("Input should be less than or equal to {bound}"),
                    Value::String(raw.to_string()),
                ));
            }
        }
        Ok(parsed)
    }

    /// The raw query string a workspace selector is read out of.
    pub fn workspace(&self) -> Option<&str> {
        self.get(lattice_auth::WORKSPACE_PARAM)
    }
}

/// `%xx` and `+` decoding, the two escapes a browser actually sends.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.replace('+', " ").into_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// One request body, parsed as the JSON object a pydantic model needs.
///
/// An absent body is `{}` — the shape FastAPI gives a model whose fields all
/// have defaults, and the reading every `POST` in these families starts from.
pub async fn json_body(body: Body) -> Result<Value, Response> {
    let bytes = axum::body::to_bytes(body, MAX_BODY_BYTES)
        .await
        .map_err(|error| {
            detail_response(400, &format!("could not read the request body: {error}"))
        })?;
    if bytes.is_empty() {
        return Ok(Value::Object(Map::new()));
    }
    // The `ctx.error` text FastAPI carries here is CPython's own decoder
    // message and changes between releases, so it is deliberately not
    // reproduced (WP-I2 §7.8 made the same call for the auth bodies).
    let parsed: Value = serde_json::from_slice(&bytes).map_err(|_| {
        validation_response(
            "json_invalid",
            serde_json::json!(["body", 0]),
            "JSON decode error",
            serde_json::json!({}),
        )
    })?;
    if !parsed.is_object() {
        return Err(validation_response(
            "model_attributes_type",
            serde_json::json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
        ));
    }
    Ok(parsed)
}

// ── the readings a pydantic field gives a handler ───────────────────────────

/// `field: str = ""` — anything that is not a string reads as the default.
pub fn body_str(body: &Value, name: &str, default: &str) -> String {
    match body.get(name) {
        Some(Value::String(text)) => text.clone(),
        _ => default.to_string(),
    }
}

/// `field: str` with no default — 422 `missing` when the key is absent.
pub fn body_required_str(body: &Value, name: &str) -> Result<String, Response> {
    match body.get(name) {
        Some(Value::String(text)) => Ok(text.clone()),
        Some(other) => Err(validation_response(
            "string_type",
            serde_json::json!(["body", name]),
            "Input should be a valid string",
            other.clone(),
        )),
        None => Err(missing_body(name, body)),
    }
}

/// `field: int = default`.
pub fn body_int(body: &Value, name: &str, default: i64) -> i64 {
    match body.get(name) {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        _ => default,
    }
}

/// `field: bool = default`.
pub fn body_bool(body: &Value, name: &str, default: bool) -> bool {
    match body.get(name) {
        Some(Value::Bool(flag)) => *flag,
        _ => default,
    }
}

/// `field: Optional[bool] = None`.
pub fn body_opt_bool(body: &Value, name: &str) -> Option<bool> {
    match body.get(name) {
        Some(Value::Bool(flag)) => Some(*flag),
        _ => None,
    }
}

/// `field: Optional[str] = None`.
pub fn body_opt_str(body: &Value, name: &str) -> Option<String> {
    match body.get(name) {
        Some(Value::String(text)) => Some(text.clone()),
        _ => None,
    }
}

/// `field: List[str] = []`.
pub fn body_str_list(body: &Value, name: &str) -> Vec<String> {
    match body.get(name) {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| item.as_str().map(str::to_string))
            .collect(),
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_query_string_is_read_the_way_starlette_reads_it() {
        let uri: Uri = "/x?source=workspace&limit=5&limit=7&q=%ED%95%9C%EA%B8%80&flag"
            .parse()
            .expect("uri");
        let query = Query::from_uri(&uri);
        assert_eq!(query.get("source"), Some("workspace"));
        assert_eq!(query.int("limit", 50, None, None).expect("int"), 7);
        assert_eq!(query.get("q"), Some("한글"));
        assert_eq!(query.get("flag"), Some(""));
        assert_eq!(query.int("missing", 3, None, None).expect("default"), 3);
    }

    #[test]
    fn a_bad_query_value_is_a_422_that_names_the_field() {
        let uri: Uri = "/x?limit=abc&q=toolong".parse().expect("uri");
        let query = Query::from_uri(&uri);
        assert_eq!(
            query.int("limit", 1, None, None).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query.string("q", "", Some(3)).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query.required_string("ts", None).unwrap_err().status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            query
                .int("limit2", 1, Some(1), None)
                .expect("absent is the default"),
            1
        );
        let bounded: Uri = "/x?limit=0&other=99".parse().expect("uri");
        let bounded = Query::from_uri(&bounded);
        assert!(bounded.int("limit", 8, Some(1), Some(20)).is_err());
        assert!(bounded.int("other", 8, Some(1), Some(20)).is_err());
    }

    #[test]
    fn the_pydantic_readings_match_their_defaults() {
        let body = serde_json::json!({
            "query": "x", "limit": 3, "confirm": true, "ids": ["a", 1], "dry_run": false,
        });
        assert_eq!(body_str(&body, "query", ""), "x");
        assert_eq!(body_str(&body, "absent", "d"), "d");
        assert_eq!(body_int(&body, "limit", 20), 3);
        assert_eq!(body_int(&body, "absent", 20), 20);
        assert!(body_bool(&body, "confirm", false));
        assert!(!body_bool(&body, "absent", false));
        assert_eq!(body_opt_bool(&body, "dry_run"), Some(false));
        assert_eq!(body_opt_bool(&body, "absent"), None);
        assert_eq!(body_opt_str(&body, "query"), Some("x".to_string()));
        assert_eq!(body_opt_str(&body, "absent"), None);
        assert_eq!(body_str_list(&body, "ids"), vec!["a".to_string()]);
        assert!(body_str_list(&body, "absent").is_empty());
        assert_eq!(body_required_str(&body, "query").expect("present"), "x");
        assert!(body_required_str(&body, "absent").is_err());
        assert!(body_required_str(&body, "limit").is_err());
    }

    #[test]
    fn the_refusal_bodies_are_the_python_ones() {
        let response = detail_response(400, "clear requires confirm=true");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response
                .headers()
                .get(axum::http::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok()),
            Some("application/json")
        );
        assert_eq!(
            message_response(404, "memory.unknown_source", "en", &[("source", "x")]).status(),
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            missing_body("scope", &serde_json::json!({})).status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            missing_query("source").status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }

    #[test]
    fn a_body_is_the_object_pydantic_would_have_seen() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime");
        runtime.block_on(async {
            assert_eq!(
                json_body(Body::empty()).await.expect("empty is {}"),
                serde_json::json!({})
            );
            assert_eq!(
                json_body(Body::from("{\"a\":1}")).await.expect("object"),
                serde_json::json!({"a": 1})
            );
            assert_eq!(
                json_body(Body::from("[1]")).await.unwrap_err().status(),
                StatusCode::UNPROCESSABLE_ENTITY
            );
            assert_eq!(
                json_body(Body::from("{nope")).await.unwrap_err().status(),
                StatusCode::UNPROCESSABLE_ENTITY
            );
        });
    }

    #[test]
    fn the_language_comes_off_the_headers_the_catalog_names() {
        let mut headers = HeaderMap::new();
        assert_eq!(lang_of(&headers), "ko");
        headers.insert("x-lattice-language", "en".parse().expect("header"));
        assert_eq!(lang_of(&headers), "en");
    }
}
