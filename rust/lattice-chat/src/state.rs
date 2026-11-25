use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_auth::{AuthState, WorkspaceResolver};
use lattice_core::graph_write::GraphWriter;
use serde_json::Value;

use crate::cloud::{CloudProvider, CloudStatus, EgressAudit, ReviewSink};
use crate::documents::DocumentSessions;
use crate::worker::ChatWorker;

pub const DEFAULT_PUBLIC_MODEL: &str = "openai:gpt-4o-mini";
pub const AUTO_READ_ENV: &str = "LATTICEAI_AUTO_READ_CHAT_PATHS";
pub const INGEST_GENERATED_ENV: &str = "LATTICEAI_INGEST_GENERATED";

pub trait AuditSink: Send + Sync {
    fn append(&self, event: &str, fields: &Value);
}

pub trait ChatNotifier: Send + Sync {
    fn notify(&self, role: &str, text: &str, source: Option<&str>);
}

pub trait FunnelMetrics: Send + Sync {
    fn increment(&self, name: &str);
    fn record_recall_success(&self);
}

pub trait TraceSink: Send + Sync {
    fn record(&self, record: &Value) -> Value;
}

#[derive(Debug, Clone)]
pub struct ChatConfig {
    pub data_dir: PathBuf,
    pub graph_db: Option<PathBuf>,
    pub agent_root: PathBuf,
    pub is_public: bool,
    pub public_model: String,
    pub auto_read_chat_paths: bool,
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

    pub fn enable_graph(&self) -> bool {
        self.graph_db.is_some()
    }

    pub fn graph(&self) -> Option<&Path> {
        self.graph_db.as_deref()
    }
}

#[derive(Clone)]
pub struct ChatState {
    pub auth: Arc<AuthState>,
    pub config: ChatConfig,
    pub worker: Option<ChatWorker>,
    pub graph: Option<GraphWriter>,
    pub workspace: Option<Arc<dyn WorkspaceResolver>>,
    pub review: Option<Arc<dyn ReviewSink>>,
    pub egress: Option<Arc<dyn EgressAudit>>,
    pub audit: Option<Arc<dyn AuditSink>>,
    pub notify: Option<Arc<dyn ChatNotifier>>,
    pub funnel: Option<Arc<dyn FunnelMetrics>>,
    pub traces: Option<Arc<dyn TraceSink>>,
    pub document_sessions: Arc<DocumentSessions>,
    pub cloud_provider: Option<Arc<CloudProvider>>,
}

impl std::fmt::Debug for ChatState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
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
            .field("cloud_provider", &self.cloud_provider.is_some())
            .field("document_sessions", &self.document_sessions.len())
            .finish()
    }
}

impl ChatState {
    pub fn new(auth: Arc<AuthState>, config: ChatConfig) -> Self {
        let document_sessions = Arc::new(DocumentSessions::with_data_dir(&config.data_dir));
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
            document_sessions,
            cloud_provider: None,
        }
    }

    pub fn with_worker(mut self, worker: ChatWorker) -> Self {
        self.worker = Some(worker);
        self
    }

    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    pub fn with_workspace(mut self, resolver: Arc<dyn WorkspaceResolver>) -> Self {
        self.workspace = Some(resolver);
        self
    }

    pub fn with_review(mut self, review: Arc<dyn ReviewSink>) -> Self {
        self.review = Some(review);
        self
    }

    pub fn with_egress(mut self, egress: Arc<dyn EgressAudit>) -> Self {
        self.egress = Some(egress);
        self
    }

    pub fn with_audit(mut self, audit: Arc<dyn AuditSink>) -> Self {
        self.audit = Some(audit);
        self
    }

    pub fn with_notifier(mut self, notify: Arc<dyn ChatNotifier>) -> Self {
        self.notify = Some(notify);
        self
    }

    pub fn with_funnel(mut self, funnel: Arc<dyn FunnelMetrics>) -> Self {
        self.funnel = Some(funnel);
        self
    }

    pub fn with_traces(mut self, traces: Arc<dyn TraceSink>) -> Self {
        self.traces = Some(traces);
        self
    }

    pub fn with_cloud_provider(mut self, provider: CloudProvider) -> Self {
        self.cloud_provider = Some(Arc::new(provider));
        self
    }

    pub fn resolved_cloud_provider(&self) -> Option<CloudProvider> {
        if let Some(bound) = self.cloud_provider.as_ref() {
            return Some((**bound).clone());
        }
        let data_dir = self.config.data_dir.as_path();
        if data_dir.as_os_str().is_empty() {
            return CloudProvider::resolve_with(data_dir, &crate::cloud::ResolveInput::from_env());
        }
        CloudProvider::resolve(data_dir)
    }

    pub fn cloud_configured(&self) -> bool {
        self.resolved_cloud_provider()
            .is_some_and(|provider| provider.configured())
    }

    pub async fn cloud_status(&self) -> CloudStatus {
        match self.resolved_cloud_provider() {
            Some(provider) => provider.status().await,
            None => CloudStatus::none(),
        }
    }

    pub fn notify(&self, role: &str, text: &str, source: Option<&str>) {
        if source == Some("telegram") {
            return;
        }
        if let Some(bridge) = self.notify.as_ref() {
            bridge.notify(role, text, source);
        }
    }

    pub fn audit_log_path(&self) -> Option<PathBuf> {
        let data_dir = self.config.data_dir.as_path();
        (!data_dir.as_os_str().is_empty())
            .then(|| lattice_platform::adminops::admin::audit_log_path(data_dir))
    }

    pub fn conversation_db(&self) -> PathBuf {
        self.config
            .graph()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| self.config.data_dir.join("knowledge_graph.sqlite"))
    }

    pub fn audit(&self, event: &str, fields: &Value) {
        if let Some(sink) = self.audit.as_ref() {
            sink.append(event, fields);
            return;
        }
        let (Some(path), Some(payload)) = (self.audit_log_path(), fields.as_object()) else {
            return;
        };
        lattice_platform::adminops::admin::append_audit_event(&path, event, payload.clone());
    }

    pub fn funnel_increment(&self, name: &str) {
        if let Some(funnel) = self.funnel.as_ref() {
            funnel.increment(name);
        }
    }

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
        let extracted: Arc<AuthState> = axum::extract::FromRef::from_ref(&state);
        assert!(Arc::ptr_eq(&extracted, &state.auth));
    }
}
