use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::{complete_counted_and_summarized, complete_created_files};
use crate::parse::action::extract_action_details;
use crate::parse::pystr::{char_slice, is_truthy, py_str, py_str_or_empty};
use crate::seam::worker::{Completion, ToolOutcome, WorkerError};

mod repair;
use repair::*;

pub fn request_wants_web(request: &str) -> bool {
    crate::kernel::agentloop::guided::request_wants_web(request)
}

pub fn request_wants_computer_use(request: &str) -> bool {
    crate::kernel::agentloop::guided::request_wants_computer_use(request)
}

pub fn request_wants_knowledge(request: &str) -> bool {
    crate::kernel::agentloop::guided::request_wants_knowledge(request)
}

pub fn url_named_in_request(request: &str) -> Option<String> {
    crate::kernel::agentloop::guided::url_named_in_request(request)
}

pub(in crate::kernel::agentloop) use repair::{
    find_request_ui_element_center, url_from_transcript,
};

pub(super) struct Chosen<'a> {
    pub name: &'a str,
    pub thoughts: &'a str,
    pub args: Map<String, Value>,
    pub final_message: Option<Value>,
}

#[derive(Clone, Copy)]
pub(super) struct Dispatched<'a> {
    pub original: &'a str,
    pub name: &'a str,
    pub thoughts: &'a str,
}

pub(super) enum StepFlow {
    Continue,
    Break,
    Finished,
}

pub(super) fn remember_catalog_name(ctx: &mut AgentRunContext, dispatched: &str, chosen: &str) {
    if dispatched == chosen {
        return;
    }
    if let Some(step) = ctx.transcript.last_mut() {
        if step.get("action").and_then(Value::as_str) == Some(dispatched) {
            step["action"] = json!(chosen);
        }
    }
}

impl Runtime {
    pub async fn execute(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.executing_model.clone().or(req.executing_model.clone());
        let profile = self.resolve_profile(ctx, model_id.as_deref()).await;
        self.consult_named_skill(ctx, req).await;
        if profile.decomposed {
            return self.execute_guided(ctx, req, profile).await;
        }
        let executed = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .count() as u32;
        let budget = req.max_steps.saturating_sub(executed).max(1);
        let mut parse_failures = 0u32;
        let mut parse_budget = profile.parse_failure_budget;
        let mut repeat_escalations = 1u32;
        let mut seen_replies: BTreeSet<String> = BTreeSet::new();
        let mut last_failure: Option<String> = None;
        let mut repeated_failures = 0u32;
        let measured_run = self.deps.probe.is_some() && self.deps.agent_profile.is_none();
        let mut adaptive_prefix = false;

        for _ in 0..budget {
            let context = self.executor_context(ctx, req, profile);
            let raw = self
                .deps
                .worker
                .llm(Completion {
                    model_id: model_id.as_deref(),
                    message: "Execute the next step.",
                    context: &context,
                    max_tokens: self.deps.phase_budgets.execute_tokens,
                    temperature: crate::kernel::profile::execute_temperature(profile),
                    stop: &[],
                    prefix: if profile.forced_json_prefix || adaptive_prefix {
                        crate::kernel::profile::EXECUTE_JSON_PREFIX
                    } else {
                        ""
                    },
                })
                .await?;
            ctx.trace.llm_call("execute", model_id.as_deref());

            let parsed = match extract_action_details(&raw) {
                Ok((action, repairs)) => {
                    ctx.trace.repair("execute", &repairs);
                    Some(action)
                }
                Err(error) => match self.fence_rescue(ctx, &raw) {
                    Some(rescued) => {
                        ctx.trace.repair("execute", &["fence_rescue".to_string()]);
                        Some(rescued)
                    }
                    None => {
                        parse_failures += 1;
                        if measured_run
                            && !profile.forced_json_prefix
                            && !adaptive_prefix
                            && parse_failures >= 2
                        {
                            adaptive_prefix = true;
                            ctx.trace.decision(
                                "execute",
                                "adaptive_prefix_enabled",
                                &[
                                    ("profile", json!(profile.name)),
                                    ("parse_failures", json!(parse_failures)),
                                ],
                            );
                        }
                        let fingerprint = raw.trim().to_string();
                        let repeated = !fingerprint.is_empty() && !seen_replies.insert(fingerprint);
                        if repeated && repeat_escalations > 0 && parse_failures >= parse_budget {
                            repeat_escalations -= 1;
                            parse_budget += 1;
                        }
                        let slip = super::fallback::ParseSlip {
                            failures: parse_failures,
                            budget: parse_budget,
                            repeated,
                        };
                        if self.note_parse_failure(ctx, &raw, &error.0, slip, profile) {
                            if self.demote_to_guided(ctx, req, profile) {
                                return self
                                    .execute_guided(ctx, req, crate::kernel::profile::GUIDED)
                                    .await;
                            }
                            if self
                                .direct_fallback(ctx, req, model_id.as_deref(), profile)
                                .await?
                            {
                                ctx.state = AgentState::Verifying;
                                return Ok(());
                            }
                            break;
                        }
                        None
                    }
                },
            };
            let Some(action) = parsed else {
                continue;
            };

            let name = py_str_or_empty(action.get("action"));
            let thoughts = char_slice(&py_str_or_empty(action.get("thoughts")), 600).to_string();
            let args: Map<String, Value> = action
                .get("args")
                .filter(|value| is_truthy(value))
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let final_message = action.get("message").cloned();

            if name == "final"
                && profile.name != crate::kernel::profile::STANDARD.name
                && !Self::has_execution_evidence(ctx)
                && !ctx.steps().is_empty()
            {
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": "final",
                    "error": "PREMATURE_FINAL: the run has a plan and no tool has run yet. \
                Nothing was finished.",
                }));
                let hint = "You answered `final` before running anything. The plan's first \
step has not been carried out — send that tool call instead of finishing.";
                if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
                    ctx.corrections.push(json!(hint));
                    ctx.trace.correction("execute", hint);
                }
                ctx.trace.decision("execute", "premature_final", &[]);
                self.emit_step(
                    "execute",
                    "blocked",
                    &[("action", json!("final")), ("reason", json!("premature"))],
                );
                parse_failures += 1;
                if parse_failures >= parse_budget {
                    if self.demote_to_guided(ctx, req, profile) {
                        return self
                            .execute_guided(ctx, req, crate::kernel::profile::GUIDED)
                            .await;
                    }
                    if self
                        .direct_fallback(ctx, req, model_id.as_deref(), profile)
                        .await?
                    {
                        ctx.state = AgentState::Verifying;
                        return Ok(());
                    }
                    break;
                }
                continue;
            }

            let flow = self
                .perform_action(
                    ctx,
                    req,
                    Chosen {
                        name: &name,
                        thoughts: &thoughts,
                        args,
                        final_message,
                    },
                )
                .await;
            if last_step_unknown_action(ctx) {
                let known = self.prompt_action_names(&req.message).join(", ");
                let hint = format!(
                    "Your last reply named an action this run does not have. \
Choose one of these exact names: {known}."
                );
                if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
                    ctx.corrections.push(json!(hint));
                    ctx.trace.correction("execute", &hint);
                }
            }
            if last_step_copied_the_example(ctx) || last_step_unknown_action(ctx) {
                parse_failures += 1;
                if measured_run
                    && !profile.forced_json_prefix
                    && !adaptive_prefix
                    && parse_failures >= 2
                {
                    adaptive_prefix = true;
                    ctx.trace.decision(
                        "execute",
                        "adaptive_prefix_enabled",
                        &[
                            ("profile", json!(profile.name)),
                            ("parse_failures", json!(parse_failures)),
                        ],
                    );
                }
                if parse_failures >= parse_budget {
                    if self.demote_to_guided(ctx, req, profile) {
                        return self
                            .execute_guided(ctx, req, crate::kernel::profile::GUIDED)
                            .await;
                    }
                    if self
                        .direct_fallback(ctx, req, model_id.as_deref(), profile)
                        .await?
                    {
                        ctx.state = AgentState::Verifying;
                        return Ok(());
                    }
                    break;
                }
            }
            match failing_dispatch_signature(ctx) {
                Some(signature) => {
                    if last_failure.as_deref() == Some(signature.as_str()) {
                        repeated_failures += 1;
                    } else {
                        repeated_failures = 1;
                    }
                    last_failure = Some(signature);
                }
                None => {
                    last_failure = None;
                    repeated_failures = 0;
                }
            }
            if repeated_failures >= crate::kernel::agentloop::guided::REPEAT_FAILURE_LIMIT {
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "action": name,
                    "error": "LOOP_DETECTED: identical action+args failed repeatedly — halted.",
                }));
                ctx.trace
                    .decision("execute", "loop_detected", &[("tool", json!(name))]);
                self.emit_step(
                    "execute",
                    "blocked",
                    &[("action", json!(name)), ("reason", json!("loop_detected"))],
                );
                if self
                    .direct_fallback(ctx, req, model_id.as_deref(), profile)
                    .await?
                {
                    ctx.state = AgentState::Verifying;
                    return Ok(());
                }
                break;
            }
            match flow {
                StepFlow::Continue => continue,
                StepFlow::Break => break,
                StepFlow::Finished => return Ok(()),
            }
        }

        ctx.state = AgentState::Verifying;
        Ok(())
    }

    fn demote_to_guided(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        profile: AgentProfile,
    ) -> bool {
        if profile.decomposed || self.deps.probe.is_none() || self.deps.agent_profile.is_some() {
            return false;
        }
        let decision = if Self::has_execution_evidence(ctx) {
            "profile_demoted_mid_run"
        } else {
            "profile_demoted"
        };
        ctx.trace.decision(
            "execute",
            decision,
            &[("from", json!(profile.name)), ("to", json!("guided"))],
        );
        self.emit_step(
            "execute",
            "profile",
            &[("profile", json!({"profile": "guided", "source": decision}))],
        );
        self.set_resolved_profile(crate::kernel::profile::GUIDED);
        if let Some(config) = &self.deps.probe {
            if let Some(model_id) = ctx
                .executing_model
                .as_deref()
                .or(req.executing_model.as_deref())
            {
                let verdict = crate::kernel::probe::verdict_for(
                    crate::kernel::probe::ActionScore::Failed,
                    true,
                );
                crate::kernel::probe::store(config, model_id, crate::VERSION, &verdict);
            }
        }
        true
    }

    pub(super) async fn perform_action(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        chosen: Chosen<'_>,
    ) -> StepFlow {
        let Chosen {
            name,
            thoughts,
            mut args,
            final_message,
        } = chosen;
        let original = name.to_string();

        let catalog = if original == "final" {
            Vec::new()
        } else {
            self.run_catalog(&req.message)
        };
        if original != "final" {
            if let Some(entry) = crate::tools::catalog::entry_for(&catalog, &original) {
                let mut coerced = crate::tools::catalog::coerce_and_unwrap_args(entry, &mut args);
                let snapshot = args.clone();
                let adopted = crate::tools::catalog::adopt_named_args(entry, &snapshot, &mut args);
                if !adopted.is_empty() {
                    coerced.extend(crate::tools::catalog::coerce_and_unwrap_args(
                        entry, &mut args,
                    ));
                    ctx.trace.decision(
                        "execute",
                        "synonym_arg_adopted",
                        &[("action", json!(original)), ("args", json!(adopted))],
                    );
                }
                if !coerced.is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "arg_coerced",
                        &[("action", json!(original)), ("args", json!(coerced))],
                    );
                }
                let filled = crate::tools::catalog::fill_documented_defaults(entry, &mut args);
                if !filled.is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "documented_default_filled",
                        &[("action", json!(original)), ("args", json!(filled))],
                    );
                }
                let stated = self.fill_stated_request_args(ctx, entry, &mut args, req, &catalog);
                if !stated.is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "request_arg_filled",
                        &[("action", json!(original)), ("args", json!(stated))],
                    );
                }
                if let Some((element, cx, cy)) =
                    self.resolve_ui_element_coords(ctx, req, &original, &mut args)
                {
                    ctx.trace.decision(
                        "execute",
                        "ui_element_resolved",
                        &[
                            ("action", json!(original)),
                            ("element", json!(element)),
                            ("x", json!(cx)),
                            ("y", json!(cy)),
                        ],
                    );
                }
                if let Some(missing) = crate::tools::catalog::missing_required(entry, &args) {
                    return self.refuse_incomplete_call(ctx, entry, &original, &args, &missing);
                }
            }
        }

        let name = match crate::tools::catalog::resolve(&original, |bare| {
            self.deps.policies.tools.contains_key(bare)
                || self.deps.tool_names.iter().any(|known| known == bare)
        }) {
            crate::tools::catalog::Resolved::Native(bare) => bare.to_string(),
            crate::tools::catalog::Resolved::External(_) => {
                let flow = self
                    .dispatch_external(ctx, req, &original, thoughts, &args)
                    .await;
                self.steer_to_write_target(ctx, req, &original);
                return flow;
            }
        };

        if self.deps.scoped_knowledge_tools.contains(&name) {
            args.insert(
                "workspace_id".into(),
                json!(req
                    .workspace_id
                    .clone()
                    .unwrap_or_else(|| "personal".into())),
            );
            args.insert(
                "user_email".into(),
                json!(req.user_email.clone().unwrap_or_else(|| "local".into())),
            );
        }

        if name == "final" {
            let said = match final_message {
                None => "작업을 완료했습니다.".to_string(),
                Some(message) => py_str_or_empty(Some(&message)),
            };
            let settled =
                complete_created_files(&said, &ctx.transcript, &self.deps.file_create_actions);
            ctx.final_message =
                complete_counted_and_summarized(&settled, &req.message, &ctx.transcript);
            if crate::kernel::transcript::rag_grounding::transcript_has_evidence(&ctx.transcript) {
                self.enforce_or_repair_grounded_final(ctx, req).await;
            }
            let exec_model = ctx.executing_model.clone().or(req.executing_model.clone());
            if self.profile(exec_model.as_deref()).decomposed {
                self.refine_synthesis_coverage(ctx, req, exec_model.as_deref())
                    .await;
            }
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": "final", "thoughts": thoughts,
            }));
            ctx.trace.decision("execute", "final", &[]);
            self.emit_step("execute", "final", &[]);
            ctx.state = AgentState::Verifying;
            return StepFlow::Finished;
        }

        if self.is_repeated_create(ctx, &name, &args) {
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": name,
                "error": "LOOP_DETECTED: identical action+args repeated — halted.",
            }));
            ctx.trace
                .decision("execute", "loop_detected", &[("tool", json!(name))]);
            self.emit_step(
                "execute",
                "blocked",
                &[("action", json!(name)), ("reason", json!("loop_detected"))],
            );
            return StepFlow::Break;
        }

        if self.deps.file_create_actions.contains(&name) && copies_the_example(&args) {
            ctx.transcript.push(json!({
                "state": AgentState::Executing.as_str(),
                "action": name,
                "args": Value::Object(args.clone()),
                "error": "COPIED_EXAMPLE: the reply repeated the prompt's worked example \
            instead of doing the task. Nothing was written.",
            }));
            let hint = "Your last reply copied the example from the instructions. \
The example only shows the JSON shape — write the file this request actually asks for, \
with its own path and its own content.";
            if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
                ctx.corrections.push(json!(hint));
                ctx.trace.correction("execute", hint);
            }
            ctx.trace
                .decision("execute", "copied_example", &[("tool", json!(name))]);
            self.emit_step(
                "execute",
                "blocked",
                &[("action", json!(name)), ("reason", json!("copied_example"))],
            );
            return StepFlow::Continue;
        }

        if name == "clear_history" {
            let keep_last = args.get("keep_last").cloned().unwrap_or(json!(0));
            let mut call = Map::new();
            call.insert("keep_last".into(), keep_last);
            let outcome = self
                .deps
                .worker
                .tool("clear_history", &call, req.workspace_id.as_deref())
                .await;
            let mut step = json!({
                "state": AgentState::Executing.as_str(),
                "action": name, "thoughts": thoughts,
                "args": Value::Object(args.clone()),
            });
            let ok = match outcome {
                ToolOutcome::Result(result) => {
                    step["result"] = result;
                    true
                }
                ToolOutcome::Error(error) => {
                    step["error"] = json!(error);
                    false
                }
            };
            ctx.transcript.push(step);
            self.emit_step(
                "execute",
                "tool",
                &[("action", json!(name)), ("ok", json!(ok))],
            );
            return StepFlow::Continue;
        }

        let dispatched = Dispatched {
            original: &original,
            name: &name,
            thoughts,
        };
        if self.govern_and_dispatch(ctx, req, dispatched, &args).await {
            self.steer_to_write_target(ctx, req, &original);
            self.repair_with_documented_default(ctx, req, &catalog, dispatched, args)
                .await;
        }
        StepFlow::Continue
    }

    pub(super) async fn govern_and_dispatch(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        dispatched: Dispatched<'_>,
        args: &Map<String, Value>,
    ) -> bool {
        let Dispatched {
            original,
            name,
            thoughts,
        } = dispatched;
        let policy = self.deps.policies.policy_for(name, args);
        let call = Call {
            name,
            thoughts,
            args,
            policy: &policy,
        };
        let (proposed, allows_additive) = self.governor_review(ctx, req, call).await;
        if proposed {
            remember_catalog_name(ctx, name, original);
            return false;
        }
        if self.blocked_by_gates(ctx, req, call, allows_additive) {
            remember_catalog_name(ctx, name, original);
            return false;
        }
        self.dispatch_step(ctx, req, call).await;
        remember_catalog_name(ctx, name, original);
        true
    }

    async fn enforce_or_repair_grounded_final(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) {
        use crate::kernel::transcript::rag_grounding::{
            abstention_message, build_evidence_card, extract_evidence_passages,
            verify_grounded_answer, GroundingDefect,
        };
        let passages = extract_evidence_passages(&ctx.transcript);
        let report = verify_grounded_answer(&ctx.final_message, &passages);
        if report.is_clean() {
            return;
        }
        if report.has(GroundingDefect::ZeroActiveEvidenceHallucination) {
            ctx.final_message = abstention_message(&req.message);
            ctx.trace.decision(
                "execute",
                "grounded_abstention_enforced",
                &[("report", report.to_detail())],
            );
            return;
        }
        let card = build_evidence_card(&passages, 1600);
        let defects = report
            .defects
            .iter()
            .map(|d| d.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        let details = report.details.join("; ");
        let repair_context = format!(
            "{card}\n\n[USER REQUEST]\n{}\n\n[DRAFT ANSWER WITH GROUNDING DEFECTS ({defects}: {details})]\n{}\n\n\
Rewrite the answer so that every number, polarity, and claim strictly matches ONLY the [ACTIVE GROUNDED EVIDENCE] passages above, \
never uses [SUPERSEDED] facts, and cites active sources (e.g. [1], [2]). Reply with the corrected answer text only.",
            req.message.trim(),
            ctx.final_message.trim(),
        );
        let model_id = ctx.executing_model.clone().or(req.executing_model.clone());
        let Ok(raw) = self
            .deps
            .worker
            .llm(Completion {
                model_id: model_id.as_deref(),
                message:
                    "Rewrite the final answer strictly grounded in [ACTIVE GROUNDED EVIDENCE].",
                context: &repair_context,
                max_tokens: self.deps.phase_budgets.execute_tokens.min(512),
                temperature: 0.0,
                stop: &[],
                prefix: "",
            })
            .await
        else {
            return;
        };
        ctx.trace.llm_call("execute", model_id.as_deref());
        let candidate = if let Ok((parsed, _)) = extract_action_details(&raw) {
            let msg = py_str_or_empty(parsed.get("message"));
            if !msg.trim().is_empty() {
                msg.trim().to_string()
            } else {
                parsed
                    .get("args")
                    .and_then(|a| a.get("message"))
                    .map(py_str)
                    .unwrap_or_default()
                    .trim()
                    .to_string()
            }
        } else {
            let stripped = match crate::parse::channel::channel_payload(&raw) {
                Some(payload) => crate::kernel::agentloop::guided::strip_control_tokens(&payload),
                None => crate::kernel::agentloop::guided::strip_control_tokens(&raw),
            };
            stripped.trim().to_string()
        };
        if !candidate.is_empty() {
            let draft_before = ctx.final_message.trim().to_string();
            ctx.final_message = candidate;
            let mut after = verify_grounded_answer(&ctx.final_message, &passages);
            if !after.is_clean() && ctx.final_message.trim() != draft_before {
                if let Some(synthesized) =
                    crate::kernel::transcript::synthesize_active_evidence_answer(&passages)
                {
                    ctx.final_message = synthesized;
                    after = verify_grounded_answer(&ctx.final_message, &passages);
                }
            }
            ctx.trace.decision(
                "execute",
                "grounded_answer_repaired",
                &[("before", report.to_detail()), ("after", after.to_detail())],
            );
        }
    }

    async fn refine_synthesis_coverage(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
    ) {
        use crate::kernel::agentloop::synthesis::{
            build_evidence_scaffold, build_synthesis_refinement_prompt, check_synthesis_coverage,
            collect_evidence_sources,
        };
        if crate::kernel::transcript::rag_grounding::is_abstention(&ctx.final_message) {
            return;
        }
        let sources = collect_evidence_sources(&ctx.transcript, 8);
        if sources.len() < 2 {
            return;
        }
        let before = check_synthesis_coverage(&ctx.final_message, &sources);
        if before.is_complete() {
            return;
        }
        let scaffold = build_evidence_scaffold(&req.message, &ctx.transcript, 1400);
        if scaffold.is_empty() {
            return;
        }
        let prompt = build_synthesis_refinement_prompt(
            &req.message,
            &ctx.final_message,
            &scaffold,
            &before.missing_sources,
        );
        let Ok(raw) = self
            .deps
            .worker
            .llm(Completion {
                model_id,
                message: "Revise the answer to cover all listed evidence sources.",
                context: &prompt,
                max_tokens: self.deps.phase_budgets.execute_tokens.min(512),
                temperature: 0.0,
                stop: &[],
                prefix: "",
            })
            .await
        else {
            return;
        };
        ctx.trace.llm_call("execute", model_id);
        let candidate = match crate::parse::channel::channel_payload(&raw) {
            Some(payload) => crate::kernel::agentloop::guided::strip_control_tokens(&payload),
            None => crate::kernel::agentloop::guided::strip_control_tokens(&raw),
        }
        .trim()
        .to_string();
        if candidate.is_empty() {
            return;
        }
        let after = check_synthesis_coverage(&candidate, &sources);
        if !after.echoed_instruction
            && !after.is_stub
            && (after.covered_sources >= before.covered_sources
                || before.echoed_instruction
                || before.is_stub)
        {
            ctx.final_message = candidate;
            ctx.trace.decision(
                "execute",
                "synthesis_coverage_refined",
                &[("before", before.to_detail()), ("after", after.to_detail())],
            );
        }
    }
}

#[cfg(test)]
mod tests;
#[cfg(test)]
mod tests_dispatch;
#[cfg(test)]
mod tests_governance;
