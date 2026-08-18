//! The PLAN → EXECUTE → VERIFY → ROLLBACK state machine, natively.
//!
//! A port of `latticeai.core.agent.{planning,execution,verification,recovery,
//! runtime}`. What lives *here* is what all four phases need: the injected
//! ports ([`LoopDeps`]), the step observer, the autonomy dial, the two disk
//! reads the loop owns (the fail-closed existence check and the pre-write
//! snapshot), and [`Runtime::run_to_completion`].
//!
//! The division of labour with the worker is the point of the whole design:
//! **every decision is made here** — budgets, gates, change classes, verdict
//! mapping, state transitions — and **every side effect happens there**, behind
//! the three seam endpoints. The one exception is recovery, which reads and
//! writes the workspace directly because a rollback that has to ask a possibly
//! broken worker for permission is not a rollback.

pub mod execution;
pub mod fallback;
pub mod gates;
pub mod guided;
#[cfg(test)]
pub(crate) mod harness;
pub mod planning;
pub mod recovery;
pub mod verification;

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Map, Value};

use crate::kernel::mode::{normalize_mode, PermissionMode};
use crate::kernel::policy::PolicyTable;
use crate::kernel::profile::{profile_for_model, AgentProfile};
use crate::kernel::proposals::{JsonProposalStore, ProposalStore};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::{PhaseBudgets, TranscriptBudget};
use crate::surface::worker::WorkerClient;
use crate::tools::documents::document_output_target;
use crate::tools::sandbox::Workspace;
use crate::tools::{NativeTools, ToolConfig, ToolHost};

/// Largest file a pre-write snapshot will hold in memory.
///
/// `ToolDispatchService._SNAPSHOT_MAX_BYTES` — 512 KiB, which is **not**
/// [`crate::tools::sandbox::MAX_FILE_BYTES`] (512,000, the file-tool read cap). They
/// differ by 288 bytes and the difference is load-bearing exactly once: a file
/// between the two sizes is snapshotted by Python and would be refused by a
/// port that reached for the more familiar constant.
pub const SNAPSHOT_MAX_BYTES: u64 = 512 * 1024;

/// `git checkout --` gets ten seconds, as `ToolDispatchService.rollback_file`.
pub const GIT_ROLLBACK_TIMEOUT_SECS: u64 = 10;

/// The prompts a host may supply. Empty means "use the built-in".
///
/// Until v11.9.0 empty meant *nothing*: `Prompts::default()` was three empty
/// strings, no code path filled them, and every phase opened its context with a
/// blank line. The loop's own decisions still do not read these — they steer
/// the model — but a model that was never told the contract cannot hold it, and
/// the compact profile was spending its whole correction budget on that.
/// [`crate::prompts`] carries the defaults; a caller's string always wins.
#[derive(Debug, Clone, Default)]
pub struct Prompts {
    pub planner: String,
    pub executor: String,
    pub critic: String,
}

impl Prompts {
    fn or_default(supplied: &str, fallback: &str) -> String {
        if supplied.trim().is_empty() {
            fallback.to_string()
        } else {
            supplied.to_string()
        }
    }

    /// The planner prompt for this run.
    pub fn planner_prompt(&self) -> String {
        Self::or_default(&self.planner, crate::prompts::DEFAULT_PLANNER_PROMPT)
    }

    /// The critic prompt for this run.
    pub fn critic_prompt(&self) -> String {
        Self::or_default(&self.critic, crate::prompts::DEFAULT_CRITIC_PROMPT)
    }

    /// The executor prompt for this run: profile-shaped, naming this run's own
    /// tools and whatever skills the request declared.
    pub fn executor_prompt(
        &self,
        profile: AgentProfile,
        tool_names: &[String],
        skills: &[crate::prompts::SkillBrief],
    ) -> String {
        if !self.executor.trim().is_empty() {
            return self.executor.clone();
        }
        crate::prompts::executor_prompt(profile, tool_names, skills)
    }
}

/// One run's request, mirroring Python's `AgentRequest` plus the data the loop
/// needs because it is not the process that owns the registry.
#[derive(Debug, Clone)]
pub struct RunRequest {
    pub message: String,
    pub conversation_id: Option<String>,
    pub workspace_id: Option<String>,
    pub source: Option<String>,
    pub user_email: Option<String>,
    pub temperature: f64,
    pub max_steps: u32,
    pub max_retry: u32,
    pub planning_model: Option<String>,
    pub executing_model: Option<String>,
    pub reviewing_model: Option<String>,
    pub permission_mode: Option<String>,
    pub language_hint: String,
    pub project_id: Option<String>,
    /// What `recent_chat_context` would have returned; the loop has no history
    /// port, so the caller supplies it or the prompt says `(none)`.
    pub recent_conversation: Option<String>,
    pub project_context: String,
    pub self_model_summary: String,
    /// Skills the host has installed, as briefs (v11.9.0). Rendered into the
    /// executor prompt and never resolved here — see [`crate::prompts`].
    ///
    /// Since v12.0.0 a brief is also a *callable* menu row when the host wires
    /// a [`crate::tools::ToolCatalog`]: the brief is what the model is told, the
    /// catalog is what makes choosing it do something. A brief with no catalog
    /// behind it is still only a suggestion, and the loop says so when a run
    /// picks one.
    pub skills: Vec<crate::prompts::SkillBrief>,
}

impl Default for RunRequest {
    fn default() -> Self {
        Self {
            message: String::new(),
            conversation_id: None,
            workspace_id: None,
            source: None,
            user_email: None,
            temperature: 0.1,
            max_steps: 25,
            max_retry: 3,
            planning_model: None,
            executing_model: None,
            reviewing_model: None,
            permission_mode: None,
            language_hint: "English".into(),
            project_id: None,
            recent_conversation: None,
            project_context: String::new(),
            self_model_summary: String::new(),
            skills: Vec::new(),
        }
    }
}

/// The ports the state machine needs from the outside world.
#[derive(Debug, Clone)]
pub struct LoopDeps {
    pub worker: WorkerClient,
    /// The native tool set (v11.6.0 §W4). Everything it `handles` is executed
    /// here; everything else is still a `POST /agent/tool`.
    pub native: Arc<dyn ToolHost>,
    /// The host's MCP surface and installed skills, as one catalog (v12.0.0).
    ///
    /// `None` is the standalone contract, exactly as it is for `hooks`: no MCP
    /// surface and no skill registry in reach of this process, so the run's
    /// catalog is its own tools and nothing is quietly missing from the menu.
    /// See [`crate::tools::catalog`] for what each kind means and how a
    /// prefixed name is routed.
    pub external: Option<Arc<dyn crate::tools::ToolCatalog>>,
    /// Where a measured profile verdict is cached, when measuring is wanted
    /// (v12.0.0).
    ///
    /// `None` means **do not probe** — the size prior decides, no completion is
    /// spent and no file is written. That is the right default for
    /// [`LoopDeps::new`], which every harness and every test builds from: a
    /// constructor that silently added two LLM calls and a write under `$HOME`
    /// would make the frozen trajectories depend on the developer's machine.
    /// The wire turns it on ([`crate::surface::runbody::RunBody`]).
    pub probe: Option<crate::kernel::probe::ProbeConfig>,
    /// Where a `strict` mutation is staged for review (v11.6.0 §P1c). The
    /// default writes `workspace_os.json` directly; a host running the Review
    /// Center **must** inject its own store — see [`crate::kernel::proposals`].
    pub proposals: Arc<dyn ProposalStore>,
    pub workspace: Workspace,
    pub policies: PolicyTable,
    pub file_create_actions: BTreeSet<String>,
    pub governed_tools: BTreeSet<String>,
    pub scoped_knowledge_tools: BTreeSet<String>,
    /// Whether a change governor is wired at all (`deps.change_governor`).
    pub governor_enabled: bool,
    /// `sorted(deps.tool_governance)` — the escalation hint names these.
    pub tool_names: Vec<String>,
    pub phase_budgets: PhaseBudgets,
    pub transcript_budget: TranscriptBudget,
    /// An injected profile wins; `None` derives it from the executing model.
    pub agent_profile: Option<AgentProfile>,
    pub prompts: Prompts,
}

/// `FILE_CREATE_ACTIONS`.
pub fn default_file_create_actions() -> BTreeSet<String> {
    [
        "create_docx",
        "create_pdf",
        "create_pptx",
        "create_web_project",
        "create_xlsx",
        "edit_file",
        "write_file",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

/// `ChangeProposalService.governed_tools`.
pub fn default_governed_tools() -> BTreeSet<String> {
    ["edit_file", "write_file"]
        .into_iter()
        .map(String::from)
        .collect()
}

/// `SCOPED_KNOWLEDGE_TOOLS`.
pub fn default_scoped_knowledge_tools() -> BTreeSet<String> {
    [
        "knowledge_save",
        "knowledge_search",
        "knowledge_tree",
        "obsidian_save",
        "obsidian_search",
        "obsidian_tree",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

impl LoopDeps {
    /// Production defaults around a worker and a workspace.
    pub fn new(worker: WorkerClient, workspace: Workspace) -> Self {
        Self::with_hook_sink(worker, workspace, None)
    }

    /// [`LoopDeps::new`], with the `pre_tool` / `post_tool` lifecycle wired.
    ///
    /// `hooks` is the host's registry adapter
    /// (`lattice_platform::hooks::NativeHookSink`). `None` is the standalone
    /// contract: no `hooks.json` in reach, so no user hook fires — which is
    /// what a harness, a test and a host with no platform routes all want.
    pub fn with_hook_sink(
        worker: WorkerClient,
        workspace: Workspace,
        hooks: Option<Arc<dyn crate::tools::HookSink>>,
    ) -> Self {
        let mut tools = NativeTools::new(workspace.clone(), ToolConfig::from_env(), worker.clone());
        if let Some(hooks) = hooks {
            tools = tools.with_hooks(hooks);
        }
        let native = Arc::new(tools);
        Self {
            worker,
            native,
            external: None,
            probe: None,
            proposals: Arc::new(JsonProposalStore::from_env()),
            workspace,
            policies: PolicyTable::default(),
            file_create_actions: default_file_create_actions(),
            governed_tools: default_governed_tools(),
            scoped_knowledge_tools: default_scoped_knowledge_tools(),
            governor_enabled: true,
            tool_names: Vec::new(),
            phase_budgets: PhaseBudgets::from_env(),
            transcript_budget: TranscriptBudget::from_env(),
            agent_profile: None,
            prompts: Prompts::default(),
        }
    }
}

/// A step observer: pure telemetry, and never allowed to break the loop.
pub type StepObserver = Box<dyn Fn(Value) + Send + Sync>;

/// Drives the agent state machine over injected [`LoopDeps`].
pub struct Runtime {
    pub deps: LoopDeps,
    /// The profile this run settled on, once (v12.0.0).
    ///
    /// A probe costs two completions, so it is taken at most once per run and
    /// remembered here. It is *not* on [`AgentRunContext`]: a resumed run
    /// re-derives it, which is right — the verdict cache makes that free, and a
    /// run paused across a model change must not execute under a dial measured
    /// for the model it was planned with.
    resolved_profile: Option<AgentProfile>,
    /// Audit events the loop raised, in order.
    ///
    /// Python writes these to the audit log through `deps.audit`. The seam
    /// contract has three endpoints and none of them is an audit sink, so the
    /// native loop *keeps* its audit trail and hands it back with the run
    /// rather than dropping the events on the floor or inventing a fourth
    /// endpoint. Named as a deviation, not presented as parity.
    pub audit: Vec<Value>,
    observer: Option<StepObserver>,
}

impl std::fmt::Debug for Runtime {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("Runtime")
            .field("deps", &self.deps)
            .field("audit", &self.audit.len())
            .field("observer", &self.observer.is_some())
            .finish()
    }
}

impl Runtime {
    pub fn new(deps: LoopDeps) -> Self {
        Self {
            deps,
            resolved_profile: None,
            audit: Vec::new(),
            observer: None,
        }
    }

    /// Attach the live step observer (the SSE stream's).
    pub fn with_observer(mut self, observer: StepObserver) -> Self {
        self.observer = Some(observer);
        self
    }

    /// The loop profile for the model actually executing this run, *derived*.
    ///
    /// The v11.9.0 answer: an injected dial, else the regex over the model id.
    /// Still the whole answer when nothing can be measured, and still what the
    /// unit suites assert against, because it is pure.
    pub fn profile(&self, model_id: Option<&str>) -> AgentProfile {
        self.resolved_profile
            .or(self.deps.agent_profile)
            .unwrap_or_else(|| profile_for_model(model_id))
    }

    /// The loop profile for this run, **measured** where measuring is possible.
    ///
    /// The order is the policy, strongest claim first:
    ///
    /// 1. an injected [`LoopDeps::agent_profile`] — a caller that states the
    ///    dial is never overruled by a measurement;
    /// 2. `LATTICEAI_AGENT_PROFILE` — the operator's pin;
    /// 3. this run's already-resolved answer, so a probe is paid once;
    /// 4. [`crate::kernel::probe::measure`] — two short completions, cached on
    ///    disk per model id and harness version;
    /// 5. [`profile_for_model`] — the size prior, which is what "we could not
    ///    measure" means.
    ///
    /// Recorded on the trace either way, because "which dial did this run use,
    /// and why" is the first question any weak-model report has to answer.
    pub async fn resolve_profile(
        &mut self,
        ctx: &mut AgentRunContext,
        model_id: Option<&str>,
    ) -> AgentProfile {
        if let Some(injected) = self.deps.agent_profile {
            return injected;
        }
        if let Some(pinned) = crate::kernel::probe::env_override() {
            self.resolved_profile = Some(pinned);
            return pinned;
        }
        if let Some(resolved) = self.resolved_profile {
            return resolved;
        }
        let measured = match &self.deps.probe {
            Some(config) => {
                crate::kernel::probe::measure(&self.deps.worker, config, model_id, crate::VERSION)
                    .await
            }
            None => None,
        };
        // Recorded **only when something was measured**. A run that fell back
        // to the prior did exactly what every run before v12.0.0 did, and
        // stamping an event on it would change every frozen trajectory to say
        // that a decision had been made where none was.
        let profile = match &measured {
            Some(verdict) => {
                let detail = verdict.to_detail();
                ctx.trace
                    .decision("execute", "profile", &[("profile", detail.clone())]);
                self.emit_step("execute", "profile", &[("profile", detail)]);
                verdict.profile
            }
            None => profile_for_model(model_id),
        };
        self.resolved_profile = Some(profile);
        profile
    }

    /// Pin the dial for the rest of this run.
    ///
    /// The one caller is [`execution::Runtime::demote_to_guided`], and it exists
    /// so verification runs under the dial the executor actually finished in —
    /// a run that fell back to `guided` must be reviewed with `guided`'s closed
    /// question, not with a JSON verdict the same model already could not emit.
    pub(super) fn set_resolved_profile(&mut self, profile: AgentProfile) {
        self.resolved_profile = Some(profile);
    }

    /// Autonomy dial for this run: a mode stamped on the context wins, so the
    /// plan a user approved and every tool step in the same run are judged by
    /// one dial even if the stored preference changes mid-run.
    pub fn resolve_permission_mode(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
    ) -> PermissionMode {
        let stamped = ctx
            .permission_mode
            .as_deref()
            .or(req.permission_mode.as_deref());
        normalize_mode(stamped.unwrap_or(""))
    }

    /// `_emit_step`: fire the observer, dropping `None` details.
    pub fn emit_step(&self, phase: &str, event: &str, details: &[(&str, Value)]) {
        let Some(observer) = &self.observer else {
            return;
        };
        let mut payload = Map::new();
        payload.insert("phase".into(), json!(phase));
        payload.insert("event".into(), json!(event));
        for (key, value) in details {
            if !value.is_null() {
                payload.insert((*key).into(), value.clone());
            }
        }
        observer(Value::Object(payload));
    }

    /// Record one audit event.
    pub fn audit(&mut self, event: &str, details: &[(&str, Value)]) {
        let mut payload = Map::new();
        payload.insert("event".into(), json!(event));
        for (key, value) in details {
            payload.insert((*key).into(), value.clone());
        }
        self.audit.push(Value::Object(payload));
    }

    /// Project-session context for prompts, or `""` for a standalone run.
    pub fn project_block(&self, ctx: &AgentRunContext) -> String {
        let summary = ctx.project_context.trim();
        if summary.is_empty() {
            String::new()
        } else {
            format!("\n\n[PROJECT SESSION]\n{summary}")
        }
    }

    /// Does this tool call's *real* target already exist?
    ///
    /// Never fails: governance must not be able to crash the loop, and an
    /// unresolvable path degrades to "new file", which the remaining gates
    /// still cover.
    pub fn governed_path_exists(&self, name: &str, path: &str) -> bool {
        let resolved = document_output_target(name, path).unwrap_or_else(|| path.to_string());
        let candidate = PathBuf::from(&resolved);
        let candidate = if candidate.is_absolute() {
            candidate
        } else {
            self.deps.workspace.root().join(candidate)
        };
        candidate.exists()
    }

    /// `run_to_completion`: EXECUTING → VERIFYING → ROLLBACK until terminal.
    ///
    /// The `Err` arm is the reasoner being unreachable, which Python expresses
    /// as an exception escaping the loop. There is no honest way to continue a
    /// reasoning loop without the reasoner, so the run does not invent a
    /// terminal state for it — the caller answers with the failure.
    pub async fn run_to_completion(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), crate::surface::worker::WorkerError> {
        while !ctx.state.is_terminal() {
            ctx.state_history.push(ctx.state.as_str().to_string());
            if ctx.state_history.len() > 200 {
                ctx.final_message =
                    "에이전트 상태 머신이 최대 반복(200)에 도달해 중단했습니다.".into();
                ctx.state = AgentState::Failed;
                break;
            }
            match ctx.state {
                AgentState::Executing => self.execute(ctx, req).await?,
                AgentState::Verifying => self.verify(ctx, req).await?,
                AgentState::Rollback => self.rollback(ctx, req).await,
                // Any other state here is a bug upstream, not a state to sit in.
                _ => ctx.state = AgentState::Failed,
            }
        }
        ctx.state_history.push(ctx.state.as_str().to_string());
        self.emit_step("terminal", "state", &[("state", json!(ctx.state.as_str()))]);
        Ok(())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use std::sync::{Arc, Mutex};

    use serde_json::{json, Value};

    use super::harness::*;
    use super::*;
    use crate::kernel::trace::LoopTrace;
    #[test]
    fn the_default_tool_sets_are_the_python_constants() {
        assert_eq!(default_file_create_actions().len(), 7);
        assert!(default_file_create_actions().contains("create_web_project"));
        assert_eq!(
            default_governed_tools(),
            ["edit_file", "write_file"]
                .into_iter()
                .map(String::from)
                .collect()
        );
        assert_eq!(default_scoped_knowledge_tools().len(), 6);
        assert_eq!(SNAPSHOT_MAX_BYTES, 524_288);
        assert_ne!(SNAPSHOT_MAX_BYTES, crate::tools::sandbox::MAX_FILE_BYTES);
        assert_eq!(GIT_ROLLBACK_TIMEOUT_SECS, 10);
    }

    #[test]
    fn the_stamped_mode_outranks_the_request_and_unknown_is_strict() {
        let (_dir, ws) = workspace();
        let runtime = runtime(ws);
        let mut ctx = AgentRunContext::new();
        let req = RunRequest {
            permission_mode: Some("bypass".into()),
            ..RunRequest::default()
        };
        assert_eq!(
            runtime.resolve_permission_mode(&ctx, &req),
            PermissionMode::Bypass
        );
        ctx.permission_mode = Some("trusted".into());
        assert_eq!(
            runtime.resolve_permission_mode(&ctx, &req),
            PermissionMode::Trusted
        );
        ctx.permission_mode = Some("nonsense".into());
        assert_eq!(
            runtime.resolve_permission_mode(&ctx, &req),
            PermissionMode::Strict
        );
        assert_eq!(
            runtime.resolve_permission_mode(&AgentRunContext::new(), &RunRequest::default()),
            PermissionMode::Strict
        );
    }

    #[test]
    fn the_existence_check_follows_the_document_creators_into_their_directory() {
        let (_dir, ws) = workspace();
        std::fs::create_dir_all(ws.root().join("generated_documents")).expect("dir");
        std::fs::write(ws.root().join("generated_documents/report.docx"), b"x").expect("file");
        std::fs::write(ws.root().join("plain.md"), b"x").expect("file");
        let runtime = runtime(ws);
        assert!(
            runtime.governed_path_exists("create_docx", "report"),
            "the raw filename never exists; the resolved target does"
        );
        assert!(!runtime.governed_path_exists("write_file", "report"));
        assert!(runtime.governed_path_exists("write_file", "plain.md"));
        assert!(!runtime.governed_path_exists("write_file", "/nope/absolutely/not"));
    }

    #[test]
    fn the_project_block_is_empty_for_a_standalone_run() {
        let (_dir, ws) = workspace();
        let runtime = runtime(ws);
        let mut ctx = AgentRunContext::new();
        assert_eq!(runtime.project_block(&ctx), "");
        ctx.project_context = "  three files so far  ".into();
        assert_eq!(
            runtime.project_block(&ctx),
            "\n\n[PROJECT SESSION]\nthree files so far"
        );
    }

    #[tokio::test]
    async fn an_unreachable_state_terminates_instead_of_spinning() {
        let (_dir, ws) = workspace();
        let mut runtime = runtime(ws);
        let mut ctx = AgentRunContext::new();
        ctx.state = AgentState::Planning; // not a drive-loop state
        runtime
            .run_to_completion(&mut ctx, &RunRequest::default())
            .await
            .expect("no worker call is made from these states");
        assert_eq!(ctx.state, AgentState::Failed);
        assert_eq!(ctx.state_history, vec!["PLANNING", "FAILED"]);
    }

    #[tokio::test]
    async fn the_two_hundred_cap_stops_a_machine_that_will_not_settle() {
        let (_dir, ws) = workspace();
        let mut runtime = runtime(ws);
        let mut ctx = AgentRunContext::new();
        ctx.trace = LoopTrace::pinned("t");
        // A history that is already at the cap: the next turn must break.
        ctx.state_history = (0..200).map(|_| "EXECUTING".to_string()).collect();
        ctx.state = AgentState::Rollback;
        runtime
            .run_to_completion(&mut ctx, &RunRequest::default())
            .await
            .expect("no worker call is made from these states");
        assert_eq!(ctx.state, AgentState::Failed);
        assert!(ctx.final_message.contains("최대 반복(200)"));
        assert_eq!(ctx.state_history.len(), 202, "the cap trips, then terminal");
    }

    #[test]
    fn the_observer_sees_named_events_without_nulls() {
        let (_dir, ws) = workspace();
        let seen: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&seen);
        let runtime = runtime(ws).with_observer(Box::new(move |event| {
            sink.lock().expect("lock").push(event);
        }));
        runtime.emit_step(
            "execute",
            "tool",
            &[("action", json!("write_file")), ("path", Value::Null)],
        );
        let events = seen.lock().expect("lock").clone();
        assert_eq!(
            events,
            vec![json!({"phase": "execute", "event": "tool", "action": "write_file"})]
        );
    }

    #[test]
    fn audit_events_are_kept_in_order_with_their_details() {
        let (_dir, ws) = workspace();
        let mut runtime = runtime(ws);
        runtime.audit("agent_blocked", &[("action", json!("write_file"))]);
        assert_eq!(
            runtime.audit,
            vec![json!({"event": "agent_blocked", "action": "write_file"})]
        );
    }

    #[test]
    fn an_injected_profile_outranks_the_model_heuristic() {
        let (_dir, ws) = workspace();
        let mut deps = LoopDeps::new(WorkerClient::new("http://127.0.0.1:1"), ws);
        assert_eq!(
            Runtime::new(deps.clone()).profile(Some("qwen-1.5b")).name,
            "compact"
        );
        deps.agent_profile = Some(crate::kernel::profile::STANDARD);
        assert_eq!(
            Runtime::new(deps).profile(Some("qwen-1.5b")).name,
            "standard"
        );
    }
}
