//! What the chat routes are wired to.
//!
//! `create_chat_router(AppContext)` in Python takes twenty-odd injected
//! callables off one context object. The same shape survives here as one struct
//! of optional seams, and the optionality is load-bearing rather than lazy: a
//! standalone gateway with no Review Center, no Telegram bridge and no funnel
//! counters must still serve chat, and every `None` below is a documented
//! Python default (`review_queue=None`, `on_chat_message=None`,
//! `funnel_metrics=None`, `workspace_service=None`).
//!
//! Two seams are **required in practice** and still typed as options, because
//! the routes have an honest answer without them:
//!
//! * no [`ChatWorker`] ⇒ no model is reachable ⇒ `POST /chat` answers the same
//!   `no_model_loaded` 400 the fixtures pin, rather than 500;
//! * no graph database ⇒ `enable_graph` is false ⇒ the context, document and
//!   hybrid branches are skipped exactly as they are in Python.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_auth::{AuthState, WorkspaceResolver};
use lattice_core::graph_write::GraphWriter;
use serde_json::Value;

use crate::cloud::{EgressAudit, ReviewSink};
use crate::documents::DocumentSessions;
use crate::worker::ChatWorker;

/// `LATTICEAI_PUBLIC_MODEL` / `LATTICEAI_DEFAULT_MODEL`'s fallback.
pub const DEFAULT_PUBLIC_MODEL: &str = "openai:gpt-4o-mini";
/// `LATTICEAI_AUTO_READ_CHAT_PATHS` — off, and the handler refuses anyway.
pub const AUTO_READ_ENV: &str = "LATTICEAI_AUTO_READ_CHAT_PATHS";
/// `LATTICEAI_INGEST_GENERATED` — index generated files into the Brain.
///
/// On by default, and since v11.7.0 that default finally does something: the
/// index write is native ([`crate::intents`]), where it used to be a `POST` to
/// a worker route v11.6.0 retired.
pub const INGEST_GENERATED_ENV: &str = "LATTICEAI_INGEST_GENERATED";

/// Where the audit trail goes.
///
/// `append_audit_event(name, **fields)`: the gateway owns the sink, chat only
/// names events. Absent, the events are dropped — which is what a process with
/// no audit log does today, and is why `lattice-auth` §7 lists the same gap.
pub trait AuditSink: Send + Sync {
    /// One audit row: an event name and its fields.
    fn append(&self, event: &str, fields: &Value);
}

/// The bridge persisted web exchanges are mirrored to (`on_chat_message`).
///
/// Never echoes: a turn whose `source` is `telegram` is not mirrored back to
/// Telegram, which is the loop `notify_chat_message` exists to break.
pub trait ChatNotifier: Send + Sync {
    /// `notify(role, text, source)`.
    fn notify(&self, role: &str, text: &str, source: Option<&str>);
}

/// The UX funnel counters (`funnel_metrics`), advisory and never fatal.
pub trait FunnelMetrics: Send + Sync {
    /// `increment(name)`.
    fn increment(&self, name: &str);
    /// `record_recall_success()`.
    fn record_recall_success(&self);
}

/// Where an answer trace is durably recorded (`WorkspaceOSStore.record_trace`).
///
/// **Stated gap.** The trace list lives in `workspace_os_state`, which WP-R1
/// owns; a second writer here would be two implementations of one table. The
/// route package therefore takes the recorder as a seam. Unbound, the pipeline
/// still *builds* the trace and still reports it on the stream trailer — it is
/// simply not appended to the workspace timeline, and `trace_id` carries the id
/// the record would have had.
pub trait TraceSink: Send + Sync {
    /// Append one answer trace; return the stored record.
    fn record(&self, record: &Value) -> Value;
}

/// The flags `create_chat_router` reads off `context.config`.
#[derive(Debug, Clone)]
pub struct ChatConfig {
    /// `<data_dir>` — the dials, and the state files chat reads.
    pub data_dir: PathBuf,
    /// `knowledge_graph.sqlite`. `None` ⇒ the graph is off.
    pub graph_db: Option<PathBuf>,
    /// `AGENT_ROOT` — where the direct file-action branch writes.
    pub agent_root: PathBuf,
    /// `config.is_public` — changes only the no-model refusal's wording.
    pub is_public: bool,
    /// `config.public_model` — named in that refusal.
    pub public_model: String,
    /// `config.auto_read_chat_paths`.
    pub auto_read_chat_paths: bool,
    /// `LATTICEAI_INGEST_GENERATED` — index generated files into the Brain.
    ///
    /// Needs [`ChatState::graph`] bound to do anything: the write is native, so
    /// a state with no writer reports no `brain_ingest` at all rather than a
    /// receipt for something that did not happen.
    pub ingest_generated: bool,
}

impl Default for ChatConfig {
    fn default() -> Self {
        Self {
            data_dir: PathBuf::new(),
            graph_db: None,
            agent_root: PathBuf::new(),
            is_public: false,
            public_model: DEFAULT_PUBLIC_MODEL.to_string(),
            auto_read_chat_paths: false,
            ingest_generated: true,
        }
    }
}

fn env_flag_on(name: &str, default: bool) -> bool {
    match std::env::var(name) {
        Ok(raw) => !matches!(
            raw.trim().to_lowercase().as_str(),
            "0" | "false" | "off" | "no"
        ),
        Err(_) => default,
    }
}

impl ChatConfig {
    /// The configuration a gateway builds from the data dir it already resolved.
    ///
    /// `graph_db` is resolved **unconditionally**, and that is a correction:
    /// until v11.6.0 §W3a the file could only appear if Python had already made
    /// it, so keying off `exists()` was a fair reading of `ENABLE_GRAPH`. Since
    /// W3a the first native chat turn *creates* the store (through W1's schema
    /// bootstrap), so an existence check at boot answered "there is no Brain"
    /// for the whole life of the process on a fresh install — the read lanes
    /// and `enable_graph()` stayed false until someone restarted the server.
    /// Python never keyed on the file either; it reads `LATTICEAI_ENABLE_GRAPH`.
    pub fn from_data_dir(data_dir: impl Into<PathBuf>, agent_root: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        let graph = data_dir.join("knowledge_graph.sqlite");
        Self {
            graph_db: Some(graph),
            agent_root: agent_root.into(),
            public_model: std::env::var("LATTICEAI_PUBLIC_MODEL")
                .ok()
                .or_else(|| std::env::var("LATTICEAI_DEFAULT_MODEL").ok())
                .filter(|model| !model.is_empty())
                .unwrap_or_else(|| DEFAULT_PUBLIC_MODEL.to_string()),
            auto_read_chat_paths: env_flag_on(AUTO_READ_ENV, false),
            ingest_generated: env_flag_on(INGEST_GENERATED_ENV, true),
            data_dir,
            ..Default::default()
        }
    }

    /// `context.enable_graph and context.knowledge_graph`.
    pub fn enable_graph(&self) -> bool {
        self.graph_db.is_some()
    }

    /// The graph database, when there is one.
    pub fn graph(&self) -> Option<&Path> {
        self.graph_db.as_deref()
    }
}

/// Everything the chat routes are wired to.
#[derive(Clone)]
pub struct ChatState {
    /// Sessions, roles, rate limits, CSRF — one process-wide instance.
    pub auth: Arc<AuthState>,
    /// Paths and flags.
    pub config: ChatConfig,
    /// The AI worker seam. `None` ⇒ no model is reachable.
    pub worker: Option<ChatWorker>,
    /// WP-W1's native knowledge-graph write engine.
    ///
    /// Built **once** at boot and shared (`GraphWriter::clone` shares the
    /// `Arc<Store>`, so two handles are still one writer). Unbound, a chat turn
    /// is still redacted, audited and stored — only the Brain does not grow, and
    /// the receipt says so with `ingested: null`, which is the same answer the
    /// graph-off install has always given.
    pub graph: Option<GraphWriter>,
    /// Workspace membership (`workspace_service`). `None` passes scopes through.
    pub workspace: Option<Arc<dyn WorkspaceResolver>>,
    /// The Review Center queue, for cloud-derived knowledge.
    pub review: Option<Arc<dyn ReviewSink>>,
    /// Where "knowledge left the machine" is recorded.
    pub egress: Option<Arc<dyn EgressAudit>>,
    /// The audit log.
    pub audit: Option<Arc<dyn AuditSink>>,
    /// The chat bridge (Telegram mirror).
    pub notify: Option<Arc<dyn ChatNotifier>>,
    /// UX funnel counters.
    pub funnel: Option<Arc<dyn FunnelMetrics>>,
    /// Where an answer trace is recorded.
    pub traces: Option<Arc<dyn TraceSink>>,
    /// Per-conversation document-generation follow-up sessions.
    pub document_sessions: Arc<DocumentSessions>,
}

impl std::fmt::Debug for ChatState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The seams are trait objects with no useful Debug, and the auth state
        // holds session tokens: name what is wired, print none of it.
        formatter
            .debug_struct("ChatState")
            .field("config", &self.config)
            .field("worker", &self.worker.as_ref().map(ChatWorker::origin))
            .field("graph", &self.graph.is_some())
            .field("workspace", &self.workspace.is_some())
            .field("review", &self.review.is_some())
            .field("egress", &self.egress.is_some())
            .field("audit", &self.audit.is_some())
            .field("notify", &self.notify.is_some())
            .field("funnel", &self.funnel.is_some())
            .field("traces", &self.traces.is_some())
            .field("document_sessions", &self.document_sessions.len())
            .finish()
    }
}

impl ChatState {
    /// The minimum: identity and configuration, every optional seam unbound.
    pub fn new(auth: Arc<AuthState>, config: ChatConfig) -> Self {
        Self {
            auth,
            config,
            worker: None,
            graph: None,
            workspace: None,
            review: None,
            egress: None,
            audit: None,
            notify: None,
            funnel: None,
            traces: None,
            document_sessions: Arc::new(DocumentSessions::new()),
        }
    }

    /// Bind the AI worker seam.
    pub fn with_worker(mut self, worker: ChatWorker) -> Self {
        self.worker = Some(worker);
        self
    }

    /// Bind WP-W1's knowledge-graph write engine.
    ///
    /// The integrator builds it once, beside the store, and hands the same
    /// handle to every family that writes the graph:
    ///
    /// ```ignore
    /// let store = Arc::new(config.open_store()?);
    /// let graph = GraphWriter::open(store, config.data_dir().join("knowledge_graph_blobs"))?;
    /// ```
    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    /// Bind workspace membership (WP-R1's resolver).
    pub fn with_workspace(mut self, resolver: Arc<dyn WorkspaceResolver>) -> Self {
        self.workspace = Some(resolver);
        self
    }

    /// Bind the Review Center sink.
    pub fn with_review(mut self, review: Arc<dyn ReviewSink>) -> Self {
        self.review = Some(review);
        self
    }

    /// Bind the cloud-egress audit sink.
    pub fn with_egress(mut self, egress: Arc<dyn EgressAudit>) -> Self {
        self.egress = Some(egress);
        self
    }

    /// Bind the audit log.
    pub fn with_audit(mut self, audit: Arc<dyn AuditSink>) -> Self {
        self.audit = Some(audit);
        self
    }

    /// Bind the chat bridge.
    pub fn with_notifier(mut self, notify: Arc<dyn ChatNotifier>) -> Self {
        self.notify = Some(notify);
        self
    }

    /// Bind the funnel counters.
    pub fn with_funnel(mut self, funnel: Arc<dyn FunnelMetrics>) -> Self {
        self.funnel = Some(funnel);
        self
    }

    /// Bind the answer-trace recorder.
    pub fn with_traces(mut self, traces: Arc<dyn TraceSink>) -> Self {
        self.traces = Some(traces);
        self
    }

    /// `notify_chat_message` — mirror, but never echo Telegram back to Telegram.
    pub fn notify(&self, role: &str, text: &str, source: Option<&str>) {
        if source == Some("telegram") {
            return;
        }
        if let Some(bridge) = self.notify.as_ref() {
            bridge.notify(role, text, source);
        }
    }

    /// `<data_dir>/audit_log.json`, or `None` when this state has no data dir.
    ///
    /// The path comes from WP-I1's `state_files::AUDIT_LOG` through R2's
    /// `admin::audit_log_path`, so there is exactly one answer in the workspace.
    /// A state built with the default (empty) data dir has no audit file rather
    /// than a relative one — a chat crate must not write `audit_log.json` into
    /// whatever directory the process happens to be started from.
    pub fn audit_log_path(&self) -> Option<PathBuf> {
        let data_dir = self.config.data_dir.as_path();
        (!data_dir.as_os_str().is_empty())
            .then(|| lattice_platform::admin::audit_log_path(data_dir))
    }

    /// The SQLite file `conversation_messages` lives in.
    ///
    /// `ConversationStore(data_dir / "knowledge_graph.sqlite")` — Python builds
    /// it whether or not the graph is enabled, so this does not go through
    /// `ChatConfig::graph()` (which answers `None` until the file exists). The
    /// first turn on a fresh machine creates the file, exactly as Python's
    /// `sqlite3.connect` does.
    pub fn conversation_db(&self) -> PathBuf {
        self.config
            .graph()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| self.config.data_dir.join("knowledge_graph.sqlite"))
    }

    /// `append_audit_event(name, **fields)`.
    ///
    /// A bound [`AuditSink`] wins — it is how the gateway routes every family's
    /// events through one writer. With none bound the event still lands, through
    /// **R2's** helper on **R2's** file: `audit_log.json` has one format, and a
    /// second one written from here would be a second format regardless of how
    /// carefully it was copied. Only a state with no data dir at all drops it.
    pub fn audit(&self, event: &str, fields: &Value) {
        if let Some(sink) = self.audit.as_ref() {
            sink.append(event, fields);
            return;
        }
        let (Some(path), Some(payload)) = (self.audit_log_path(), fields.as_object()) else {
            return;
        };
        lattice_platform::admin::append_audit_event(&path, event, payload.clone());
    }

    /// `funnel_metrics.increment(name)` — advisory, never fatal.
    pub fn funnel_increment(&self, name: &str) {
        if let Some(funnel) = self.funnel.as_ref() {
            funnel.increment(name);
        }
    }

    /// `get_history_user` — email plus the nickname the store would persist.
    pub fn history_user(
        &self,
        email: Option<&str>,
        nickname: Option<&str>,
    ) -> (Option<String>, Option<String>) {
        let email = email.filter(|value| !value.is_empty());
        let Some(email) = email else {
            return (
                None,
                nickname
                    .filter(|value| !value.is_empty())
                    .map(str::to_string),
            );
        };
        let nick = nickname
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .or_else(|| {
                let users = self.auth.users().load();
                users.get(email).and_then(|record| {
                    record
                        .get("nickname")
                        .and_then(serde_json::Value::as_str)
                        .or_else(|| record.get("name").and_then(serde_json::Value::as_str))
                        .filter(|value| !value.is_empty())
                        .map(str::to_string)
                })
            })
            .or_else(|| Some(email.to_string()));
        (Some(email.to_string()), nick)
    }
}

impl axum::extract::FromRef<ChatState> for Arc<AuthState> {
    fn from_ref(state: &ChatState) -> Self {
        Arc::clone(&state.auth)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_auth::AuthConfig;
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::Mutex;

    fn auth(dir: &Path) -> Arc<AuthState> {
        let mut env: HashMap<String, String> = HashMap::new();
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            dir.to_string_lossy().into_owned(),
        );
        AuthState::new(AuthConfig::from_map(&env, None))
    }

    #[derive(Default)]
    struct Recorder {
        rows: Mutex<Vec<String>>,
    }

    impl AuditSink for Recorder {
        fn append(&self, event: &str, fields: &Value) {
            self.rows.lock().unwrap().push(format!("{event}:{fields}"));
        }
    }

    impl ChatNotifier for Recorder {
        fn notify(&self, role: &str, text: &str, source: Option<&str>) {
            self.rows
                .lock()
                .unwrap()
                .push(format!("{role}/{text}/{source:?}"));
        }
    }

    impl FunnelMetrics for Recorder {
        fn increment(&self, name: &str) {
            self.rows.lock().unwrap().push(format!("+{name}"));
        }
        fn record_recall_success(&self) {
            self.rows.lock().unwrap().push("recall".into());
        }
    }

    #[test]
    fn the_graph_is_on_only_when_the_database_exists() {
        let dir = tempfile::tempdir().unwrap();
        // The store does not exist yet, and that is exactly the case the
        // existence check used to get wrong: the first native turn creates it,
        // so a config built at boot must already name it.
        let config = ChatConfig::from_data_dir(dir.path(), dir.path().join("agent"));
        assert!(config.enable_graph());
        assert_eq!(
            config.graph(),
            Some(dir.path().join("knowledge_graph.sqlite").as_path())
        );
        std::fs::write(dir.path().join("knowledge_graph.sqlite"), b"").unwrap();
        let config = ChatConfig::from_data_dir(dir.path(), dir.path().join("agent"));
        assert!(config.enable_graph());
        assert!(config.graph().unwrap().ends_with("knowledge_graph.sqlite"));
        assert!(format!("{config:?}").contains("public_model"));
    }

    #[test]
    fn unbound_seams_are_no_ops_and_telegram_is_never_echoed() {
        let dir = tempfile::tempdir().unwrap();
        let state = ChatState::new(auth(dir.path()), ChatConfig::default());
        state.audit("chat_message", &json!({}));
        state.notify("user", "hi", None);
        state.funnel_increment("file_requests");
        assert!(format!("{state:?}").contains("worker: None"));

        let recorder = Arc::new(Recorder::default());
        let state = ChatState::new(auth(dir.path()), ChatConfig::default())
            .with_audit(recorder.clone())
            .with_notifier(recorder.clone())
            .with_funnel(recorder.clone());
        state.audit("clear_command", &json!({"removed": 1}));
        state.notify("user", "hi", Some("web"));
        state.notify("assistant", "no", Some("telegram"));
        state.funnel_increment("real_file_delivered");
        let rows = recorder.rows.lock().unwrap().clone();
        assert_eq!(
            rows,
            [
                "clear_command:{\"removed\":1}",
                "user/hi/Some(\"web\")",
                "+real_file_delivered",
            ]
        );
    }

    #[test]
    fn every_builder_binds_its_seam() {
        let dir = tempfile::tempdir().unwrap();
        let state = ChatState::new(auth(dir.path()), ChatConfig::default())
            .with_worker(crate::worker::ChatWorker::new("http://127.0.0.1:9").unwrap());
        assert!(state.worker.is_some());
        assert!(format!("{state:?}").contains("127.0.0.1:9"));
        // FromRef is what the extractors resolve `Arc<AuthState>` through.
        let extracted: Arc<AuthState> = axum::extract::FromRef::from_ref(&state);
        assert!(Arc::ptr_eq(&extracted, &state.auth));
    }
}
