//! The wire shapes of `/rust/agent/*` — request in, run payload out.
//!
//! Kept apart from the handlers so the *protocol* is readable on its own. Two
//! things it is deliberately faithful about:
//!
//! * the request mirrors Python's `AgentRequest` field for field, plus the data
//!   the native loop needs because it is not the process that owns the tool
//!   registry (the policy table, the tool-name sets, the prompts);
//! * the response mirrors what `AgentHTTPController._finish` and
//!   `_pause_for_approval` return, so a client written against `/agent` reads
//!   `/rust/agent/run` without a second parser.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::agentloop::{
    default_file_create_actions, default_governed_tools, default_scoped_knowledge_tools, LoopDeps,
    Prompts, RunRequest,
};
use crate::policy::PolicyTable;
use crate::pystr::{is_truthy, py_str};
use crate::sandbox::Workspace;
use crate::state::{AgentRunContext, AgentState};
use crate::transcript::path_name;
use crate::worker::WorkerClient;

/// `PREVIEWABLE_EXTENSIONS`, sorted for binary search.
const PREVIEWABLE_EXTENSIONS: [&str; 20] = [
    ".css",
    ".csv",
    ".htm",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".markdown",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".svelte",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
];

fn default_max_steps() -> u32 {
    25
}

fn default_temperature() -> f64 {
    0.1
}

fn default_language_hint() -> String {
    "English".into()
}

/// `POST /rust/agent/run`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RunBody {
    pub message: String,
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default = "default_max_steps")]
    pub max_steps: u32,
    #[serde(default = "default_temperature")]
    pub temperature: f64,
    #[serde(default)]
    pub user_email: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default)]
    pub planning_model: Option<String>,
    #[serde(default)]
    pub executing_model: Option<String>,
    #[serde(default)]
    pub reviewing_model: Option<String>,
    #[serde(default)]
    pub permission_mode: Option<String>,
    #[serde(default)]
    pub human_in_loop: bool,
    #[serde(default)]
    pub project_id: Option<String>,
    #[serde(default)]
    pub stream: bool,
    #[serde(default = "default_language_hint")]
    pub language_hint: String,
    #[serde(default)]
    pub recent_conversation: Option<String>,
    #[serde(default)]
    pub project_context: Option<String>,
    #[serde(default)]
    pub self_model_summary: Option<String>,
    // ── registry data the loop takes as input, never re-derives ─────────
    #[serde(default)]
    pub policies: Option<PolicyTable>,
    #[serde(default)]
    pub file_create_actions: Option<Vec<String>>,
    #[serde(default)]
    pub governed_tools: Option<Vec<String>>,
    #[serde(default)]
    pub scoped_knowledge_tools: Option<Vec<String>>,
    #[serde(default)]
    pub tool_names: Option<Vec<String>>,
    #[serde(default = "governor_default")]
    pub governor_enabled: bool,
    #[serde(default)]
    pub planner_prompt: Option<String>,
    #[serde(default)]
    pub executor_prompt: Option<String>,
    #[serde(default)]
    pub critic_prompt: Option<String>,
}

fn governor_default() -> bool {
    true
}

fn set_of(values: Option<&Vec<String>>, fallback: BTreeSet<String>) -> BTreeSet<String> {
    match values {
        Some(values) => values.iter().cloned().collect(),
        None => fallback,
    }
}

impl RunBody {
    /// `max(1, min(max_steps, 50))`.
    pub fn clamped_max_steps(&self) -> u32 {
        self.max_steps.clamp(1, 50)
    }

    /// The loop request this body describes.
    pub fn to_request(&self) -> RunRequest {
        RunRequest {
            message: self.message.clone(),
            conversation_id: self.conversation_id.clone(),
            workspace_id: self.workspace_id.clone(),
            source: self.source.clone(),
            user_email: self.user_email.clone(),
            temperature: self.temperature,
            max_steps: self.clamped_max_steps(),
            // Python fixes the verification retry ceiling at the call site.
            max_retry: 3,
            planning_model: self.planning_model.clone(),
            executing_model: self.executing_model.clone(),
            reviewing_model: self.reviewing_model.clone(),
            permission_mode: self.permission_mode.clone(),
            language_hint: self.language_hint.clone(),
            project_id: self.project_id.clone(),
            recent_conversation: self.recent_conversation.clone(),
            project_context: self.project_context.clone().unwrap_or_default(),
            self_model_summary: self.self_model_summary.clone().unwrap_or_default(),
        }
    }

    /// The ports this body configures around a worker and a workspace.
    pub fn to_deps(&self, worker: WorkerClient, workspace: Workspace) -> LoopDeps {
        let policies = self.policies.clone().unwrap_or_default();
        // Absent `tool_names` means the escalation hint names whatever the
        // policy table knows, which is the closest honest stand-in for
        // `sorted(deps.tool_governance)`.
        let tool_names = self
            .tool_names
            .clone()
            .unwrap_or_else(|| policies.tools.keys().cloned().collect::<Vec<String>>());
        LoopDeps {
            policies,
            tool_names,
            file_create_actions: set_of(
                self.file_create_actions.as_ref(),
                default_file_create_actions(),
            ),
            governed_tools: set_of(self.governed_tools.as_ref(), default_governed_tools()),
            scoped_knowledge_tools: set_of(
                self.scoped_knowledge_tools.as_ref(),
                default_scoped_knowledge_tools(),
            ),
            governor_enabled: self.governor_enabled,
            prompts: Prompts {
                planner: self.planner_prompt.clone().unwrap_or_default(),
                executor: self.executor_prompt.clone().unwrap_or_default(),
                critic: self.critic_prompt.clone().unwrap_or_default(),
            },
            ..LoopDeps::new(worker, workspace)
        }
    }
}

/// `POST /rust/agent/resume`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ResumeBody {
    #[serde(default)]
    pub run_id: Option<String>,
    #[serde(default)]
    pub approval_token: Option<String>,
    #[serde(default = "approved_default")]
    pub approved: bool,
    /// Python accepts either spelling; `approve` wins when present.
    #[serde(default)]
    pub approve: Option<bool>,
    #[serde(default)]
    pub modified_plan: Option<Value>,
    #[serde(default)]
    pub edited_plan: Option<Value>,
    #[serde(default)]
    pub executing_model: Option<String>,
    #[serde(default)]
    pub reviewing_model: Option<String>,
    #[serde(default)]
    pub user_email: Option<String>,
}

fn approved_default() -> bool {
    true
}

impl ResumeBody {
    pub fn is_approved(&self) -> bool {
        self.approve.unwrap_or(self.approved)
    }

    pub fn plan_edit(&self) -> Option<&Value> {
        self.edited_plan
            .as_ref()
            .or(self.modified_plan.as_ref())
            .filter(|plan| is_truthy(plan))
    }
}

/// `collect_created_files`.
pub fn collect_created_files(
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> Value {
    let mut files = Vec::new();
    for step in transcript {
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        if !file_create_actions.contains(action) {
            continue;
        }
        let result = step.get("result").cloned().unwrap_or(json!({}));
        if let Some(created) = result.get("created_files").and_then(Value::as_array) {
            for path in created {
                let path = py_str(path);
                files.push(json!({
                    "path": path, "filename": path_name(&path), "bytes": 0, "action": action,
                }));
            }
            continue;
        }
        if let Some(path) = result.get("path").filter(|value| is_truthy(value)) {
            let path = py_str(path);
            files.push(json!({
                "path": path,
                "filename": path_name(&path),
                "bytes": result.get("bytes").cloned().unwrap_or(json!(0)),
                "action": action,
            }));
        }
    }
    Value::Array(files)
}

/// `collect_artifacts`.
pub fn collect_artifacts(transcript: &[Value], file_create_actions: &BTreeSet<String>) -> Value {
    let mut artifacts = Vec::new();
    for step in transcript {
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        if !file_create_actions.contains(action) {
            continue;
        }
        let Some(result) = step.get("result").filter(|value| value.is_object()) else {
            continue;
        };
        let repaired = step
            .get("content_sanitize")
            .and_then(|meta| meta.get("repaired"))
            .is_some_and(is_truthy);
        let paths: Vec<String> = match result.get("created_files").and_then(Value::as_array) {
            Some(created) => created.iter().map(py_str).collect(),
            None => result
                .get("path")
                .filter(|value| is_truthy(value))
                .map(|path| vec![py_str(path)])
                .unwrap_or_default(),
        };
        for path in &paths {
            let name = path_name(path).to_lowercase();
            let extension = name.rfind('.').map(|dot| name[dot..].to_string());
            artifacts.push(json!({
                "kind": "file",
                "path": path,
                "filename": path_name(path),
                "bytes": if paths.len() == 1 {
                    result.get("bytes").cloned().unwrap_or(json!(0))
                } else {
                    json!(0)
                },
                "previewable": extension
                    .is_some_and(|ext| PREVIEWABLE_EXTENSIONS.binary_search(&ext.as_str()).is_ok()),
                "valid": true,
                "repaired": repaired,
            }));
        }
    }
    Value::Array(artifacts)
}

/// The terminal payload, mirroring `AgentHTTPController._finish`.
///
/// Two named differences from Python's: `explanation` (plain-language outcome
/// prose, produced by `latticeai.core.run_explain`, which stays with the
/// surface that renders it) is absent, and `audit` — the events the loop
/// raised — is present, because the seam has no audit sink to write them to.
pub fn finish_payload(
    ctx: &AgentRunContext,
    body: &RunBody,
    workspace: &Workspace,
    file_create_actions: &BTreeSet<String>,
    audit: &[Value],
) -> Value {
    let message = if ctx.final_message.is_empty() {
        "작업을 완료했습니다.".to_string()
    } else {
        ctx.final_message.clone()
    };
    let mut payload = json!({
        "status": if ctx.state == AgentState::Done { "ok" } else { "failed" },
        "response": message,
        "workspace": workspace.root().display().to_string(),
        "steps": ctx.transcript,
        "state_history": ctx.state_history,
        "final_state": ctx.state.as_str(),
        "created_files": collect_created_files(&ctx.transcript, file_create_actions),
        "artifacts": collect_artifacts(&ctx.transcript, file_create_actions),
        "loop": ctx.trace.summary(),
        "audit": audit,
    });
    if let Some(project_id) = &body.project_id {
        payload["project_id"] = json!(project_id);
    }
    payload
}

/// The pause payload, mirroring `AgentHTTPController._pause_for_approval`.
pub fn pause_payload(
    ctx: &AgentRunContext,
    body: &RunBody,
    requirements: &Value,
    run_id: &str,
    token: &str,
    expires_at: &str,
) -> Value {
    let mut payload = Map::new();
    payload.insert(
        "status".into(),
        json!(if body.human_in_loop {
            "waiting_approval"
        } else {
            "awaiting_approval"
        }),
    );
    payload.insert("run_id".into(), json!(run_id));
    payload.insert(
        "approval".into(),
        json!({
            "token": token,
            "expires_at": expires_at,
            "plan_summary": requirements.get("plan_summary").cloned().unwrap_or(json!("")),
        }),
    );
    payload.insert(
        "response".into(),
        json!(
            "이 작업에는 승인이 필요한 단계가 있어 실행을 잠시 멈췄습니다. \
계획을 확인한 뒤 승인하면 이어서 실행합니다."
        ),
    );
    payload.insert("plan".into(), Value::Object(ctx.plan.clone()));
    payload.insert("steps".into(), json!(ctx.transcript));
    payload.insert("state_history".into(), json!(ctx.state_history));
    payload.insert(
        "final_state".into(),
        json!(AgentState::WaitingApproval.as_str()),
    );
    payload.insert(
        "non_auto_steps".into(),
        requirements
            .get("non_auto_steps")
            .cloned()
            .unwrap_or(json!([])),
    );
    payload.insert("planning_model".into(), json!(body.planning_model));
    payload.insert("executing_model".into(), json!(body.executing_model));
    payload.insert("reviewing_model".into(), json!(body.reviewing_model));
    payload.insert("loop".into(), ctx.trace.summary());
    if body.human_in_loop {
        // Historical wire field — the run id doubles as the context id.
        payload.insert("context_id".into(), json!(run_id));
    }
    Value::Object(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn actions() -> BTreeSet<String> {
        default_file_create_actions()
    }

    #[test]
    fn the_defaults_are_pythons_agent_request_defaults() {
        let body: RunBody = serde_json::from_str(r#"{"message": "hi"}"#).expect("body");
        assert_eq!(body.max_steps, 25);
        assert_eq!(body.temperature, 0.1);
        assert!(!body.stream && !body.human_in_loop);
        assert!(body.governor_enabled, "production wires a governor");
        assert_eq!(body.language_hint, "English");
        let request = body.to_request();
        assert_eq!(request.max_steps, 25);
        assert_eq!(request.max_retry, 3);
    }

    #[test]
    fn max_steps_is_clamped_into_one_to_fifty() {
        for (raw, expected) in [(0, 1), (1, 1), (25, 25), (50, 50), (5_000, 50)] {
            let body = RunBody {
                max_steps: raw,
                ..RunBody::default()
            };
            assert_eq!(body.clamped_max_steps(), expected, "{raw}");
            assert_eq!(body.to_request().max_steps, expected);
        }
    }

    #[test]
    fn the_registry_data_defaults_to_the_python_constants() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("ws")).expect("workspace");
        let body = RunBody::default();
        let deps = body.to_deps(WorkerClient::new("http://127.0.0.1:1"), workspace.clone());
        assert_eq!(deps.file_create_actions, default_file_create_actions());
        assert_eq!(deps.governed_tools, default_governed_tools());
        assert_eq!(
            deps.scoped_knowledge_tools,
            default_scoped_knowledge_tools()
        );
        assert!(deps.tool_names.is_empty(), "an empty table names nothing");

        let body = RunBody {
            file_create_actions: Some(vec!["only_this".into()]),
            governed_tools: Some(vec![]),
            tool_names: Some(vec!["a".into(), "b".into()]),
            ..RunBody::default()
        };
        let deps = body.to_deps(WorkerClient::new("http://127.0.0.1:1"), workspace);
        assert_eq!(deps.file_create_actions.len(), 1);
        assert!(
            deps.governed_tools.is_empty(),
            "an explicit empty set is honoured"
        );
        assert_eq!(deps.tool_names, vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn resume_reads_either_spelling_of_approval_and_of_the_edited_plan() {
        let body: ResumeBody = serde_json::from_str("{}").expect("body");
        assert!(body.is_approved(), "Python's default is approved=True");
        assert_eq!(body.plan_edit(), None);

        let body: ResumeBody =
            serde_json::from_str(r#"{"approved": true, "approve": false}"#).expect("body");
        assert!(!body.is_approved(), "the explicit `approve` wins");

        let body: ResumeBody = serde_json::from_str(
            r#"{"modified_plan": {"goal": "m"}, "edited_plan": {"goal": "e"}}"#,
        )
        .expect("body");
        assert_eq!(body.plan_edit(), Some(&json!({"goal": "e"})));
        let body: ResumeBody =
            serde_json::from_str(r#"{"modified_plan": {"goal": "m"}}"#).expect("body");
        assert_eq!(body.plan_edit(), Some(&json!({"goal": "m"})));
        // An empty edit is no edit.
        let body: ResumeBody = serde_json::from_str(r#"{"edited_plan": {}}"#).expect("body");
        assert_eq!(body.plan_edit(), None);
    }

    #[test]
    fn created_files_and_artifacts_read_the_same_steps() {
        let transcript = vec![
            json!({"state": "EXECUTING", "action": "write_file", "args": {"path": "a.md"},
                   "result": {"path": "a.md", "bytes": 12}}),
            json!({"state": "EXECUTING", "action": "create_web_project",
                   "result": {"created_files": ["site/index.html", "site/app.js"]}}),
            json!({"state": "EXECUTING", "action": "read_file", "result": {"path": "skip.md"}}),
            json!({"state": "EXECUTING", "action": "write_file", "args": {"path": "blocked.md"},
                   "error": "BLOCKED"}),
        ];
        let files = collect_created_files(&transcript, &actions());
        assert_eq!(files.as_array().expect("files").len(), 3);
        assert_eq!(
            files[0],
            json!({"path": "a.md", "filename": "a.md", "bytes": 12,
                                    "action": "write_file"})
        );
        assert_eq!(files[1]["path"], "site/index.html");
        assert_eq!(files[1]["bytes"], 0, "a bundle reports no per-file size");

        let artifacts = collect_artifacts(&transcript, &actions());
        assert_eq!(artifacts.as_array().expect("rows").len(), 3);
        assert_eq!(artifacts[0]["previewable"], true);
        assert_eq!(artifacts[0]["bytes"], 12);
        assert_eq!(artifacts[0]["repaired"], false);
        assert_eq!(artifacts[2]["filename"], "app.js");
        assert_eq!(artifacts[2]["bytes"], 0);
    }

    #[test]
    fn a_repaired_artifact_says_so_and_a_binary_is_not_previewable() {
        let transcript = vec![json!({
            "state": "EXECUTING", "action": "create_docx",
            "result": {"path": "generated_documents/report.docx", "bytes": 900},
            "content_sanitize": {"sanitized": true, "repaired": true},
        })];
        let artifacts = collect_artifacts(&transcript, &actions());
        assert_eq!(artifacts[0]["repaired"], true);
        assert_eq!(artifacts[0]["previewable"], false);
    }

    #[test]
    fn the_preview_table_is_sorted_so_the_lookup_is_valid() {
        let mut sorted = PREVIEWABLE_EXTENSIONS.to_vec();
        sorted.sort_unstable();
        assert_eq!(PREVIEWABLE_EXTENSIONS.to_vec(), sorted);
        // The Python set has twenty members; a silent drop shows up here.
        assert_eq!(PREVIEWABLE_EXTENSIONS.len(), 20);
        assert!(PREVIEWABLE_EXTENSIONS.binary_search(&".yml").is_ok());
    }
}
