//! Durable DAG Agent Workflow Engine with per-step checkpoints, idempotency ledger,
//! crash recovery, approval pause/resume across restarts, cancel/retry, pre-mutation
//! file rollback, and Knowledge Graph provenance lineage.
//!
//! Addresses Review Sections 9.1, 9.2, 15 (P1 — Durable Agent Workflow), and 16.4.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::kernel::runs::{token_matches, token_urlsafe, APPROVAL_TTL_SECONDS};

fn sha256_hex(input: &str) -> String {
    let digest = Sha256::digest(input.as_bytes());
    let mut hex = String::with_capacity(64);
    for b in digest {
        use std::fmt::Write as _;
        let _ = write!(hex, "{b:02x}");
    }
    hex
}

/// Subdirectory under `LATTICEAI_DATA_DIR` storing durable workflow checkpoints.
pub const WORKFLOWS_DIR_NAME: &str = "rust_agent_workflows";

/// Lifecycle status of the overall durable workflow run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowStatus {
    Pending,
    Running,
    WaitingApproval,
    Completed,
    PartialSuccess,
    Failed,
    Cancelled,
    RolledBack,
}

/// Typed classification separating retryable transient errors from permanent/policy failures (P1.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepErrorKind {
    TransientNetwork,
    RateLimited,
    Timeout,
    InvalidArgs,
    PermissionDenied,
    PolicyBlocked,
    PermanentToolFailure,
}

impl StepErrorKind {
    pub fn classify(error_msg: &str) -> Self {
        let lower = error_msg.to_ascii_lowercase();
        if lower.contains("timeout") || lower.contains("timed out") {
            Self::Timeout
        } else if lower.contains("429") || lower.contains("rate limit") {
            Self::RateLimited
        } else if lower.contains("503")
            || lower.contains("connection refused")
            || lower.contains("connection reset")
            || lower.contains("temporarily unavailable")
        {
            Self::TransientNetwork
        } else if lower.contains("permission")
            || lower.contains("unauthorized")
            || lower.contains("mismatch")
        {
            Self::PermissionDenied
        } else if lower.contains("blocked") || lower.contains("policy") || lower.contains("sandbox")
        {
            Self::PolicyBlocked
        } else if lower.contains("invalid") || lower.contains("missing required") {
            Self::InvalidArgs
        } else {
            Self::PermanentToolFailure
        }
    }

    pub fn is_retryable(self) -> bool {
        matches!(
            self,
            Self::TransientNetwork | Self::RateLimited | Self::Timeout
        )
    }
}

/// Pre-execution blast-radius & reversibility preview attached to destructive/approval gates (P1.5).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ImpactPreview {
    pub affected_paths: Vec<String>,
    pub op_types: Vec<String>,
    pub reversible: bool,
    pub rollback_mode: String,
    pub external_targets: Vec<String>,
    pub risk_tier: String,
}

/// Lifecycle status of an individual DAG step.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowStepStatus {
    Pending,
    Running,
    WaitingApproval,
    Completed,
    Failed,
    Cancelled,
    RolledBack,
}

/// Specification of one step inside a [`WorkflowSpec`] DAG.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowStepSpec {
    pub step_id: String,
    pub title: String,
    pub action: String,
    #[serde(default)]
    pub args: Value,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub mutating: bool,
    #[serde(default)]
    pub requires_approval: bool,
    #[serde(default)]
    pub max_retries: u32,
    #[serde(default)]
    pub rollback_path: Option<PathBuf>,
    #[serde(default)]
    pub consulted_node_ids: Vec<String>,
}

/// Declarative DAG workflow definition.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowSpec {
    pub workflow_id: String,
    pub goal: String,
    #[serde(default)]
    pub user_email: Option<String>,
    #[serde(default)]
    pub workspace_id: Option<String>,
    #[serde(default = "default_true")]
    pub rollback_on_failure: bool,
    pub steps: Vec<WorkflowStepSpec>,
}

fn default_true() -> bool {
    true
}

impl WorkflowSpec {
    /// Validate DAG integrity: non-empty, unique step IDs, known dependencies, and acyclic.
    /// Returns a deterministic topological ordering on success.
    pub fn validate_dag(&self) -> Result<Vec<String>, String> {
        if self.workflow_id.trim().is_empty() {
            return Err("workflow_id must not be empty".into());
        }
        if self.steps.is_empty() {
            return Err("workflow must contain at least one step".into());
        }

        let mut in_degree: BTreeMap<String, usize> = BTreeMap::new();
        let mut dependents: BTreeMap<String, Vec<String>> = BTreeMap::new();

        for step in &self.steps {
            let id = step.step_id.trim();
            if id.is_empty() {
                return Err("step_id must not be empty".into());
            }
            if in_degree.contains_key(id) {
                return Err(format!("duplicate step_id '{id}'"));
            }
            in_degree.insert(id.to_string(), 0);
            dependents.insert(id.to_string(), Vec::new());
        }

        for step in &self.steps {
            let mut seen_deps = BTreeSet::new();
            for dep in &step.depends_on {
                if dep == &step.step_id {
                    return Err(format!("step '{}' cannot depend on itself", step.step_id));
                }
                if !in_degree.contains_key(dep) {
                    return Err(format!(
                        "step '{}' depends on unknown step '{dep}'",
                        step.step_id
                    ));
                }
                if seen_deps.insert(dep.clone()) {
                    *in_degree.get_mut(&step.step_id).expect("registered") += 1;
                    dependents
                        .get_mut(dep)
                        .expect("registered")
                        .push(step.step_id.clone());
                }
            }
        }

        let mut queue: VecDeque<String> = in_degree
            .iter()
            .filter(|(_, &deg)| deg == 0)
            .map(|(id, _)| id.clone())
            .collect();
        let mut order = Vec::with_capacity(self.steps.len());

        while let Some(id) = queue.pop_front() {
            order.push(id.clone());
            if let Some(children) = dependents.get(&id) {
                for child in children {
                    let deg = in_degree.get_mut(child).expect("child");
                    *deg -= 1;
                    if *deg == 0 {
                        queue.push_back(child.clone());
                    }
                }
            }
        }

        if order.len() != self.steps.len() {
            return Err("workflow DAG contains a dependency cycle".into());
        }
        Ok(order)
    }
}

/// Ready-to-run frontier wave computed over the DAG.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionWave {
    /// Independent read-only steps whose dependencies are satisfied and can run concurrently.
    ParallelReadOnly(Vec<String>),
    /// A mutating or approval-gated step scheduled in isolation.
    SerialStep(String),
}

/// Cached receipt for an idempotent step execution.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StepReceipt {
    pub idempotency_key: String,
    pub step_id: String,
    pub output: Value,
    #[serde(default)]
    pub generated_artifacts: Vec<String>,
    pub completed_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct FileRollbackSnapshot {
    path: PathBuf,
    existed_before: bool,
    prior_bytes: Option<Vec<u8>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct PendingStepApproval {
    step_id: String,
    token_sha256: String,
    expires_at: f64,
}

/// Persisted checkpoint state for one step.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowStepState {
    pub spec: WorkflowStepSpec,
    pub idempotency_key: String,
    pub status: WorkflowStepStatus,
    pub attempts: u32,
    pub approved: bool,
    pub replayed_from_ledger: bool,
    pub last_error: Option<String>,
    pub output: Option<Value>,
    pub updated_at: f64,
}

/// Persisted durable record of an entire workflow run.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowRunRecord {
    pub spec: WorkflowSpec,
    pub status: WorkflowStatus,
    pub steps: BTreeMap<String, WorkflowStepState>,
    pub idempotency_ledger: BTreeMap<String, StepReceipt>,
    pub crash_recoveries: u32,
    pub cancel_reason: Option<String>,
    pub updated_at: f64,
    pending_approval: Option<PendingStepApproval>,
    rollback_snapshots: Vec<FileRollbackSnapshot>,
}

/// Outcome of advancing a workflow until completion, pause, or failure.
#[derive(Debug, Clone, PartialEq)]
pub enum WorkflowAdvanceOutcome {
    Completed {
        workflow_id: String,
    },
    PausedForApproval {
        workflow_id: String,
        step_id: String,
        approval_token: String,
        expires_at: f64,
    },
    Failed {
        workflow_id: String,
        step_id: String,
        error: String,
        rolled_back: bool,
    },
    Cancelled {
        workflow_id: String,
        reason: String,
    },
}

/// Step execution output returned by a host tool/action runner.
#[derive(Debug, Clone, Default)]
pub struct StepExecOutput {
    pub value: Value,
    pub generated_artifacts: Vec<String>,
}

/// Compute a deterministic SHA-256 idempotency key for `(workflow_id, step_id, action, args)`.
pub fn compute_idempotency_key(
    workflow_id: &str,
    step_id: &str,
    action: &str,
    args: &Value,
) -> String {
    let canonical = serde_json::to_string(args).unwrap_or_else(|_| "null".into());
    sha256_hex(&format!("{workflow_id}|{step_id}|{action}|{canonical}"))
}

impl WorkflowRunRecord {
    /// Initialize a validated [`WorkflowRunRecord`] from a [`WorkflowSpec`].
    pub fn from_spec(spec: WorkflowSpec, now_secs: f64) -> Result<Self, String> {
        let _ = spec.validate_dag()?;
        let mut steps = BTreeMap::new();
        for s in &spec.steps {
            let key = compute_idempotency_key(&spec.workflow_id, &s.step_id, &s.action, &s.args);
            steps.insert(
                s.step_id.clone(),
                WorkflowStepState {
                    spec: s.clone(),
                    idempotency_key: key,
                    status: WorkflowStepStatus::Pending,
                    attempts: 0,
                    approved: !s.requires_approval,
                    replayed_from_ledger: false,
                    last_error: None,
                    output: None,
                    updated_at: now_secs,
                },
            );
        }
        Ok(Self {
            spec,
            status: WorkflowStatus::Pending,
            steps,
            idempotency_ledger: BTreeMap::new(),
            crash_recoveries: 0,
            cancel_reason: None,
            updated_at: now_secs,
            pending_approval: None,
            rollback_snapshots: Vec::new(),
        })
    }

    /// Reconcile any step left in `Running` due to an abrupt process crash.
    pub fn recover_after_crash(&mut self, now_secs: f64) {
        let mut recovered = false;
        for state in self.steps.values_mut() {
            if state.status == WorkflowStepStatus::Running {
                recovered = true;
                if let Some(receipt) = self.idempotency_ledger.get(&state.idempotency_key) {
                    state.status = WorkflowStepStatus::Completed;
                    state.output = Some(receipt.output.clone());
                    state.replayed_from_ledger = true;
                } else {
                    state.status = WorkflowStepStatus::Pending;
                }
                state.updated_at = now_secs;
            }
        }
        if recovered {
            self.crash_recoveries += 1;
            if self.status == WorkflowStatus::Running {
                self.status = WorkflowStatus::Pending;
            }
            self.updated_at = now_secs;
        }
    }

    /// Determine the next ready execution wave over the DAG.
    pub fn next_wave(&self) -> Option<ExecutionWave> {
        let mut ready_ro = Vec::new();
        let mut first_serial: Option<String> = None;

        for step_spec in &self.spec.steps {
            let Some(st) = self.steps.get(&step_spec.step_id) else {
                continue;
            };
            if st.status != WorkflowStepStatus::Pending {
                continue;
            }
            let deps_done = step_spec.depends_on.iter().all(|dep| {
                self.steps
                    .get(dep)
                    .is_some_and(|d| d.status == WorkflowStepStatus::Completed)
            });
            if !deps_done {
                continue;
            }
            if step_spec.mutating || (step_spec.requires_approval && !st.approved) {
                if first_serial.is_none() {
                    first_serial = Some(step_spec.step_id.clone());
                }
            } else {
                ready_ro.push(step_spec.step_id.clone());
            }
        }

        if !ready_ro.is_empty() {
            Some(ExecutionWave::ParallelReadOnly(ready_ro))
        } else {
            first_serial.map(ExecutionWave::SerialStep)
        }
    }

    /// Export Brain provenance payload (`Workflow` node + `USED_BY_AGENT` / `GENERATED_BY` edges)
    /// linking consulted Knowledge Graph nodes and generated output artifacts.
    pub fn brain_provenance_payload(&self) -> Value {
        let wf_node_id = format!("workflow:{}", self.spec.workflow_id);
        let mut consulted: BTreeSet<String> = BTreeSet::new();
        let mut artifacts: BTreeSet<String> = BTreeSet::new();
        for st in self.steps.values() {
            for nid in &st.spec.consulted_node_ids {
                if !nid.trim().is_empty() {
                    consulted.insert(nid.clone());
                }
            }
        }
        for receipt in self.idempotency_ledger.values() {
            for art in &receipt.generated_artifacts {
                if !art.trim().is_empty() {
                    artifacts.insert(art.clone());
                }
            }
        }

        let mut edges = Vec::new();
        for nid in &consulted {
            edges.push(json!({
                "from_node": nid,
                "to_node": wf_node_id,
                "type": "USED_BY_AGENT",
            }));
        }
        for art in &artifacts {
            edges.push(json!({
                "from_node": art,
                "to_node": wf_node_id,
                "type": "GENERATED_BY",
            }));
        }

        json!({
            "workflow_node": {
                "id": wf_node_id,
                "type": "Workflow",
                "title": self.spec.goal,
                "status": self.status,
                "workspace_id": self.spec.workspace_id,
            },
            "consulted_node_ids": consulted.into_iter().collect::<Vec<_>>(),
            "generated_artifacts": artifacts.into_iter().collect::<Vec<_>>(),
            "edges": edges,
        })
    }
}

/// Atomic file-backed durable store + execution engine for [`WorkflowRunRecord`]s.
#[derive(Debug, Clone)]
pub struct DurableWorkflowStore {
    dir: PathBuf,
}

impl DurableWorkflowStore {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self { dir: dir.into() }
    }

    fn path_for(&self, workflow_id: &str) -> Result<PathBuf, String> {
        let trimmed = workflow_id.trim();
        if trimmed.is_empty()
            || !trimmed
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err(format!("invalid workflow_id '{workflow_id}'"));
        }
        Ok(self.dir.join(format!("workflow_{trimmed}.json")))
    }

    /// Persist a workflow checkpoint atomically via temp file + rename.
    pub fn save(&self, record: &WorkflowRunRecord) -> Result<(), String> {
        std::fs::create_dir_all(&self.dir).map_err(|e| e.to_string())?;
        let dest = self.path_for(&record.spec.workflow_id)?;
        let nonce = token_urlsafe(6).map_err(|e| e.to_string())?;
        let tmp = self
            .dir
            .join(format!(".tmp_{}_{nonce}.json", record.spec.workflow_id));
        let bytes = serde_json::to_vec_pretty(record).map_err(|e| e.to_string())?;
        std::fs::write(&tmp, bytes).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &dest).map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            e.to_string()
        })
    }

    /// Load a persisted workflow record and automatically reconcile any mid-step crash state.
    pub fn load(
        &self,
        workflow_id: &str,
        now_secs: f64,
    ) -> Result<Option<WorkflowRunRecord>, String> {
        let path = self.path_for(workflow_id)?;
        let bytes = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.to_string()),
        };
        let mut record: WorkflowRunRecord =
            serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        let prev_recoveries = record.crash_recoveries;
        record.recover_after_crash(now_secs);
        if record.crash_recoveries != prev_recoveries {
            self.save(&record)?;
        }
        Ok(Some(record))
    }

    /// Workspace-scoped load enforcing strict P0.4 cross-workspace isolation.
    pub fn load_scoped(
        &self,
        workflow_id: &str,
        workspace_id: &str,
        now_secs: f64,
    ) -> Result<Option<WorkflowRunRecord>, String> {
        let Some(record) = self.load(workflow_id, now_secs)? else {
            return Ok(None);
        };
        let record_ws = record
            .spec
            .workspace_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("personal");
        let caller_ws = workspace_id.trim();
        if !caller_ws.is_empty() && record_ws != caller_ws {
            return Err(format!(
                "workspace isolation violation: workflow '{workflow_id}' belongs to '{record_ws}', not '{caller_ws}'"
            ));
        }
        Ok(Some(record))
    }

    /// Export a reproducible, PII/secret-redacted workflow trace bundle (P3.2).
    pub fn export_redacted_trace(
        &self,
        workflow_id: &str,
        workspace_id: &str,
        now_secs: f64,
    ) -> Result<Value, String> {
        let record = self
            .load_scoped(workflow_id, workspace_id, now_secs)?
            .ok_or_else(|| format!("workflow '{workflow_id}' not found"))?;
        let raw = serde_json::to_value(&record).map_err(|e| e.to_string())?;
        Ok(json!({
            "schema_version": "lattice.workflow.trace.v1",
            "workflow_id": workflow_id,
            "workspace_id": workspace_id,
            "redacted": true,
            "record": lattice_core::redact_pii_and_secrets_value(&raw),
        }))
    }

    /// Register a new workflow run and persist its initial checkpoint.
    pub fn create(&self, spec: WorkflowSpec, now_secs: f64) -> Result<WorkflowRunRecord, String> {
        let record = WorkflowRunRecord::from_spec(spec, now_secs)?;
        self.save(&record)?;
        Ok(record)
    }

    /// Approve a paused step using constant-time token verification, user match, and TTL check.
    pub fn approve_step(
        &self,
        workflow_id: &str,
        step_id: &str,
        presented_token: &str,
        user_email: Option<&str>,
        now_secs: f64,
    ) -> Result<WorkflowRunRecord, String> {
        let mut record = self
            .load(workflow_id, now_secs)?
            .ok_or_else(|| format!("workflow '{workflow_id}' not found"))?;
        if record.status != WorkflowStatus::WaitingApproval {
            return Err("workflow is not awaiting approval".into());
        }
        if record.spec.user_email.as_deref() != user_email {
            return Err("approval user mismatch".into());
        }
        let pending = record
            .pending_approval
            .as_ref()
            .ok_or_else(|| "missing pending approval record".to_string())?;
        if pending.step_id != step_id {
            return Err(format!(
                "approval step mismatch: expected '{}', got '{step_id}'",
                pending.step_id
            ));
        }
        if now_secs > pending.expires_at {
            return Err("approval token expired".into());
        }
        if !token_matches(&pending.token_sha256, presented_token) {
            return Err("invalid approval token".into());
        }

        record.pending_approval = None;
        record.status = WorkflowStatus::Pending;
        if let Some(st) = record.steps.get_mut(step_id) {
            st.approved = true;
            st.status = WorkflowStepStatus::Pending;
            st.updated_at = now_secs;
        }
        record.updated_at = now_secs;
        self.save(&record)?;
        Ok(record)
    }

    /// Cancel an active or paused workflow and mark remaining unfinished steps `Cancelled`.
    pub fn cancel(
        &self,
        workflow_id: &str,
        reason: &str,
        now_secs: f64,
    ) -> Result<WorkflowRunRecord, String> {
        let mut record = self
            .load(workflow_id, now_secs)?
            .ok_or_else(|| format!("workflow '{workflow_id}' not found"))?;
        record.status = WorkflowStatus::Cancelled;
        record.cancel_reason = Some(reason.to_string());
        record.pending_approval = None;
        for st in record.steps.values_mut() {
            if matches!(
                st.status,
                WorkflowStepStatus::Pending
                    | WorkflowStepStatus::Running
                    | WorkflowStepStatus::WaitingApproval
            ) {
                st.status = WorkflowStepStatus::Cancelled;
                st.updated_at = now_secs;
            }
        }
        record.updated_at = now_secs;
        self.save(&record)?;
        Ok(record)
    }

    /// Reset a failed step back to `Pending` if within `max_retries`, without re-running completed steps.
    pub fn retry_failed_step(
        &self,
        workflow_id: &str,
        step_id: &str,
        now_secs: f64,
    ) -> Result<WorkflowRunRecord, String> {
        let mut record = self
            .load(workflow_id, now_secs)?
            .ok_or_else(|| format!("workflow '{workflow_id}' not found"))?;
        let st = record
            .steps
            .get_mut(step_id)
            .ok_or_else(|| format!("unknown step '{step_id}'"))?;
        if st.status != WorkflowStepStatus::Failed {
            return Err(format!("step '{step_id}' is not in failed state"));
        }
        if st.attempts > st.spec.max_retries {
            return Err(format!(
                "step '{step_id}' exhausted max_retries ({})",
                st.spec.max_retries
            ));
        }
        st.status = WorkflowStepStatus::Pending;
        st.last_error = None;
        st.updated_at = now_secs;
        record.status = WorkflowStatus::Pending;
        record.updated_at = now_secs;
        self.save(&record)?;
        Ok(record)
    }

    /// Drive a workflow forward wave-by-wave, checkpointing BEFORE and AFTER every step,
    /// enforcing idempotency, approval gates, and deterministic rollback on failure.
    pub fn run_until_yield<F>(
        &self,
        workflow_id: &str,
        now_secs: f64,
        mut executor: F,
    ) -> Result<WorkflowAdvanceOutcome, String>
    where
        F: FnMut(&WorkflowStepSpec) -> Result<StepExecOutput, String>,
    {
        let mut record = self
            .load(workflow_id, now_secs)?
            .ok_or_else(|| format!("workflow '{workflow_id}' not found"))?;

        if record.status == WorkflowStatus::Cancelled {
            return Ok(WorkflowAdvanceOutcome::Cancelled {
                workflow_id: workflow_id.to_string(),
                reason: record.cancel_reason.unwrap_or_else(|| "cancelled".into()),
            });
        }

        while let Some(wave) = record.next_wave() {
            let step_ids = match wave {
                ExecutionWave::ParallelReadOnly(ids) => ids,
                ExecutionWave::SerialStep(id) => vec![id],
            };

            for step_id in step_ids {
                let spec_snapshot = record.steps[&step_id].spec.clone();
                let idem_key = record.steps[&step_id].idempotency_key.clone();
                let is_approved = record.steps[&step_id].approved;

                // 1. Approval gate: fail-closed pause to disk before any execution!
                if spec_snapshot.requires_approval && !is_approved {
                    let raw_token = token_urlsafe(24).map_err(|e| e.to_string())?;
                    let expires_at = now_secs + APPROVAL_TTL_SECONDS;
                    record.status = WorkflowStatus::WaitingApproval;
                    record.pending_approval = Some(PendingStepApproval {
                        step_id: step_id.clone(),
                        token_sha256: sha256_hex(&raw_token),
                        expires_at,
                    });
                    if let Some(st) = record.steps.get_mut(&step_id) {
                        st.status = WorkflowStepStatus::WaitingApproval;
                        st.updated_at = now_secs;
                    }
                    record.updated_at = now_secs;
                    self.save(&record)?;
                    return Ok(WorkflowAdvanceOutcome::PausedForApproval {
                        workflow_id: workflow_id.to_string(),
                        step_id,
                        approval_token: raw_token,
                        expires_at,
                    });
                }

                // 2. Idempotency check: if receipt already exists in ledger, replay without re-executing!
                if let Some(receipt) = record.idempotency_ledger.get(&idem_key).cloned() {
                    if let Some(st) = record.steps.get_mut(&step_id) {
                        st.status = WorkflowStepStatus::Completed;
                        st.output = Some(receipt.output);
                        st.replayed_from_ledger = true;
                        st.updated_at = now_secs;
                    }
                    record.updated_at = now_secs;
                    self.save(&record)?;
                    continue;
                }

                // 3. Pre-mutation file snapshot for rollback safety.
                if spec_snapshot.mutating {
                    if let Some(target) = &spec_snapshot.rollback_path {
                        capture_rollback_snapshot(&mut record.rollback_snapshots, target);
                    }
                }

                // 4. Checkpoint `Running` BEFORE invoking external side effect!
                record.status = WorkflowStatus::Running;
                if let Some(st) = record.steps.get_mut(&step_id) {
                    st.status = WorkflowStepStatus::Running;
                    st.attempts += 1;
                    st.updated_at = now_secs;
                }
                self.save(&record)?;

                // 5. Execute step!
                match executor(&spec_snapshot) {
                    Ok(exec_out) => {
                        let receipt = StepReceipt {
                            idempotency_key: idem_key.clone(),
                            step_id: step_id.clone(),
                            output: exec_out.value.clone(),
                            generated_artifacts: exec_out.generated_artifacts,
                            completed_at: now_secs,
                        };
                        record.idempotency_ledger.insert(idem_key, receipt);
                        if let Some(st) = record.steps.get_mut(&step_id) {
                            st.status = WorkflowStepStatus::Completed;
                            st.output = Some(exec_out.value);
                            st.last_error = None;
                            st.updated_at = now_secs;
                        }
                        record.updated_at = now_secs;
                        self.save(&record)?;
                    }
                    Err(err) => {
                        if let Some(st) = record.steps.get_mut(&step_id) {
                            st.status = WorkflowStepStatus::Failed;
                            st.last_error = Some(err.clone());
                            st.updated_at = now_secs;
                        }
                        let can_still_retry = record.steps[&step_id].attempts
                            <= record.steps[&step_id].spec.max_retries;
                        let mut rolled_back = false;
                        if record.spec.rollback_on_failure && !can_still_retry {
                            restore_rollback_snapshots(&record.rollback_snapshots);
                            for st in record.steps.values_mut() {
                                if st.spec.mutating && st.status == WorkflowStepStatus::Completed {
                                    st.status = WorkflowStepStatus::RolledBack;
                                }
                            }
                            record.status = WorkflowStatus::RolledBack;
                            rolled_back = true;
                        } else {
                            record.status = WorkflowStatus::Failed;
                        }
                        record.updated_at = now_secs;
                        self.save(&record)?;
                        return Ok(WorkflowAdvanceOutcome::Failed {
                            workflow_id: workflow_id.to_string(),
                            step_id,
                            error: err,
                            rolled_back,
                        });
                    }
                }
            }
        }

        let all_completed = record
            .steps
            .values()
            .all(|s| s.status == WorkflowStepStatus::Completed);
        if all_completed {
            record.status = WorkflowStatus::Completed;
            record.updated_at = now_secs;
            self.save(&record)?;
            Ok(WorkflowAdvanceOutcome::Completed {
                workflow_id: workflow_id.to_string(),
            })
        } else {
            Err("workflow yielded with blocked unfinished steps".into())
        }
    }
}

fn capture_rollback_snapshot(snapshots: &mut Vec<FileRollbackSnapshot>, path: &Path) {
    if snapshots.iter().any(|s| s.path == path) {
        return;
    }
    match std::fs::read(path) {
        Ok(bytes) => snapshots.push(FileRollbackSnapshot {
            path: path.to_path_buf(),
            existed_before: true,
            prior_bytes: Some(bytes),
        }),
        Err(_) => snapshots.push(FileRollbackSnapshot {
            path: path.to_path_buf(),
            existed_before: false,
            prior_bytes: None,
        }),
    }
}

fn restore_rollback_snapshots(snapshots: &[FileRollbackSnapshot]) {
    for snap in snapshots.iter().rev() {
        if snap.existed_before {
            if let Some(bytes) = &snap.prior_bytes {
                let _ = std::fs::write(&snap.path, bytes);
            }
        } else {
            let _ = std::fs::remove_file(&snap.path);
        }
    }
}

#[cfg(test)]
mod tests;
