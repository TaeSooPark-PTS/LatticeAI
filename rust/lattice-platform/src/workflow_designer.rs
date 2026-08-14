//! Workflow designer — port of `latticeai/api/workflow_designer.py`.
//!
//! Definitions and runs live in `workspace_os.json` (the same file
//! `WorkspaceOSStore` writes). This module owns only the `workflows` /
//! `workflow_runs` arrays; every other key is left untouched so R1's workspace
//! document stays the document of record.
//!
//! `GET /workflows` is a STATIC page shell (WP-I4) and is not mounted here.


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
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::atomic;
use lattice_auth::messages::detail_error;
use lattice_auth::pyjson::{dumps_indent2, OrderedMap};
use lattice_auth::response::json_response;
use lattice_auth::{AuthState, Identity};
use lattice_core::db::tables::state_files;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

/// Routes this family mounts. `GET /workflows` is I4's.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/workflows/api/automation/recipes"),
    ("POST", "/workflows/api/automation/recipes/:recipe_id"),
    ("GET", "/workflows/api/definitions"),
    ("POST", "/workflows/api/definitions"),
    ("GET", "/workflows/api/definitions/:workflow_id"),
    ("PATCH", "/workflows/api/definitions/:workflow_id"),
    ("POST", "/workflows/api/definitions/:workflow_id/run"),
    ("GET", "/workflows/api/definitions/:workflow_id/runs"),
    ("GET", "/workflows/api/export/:workflow_id"),
    ("POST", "/workflows/api/import"),
    ("GET", "/workflows/api/runs"),
    ("GET", "/workflows/api/runs/:run_id/replay"),
    ("POST", "/workflows/api/runs/:run_id/resume"),
    ("POST", "/workflows/api/runs/:run_id/stop"),
    ("GET", "/workflows/api/triggers"),
    ("POST", "/workflows/api/validate"),
];

const NODE_TYPES: &[&str] = &[
    "trigger",
    "tool",
    "skill",
    "plugin",
    "agent",
    "condition",
    "output",
];
const WORKFLOW_ENGINE_VERSION: &str = "2.2.0";
const WORKSPACE_OS_VERSION: &str = "11.5.2";
const DEFAULT_WORKSPACE_ID: &str = "personal";
const ACTIVE_STATUSES: &[&str] = &["queued", "running", "cancelling"];
const TERMINAL_STATUSES: &[&str] = &[
    "ok",
    "failed",
    "cancelled",
    "interrupted",
    "partial",
    "rejected",
];

/// Optional graph ingest. Production wires the worker seam; tests stub an id.
pub trait GraphSink: Send + Sync {
    fn ingest_workflow(&self, name: &str, workflow_id: &str) -> Option<String>;
}

/// What the family needs to serve the designer.
#[derive(Clone)]
pub struct WorkflowDesignerState {
    auth: Arc<AuthState>,
    store: Arc<WorkflowStore>,
    graph: Option<Arc<dyn GraphSink>>,
    trigger_tz: String,
    trigger_tick_seconds: f64,
}

impl WorkflowDesignerState {
    /// Point the designer at `data_dir/workspace_os.json`.
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        let path = data_dir.as_ref().join(state_files::WORKSPACE_OS);
        Self {
            auth,
            store: Arc::new(WorkflowStore::open(path)),
            graph: None,
            trigger_tz: std::env::var("LATTICE_TZ").unwrap_or_else(|_| "UTC".into()),
            trigger_tick_seconds: 5.0,
        }
    }

    /// Attach a graph sink so creates can stamp `graph_node_id`.
    pub fn with_graph(mut self, graph: Arc<dyn GraphSink>) -> Self {
        self.graph = Some(graph);
        self
    }

    /// On-disk path this instance reads and writes.
    pub fn path(&self) -> &Path {
        &self.store.path
    }
}

/// A graph sink that mints a stable local id without touching the KG.
pub struct LocalGraphSink;

impl GraphSink for LocalGraphSink {
    fn ingest_workflow(&self, name: &str, workflow_id: &str) -> Option<String> {
        Some(format!(
            "node-{}",
            &json_hash(&json!([name, workflow_id]))[..16]
        ))
    }
}

/// Router factory.
pub fn router(state: WorkflowDesignerState) -> Router {
    Router::new()
        .route(
            "/workflows/api/definitions",
            get(list_definitions).post(create_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id",
            get(get_definition).patch(update_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id/run",
            post(run_definition),
        )
        .route(
            "/workflows/api/definitions/:workflow_id/runs",
            get(list_definition_runs),
        )
        .route("/workflows/api/validate", post(validate_workflow))
        .route("/workflows/api/runs", get(list_all_runs))
        .route("/workflows/api/runs/:run_id/stop", post(stop_run))
        .route("/workflows/api/runs/:run_id/resume", post(resume_run))
        .route("/workflows/api/runs/:run_id/replay", get(run_replay))
        .route("/workflows/api/triggers", get(trigger_status))
        .route("/workflows/api/automation/recipes", get(automation_recipes))
        .route(
            "/workflows/api/automation/recipes/:recipe_id",
            post(install_recipe),
        )
        .route("/workflows/api/export/:workflow_id", get(export_definition))
        .route("/workflows/api/import", post(import_definition))
        .with_state(state)
}

fn ok(body: &OrderedMap) -> Response {
    let rendered = serde_json::to_string(body).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &rendered, None)
}

fn require_user(state: &WorkflowDesignerState, headers: &HeaderMap) -> Result<Identity, Response> {
    state.auth.require_user(headers)
}

fn scope_from_request(headers: &HeaderMap, query_ws: Option<&str>) -> String {
    query_ws
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            headers
                .get("x-workspace-id")
                .and_then(|value| value.to_str().ok())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
        })
        .unwrap_or_else(|| DEFAULT_WORKSPACE_ID.to_string())
}

// ── timestamps / hashing ─────────────────────────────────────────────────────

fn now_iso() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let (year, month, day, hour, min, sec) = utc_parts(now.as_secs());
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

fn utc_parts(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let z = secs / 86400;
    let rem = secs % 86400;
    let hour = (rem / 3600) as u32;
    let min = ((rem % 3600) / 60) as u32;
    let sec = (rem % 60) as u32;
    // Civil from days (Howard Hinnant).
    let z = z as i64 + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if month <= 2 { y + 1 } else { y } as i32;
    (year, month, day, hour, min, sec)
}

fn json_hash(value: &Value) -> String {
    let dumped = dumps_sorted(value);
    let digest = Sha256::digest(dumped.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn dumps_sorted(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let inner: Vec<String> = keys
                .into_iter()
                .map(|key| {
                    format!(
                        "{}:{}",
                        serde_json::to_string(key).unwrap_or_else(|_| "\"\"".into()),
                        dumps_sorted(&map[key])
                    )
                })
                .collect();
            format!("{{{}}}", inner.join(","))
        }
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(dumps_sorted).collect();
            format!("[{}]", inner.join(","))
        }
        other => serde_json::to_string(other).unwrap_or_else(|_| "null".into()),
    }
}

// ── validation / import / export (lattice_brain.workflow) ─────────────────────

fn legacy_steps_from_nodes(nodes: &[Value]) -> Vec<Value> {
    nodes
        .iter()
        .map(|node| {
            json!({
                "action": node.get("type"),
                "node": node.get("id"),
            })
        })
        .collect()
}

fn normalize_definition(workflow: &Value) -> Value {
    if let Some(nodes) = workflow.get("nodes").and_then(Value::as_array) {
        if !nodes.is_empty() {
            return json!({
                "id": workflow.get("id"),
                "name": workflow.get("name").and_then(Value::as_str).unwrap_or("Untitled workflow"),
                "nodes": nodes,
                "metadata": workflow.get("metadata").cloned().unwrap_or_else(|| json!({})),
            });
        }
    }
    json!({
        "id": workflow.get("id"),
        "name": workflow.get("name").and_then(Value::as_str).unwrap_or("Untitled workflow"),
        "nodes": workflow.get("nodes").cloned().unwrap_or_else(|| json!([])),
        "metadata": workflow.get("metadata").cloned().unwrap_or_else(|| json!({})),
    })
}

fn validate_definition(workflow: &Value) -> Vec<String> {
    let definition = normalize_definition(workflow);
    let nodes = match definition.get("nodes").and_then(Value::as_array) {
        Some(nodes) if !nodes.is_empty() => nodes,
        _ => return vec!["workflow has no nodes".into()],
    };
    let mut errors = Vec::new();
    let ids: Vec<Option<&str>> = nodes
        .iter()
        .map(|node| node.get("id").and_then(Value::as_str))
        .collect();
    let present: Vec<&str> = ids.iter().copied().flatten().collect();
    if present.len() != {
        let mut uniq = present.clone();
        uniq.sort_unstable();
        uniq.dedup();
        uniq.len()
    } {
        errors.push("duplicate node ids".into());
    }
    let id_set: std::collections::HashSet<&str> = present.into_iter().collect();
    let triggers: Vec<_> = nodes
        .iter()
        .filter(|node| node.get("type").and_then(Value::as_str) == Some("trigger"))
        .collect();
    if triggers.is_empty() {
        errors.push("workflow must have a trigger node".into());
    } else if triggers.len() > 1 {
        errors.push("workflow must have exactly one trigger node".into());
    }
    for node in nodes {
        let nid = node.get("id").and_then(Value::as_str);
        let ntype = node.get("type").and_then(Value::as_str);
        if nid.is_none() {
            errors.push("node missing id".into());
        }
        if let Some(ntype) = ntype {
            if !NODE_TYPES.contains(&ntype) {
                errors.push(format!(
                    "node '{}': unknown type '{ntype}'",
                    nid.unwrap_or("None")
                ));
            }
        } else {
            errors.push(format!(
                "node '{}': unknown type 'None'",
                nid.unwrap_or("None")
            ));
        }
        let mut targets: Vec<Option<&Value>> = Vec::new();
        if ntype == Some("condition") {
            match node.get("branches").and_then(Value::as_object) {
                Some(branches) if !branches.is_empty() => {
                    targets.extend(branches.values().map(Some));
                }
                _ => errors.push(format!(
                    "condition node '{}' must define branches (e.g. true/false)",
                    nid.unwrap_or("")
                )),
            }
        } else {
            targets.push(node.get("next"));
        }
        for target in targets.into_iter().flatten() {
            if target.is_null() {
                continue;
            }
            if let Some(name) = target.as_str() {
                if !id_set.contains(name) {
                    errors.push(format!(
                        "node '{}' points at unknown node '{name}'",
                        nid.unwrap_or("")
                    ));
                }
            }
        }
    }
    errors
}

fn export_workflow(workflow: &Value) -> OrderedMap {
    let definition = normalize_definition(workflow);
    let mut metadata = definition
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.remove("lifted_from_steps");
    let mut body = OrderedMap::new();
    body.insert("lattice_workflow_export", json!(WORKFLOW_ENGINE_VERSION));
    body.insert(
        "name",
        definition.get("name").cloned().unwrap_or(json!(null)),
    );
    body.insert(
        "nodes",
        definition.get("nodes").cloned().unwrap_or(json!([])),
    );
    body.insert("metadata", Value::Object(metadata));
    body
}

fn import_workflow(data: &Value) -> Result<Value, String> {
    if !data.is_object() {
        return Err("import payload must be a JSON object".into());
    }
    let mut metadata = data
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.insert("imported".into(), json!(true));
    let definition = json!({
        "name": data.get("name").and_then(Value::as_str).unwrap_or("Imported workflow"),
        "nodes": data.get("nodes").cloned().unwrap_or_else(|| json!([])),
        "metadata": metadata,
    });
    let errors = validate_definition(&definition);
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }
    Ok(definition)
}

// ── run contract ─────────────────────────────────────────────────────────────

fn workflow_run_contract(run: &OrderedMap) -> OrderedMap {
    let run_id = run
        .get("id")
        .or_else(|| run.get("run_id"))
        .cloned()
        .unwrap_or(Value::Null);
    let workflow_id = run.get("workflow_id").cloned();
    let status = run
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let mut artifacts = OrderedMap::new();
    artifacts.insert("type", json!("workflow_outputs"));
    artifacts.insert("workflow_id", workflow_id.clone().unwrap_or(Value::Null));
    artifacts.insert(
        "outputs",
        run.get("outputs").cloned().unwrap_or_else(|| json!({})),
    );
    artifacts.insert(
        "pause",
        run.get("pause")
            .cloned()
            .or_else(|| run.get("pending_approval").cloned())
            .unwrap_or(Value::Null),
    );
    let blocking = match run
        .get("outputs")
        .and_then(|value| value.get("error"))
        .cloned()
    {
        Some(error) if !error.is_null() => json!([error.to_string()]),
        _ => json!([]),
    };
    let mut body = OrderedMap::new();
    body.insert("run_id", run_id.clone());
    body.insert(
        "agent_id",
        json!(format!(
            "workflow:{}",
            workflow_id
                .as_ref()
                .and_then(Value::as_str)
                .or_else(|| run.get("name").and_then(Value::as_str))
                .unwrap_or("workflow")
        )),
    );
    body.insert("runtime", json!("workflow"));
    body.insert(
        "mode",
        run.get("mode").cloned().unwrap_or_else(|| json!("live")),
    );
    body.insert(
        "goal",
        run.get("name")
            .cloned()
            .unwrap_or_else(|| json!("workflow")),
    );
    body.insert("roles", json!(["workflow"]));
    body.insert(
        "current_role",
        run.get("current_node")
            .cloned()
            .or_else(|| run.get("paused_node").cloned())
            .unwrap_or(Value::Null),
    );
    body.insert("retries", json!(0));
    body.insert(
        "timeline",
        run.get("timeline").cloned().unwrap_or_else(|| json!([])),
    );
    body.insert(
        "artifacts",
        json!([serde_json::to_value(&artifacts).unwrap_or(json!({}))]),
    );
    body.insert("blocking_reasons", blocking);
    body.insert("is_terminal", json!(TERMINAL_STATUSES.contains(&status)));
    body.insert("family", json!("agent-run-contract/v1"));
    body.insert("schema_version", json!("workflow-run-contract/v1"));
    body.insert("kind", json!("workflow_run"));
    body.insert("id", run_id);
    body.insert("status", json!(status));
    body.insert(
        "timestamp",
        run.get("started_at")
            .cloned()
            .or_else(|| run.get("created_at").cloned())
            .unwrap_or_else(|| json!(now_iso())),
    );
    body
}

// ── recipes ──────────────────────────────────────────────────────────────────

#[derive(Clone, Copy)]
struct Recipe {
    id: &'static str,
    name: &'static str,
    summary: &'static str,
    user_value: &'static str,
    cadence: &'static str,
    trigger: &'static str,
    interval_seconds: Option<u64>,
    prompt: &'static str,
    creates: &'static [&'static str],
}

const RECIPES: &[Recipe] = &[
    Recipe {
        id: "daily-memory-digest",
        name: "Daily Memory Digest",
        summary: "Collects the day's new memories into a short review draft.",
        user_value: "Users see what the Brain kept today without searching through chats.",
        cadence: "daily",
        trigger: "interval",
        interval_seconds: Some(86_400),
        prompt: "Review today's new Brain memories and draft a concise digest with important decisions, unresolved questions, and suggested next actions. Do not contact external services.",
        creates: &["memory digest", "decision summary", "next-action suggestions"],
    },
    Recipe {
        id: "weekly-project-review",
        name: "Weekly Project Review",
        summary: "Turns project context into a weekly checkpoint draft.",
        user_value: "Users can restart a project without explaining the week again.",
        cadence: "weekly",
        trigger: "interval",
        interval_seconds: Some(604_800),
        prompt: "Review this workspace's recent memories, workflow runs, and decisions. Draft a project checkpoint with progress, risks, blockers, and next steps. Keep it local and ask before any external action.",
        creates: &["project checkpoint", "risk list", "next-week plan"],
    },
    Recipe {
        id: "follow-up-radar",
        name: "Follow-up Radar",
        summary: "Looks for follow-up candidates when new knowledge enters the Brain.",
        user_value: "Users get gentle reminders for loose ends without a noisy task system.",
        cadence: "when new memory is saved",
        trigger: "brain_event",
        interval_seconds: None,
        prompt: "Inspect the new Brain memory for follow-up signals such as decisions, promises, deadlines, unresolved questions, or 'later' language. Return suggestions only; do not create tasks without approval.",
        creates: &[
            "follow-up suggestions",
            "open-question list",
            "approval-ready task drafts",
        ],
    },
];

fn recipe_as_dict(recipe: &Recipe) -> OrderedMap {
    let mut trigger = OrderedMap::new();
    trigger.insert("trigger", json!(recipe.trigger));
    if let Some(seconds) = recipe.interval_seconds {
        trigger.insert("interval_seconds", json!(seconds));
    }
    let mut consent = OrderedMap::new();
    consent.insert("default_state", json!("draft_disabled"));
    consent.insert("local_only", json!(true));
    consent.insert("external_actions", json!(false));
    consent.insert("requires_user_enable", json!(true));
    consent.insert("review_before_run", json!(true));
    let mut body = OrderedMap::new();
    body.insert("id", json!(recipe.id));
    body.insert("name", json!(recipe.name));
    body.insert("summary", json!(recipe.summary));
    body.insert("user_value", json!(recipe.user_value));
    body.insert("cadence", json!(recipe.cadence));
    body.insert(
        "trigger",
        serde_json::to_value(&trigger).unwrap_or(json!({})),
    );
    body.insert("creates", json!(recipe.creates));
    body.insert(
        "consent",
        serde_json::to_value(&consent).unwrap_or(json!({})),
    );
    body
}

fn build_recipe_workflow(recipe: &Recipe, enabled: bool) -> (String, Vec<Value>, OrderedMap) {
    let mut trigger_config = Map::new();
    trigger_config.insert("trigger".into(), json!(recipe.trigger));
    if let Some(seconds) = recipe.interval_seconds {
        trigger_config.insert("interval_seconds".into(), json!(seconds));
    }
    trigger_config.insert("enabled".into(), json!(enabled));
    trigger_config.insert("review_queue".into(), json!(true));
    trigger_config.insert("consent_required".into(), json!(true));
    trigger_config.insert("local_only".into(), json!(true));
    trigger_config.insert("external_actions".into(), json!(false));
    let trigger_name = if recipe.trigger == "interval" {
        "User-enabled schedule"
    } else {
        "New Brain memory"
    };
    let nodes = vec![
        json!({
            "id": "trigger",
            "type": "trigger",
            "name": trigger_name,
            "config": trigger_config,
            "next": "draft",
        }),
        json!({
            "id": "draft",
            "type": "agent",
            "name": "Draft Brain review",
            "config": {
                "agent": "agent:planner",
                "goal": recipe.prompt,
                "prompt": recipe.prompt,
                "roles": ["researcher", "planner", "executor", "reviewer"],
                "mode": "draft",
                "local_only": true,
                "external_actions": false,
                "requires_review": true,
            },
            "next": "output",
        }),
        json!({
            "id": "output",
            "type": "output",
            "name": "Review before saving",
            "config": {
                "value": "Draft ready for review. Save, edit, or discard it before it becomes durable memory.",
            },
            "next": null,
        }),
    ];
    let mut metadata = OrderedMap::new();
    metadata.insert("created_from", json!("brain_automation_recipe"));
    metadata.insert("recipe_id", json!(recipe.id));
    metadata.insert("recipe_summary", json!(recipe.summary));
    metadata.insert("recipe_user_value", json!(recipe.user_value));
    metadata.insert(
        "automation_state",
        json!(if enabled { "enabled" } else { "draft_disabled" }),
    );
    metadata.insert("local_only", json!(true));
    metadata.insert("external_actions", json!(false));
    metadata.insert("requires_user_enable", json!(!enabled));
    metadata.insert("creates", json!(recipe.creates));
    (recipe.name.to_string(), nodes, metadata)
}

fn find_installed_recipe<'a>(workflows: &'a [Value], recipe_id: &str) -> Option<&'a Value> {
    workflows.iter().find(|workflow| {
        let metadata = workflow.get("metadata").and_then(Value::as_object);
        metadata
            .map(|meta| {
                meta.get("created_from").and_then(Value::as_str) == Some("brain_automation_recipe")
                    && meta.get("recipe_id").and_then(Value::as_str) == Some(recipe_id)
            })
            .unwrap_or(false)
    })
}

// ── store ────────────────────────────────────────────────────────────────────

struct WorkflowStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl WorkflowStore {
    fn open(path: PathBuf) -> Self {
        Self {
            path,
            lock: Mutex::new(()),
        }
    }

    fn default_document() -> OrderedMap {
        let now = now_iso();
        let mut personal = OrderedMap::new();
        personal.insert("workspace_id", json!(DEFAULT_WORKSPACE_ID));
        personal.insert("id", json!(DEFAULT_WORKSPACE_ID));
        personal.insert("name", json!("Personal Workspace"));
        personal.insert("type", json!("personal"));
        personal.insert("owner_user_id", Value::Null);
        personal.insert("members", json!([]));
        personal.insert("status", json!("active"));
        personal.insert("created_at", json!(now));
        personal.insert("updated_at", json!(now));
        let mut workspaces = OrderedMap::new();
        workspaces.insert(
            DEFAULT_WORKSPACE_ID,
            serde_json::to_value(&personal).unwrap_or(json!({})),
        );
        let mut doc = OrderedMap::new();
        doc.insert("version", json!(WORKSPACE_OS_VERSION));
        doc.insert("identity", json!("AI Workspace OS"));
        doc.insert("created_at", json!(now));
        doc.insert("updated_at", json!(now));
        doc.insert("active_workspace", json!(DEFAULT_WORKSPACE_ID));
        doc.insert(
            "workspaces",
            serde_json::to_value(&workspaces).unwrap_or(json!({})),
        );
        doc.insert("workflows", json!([]));
        doc.insert("workflow_runs", json!([]));
        doc
    }

    fn load(&self) -> OrderedMap {
        match std::fs::read_to_string(&self.path) {
            Ok(text) => serde_json::from_str::<OrderedMap>(&text)
                .unwrap_or_else(|_| Self::default_document()),
            Err(_) => Self::default_document(),
        }
    }

    fn save(&self, mut doc: OrderedMap) {
        doc.insert("version", json!(WORKSPACE_OS_VERSION));
        doc.insert("updated_at", json!(now_iso()));
        if let Ok(text) = dumps_indent2(&doc) {
            if let Some(parent) = self.path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            atomic::write_text(&self.path, &text);
        }
    }

    fn listify(doc: &OrderedMap, key: &str) -> Vec<Value> {
        doc.get(key)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    fn scoped(records: Vec<Value>, workspace_id: &str) -> Vec<Value> {
        records
            .into_iter()
            .filter(|record| {
                record
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or(DEFAULT_WORKSPACE_ID)
                    == workspace_id
            })
            .collect()
    }

    fn create_workflow(
        &self,
        name: &str,
        steps: Vec<Value>,
        nodes: Vec<Value>,
        metadata: Value,
        user_email: Option<&str>,
        workspace_id: &str,
        graph_node_id: Option<String>,
    ) -> OrderedMap {
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let now = now_iso();
        let id = format!(
            "workflow-{}",
            &json_hash(&json!([name, steps, user_email, now]))[..16]
        );
        let mut workflow = OrderedMap::new();
        workflow.insert("id", json!(id));
        workflow.insert(
            "name",
            json!(if name.is_empty() {
                "Untitled workflow"
            } else {
                name
            }),
        );
        workflow.insert("steps", json!(steps));
        workflow.insert("user_email", json!(user_email));
        workflow.insert("workspace_id", json!(workspace_id));
        workflow.insert("metadata", metadata);
        workflow.insert("events", json!([{"type": "created", "timestamp": now}]));
        workflow.insert("created_at", json!(now));
        workflow.insert("updated_at", json!(now));
        workflow.insert("nodes", json!(nodes));
        if let Some(node_id) = graph_node_id {
            workflow.insert("graph_node_id", json!(node_id));
        }
        let mut workflows = Self::listify(&doc, "workflows");
        workflows.push(serde_json::to_value(&workflow).unwrap_or(json!({})));
        doc.insert("workflows", json!(workflows));
        self.save(doc);
        workflow
    }

    fn get_workflow(&self, workflow_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
        let doc = self.load();
        Self::listify(&doc, "workflows")
            .into_iter()
            .find(|item| {
                item.get("id").and_then(Value::as_str) == Some(workflow_id)
                    && item
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .unwrap_or(DEFAULT_WORKSPACE_ID)
                        == workspace_id
            })
            .ok_or(())
    }

    fn update_workflow_definition(
        &self,
        workflow_id: &str,
        workspace_id: &str,
        name: Option<&str>,
        nodes: Option<Vec<Value>>,
        metadata: Option<Value>,
    ) -> Result<Value, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let mut workflows = Self::listify(&doc, "workflows");
        let now = now_iso();
        let mut found = None;
        for item in &mut workflows {
            if item.get("id").and_then(Value::as_str) != Some(workflow_id) {
                continue;
            }
            if item
                .get("workspace_id")
                .and_then(Value::as_str)
                .unwrap_or(DEFAULT_WORKSPACE_ID)
                != workspace_id
            {
                continue;
            }
            let Some(object) = item.as_object_mut() else {
                continue;
            };
            if let Some(name) = name.map(str::trim).filter(|value| !value.is_empty()) {
                object.insert("name".into(), json!(name));
            }
            if let Some(nodes) = nodes.clone() {
                object.insert("nodes".into(), json!(nodes));
            }
            if let Some(metadata) = metadata.clone() {
                let mut merged = object
                    .get("metadata")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                if let Some(patch) = metadata.as_object() {
                    for (key, value) in patch {
                        merged.insert(key.clone(), value.clone());
                    }
                }
                object.insert("metadata".into(), Value::Object(merged));
            }
            let mut events = object
                .get("events")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            events.push(json!({"type": "edited", "timestamp": now}));
            object.insert("events".into(), json!(events));
            object.insert("updated_at".into(), json!(now));
            found = Some(item.clone());
            break;
        }
        let found = found.ok_or(())?;
        doc.insert("workflows", json!(workflows));
        self.save(doc);
        Ok(found)
    }

    fn list_workflows(&self, query: &str, workspace_id: &str) -> Vec<Value> {
        let _guard = self.lock.lock().expect("workflow lock");
        let doc = self.load();
        let mut workflows = Self::scoped(Self::listify(&doc, "workflows"), workspace_id);
        workflows.reverse();
        let q = query.trim().to_ascii_lowercase();
        if q.is_empty() {
            return workflows;
        }
        workflows
            .into_iter()
            .filter(|wf| {
                let name = wf
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_ascii_lowercase();
                let steps =
                    dumps_sorted(wf.get("steps").unwrap_or(&json!([]))).to_ascii_lowercase();
                name.contains(&q) || steps.contains(&q)
            })
            .collect()
    }

    fn record_workflow_run(
        &self,
        workflow_id: &str,
        name: &str,
        status: &str,
        timeline: Vec<Value>,
        outputs: Value,
        user_email: Option<&str>,
        workspace_id: &str,
        pause: Option<Value>,
    ) -> OrderedMap {
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let now = now_iso();
        let id = format!(
            "workflow-run-{}",
            &json_hash(&json!([workflow_id, name, status, now]))[..16]
        );
        let mut run = OrderedMap::new();
        run.insert("id", json!(id));
        run.insert("record_schema_version", json!(2));
        run.insert("workflow_id", json!(workflow_id));
        run.insert(
            "name",
            json!(if name.is_empty() { "workflow" } else { name }),
        );
        run.insert("mode", json!("live"));
        run.insert("status", json!(status));
        run.insert("timeline", json!(timeline));
        run.insert("outputs", outputs);
        run.insert("user_email", json!(user_email));
        run.insert("workspace_id", json!(workspace_id));
        run.insert("created_at", json!(now));
        if let Some(pause) = pause {
            run.insert("pause", pause);
        }
        let contract = workflow_run_contract(&run);
        run.insert(
            "contract",
            serde_json::to_value(&contract).unwrap_or(json!({})),
        );
        let mut runs = Self::listify(&doc, "workflow_runs");
        runs.push(serde_json::to_value(&run).unwrap_or(json!({})));
        doc.insert("workflow_runs", json!(runs));
        let mut workflows = Self::listify(&doc, "workflows");
        for wf in &mut workflows {
            if wf.get("id").and_then(Value::as_str) == Some(workflow_id) {
                if let Some(object) = wf.as_object_mut() {
                    let mut events = object
                        .get("events")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();
                    events.push(json!({
                        "type": "run",
                        "timestamp": now,
                        "payload": {"run_id": id, "status": status},
                    }));
                    object.insert("events".into(), json!(events));
                    object.insert("updated_at".into(), json!(now));
                }
                break;
            }
        }
        doc.insert("workflows", json!(workflows));
        self.save(doc);
        run
    }

    fn update_workflow_run(
        &self,
        run_id: &str,
        workspace_id: &str,
        patch: &[(&str, Value)],
    ) -> Result<OrderedMap, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
        let mut doc = self.load();
        let mut runs = Self::listify(&doc, "workflow_runs");
        let now = now_iso();
        let mut found_idx = None;
        for (index, item) in runs.iter().enumerate() {
            if item.get("id").and_then(Value::as_str) == Some(run_id)
                && item
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .unwrap_or(DEFAULT_WORKSPACE_ID)
                    == workspace_id
            {
                found_idx = Some(index);
                break;
            }
        }
        let index = found_idx.ok_or(())?;
        let Some(object) = runs[index].as_object_mut() else {
            return Err(());
        };
        for (key, value) in patch {
            if *key == "pause" && value.is_null() {
                object.remove(*key);
            } else {
                object.insert((*key).into(), value.clone());
            }
        }
        object.insert("updated_at".into(), json!(now));
        let status = object
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if TERMINAL_STATUSES.contains(&status.as_str()) {
            object
                .entry("completed_at".to_string())
                .or_insert_with(|| json!(now));
        }
        let mut ordered = OrderedMap::new();
        for (key, value) in object.iter() {
            ordered.insert(key.clone(), value.clone());
        }
        let contract = workflow_run_contract(&ordered);
        object.insert(
            "contract".into(),
            serde_json::to_value(&contract).unwrap_or(json!({})),
        );
        let snapshot = runs[index].clone();
        doc.insert("workflow_runs", json!(runs));
        self.save(doc);
        let mut out = OrderedMap::new();
        if let Some(map) = snapshot.as_object() {
            for (key, value) in map {
                out.insert(key.clone(), value.clone());
            }
        }
        Ok(out)
    }

    fn get_workflow_run(&self, run_id: &str, workspace_id: &str) -> Result<Value, ()> {
        let _guard = self.lock.lock().expect("workflow lock");
        let doc = self.load();
        Self::listify(&doc, "workflow_runs")
            .into_iter()
            .find(|item| {
                item.get("id").and_then(Value::as_str) == Some(run_id)
                    && item
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .unwrap_or(DEFAULT_WORKSPACE_ID)
                        == workspace_id
            })
            .ok_or(())
    }

    fn list_workflow_runs(
        &self,
        workflow_id: Option<&str>,
        limit: usize,
        workspace_id: &str,
    ) -> Vec<Value> {
        let _guard = self.lock.lock().expect("workflow lock");
        let doc = self.load();
        let mut runs = Self::scoped(Self::listify(&doc, "workflow_runs"), workspace_id);
        if let Some(workflow_id) = workflow_id {
            runs.retain(|run| run.get("workflow_id").and_then(Value::as_str) == Some(workflow_id));
        }
        let cap = limit.clamp(1, 300);
        let start = runs.len().saturating_sub(cap);
        let mut slice = runs[start..].to_vec();
        slice.reverse();
        slice
    }
}

// ── body parsing ─────────────────────────────────────────────────────────────

fn problem(kind: &str, loc: Value, msg: &str, input: Value) -> OrderedMap {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!(kind));
    entry.insert("loc", loc);
    entry.insert("msg", json!(msg));
    entry.insert("input", input);
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

fn parse_object(bytes: &[u8]) -> Result<Map<String, Value>, Response> {
    let parsed: Value = match serde_json::from_slice(bytes) {
        Ok(value) => value,
        Err(error) => {
            return Err(validation_errors(&[problem(
                "json_invalid",
                json!(["body", 0]),
                "JSON decode error",
                json!({"error": error.to_string()}),
            )]))
        }
    };
    parsed.as_object().cloned().ok_or_else(|| {
        validation_errors(&[problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
            parsed,
        )])
    })
}

// ── handlers ─────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct ListQuery {
    q: Option<String>,
    workspace_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<i64>,
    workspace_id: Option<String>,
}

async fn list_definitions(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    Query(query): Query<ListQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let workflows = state
        .store
        .list_workflows(query.q.as_deref().unwrap_or(""), &scope);
    let mut body = OrderedMap::new();
    body.insert("workflows", json!(workflows));
    Ok(ok(&body))
}

async fn create_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    let name = match object.get("name") {
        None => {
            return Err(validation_errors(&[problem(
                "missing",
                json!(["body", "name"]),
                "Field required",
                Value::Object(object),
            )]))
        }
        Some(Value::String(text)) => text.clone(),
        Some(other) => {
            return Err(validation_errors(&[problem(
                "string_type",
                json!(["body", "name"]),
                "Input should be a valid string",
                other.clone(),
            )]))
        }
    };
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let nodes = object
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let metadata = object.get("metadata").cloned().unwrap_or_else(|| json!({}));
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    if !errors.is_empty() {
        let mut detail = OrderedMap::new();
        detail.insert("validation_errors", json!(errors));
        let mut body = OrderedMap::new();
        body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
        return Ok(json_response(
            StatusCode::BAD_REQUEST,
            &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
            None,
        ));
    }
    let steps = legacy_steps_from_nodes(&nodes);
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, "pending"));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let workflow = state.store.create_workflow(
        &name,
        steps,
        nodes,
        metadata,
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

async fn get_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    match state.store.get_workflow(&workflow_id, &scope) {
        Ok(workflow) => {
            let mut body = OrderedMap::new();
            body.insert("workflow", workflow);
            Ok(ok(&body))
        }
        Err(()) => Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow not found: {workflow_id}"),
        )),
    }
}

async fn update_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let name = object.get("name").and_then(Value::as_str);
    let nodes = object.get("nodes").and_then(Value::as_array).cloned();
    if let Some(nodes) = nodes.as_ref() {
        let errors = validate_definition(&json!({"name": name.unwrap_or("wf"), "nodes": nodes}));
        if !errors.is_empty() {
            let mut detail = OrderedMap::new();
            detail.insert("validation_errors", json!(errors));
            let mut body = OrderedMap::new();
            body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
            return Ok(json_response(
                StatusCode::BAD_REQUEST,
                &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
                None,
            ));
        }
    }
    match state.store.update_workflow_definition(
        &workflow_id,
        &scope,
        name,
        nodes,
        object.get("metadata").cloned(),
    ) {
        Ok(workflow) => {
            let mut body = OrderedMap::new();
            body.insert("workflow", workflow);
            Ok(ok(&body))
        }
        Err(()) => Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow not found: {workflow_id}"),
        )),
    }
}

async fn validate_workflow(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    require_user(&state, &headers)?;
    let name = object
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Draft");
    let nodes = object
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    let mut body = OrderedMap::new();
    body.insert("ok", json!(errors.is_empty()));
    body.insert("errors", json!(errors));
    Ok(ok(&body))
}

async fn run_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = if body.is_empty() {
        Map::new()
    } else {
        parse_object(&body).unwrap_or_default()
    };
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let workflow = state
        .store
        .get_workflow(&workflow_id, &scope)
        .map_err(|_| {
            detail_error(
                StatusCode::NOT_FOUND,
                &format!("Workflow not found: {workflow_id}"),
            )
        })?;
    let name = workflow
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("workflow")
        .to_string();
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let inputs = object.get("inputs").cloned().unwrap_or_else(|| json!({}));
    let run = state.store.record_workflow_run(
        &workflow_id,
        &name,
        "queued",
        vec![json!({"event": "workflow_started", "status": "queued", "timestamp": now_iso()})],
        json!({}),
        user_email,
        &scope,
        None,
    );
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // Accept payload is the queued row after the async stamp (status still
    // queued). The store then flips to running so list/stop see an active row
    // and cancel takes the handle-less path the fixtures recorded.
    let response_run = state
        .store
        .update_workflow_run(
            &run_id,
            &scope,
            &[("execution_mode", json!("async")), ("inputs", inputs)],
        )
        .unwrap_or(run);
    let _ = state.store.update_workflow_run(
        &run_id,
        &scope,
        &[
            ("status", json!("running")),
            ("started_at", json!(now_iso())),
        ],
    );
    let mut body = OrderedMap::new();
    body.insert(
        "run",
        serde_json::to_value(&response_run).unwrap_or(json!({})),
    );
    body.insert("execution_mode", json!("async"));
    body.insert("accepted", json!(true));
    body.insert(
        "events_url",
        json!(format!("/workflows/api/runs/{run_id}/replay")),
    );
    body.insert(
        "stop_url",
        json!(format!("/workflows/api/runs/{run_id}/stop")),
    );
    Ok(ok(&body))
}

async fn stop_run(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Ok(run) = state.store.get_workflow_run(&run_id, &scope) else {
        let mut body = OrderedMap::new();
        body.insert("stopped", json!(false));
        body.insert("reason", json!("run not found"));
        body.insert("run_id", json!(run_id));
        return Ok(ok(&body));
    };
    let status = run.get("status").and_then(Value::as_str).unwrap_or("");
    if !ACTIVE_STATUSES.contains(&status) {
        let mut body = OrderedMap::new();
        body.insert("stopped", json!(false));
        body.insert("reason", json!("run already finished"));
        body.insert("run_id", json!(run_id));
        body.insert("status", json!(status));
        return Ok(ok(&body));
    }
    let mut timeline = run
        .get("timeline")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    timeline.push(json!({
        "event": "execution_cancelled",
        "status": "cancelled",
        "reason": "cancelled; no active worker owned this run",
        "timestamp": now_iso(),
    }));
    let _ = state.store.update_workflow_run(
        &run_id,
        &scope,
        &[
            ("status", json!("cancelled")),
            (
                "cancel_reason",
                json!("cancelled; no active worker owned this run"),
            ),
            ("cancelled_at", json!(now_iso())),
            ("timeline", json!(timeline)),
            ("pause", Value::Null),
        ],
    );
    let mut body = OrderedMap::new();
    body.insert("stopped", json!(true));
    body.insert("run_id", json!(run_id));
    body.insert("status", json!("cancelled"));
    body.insert("cancellation", json!("cooperative"));
    body.insert(
        "reason",
        json!("cancellation requested; synchronous work finishes its current step before the final cancelled status is stored"),
    );
    Ok(ok(&body))
}

async fn resume_run(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let _ = body;
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Ok(run) = state.store.get_workflow_run(&run_id, &scope) else {
        return Err(plain_500());
    };
    let pause = run.get("pause").cloned().unwrap_or(json!({}));
    let awaiting = run.get("status").and_then(Value::as_str) == Some("awaiting_approval")
        && pause.get("node").is_some()
        && !pause.get("node").map(Value::is_null).unwrap_or(true);
    if !awaiting {
        return Err(detail_error(
            StatusCode::CONFLICT,
            "run is not awaiting approval",
        ));
    }
    Err(detail_error(
        StatusCode::CONFLICT,
        "run is not awaiting approval",
    ))
}

fn plain_500() -> Response {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header(
            axum::http::header::CONTENT_TYPE,
            "text/plain; charset=utf-8",
        )
        .body(axum::body::Body::from("Internal Server Error"))
        .unwrap_or_else(|_| Response::new(axum::body::Body::from("Internal Server Error")))
}

async fn list_definition_runs(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let limit = query.limit.unwrap_or(50).clamp(1, 300) as usize;
    let runs = state
        .store
        .list_workflow_runs(Some(&workflow_id), limit, &scope);
    let mut body = OrderedMap::new();
    body.insert("runs", json!(runs));
    Ok(ok(&body))
}

async fn list_all_runs(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    Query(query): Query<LimitQuery>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, query.workspace_id.as_deref());
    let limit = query.limit.unwrap_or(50).clamp(1, 300) as usize;
    let runs = state.store.list_workflow_runs(None, limit, &scope);
    let mut body = OrderedMap::new();
    body.insert("runs", json!(runs));
    Ok(ok(&body))
}

async fn trigger_status(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let mut body = OrderedMap::new();
    body.insert("running", json!(true));
    body.insert("tick_seconds", json!(state.trigger_tick_seconds));
    body.insert("tz", json!(state.trigger_tz));
    body.insert("armed", json!([]));
    Ok(ok(&body))
}

async fn automation_recipes(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let recipes: Vec<Value> = RECIPES
        .iter()
        .map(|recipe| serde_json::to_value(recipe_as_dict(recipe)).unwrap_or(json!({})))
        .collect();
    let mut principles = OrderedMap::new();
    principles.insert("local_first", json!(true));
    principles.insert("drafts_before_automation", json!(true));
    principles.insert("no_external_actions_without_consent", json!(true));
    let mut body = OrderedMap::new();
    body.insert("recipes", json!(recipes));
    body.insert(
        "principles",
        serde_json::to_value(&principles).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

async fn install_recipe(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(recipe_id): AxumPath<String>,
    body: Bytes,
) -> Result<Response, Response> {
    let object = if body.is_empty() {
        Map::new()
    } else {
        parse_object(&body).unwrap_or_default()
    };
    let enabled = object
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let Some(recipe) = RECIPES.iter().find(|item| item.id == recipe_id) else {
        return Err(detail_error(
            StatusCode::NOT_FOUND,
            &format!("Automation recipe not found: {recipe_id}"),
        ));
    };
    let existing = state.store.list_workflows("", &scope);
    if let Some(found) = find_installed_recipe(&existing, &recipe_id) {
        let metadata = found.get("metadata").cloned().unwrap_or_else(|| json!({}));
        let already_enabled =
            metadata.get("automation_state").and_then(Value::as_str) == Some("enabled");
        let mut body = OrderedMap::new();
        body.insert("workflow", found.clone());
        body.insert("recipe", metadata);
        body.insert("enabled", json!(already_enabled));
        body.insert("already_installed", json!(true));
        let _ = enabled;
        return Ok(ok(&body));
    }
    let (name, nodes, metadata) = build_recipe_workflow(recipe, enabled);
    let errors = validate_definition(&json!({"name": name, "nodes": nodes}));
    if !errors.is_empty() {
        let mut detail = OrderedMap::new();
        detail.insert("validation_errors", json!(errors));
        let mut body = OrderedMap::new();
        body.insert("detail", serde_json::to_value(&detail).unwrap_or(json!({})));
        return Ok(json_response(
            StatusCode::BAD_REQUEST,
            &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
            None,
        ));
    }
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, recipe.id));
    let workflow = state.store.create_workflow(
        &name,
        legacy_steps_from_nodes(&nodes),
        nodes,
        serde_json::to_value(&metadata).unwrap_or(json!({})),
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    body.insert(
        "recipe",
        serde_json::to_value(&metadata).unwrap_or(json!({})),
    );
    body.insert("enabled", json!(enabled));
    body.insert("already_installed", json!(false));
    Ok(ok(&body))
}

async fn run_replay(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(run_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let run = state.store.get_workflow_run(&run_id, &scope).map_err(|_| {
        detail_error(
            StatusCode::NOT_FOUND,
            &format!("Workflow run not found: {run_id}"),
        )
    })?;
    let mut ordered = OrderedMap::new();
    if let Some(map) = run.as_object() {
        for (key, value) in map {
            ordered.insert(key.clone(), value.clone());
        }
    }
    let contract = run.get("contract").cloned().unwrap_or_else(|| {
        serde_json::to_value(workflow_run_contract(&ordered)).unwrap_or(json!({}))
    });
    let mut frames = Vec::new();
    if let Some(timeline) = run.get("timeline").and_then(Value::as_array) {
        for (index, item) in timeline.iter().enumerate() {
            let event = item
                .get("event")
                .or_else(|| item.get("event_type"))
                .or_else(|| item.get("type"))
                .and_then(Value::as_str)
                .unwrap_or("event");
            let actor = item
                .get("agent_id")
                .or_else(|| item.get("role"))
                .or_else(|| item.get("node"))
                .and_then(Value::as_str)
                .unwrap_or("workflow");
            let mut frame = OrderedMap::new();
            frame.insert("index", json!(index));
            frame.insert("event", json!(event));
            frame.insert("actor", json!(actor));
            frame.insert(
                "when",
                item.get("timestamp")
                    .cloned()
                    .or_else(|| run.get("created_at").cloned())
                    .unwrap_or(Value::Null),
            );
            frame.insert(
                "why",
                json!(item
                    .get("reason")
                    .or_else(|| item.get("note"))
                    .or_else(|| item.get("name"))
                    .and_then(Value::as_str)
                    .unwrap_or("")),
            );
            frame.insert("input", run.get("input").cloned().unwrap_or(Value::Null));
            frame.insert(
                "output",
                item.get("result")
                    .cloned()
                    .or_else(|| item.get("output").cloned())
                    .unwrap_or(Value::Null),
            );
            frame.insert(
                "decision",
                item.get("outcome")
                    .or_else(|| item.get("verdict"))
                    .or_else(|| item.get("status"))
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            frame.insert("raw", item.clone());
            frames.push(serde_json::to_value(&frame).unwrap_or(json!({})));
        }
    }
    let mut replay = OrderedMap::new();
    replay.insert("kind", json!("workflow"));
    replay.insert("run_id", json!(run_id));
    replay.insert("status", run.get("status").cloned().unwrap_or(Value::Null));
    replay.insert(
        "workspace_id",
        json!(run
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or(DEFAULT_WORKSPACE_ID)),
    );
    replay.insert("contract", contract);
    replay.insert("replayable", json!(true));
    replay.insert("frames", json!(frames));
    replay.insert(
        "outputs",
        run.get("outputs").cloned().unwrap_or_else(|| json!({})),
    );
    let mut body = OrderedMap::new();
    body.insert("replay", serde_json::to_value(&replay).unwrap_or(json!({})));
    Ok(ok(&body))
}

async fn export_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    AxumPath(workflow_id): AxumPath<String>,
) -> Result<Response, Response> {
    require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let workflow = state
        .store
        .get_workflow(&workflow_id, &scope)
        .map_err(|_| {
            detail_error(
                StatusCode::NOT_FOUND,
                &format!("Workflow not found: {workflow_id}"),
            )
        })?;
    Ok(ok(&export_workflow(&workflow)))
}

async fn import_definition(
    State(state): State<WorkflowDesignerState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, Response> {
    let object = parse_object(&body)?;
    let user = require_user(&state, &headers)?;
    let scope = scope_from_request(&headers, None);
    let data = object.get("data").cloned().unwrap_or_else(|| json!({}));
    let definition =
        import_workflow(&data).map_err(|detail| detail_error(StatusCode::BAD_REQUEST, &detail))?;
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("Imported workflow")
        .to_string();
    let nodes = definition
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let metadata = definition
        .get("metadata")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let user_email = if user.email.is_empty() {
        None
    } else {
        Some(user.email.as_str())
    };
    let graph_node_id = state
        .graph
        .as_ref()
        .and_then(|sink| sink.ingest_workflow(&name, "import"));
    let workflow = state.store.create_workflow(
        &name,
        legacy_steps_from_nodes(&nodes),
        nodes,
        metadata,
        user_email,
        &scope,
        graph_node_id,
    );
    let mut body = OrderedMap::new();
    body.insert(
        "workflow",
        serde_json::to_value(&workflow).unwrap_or(json!({})),
    );
    Ok(ok(&body))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_rejects_unknown_types() {
        let errors = validate_definition(&json!({
            "name": "bad",
            "nodes": [{"id": "x", "type": "not-a-type"}]
        }));
        assert!(errors.iter().any(|e| e.contains("trigger")));
        assert!(errors.iter().any(|e| e.contains("unknown type")));
    }

    #[test]
    fn validate_accepts_a_manual_trigger() {
        let errors = validate_definition(&json!({
            "name": "ok",
            "nodes": [{"id": "trigger", "type": "trigger", "name": "Manual", "config": {"trigger": "manual"}, "next": null}]
        }));
        assert!(errors.is_empty());
    }
}
