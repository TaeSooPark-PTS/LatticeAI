use std::path::PathBuf;
use std::sync::Arc;

use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Value};

use super::constants::WORKSPACE_OS_VERSION;

pub trait GraphReads: Send + Sync {
    fn stats(&self) -> Option<Value>;
    fn window(&self, limit: usize) -> Option<Value>;
    fn local_sources(&self) -> Option<Value>;
    fn neighbors(&self, node_id: &str) -> Option<Value>;
}

#[derive(Debug, Clone, Default)]
pub struct IngestEvent {
    pub event_type: String,
    pub title: String,
    pub user_email: Option<String>,
    pub source: String,
    pub workspace_id: Option<String>,
    pub metadata: Value,
}

impl IngestEvent {
    pub fn as_args(&self) -> Value {
        json!({
            "event_type": self.event_type,
            "title": self.title,
            "user_email": self.user_email,
            "source": self.source,
            "workspace_id": self.workspace_id,
            "metadata": if self.metadata.is_object() { self.metadata.clone() } else { json!({}) },
        })
    }
}

#[derive(Clone)]
pub enum GraphSeam {
    Absent,
    Native(GraphWriter),
    Stub(GraphMutator),
}

pub type GraphMutator = Arc<dyn Fn(&str, &Value) -> Result<Value, String> + Send + Sync>;

impl std::fmt::Debug for GraphSeam {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::Absent => "GraphSeam::Absent",
            Self::Native(_) => "GraphSeam::Native",
            Self::Stub(_) => "GraphSeam::Stub",
        })
    }
}

impl GraphSeam {
    pub fn is_available(&self) -> bool {
        !matches!(self, Self::Absent)
    }

    pub async fn mutate(&self, op: &str, args: Value) -> Result<Value, String> {
        match self {
            Self::Absent => Err("knowledge graph is disabled".to_string()),
            Self::Stub(answer) => answer(op, &args),
            Self::Native(graph) => {
                let graph = graph.clone();
                let op = op.to_string();
                tokio::task::spawn_blocking(move || native_dispatch(&graph, &op, &args))
                    .await
                    .map_err(|error| error.to_string())?
            }
        }
    }

    pub async fn ingest_event(&self, event: &IngestEvent) -> Result<Value, String> {
        self.mutate("ingest_event", event.as_args()).await
    }

    pub async fn import_graph(&self, data: &Value, mode: &str) -> Result<Value, String> {
        self.mutate("import_graph_data", json!({"data": data, "mode": mode}))
            .await
    }

    pub async fn set_local_source_watch(
        &self,
        source_id: &str,
        enabled: bool,
    ) -> Result<Value, String> {
        self.mutate(
            "set_local_source_watch",
            json!({"source_id": source_id, "enabled": enabled}),
        )
        .await
    }

    pub async fn remove_local_source(&self, source_id: &str) -> Result<Value, String> {
        self.mutate("remove_local_source", json!({"source_id": source_id}))
            .await
    }
}

fn native_dispatch(graph: &GraphWriter, op: &str, args: &Value) -> Result<Value, String> {
    use lattice_core::graph_write::types::{
        CurateNoiseRequest, CurateRequest, ImportRequest, IngestEventRequest, RebuildRequest,
    };
    let result = match op {
        "curate" => {
            let request = CurateRequest {
                max_documents: args
                    .get("max_documents")
                    .and_then(Value::as_i64)
                    .unwrap_or(200),
                max_new_nodes: args
                    .get("max_new_nodes")
                    .and_then(Value::as_i64)
                    .unwrap_or(8),
                review_mode: args.get("review_mode").and_then(Value::as_bool),
                overlay: Default::default(),
            };
            graph.curate(&request)
        }
        "curate_noise" => graph.curate_noise(
            &serde_json::from_value(args.clone()).unwrap_or_else(|_| CurateNoiseRequest::default()),
        ),
        "apply_pending_promotions" => {
            let ids = args.get("ids").and_then(Value::as_array).map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect::<Vec<_>>()
            });
            graph.apply_promotions(ids.as_deref())
        }
        "reject_pending_promotions" => {
            let ids = args.get("ids").and_then(Value::as_array).map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect::<Vec<_>>()
            });
            graph.reject_promotions(ids.as_deref())
        }
        "rebuild_vector_index" => graph
            .rebuild_vector_index(
                &serde_json::from_value(args.clone()).unwrap_or_else(|_| RebuildRequest::default()),
            )
            .map(|outcome| outcome.to_json()),
        "ingest_event" => {
            let request: IngestEventRequest =
                serde_json::from_value(args.clone()).unwrap_or_default();
            graph
                .ingest_event(&request)
                .map(|outcome| outcome.to_json())
        }
        "set_node_sensitivity" => graph.set_node_sensitivity(
            args.get("node_id").and_then(Value::as_str).unwrap_or(""),
            args.get("local_only")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            args.get("reason").and_then(Value::as_str),
        ),
        "import_graph_data" => {
            let request = ImportRequest {
                data: args
                    .get("data")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default(),
                mode: args
                    .get("mode")
                    .and_then(Value::as_str)
                    .unwrap_or("merge")
                    .to_string(),
                dry_run: args
                    .get("dry_run")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            };
            graph
                .import_graph_data(&request)
                .map(|outcome| outcome.to_json())
        }
        "delete_document_tree" => {
            graph.delete_document_tree(args.get("node_id").and_then(Value::as_str).unwrap_or(""))
        }
        "set_local_source_watch" => graph.set_local_source_watch(
            args.get("source_id").and_then(Value::as_str).unwrap_or(""),
            args.get("enabled").and_then(Value::as_bool).unwrap_or(true),
        ),
        "remove_local_source" => {
            graph.remove_local_source(args.get("source_id").and_then(Value::as_str).unwrap_or(""))
        }
        other => {
            return Err(format!("graph mutation op not allowed: {other}"));
        }
    };
    result.map_err(|error| error.to_string())
}

pub type JsonProvider = Arc<dyn Fn() -> Value + Send + Sync>;
pub type RecordsProvider = Arc<dyn Fn() -> Vec<Value> + Send + Sync>;
pub type AuditAppender = Arc<dyn Fn(&str, &Value) + Send + Sync>;
pub type SkillsCatalog = Arc<dyn Fn() -> Result<Vec<Value>, String> + Send + Sync>;
pub type SkillInstaller = Arc<dyn Fn(&str, &str) -> Result<Value, String> + Send + Sync>;
pub type ModelRecommender = Arc<dyn Fn(&Value) -> (Value, Value) + Send + Sync>;

#[derive(Clone)]
pub struct WorkspaceProviders {
    pub graph_reads: Option<Arc<dyn GraphReads>>,
    pub models: JsonProvider,
    pub settings: JsonProvider,
    pub history: RecordsProvider,
    pub audit_events: RecordsProvider,
    pub append_audit_event: AuditAppender,
    pub skills_dir: Option<PathBuf>,
    pub skills_marketplace: SkillsCatalog,
    pub install_skill: Option<SkillInstaller>,
    pub scan_environment: Option<JsonProvider>,
    pub local_sysinfo: Option<JsonProvider>,
    pub model_recommendations: Option<ModelRecommender>,
    pub watcher_status: Option<JsonProvider>,
    pub local_model: String,
    pub public_model: String,
}

impl Default for WorkspaceProviders {
    fn default() -> Self {
        Self {
            graph_reads: None,
            models: Arc::new(default_models_payload),
            settings: Arc::new(default_settings_payload),
            history: Arc::new(Vec::new),
            audit_events: Arc::new(Vec::new),
            append_audit_event: Arc::new(|_, _| {}),
            skills_dir: None,
            skills_marketplace: Arc::new(|| Ok(Vec::new())),
            install_skill: None,
            scan_environment: None,
            local_sysinfo: None,
            model_recommendations: None,
            watcher_status: None,
            local_model: env_or("LATTICEAI_LOCAL_MODEL", "mlx-community/gemma-4-12B-it-4bit"),
            public_model: env_or(
                "LATTICEAI_PUBLIC_MODEL",
                &env_or("LATTICEAI_DEFAULT_MODEL", "openai:gpt-4o-mini"),
            ),
        }
    }
}

fn env_or(name: &str, fallback: &str) -> String {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| fallback.to_string())
}

fn env_bool(name: &str, fallback: bool) -> bool {
    match std::env::var(name) {
        Ok(value) => matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        Err(_) => fallback,
    }
}

fn default_models_payload() -> Value {
    json!({
        "current_model": Value::Null,
        "loaded_models": [],
        "public_model": env_or("LATTICEAI_PUBLIC_MODEL",
                               &env_or("LATTICEAI_DEFAULT_MODEL", "openai:gpt-4o-mini")),
        "local_model": env_or("LATTICEAI_LOCAL_MODEL", "mlx-community/gemma-4-12B-it-4bit"),
        "local_draft_model": env_or("LATTICEAI_LOCAL_DRAFT_MODEL", ""),
    })
}

fn default_settings_payload() -> Value {
    let is_public = env_or("LATTICEAI_MODE", "local") == "public";
    json!({
        "mode": env_or("LATTICEAI_MODE", "local"),
        "host": env_or("LATTICEAI_HOST", "127.0.0.1"),
        "port": env_or("LATTICEAI_PORT", "4825").parse::<u16>().unwrap_or(4825),
        "require_auth": env_bool("LATTICEAI_REQUIRE_AUTH", is_public),
        "enable_graph": env_bool("LATTICEAI_ENABLE_GRAPH", true),
        "allow_local_models": env_bool("LATTICEAI_ALLOW_LOCAL_MODELS", !is_public),
        "static_dir": env_or("LATTICEAI_STATIC_DIR", ""),
        "data_dir": env_or("LATTICEAI_DATA_DIR", ""),
    })
}

#[derive(Clone)]
pub struct WorkspaceDeps {
    pub seam: GraphSeam,
    pub providers: WorkspaceProviders,
}

impl Default for WorkspaceDeps {
    fn default() -> Self {
        Self {
            seam: GraphSeam::Absent,
            providers: WorkspaceProviders::default(),
        }
    }
}

impl WorkspaceDeps {
    pub fn graph(&self) -> Option<&dyn GraphReads> {
        self.providers.graph_reads.as_deref()
    }

    pub fn graph_stats_safe(&self) -> Value {
        match self.graph().and_then(GraphReads::stats) {
            Some(stats) => stats,
            None if self.providers.graph_reads.is_some() => {
                json!({"error": "graph stats unavailable"})
            }
            None => json!({"disabled": true}),
        }
    }

    pub fn edition(&self) -> Value {
        json!({
            "edition": "community",
            "is_enterprise": false,
            "capabilities": {
                "sso_advanced": false,
                "idp_provisioning": false,
                "scim": false,
                "rbac_abac_advanced": false,
                "tenant_isolation": false,
                "compliance_retention": false,
                "siem_export": false,
                "private_vpc": false,
                "air_gapped_deployment": false,
                "dlp_policy": false,
                "ediscovery": false,
                "admin_policy_packs": false,
                "graph_promotion_review": false,
            },
            "community_notice": "All listed capabilities are Enterprise-only extension points. \
        The open-source Community edition ships none of them enabled; \
        see docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md.",
        })
    }

    pub fn version(&self) -> &'static str {
        WORKSPACE_OS_VERSION
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Reader(Option<Value>);
    impl GraphReads for Reader {
        fn stats(&self) -> Option<Value> {
            self.0.clone()
        }
        fn window(&self, _limit: usize) -> Option<Value> {
            None
        }
        fn local_sources(&self) -> Option<Value> {
            None
        }
        fn neighbors(&self, _node_id: &str) -> Option<Value> {
            None
        }
    }

    #[test]
    fn no_graph_reads_as_disabled_and_a_broken_reader_as_an_error() {
        let deps = WorkspaceDeps::default();
        assert_eq!(deps.graph_stats_safe(), json!({"disabled": true}));
        assert!(deps.graph().is_none());

        let mut with_reader = WorkspaceDeps::default();
        with_reader.providers.graph_reads = Some(Arc::new(Reader(None)));
        assert_eq!(
            with_reader.graph_stats_safe(),
            json!({"error": "graph stats unavailable"})
        );

        let mut working = WorkspaceDeps::default();
        working.providers.graph_reads = Some(Arc::new(Reader(Some(json!({"nodes": {}})))));
        assert_eq!(working.graph_stats_safe(), json!({"nodes": {}}));
    }

    #[test]
    fn the_edition_matrix_is_community_with_nothing_enabled() {
        let edition = WorkspaceDeps::default().edition();
        assert_eq!(edition["edition"], json!("community"));
        assert_eq!(edition["is_enterprise"], json!(false));
        let capabilities = edition["capabilities"].as_object().unwrap();
        assert_eq!(capabilities.len(), 13);
        assert!(capabilities.values().all(|value| value == &json!(false)));
    }

    #[tokio::test]
    async fn an_absent_seam_refuses_and_a_stub_answers() {
        let absent = GraphSeam::Absent;
        assert!(!absent.is_available());
        assert_eq!(
            absent
                .ingest_event(&IngestEvent::default())
                .await
                .unwrap_err(),
            "knowledge graph is disabled"
        );

        let stub = GraphSeam::Stub(Arc::new(|op, args| {
            Ok(json!({"op": op, "args": args.clone(), "node_id": "node-1"}))
        }));
        assert!(stub.is_available());
        let event = IngestEvent {
            event_type: "Memory".into(),
            title: "decisions: x".into(),
            user_email: Some("a@b.test".into()),
            source: "workspace_os".into(),
            workspace_id: Some("personal".into()),
            metadata: json!({"memory_id": "m1"}),
        };
        let answer = stub.ingest_event(&event).await.unwrap();
        assert_eq!(answer["op"], json!("ingest_event"));
        assert_eq!(answer["args"]["title"], json!("decisions: x"));
        assert_eq!(answer["args"]["metadata"]["memory_id"], json!("m1"));
        assert_eq!(answer["node_id"], json!("node-1"));

        assert_eq!(
            stub.import_graph(&json!({"nodes": []}), "merge")
                .await
                .unwrap()["op"],
            json!("import_graph_data")
        );
        assert_eq!(
            stub.set_local_source_watch("s1", true).await.unwrap()["args"]["enabled"],
            json!(true)
        );
        assert_eq!(
            stub.remove_local_source("s1").await.unwrap()["args"]["source_id"],
            json!("s1")
        );
    }

    #[test]
    fn a_non_object_metadata_is_normalised_to_an_object() {
        let event = IngestEvent {
            metadata: json!("not an object"),
            ..IngestEvent::default()
        };
        assert_eq!(event.as_args()["metadata"], json!({}));
    }

    #[test]
    fn the_default_providers_answer_empty_rather_than_invented() {
        let providers = WorkspaceProviders::default();
        assert!((providers.history)().is_empty());
        assert!((providers.audit_events)().is_empty());
        assert!((providers.skills_marketplace)().unwrap().is_empty());
        assert!(providers.install_skill.is_none());
        assert!(providers.scan_environment.is_none());
        assert_eq!((providers.models)()["loaded_models"], json!([]));
        assert!((providers.settings)()["port"].is_number());
        assert_eq!(format!("{:?}", GraphSeam::Absent), "GraphSeam::Absent");
    }
}
