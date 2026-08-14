//! What this family needs from the rest of the product, stated as one object.
//!
//! Port of the workspace slice of `latticeai/services/app_context.py`. The
//! Python router took ~20 callables through `context.require(...)`; the same
//! dependencies are gathered here, for the same reason: this module must not
//! import the families that own the knowledge graph, the model router, or the
//! setup wizard, or the crate becomes one tangle instead of thirteen families.
//!
//! Two kinds of dependency, and the split is the important part:
//!
//! * **Graph reads** ([`GraphReads`]) — stats, the node/edge window, local
//!   sources, neighbours. Owned by the knowledge-graph family, supplied by the
//!   integrator. Absent ⇒ the graph is off, which is a state Python models too
//!   (`_workspace_graph()` answers `None`, `graph_stats_safe()` answers
//!   `{"disabled": true}`).
//! * **Graph writes** ([`GraphSeam`]) — every one of them goes over
//!   `POST /worker/graph/mutate` (WAVE2_COMMON rule 6). There is one writer of
//!   `knowledge_graph.sqlite` and it is the Python worker; this family asks.
//!
//! Everything else is a provider closure with an honest default. A default
//! that cannot answer says so (`None`, `{"disabled": true}`, a 503) rather than
//! inventing a plausible-looking payload — a fabricated hardware probe is worse
//! than a missing one.

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
use std::path::PathBuf;
use std::sync::Arc;

use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::{WorkerSeamClient, WorkerSeamError};
use serde_json::{json, Value};

use super::constants::WORKSPACE_OS_VERSION;

/// The knowledge-graph reads this family performs.
///
/// Every method answers `Option`: `None` is "this reader could not say", which
/// the callers turn into the same empty shape Python produces when the graph is
/// disabled. Implementations are synchronous because they are SQLite reads;
/// callers that can block for long run them off the request path.
pub trait GraphReads: Send + Sync {
    /// `graph.stats()`.
    fn stats(&self) -> Option<Value>;
    /// `graph.graph(limit=…)` — `{"nodes": [...], "edges": [...]}`.
    fn window(&self, limit: usize) -> Option<Value>;
    /// `graph.local_sources()` — `{"sources": [...]}`.
    fn local_sources(&self) -> Option<Value>;
    /// `graph.neighbors(node_id)` — `{"neighbors": [...], "edges": [...]}`.
    fn neighbors(&self, node_id: &str) -> Option<Value>;
}

/// One `ingest_event` call, named the way the Python store names it.
#[derive(Debug, Clone, Default)]
pub struct IngestEvent {
    /// The node type — `"Memory"`, `"AgentRun"`, `"Workflow"`, …
    pub event_type: String,
    /// The human-readable title.
    pub title: String,
    /// Who caused it, when there is a signed-in person.
    pub user_email: Option<String>,
    /// Always `"workspace_os"` or `"vscode"` from this family.
    pub source: String,
    /// The workspace the resulting node belongs to.
    pub workspace_id: Option<String>,
    /// Free-form metadata carried onto the node.
    pub metadata: Value,
}

impl IngestEvent {
    /// The `args` object `POST /worker/graph/mutate` expects for `ingest_event`.
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

/// How this family writes the knowledge graph: it does not — it asks.
///
/// [`GraphSeam::Absent`] is the graph-disabled install. [`GraphSeam::Worker`]
/// is production. [`GraphSeam::Stub`] exists so the fixture replay can pin the
/// *shape* of a mutation without standing up a Python worker; it is the only
/// variant a test uses and no production path can reach it.
#[derive(Clone)]
pub enum GraphSeam {
    /// No graph on this install — every write is skipped, as Python skips it.
    Absent,
    /// Native write engine (W3b).
    Native(GraphWriter),
    /// The real seam: `POST /worker/graph/mutate` on the worker origin.
    Worker(WorkerSeamClient),
    /// A caller-supplied answer, for tests.
    Stub(Arc<dyn Fn(&str, &Value) -> Result<Value, String> + Send + Sync>),
}

impl std::fmt::Debug for GraphSeam {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::Absent => "GraphSeam::Absent",
            Self::Native(_) => "GraphSeam::Native",
            Self::Worker(_) => "GraphSeam::Worker",
            Self::Stub(_) => "GraphSeam::Stub",
        })
    }
}

/// The seam path every graph mutation in the product goes through.
pub const GRAPH_MUTATE_PATH: &str = "/worker/graph/mutate";

impl GraphSeam {
    /// Whether a write can even be attempted.
    pub fn is_available(&self) -> bool {
        !matches!(self, Self::Absent)
    }

    /// Run one whitelisted op, returning the worker's `result` verbatim.
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
            Self::Worker(client) => {
                let payload = json!({"op": op, "args": args});
                match client.post_json(GRAPH_MUTATE_PATH, &payload).await {
                    Ok(body) => Ok(body.get("result").cloned().unwrap_or(Value::Null)),
                    Err(error) => Err(describe(&error)),
                }
            }
        }
    }

    /// `graph.ingest_event(...)` — the op five of this family's writes use.
    pub async fn ingest_event(&self, event: &IngestEvent) -> Result<Value, String> {
        self.mutate("ingest_event", event.as_args()).await
    }

    /// `graph.import_graph(data, mode="merge")` — snapshot restore.
    pub async fn import_graph(&self, data: &Value, mode: &str) -> Result<Value, String> {
        self.mutate("import_graph_data", json!({"data": data, "mode": mode}))
            .await
    }

    /// `graph.set_local_source_watch(source_id, enabled)`.
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

    /// `graph.remove_local_source(source_id)`.
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

/// A seam refusal, rendered the way Python's `str(exc)` would read.
fn describe(error: &WorkerSeamError) -> String {
    match error.status() {
        Some(status) => format!("{status}: {error}"),
        None => error.to_string(),
    }
}

/// A closure that answers a JSON payload.
pub type JsonProvider = Arc<dyn Fn() -> Value + Send + Sync>;
/// A closure that answers a list of JSON records.
pub type RecordsProvider = Arc<dyn Fn() -> Vec<Value> + Send + Sync>;

/// Everything this family reads from elsewhere, with its defaults.
#[derive(Clone)]
pub struct WorkspaceProviders {
    /// Knowledge-graph reads, or `None` when the graph is off.
    pub graph_reads: Option<Arc<dyn GraphReads>>,
    /// `_workspace_models_payload()`.
    pub models: JsonProvider,
    /// `_workspace_settings_payload()`.
    pub settings: JsonProvider,
    /// `get_history()` — the chat log a snapshot captures.
    pub history: RecordsProvider,
    /// `get_audit_log()` — the events the timeline and audit views merge in.
    pub audit_events: RecordsProvider,
    /// `append_audit_event(event_type, fields)`.
    pub append_audit_event: Arc<dyn Fn(&str, &Value) + Send + Sync>,
    /// Where installed skills live (`SKILLS_DIR`; package-relative in Python).
    pub skills_dir: Option<PathBuf>,
    /// `_fetch_skills_marketplace()`.
    pub skills_marketplace: Arc<dyn Fn() -> Result<Vec<Value>, String> + Send + Sync>,
    /// `install_skill(plugin, skill)`.
    pub install_skill: Option<Arc<dyn Fn(&str, &str) -> Result<Value, String> + Send + Sync>>,
    /// `scan_environment()` — the setup wizard's host probe.
    pub scan_environment: Option<JsonProvider>,
    /// `local_sysinfo(request)` — CPU/RAM/GPU telemetry.
    pub local_sysinfo: Option<JsonProvider>,
    /// `get_recommendations(environment)` + the tri-state catalog.
    pub model_recommendations: Option<Arc<dyn Fn(&Value) -> (Value, Value) + Send + Sync>>,
    /// `LOCAL_KG_WATCHER.status()`.
    pub watcher_status: Option<JsonProvider>,
    /// `LOCAL_MODEL` — the onboarding recommendation default.
    pub local_model: String,
    /// `PUBLIC_MODEL` — the same, for the hosted lane.
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

/// `_value(env, name, fallback)` — set-but-empty counts as unset.
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

/// `_workspace_models_payload()` with no model router attached.
///
/// The two *configured* names are environment, so they are real; the two
/// *runtime* fields belong to the model router and are stated as "nothing
/// loaded", which is what an install with no router has.
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

/// `_workspace_settings_payload()` — every field is an environment value.
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

/// The dependency bundle a router is built with.
#[derive(Clone)]
pub struct WorkspaceDeps {
    /// How graph writes leave this process.
    pub seam: GraphSeam,
    /// Everything read from elsewhere.
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
    /// `_workspace_graph()` — the graph object, or `None` when it is off.
    pub fn graph(&self) -> Option<&dyn GraphReads> {
        self.providers.graph_reads.as_deref()
    }

    /// `graph_stats_safe()` — stats, `{"disabled": true}`, or `{"error": …}`.
    pub fn graph_stats_safe(&self) -> Value {
        match self.graph().and_then(GraphReads::stats) {
            Some(stats) => stats,
            None if self.providers.graph_reads.is_some() => {
                json!({"error": "graph stats unavailable"})
            }
            None => json!({"disabled": true}),
        }
    }

    /// The edition/capability matrix `capability_registry.describe()` answers.
    ///
    /// Community is the only edition this binary can be: the Enterprise seam is
    /// a Python plugin protocol (`core/enterprise.py`) with no Rust registrar,
    /// so answering anything else would be a claim the build cannot support.
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

    /// The product version, for callers that want it without the constant.
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
