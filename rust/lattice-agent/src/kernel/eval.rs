//! Agent quality evaluation metrics and deterministic run scoring (P2).
//!
//! Pure kernel evaluator over [`AgentRunContext`], [`LoopTrace`], and the
//! post-run [`Workspace`] state. Measures:
//!
//! * Requirement fulfillment rate
//! * Task success rate
//! * Unsupported / ungrounded claim rate
//! * File modification accuracy
//! * Retry count & token / execution cost tracking
//! * Safe abort rate after change-proposal / approval rejection
//! * Cross-model profile consistency (`standard` / `compact` / `guided`)

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::kernel::state::{AgentRunContext, AgentState};
use crate::tools::sandbox::Workspace;

/// Deterministic character-to-token estimate (~4 chars/token) used when a
/// worker response omits explicit token counts.
pub fn estimate_tokens(text: &str) -> u64 {
    let chars = text.chars().count() as u64;
    if chars == 0 {
        0
    } else {
        chars.div_ceil(4)
    }
}

/// Token consumption recorded or estimated for an evaluated run.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenCost {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
}

impl TokenCost {
    pub fn new(prompt_tokens: u64, completion_tokens: u64) -> Self {
        Self {
            prompt_tokens,
            completion_tokens,
            total_tokens: prompt_tokens.saturating_add(completion_tokens),
        }
    }

    /// Estimate token usage from the run's transcript and final message when
    /// exact provider counters were not supplied.
    pub fn estimate_from_context(ctx: &AgentRunContext, prompt_text: &str) -> Self {
        let mut prompt_chars = estimate_tokens(prompt_text);
        let mut completion_chars = estimate_tokens(&ctx.final_message);
        for step in &ctx.transcript {
            if let Ok(serialized) = serde_json::to_string(step) {
                completion_chars = completion_chars.saturating_add(estimate_tokens(&serialized));
            }
            prompt_chars = prompt_chars.saturating_add(64);
        }
        Self::new(prompt_chars, completion_chars)
    }

    pub fn add(&mut self, other: TokenCost) {
        self.prompt_tokens = self.prompt_tokens.saturating_add(other.prompt_tokens);
        self.completion_tokens = self
            .completion_tokens
            .saturating_add(other.completion_tokens);
        self.total_tokens = self.total_tokens.saturating_add(other.total_tokens);
    }
}

/// Execution telemetry extracted from [`LoopTrace`] and wall-clock duration.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionCost {
    pub duration_ms: u64,
    pub llm_calls: u32,
    pub tool_calls: u32,
    pub parse_errors: u32,
    pub repairs: u32,
    pub retries: u32,
}

impl ExecutionCost {
    pub fn from_context(ctx: &AgentRunContext, duration_ms: u64) -> Self {
        let summary = ctx.trace.summary();
        let u32_of = |key: &str| {
            summary
                .get(key)
                .and_then(Value::as_u64)
                .unwrap_or(0)
                .min(u32::MAX as u64) as u32
        };
        let sum_map = |key: &str| -> u32 {
            summary
                .get(key)
                .and_then(Value::as_object)
                .map(|map| {
                    map.values()
                        .filter_map(Value::as_u64)
                        .fold(0u64, u64::saturating_add)
                })
                .unwrap_or(0)
                .min(u32::MAX as u64) as u32
        };
        let trace_retries = u32_of("retries");
        let trace_tools = sum_map("tool_outcomes").max(u32_of("tool_calls"));
        let trace_repairs = sum_map("repairs").max(u32_of("repairs"));
        Self {
            duration_ms,
            llm_calls: u32_of("llm_calls"),
            tool_calls: trace_tools,
            parse_errors: u32_of("parse_errors"),
            repairs: trace_repairs,
            retries: trace_retries.max(ctx.retry_count),
        }
    }

    pub fn add(&mut self, other: ExecutionCost) {
        self.duration_ms = self.duration_ms.saturating_add(other.duration_ms);
        self.llm_calls = self.llm_calls.saturating_add(other.llm_calls);
        self.tool_calls = self.tool_calls.saturating_add(other.tool_calls);
        self.parse_errors = self.parse_errors.saturating_add(other.parse_errors);
        self.repairs = self.repairs.saturating_add(other.repairs);
        self.retries = self.retries.saturating_add(other.retries);
    }
}

/// Expected workspace file state after a run completes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileExpectation {
    pub path: String,
    pub must_exist: bool,
    #[serde(default)]
    pub must_contain: Vec<String>,
    #[serde(default)]
    pub exact_content: Option<String>,
}

/// A single functional requirement checked against the run's transcript and
/// workspace side effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RequirementSpec {
    pub id: String,
    pub description: String,
    #[serde(default)]
    pub required_tools: Vec<String>,
    #[serde(default)]
    pub expected_files: Vec<FileExpectation>,
    #[serde(default)]
    pub required_response_terms: Vec<String>,
}

/// Explicit claim in the final answer that must be backed by tool/workspace evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClaimAssertion {
    pub statement: String,
    #[serde(default)]
    pub evidence_paths: Vec<String>,
    #[serde(default)]
    pub evidence_tools: Vec<String>,
    #[serde(default)]
    pub evidence_substrings: Vec<String>,
}

/// Governance/approval rejection contract for verifying safe aborts.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ProposalAbortExpectation {
    #[default]
    NotRejected,
    Rejected {
        /// Whether the run was cleanly cancelled/aborted (e.g. proposal rejected
        /// or user denied resume) and must NOT leave mutations on disk.
        aborted_cleanly: bool,
        /// Baseline workspace bytes before the run/rejection (`path -> Some(bytes) | None`).
        baseline_files: BTreeMap<String, Option<Vec<u8>>>,
    },
}

/// One evaluation case executed against the agent loop.
#[derive(Debug, Clone)]
pub struct EvalCase {
    pub case_id: String,
    pub profile_name: String,
    pub model_id: String,
    pub prompt_text: String,
    pub expect_success: bool,
    pub requirements: Vec<RequirementSpec>,
    pub expected_files: Vec<FileExpectation>,
    pub explicit_claims: Vec<ClaimAssertion>,
    pub allow_unexpected_edits: bool,
    pub pre_existing_paths: BTreeSet<String>,
    pub abort_expectation: ProposalAbortExpectation,
}

impl EvalCase {
    pub fn new(
        case_id: impl Into<String>,
        profile_name: impl Into<String>,
        model_id: impl Into<String>,
    ) -> Self {
        Self {
            case_id: case_id.into(),
            profile_name: profile_name.into(),
            model_id: model_id.into(),
            prompt_text: String::new(),
            expect_success: true,
            requirements: Vec::new(),
            expected_files: Vec::new(),
            explicit_claims: Vec::new(),
            allow_unexpected_edits: true,
            pre_existing_paths: BTreeSet::new(),
            abort_expectation: ProposalAbortExpectation::NotRejected,
        }
    }
}

/// Evaluation score for a single agent run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalRunResult {
    pub case_id: String,
    pub profile_name: String,
    pub model_id: String,
    pub final_state: String,
    pub task_succeeded: bool,
    pub requirements_total: usize,
    pub requirements_fulfilled: usize,
    pub requirement_fulfillment_rate: f64,
    pub claims_total: usize,
    pub ungrounded_claims: usize,
    pub unsupported_claim_rate: f64,
    pub expected_files_total: usize,
    pub accurate_file_modifications: usize,
    pub unexpected_modifications: usize,
    pub file_modification_accuracy: f64,
    pub retry_count: u32,
    pub token_cost: TokenCost,
    pub execution_cost: ExecutionCost,
    pub proposal_rejection_observed: bool,
    pub safe_abort_verified: bool,
}

/// Per-profile rollup inside [`CrossModelConsistencyReport`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProfileEvalSummary {
    pub profile_name: String,
    pub runs: usize,
    pub task_success_rate: f64,
    pub requirement_fulfillment_rate: f64,
    pub unsupported_claim_rate: f64,
    pub mean_retries: f64,
}

/// Consistency of behavior across models/profiles (`standard`, `compact`, `guided`)
/// evaluated on the same `case_id` tasks.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrossModelConsistencyReport {
    pub shared_case_count: usize,
    pub outcome_agreement_rate: f64,
    pub max_fulfillment_spread: f64,
    pub per_profile: Vec<ProfileEvalSummary>,
}

/// Aggregate quality metrics report across a benchmark/regression suite.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QualityMetricsReport {
    pub total_runs: usize,
    pub requirement_fulfillment_rate: f64,
    pub task_success_rate: f64,
    pub unsupported_claim_rate: f64,
    pub file_modification_accuracy: f64,
    pub total_retries: u32,
    pub mean_retries_per_run: f64,
    pub total_token_cost: TokenCost,
    pub total_execution_cost: ExecutionCost,
    pub proposal_rejections: usize,
    pub safe_abort_rate: f64,
    pub cross_model_consistency: CrossModelConsistencyReport,
}

fn is_succeeded_tool_step(step: &Value) -> Option<&str> {
    let action = step.get("action").and_then(Value::as_str)?;
    if matches!(action, "plan" | "final" | "verdict" | "think") {
        return None;
    }
    if step.get("error").is_some_and(|v| !v.is_null())
        || step.get("blocked").and_then(Value::as_bool) == Some(true)
    {
        return None;
    }
    if let Some(res) = step.get("result") {
        if res.get("error").is_some_and(|v| !v.is_null()) {
            return None;
        }
    }
    Some(action)
}

fn succeeded_tools(ctx: &AgentRunContext) -> BTreeSet<String> {
    ctx.transcript
        .iter()
        .filter_map(|step| is_succeeded_tool_step(step).map(str::to_string))
        .collect()
}

fn succeeded_tool_outputs(ctx: &AgentRunContext) -> String {
    ctx.transcript
        .iter()
        .filter(|step| is_succeeded_tool_step(step).is_some())
        .filter_map(|step| serde_json::to_string(step).ok())
        .collect::<Vec<_>>()
        .join("\n")
}

fn final_claim_messages(ctx: &AgentRunContext) -> String {
    let mut parts = Vec::new();
    if !ctx.final_message.trim().is_empty() {
        parts.push(ctx.final_message.clone());
    }
    for step in &ctx.transcript {
        if step.get("action").and_then(Value::as_str) == Some("final") {
            if let Some(msg) = step.get("message").and_then(Value::as_str) {
                parts.push(msg.to_string());
            }
        }
    }
    parts.join("\n")
}

fn transcript_text(ctx: &AgentRunContext) -> String {
    ctx.transcript
        .iter()
        .filter_map(|step| serde_json::to_string(step).ok())
        .collect::<Vec<_>>()
        .join("\n")
}

fn file_matches(workspace: &Workspace, spec: &FileExpectation) -> bool {
    let Ok(resolved) = workspace.resolve(&spec.path) else {
        return false;
    };
    let exists = resolved.is_file();
    if exists != spec.must_exist {
        return false;
    }
    if !spec.must_exist {
        return true;
    }
    let Ok(content) = std::fs::read_to_string(&resolved) else {
        return false;
    };
    if let Some(ref exact) = spec.exact_content {
        if &content != exact {
            return false;
        }
    }
    spec.must_contain
        .iter()
        .all(|needle| content.contains(needle.as_str()))
}

fn requirement_fulfilled(
    req: &RequirementSpec,
    ctx: &AgentRunContext,
    workspace: &Workspace,
    ok_tools: &BTreeSet<String>,
) -> bool {
    let tools_ok = req
        .required_tools
        .iter()
        .all(|tool| ok_tools.contains(tool.as_str()));
    let files_ok = req
        .expected_files
        .iter()
        .all(|spec| file_matches(workspace, spec));
    let response_lower = ctx.final_message.to_ascii_lowercase();
    let terms_ok = req
        .required_response_terms
        .iter()
        .all(|term| response_lower.contains(&term.to_ascii_lowercase()));
    tools_ok && files_ok && terms_ok
}

/// Extract file path tokens mentioned in `final_message` (e.g. `src/app.rs`,
/// `notes/todo.md`, `report.txt`) so ungrounded claims about nonexistent files
/// are caught automatically even without explicit `ClaimAssertion`s.
pub fn extract_claimed_paths(final_message: &str) -> Vec<String> {
    const KNOWN_EXTS: &[&str] = &[
        "rs", "py", "ts", "tsx", "js", "jsx", "json", "toml", "yaml", "yml", "md", "txt", "html",
        "css", "csv", "docx", "xlsx", "pptx", "pdf",
    ];
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for raw in final_message.split_whitespace() {
        let trimmed = raw.trim_matches(|c: char| {
            matches!(
                c,
                '`' | '"' | '\'' | ',' | '.' | ';' | ':' | '(' | ')' | '[' | ']' | '!' | '?'
            )
        });
        if trimmed.is_empty() || trimmed.contains("://") {
            continue;
        }
        let Some((_, ext)) = trimmed.rsplit_once('.') else {
            continue;
        };
        if KNOWN_EXTS.contains(&ext.to_ascii_lowercase().as_str())
            && !trimmed.starts_with('.')
            && seen.insert(trimmed.to_string())
        {
            out.push(trimmed.to_string());
        }
    }
    out
}

fn workspace_relative_files(workspace: &Workspace) -> BTreeSet<String> {
    fn walk(root: &std::path::Path, dir: &std::path::Path, out: &mut BTreeSet<String>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(root, &path, out);
            } else if path.is_file() {
                if let Ok(rel) = path.strip_prefix(root) {
                    out.insert(rel.to_string_lossy().replace('\\', "/"));
                }
            }
        }
    }
    let mut out = BTreeSet::new();
    walk(workspace.root(), workspace.root(), &mut out);
    out
}

fn claim_is_grounded(
    claim: &ClaimAssertion,
    workspace: &Workspace,
    ok_tools: &BTreeSet<String>,
    transcript_blob: &str,
) -> bool {
    let tools_ok = claim
        .evidence_tools
        .iter()
        .all(|t| ok_tools.contains(t.as_str()));
    let paths_ok = claim.evidence_paths.iter().all(|p| {
        workspace
            .resolve(p)
            .map(|resolved| resolved.exists())
            .unwrap_or(false)
    });
    let substrings_ok = claim.evidence_substrings.iter().all(|needle| {
        if transcript_blob.contains(needle.as_str()) {
            return true;
        }
        claim.evidence_paths.iter().any(|p| {
            workspace
                .resolve(p)
                .ok()
                .and_then(|resolved| std::fs::read_to_string(resolved).ok())
                .is_some_and(|text| text.contains(needle.as_str()))
        })
    });
    tools_ok && paths_ok && substrings_ok
}

fn verify_safe_abort(
    expectation: &ProposalAbortExpectation,
    ctx: &AgentRunContext,
    workspace: &Workspace,
) -> (bool, bool) {
    match expectation {
        ProposalAbortExpectation::NotRejected => (false, true),
        ProposalAbortExpectation::Rejected {
            aborted_cleanly,
            baseline_files,
        } => {
            if !*aborted_cleanly {
                return (true, false);
            }
            if ctx.state == AgentState::Done && ctx.approved_by_human {
                return (true, false);
            }
            let baseline_intact = baseline_files.iter().all(|(rel, expected_bytes)| {
                let Ok(resolved) = workspace.resolve(rel) else {
                    return false;
                };
                match expected_bytes {
                    None => !resolved.exists(),
                    Some(bytes) => std::fs::read(&resolved).ok().as_ref() == Some(bytes),
                }
            });
            (true, baseline_intact)
        }
    }
}

/// Score one completed or aborted run deterministically.
pub fn evaluate_run(
    case: &EvalCase,
    ctx: &AgentRunContext,
    workspace: &Workspace,
    explicit_tokens: Option<TokenCost>,
    duration_ms: u64,
) -> EvalRunResult {
    let ok_tools = succeeded_tools(ctx);
    let transcript_blob = transcript_text(ctx);

    let requirements_total = case.requirements.len();
    let requirements_fulfilled = case
        .requirements
        .iter()
        .filter(|req| requirement_fulfilled(req, ctx, workspace, &ok_tools))
        .count();
    let requirement_fulfillment_rate = if requirements_total == 0 {
        1.0
    } else {
        requirements_fulfilled as f64 / requirements_total as f64
    };

    let expected_files_total = case.expected_files.len();
    let accurate_file_modifications = case
        .expected_files
        .iter()
        .filter(|spec| file_matches(workspace, spec))
        .count();

    let unexpected_modifications = if case.allow_unexpected_edits {
        0
    } else {
        let expected_paths: BTreeSet<&str> = case
            .expected_files
            .iter()
            .filter(|f| f.must_exist)
            .map(|f| f.path.as_str())
            .collect();
        workspace_relative_files(workspace)
            .into_iter()
            .filter(|rel| {
                !expected_paths.contains(rel.as_str()) && !case.pre_existing_paths.contains(rel)
            })
            .count()
    };

    let file_modification_accuracy = if expected_files_total == 0 {
        if unexpected_modifications == 0 {
            1.0
        } else {
            0.0
        }
    } else {
        let denom = expected_files_total + unexpected_modifications;
        accurate_file_modifications as f64 / denom as f64
    };

    let mut claims_total = case.explicit_claims.len();
    let mut ungrounded_claims = case
        .explicit_claims
        .iter()
        .filter(|claim| !claim_is_grounded(claim, workspace, &ok_tools, &transcript_blob))
        .count();

    let tool_outputs_blob = succeeded_tool_outputs(ctx);
    let auto_paths = extract_claimed_paths(&final_claim_messages(ctx));
    for claimed in auto_paths {
        claims_total += 1;
        let grounded = workspace
            .resolve(&claimed)
            .map(|resolved| resolved.exists())
            .unwrap_or(false)
            || tool_outputs_blob.contains(&claimed);
        if !grounded {
            ungrounded_claims += 1;
        }
    }
    let unsupported_claim_rate = if claims_total == 0 {
        0.0
    } else {
        ungrounded_claims as f64 / claims_total as f64
    };

    let (proposal_rejection_observed, safe_abort_verified) =
        verify_safe_abort(&case.abort_expectation, ctx, workspace);

    let task_succeeded = if case.expect_success {
        ctx.state == AgentState::Done
            && requirements_fulfilled == requirements_total
            && accurate_file_modifications == expected_files_total
            && unexpected_modifications == 0
    } else {
        ctx.state != AgentState::Done && safe_abort_verified
    };

    let token_cost =
        explicit_tokens.unwrap_or_else(|| TokenCost::estimate_from_context(ctx, &case.prompt_text));
    let execution_cost = ExecutionCost::from_context(ctx, duration_ms);

    EvalRunResult {
        case_id: case.case_id.clone(),
        profile_name: case.profile_name.clone(),
        model_id: case.model_id.clone(),
        final_state: ctx.state.as_str().to_string(),
        task_succeeded,
        requirements_total,
        requirements_fulfilled,
        requirement_fulfillment_rate,
        claims_total,
        ungrounded_claims,
        unsupported_claim_rate,
        expected_files_total,
        accurate_file_modifications,
        unexpected_modifications,
        file_modification_accuracy,
        retry_count: execution_cost.retries,
        token_cost,
        execution_cost,
        proposal_rejection_observed,
        safe_abort_verified,
    }
}

/// Aggregate a suite of [`EvalRunResult`]s into a [`QualityMetricsReport`],
/// including cross-model / cross-profile consistency across shared `case_id`s.
pub fn aggregate_report(results: &[EvalRunResult]) -> QualityMetricsReport {
    let total_runs = results.len();
    if total_runs == 0 {
        return QualityMetricsReport {
            total_runs: 0,
            requirement_fulfillment_rate: 0.0,
            task_success_rate: 0.0,
            unsupported_claim_rate: 0.0,
            file_modification_accuracy: 0.0,
            total_retries: 0,
            mean_retries_per_run: 0.0,
            total_token_cost: TokenCost::default(),
            total_execution_cost: ExecutionCost::default(),
            proposal_rejections: 0,
            safe_abort_rate: 1.0,
            cross_model_consistency: CrossModelConsistencyReport {
                shared_case_count: 0,
                outcome_agreement_rate: 1.0,
                max_fulfillment_spread: 0.0,
                per_profile: Vec::new(),
            },
        };
    }

    let req_total: usize = results.iter().map(|r| r.requirements_total).sum();
    let req_ok: usize = results.iter().map(|r| r.requirements_fulfilled).sum();
    let requirement_fulfillment_rate = if req_total == 0 {
        results
            .iter()
            .map(|r| r.requirement_fulfillment_rate)
            .sum::<f64>()
            / total_runs as f64
    } else {
        req_ok as f64 / req_total as f64
    };

    let task_success_rate =
        results.iter().filter(|r| r.task_succeeded).count() as f64 / total_runs as f64;

    let claims_total: usize = results.iter().map(|r| r.claims_total).sum();
    let claims_bad: usize = results.iter().map(|r| r.ungrounded_claims).sum();
    let unsupported_claim_rate = if claims_total == 0 {
        0.0
    } else {
        claims_bad as f64 / claims_total as f64
    };

    let file_modification_accuracy = results
        .iter()
        .map(|r| r.file_modification_accuracy)
        .sum::<f64>()
        / total_runs as f64;

    let total_retries: u32 = results.iter().map(|r| r.retry_count).sum();
    let mean_retries_per_run = total_retries as f64 / total_runs as f64;

    let mut total_token_cost = TokenCost::default();
    let mut total_execution_cost = ExecutionCost::default();
    for r in results {
        total_token_cost.add(r.token_cost);
        total_execution_cost.add(r.execution_cost);
    }

    let rejections: Vec<&EvalRunResult> = results
        .iter()
        .filter(|r| r.proposal_rejection_observed)
        .collect();
    let proposal_rejections = rejections.len();
    let safe_abort_rate = if proposal_rejections == 0 {
        1.0
    } else {
        rejections.iter().filter(|r| r.safe_abort_verified).count() as f64
            / proposal_rejections as f64
    };

    let cross_model_consistency = compute_cross_model_consistency(results);

    QualityMetricsReport {
        total_runs,
        requirement_fulfillment_rate,
        task_success_rate,
        unsupported_claim_rate,
        file_modification_accuracy,
        total_retries,
        mean_retries_per_run,
        total_token_cost,
        total_execution_cost,
        proposal_rejections,
        safe_abort_rate,
        cross_model_consistency,
    }
}

fn compute_cross_model_consistency(results: &[EvalRunResult]) -> CrossModelConsistencyReport {
    let mut by_case: BTreeMap<&str, Vec<&EvalRunResult>> = BTreeMap::new();
    let mut by_profile: BTreeMap<&str, Vec<&EvalRunResult>> = BTreeMap::new();
    for r in results {
        by_case.entry(r.case_id.as_str()).or_default().push(r);
        by_profile
            .entry(r.profile_name.as_str())
            .or_default()
            .push(r);
    }

    let mut shared_case_count = 0usize;
    let mut agreed_cases = 0usize;
    let mut max_spread = 0.0f64;

    for group in by_case.values() {
        let distinct_profiles: BTreeSet<(&str, &str)> = group
            .iter()
            .map(|r| (r.profile_name.as_str(), r.model_id.as_str()))
            .collect();
        if distinct_profiles.len() < 2 {
            continue;
        }
        shared_case_count += 1;
        let first_ok = group[0].task_succeeded;
        if group.iter().all(|r| r.task_succeeded == first_ok) {
            agreed_cases += 1;
        }
        let min_f = group
            .iter()
            .map(|r| r.requirement_fulfillment_rate)
            .fold(f64::INFINITY, f64::min);
        let max_f = group
            .iter()
            .map(|r| r.requirement_fulfillment_rate)
            .fold(f64::NEG_INFINITY, f64::max);
        max_spread = max_spread.max(max_f - min_f);
    }

    let outcome_agreement_rate = if shared_case_count == 0 {
        1.0
    } else {
        agreed_cases as f64 / shared_case_count as f64
    };

    let per_profile = by_profile
        .into_iter()
        .map(|(profile_name, runs)| {
            let count = runs.len() as f64;
            let succ = runs.iter().filter(|r| r.task_succeeded).count() as f64 / count;
            let req = runs
                .iter()
                .map(|r| r.requirement_fulfillment_rate)
                .sum::<f64>()
                / count;
            let claims = runs.iter().map(|r| r.unsupported_claim_rate).sum::<f64>() / count;
            let retries = runs.iter().map(|r| r.retry_count as f64).sum::<f64>() / count;
            ProfileEvalSummary {
                profile_name: profile_name.to_string(),
                runs: runs.len(),
                task_success_rate: succ,
                requirement_fulfillment_rate: req,
                unsupported_claim_rate: claims,
                mean_retries: retries,
            }
        })
        .collect();

    CrossModelConsistencyReport {
        shared_case_count,
        outcome_agreement_rate,
        max_fulfillment_spread: max_spread,
        per_profile,
    }
}
