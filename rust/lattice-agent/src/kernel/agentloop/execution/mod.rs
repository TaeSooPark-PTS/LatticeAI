//! EXECUTE — one tool call at a time, through every gate, on the record.
//!
//! A port of `latticeai.core.agent.execution`. The longest phase and the only
//! one that changes anything, so it is also where all the governance lives (see
//! [`super::gates`]). The per-iteration order is the contract:
//!
//! budget → completion → parse (with its own failure budget and escalation) →
//! `final` → repeated-create guard → scoped-arg forcing → `clear_history` →
//! policy/risk → governor → gates → dispatch.
//!
//! The direct-path fallback at the bottom is the escape hatch for a model too
//! small to hold the tool-call protocol. It never fabricates evidence: no
//! planned paths, a staged proposal, or a tool error all leave the run to end
//! as it would have.
//!
//! v11.9.0 added two things to the parse step, both for the ~2B models the
//! compact profile exists for and both in [`super::fallback`]: a reply that
//! fenced the file instead of the tool call is turned into the write the plan
//! asked for, and a byte-identical rejected reply buys one extra attempt with
//! a prompt that names the repetition. Neither is reachable by a model that
//! answers correctly, and neither changes what a `standard` run does.

use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::{complete_a_count, complete_created_files};
use crate::parse::action::extract_action_details;
use crate::parse::pystr::{char_slice, is_truthy, py_str, py_str_or_empty};
use crate::surface::worker::{Completion, ToolOutcome, WorkerError};

mod repair;
use repair::*;

/// One action the executor decided on, however it decided.
///
/// The JSON loop reads these four out of a parsed object; the guided loop
/// assembles them from micro-turns. Bundling them is what lets both hand the
/// *same* value to [`Runtime::perform_action`].
pub(super) struct Chosen<'a> {
    pub name: &'a str,
    pub thoughts: &'a str,
    pub args: Map<String, Value>,
    /// `final`'s message, when the action is `final`. Kept as the raw `Value`
    /// the model sent so `py_str_or_empty` renders it exactly as Python did.
    pub final_message: Option<Value>,
}

/// One call on its way through the gate chain, named twice.
///
/// `original` is the catalog row the model chose and the transcript records
/// (`mcp.grep`); `name` is what [`crate::tools::catalog::resolve`] dispatches it
/// as (`grep`). Both are needed after a dispatch — the repair tail looks the
/// call up in the catalog under one and re-sends it under the other — and
/// carrying them together is what keeps the two from being passed in the wrong
/// order.
#[derive(Clone, Copy)]
pub(super) struct Dispatched<'a> {
    pub original: &'a str,
    pub name: &'a str,
    pub thoughts: &'a str,
}

/// What one performed action means for the loop that is driving it.
pub(super) enum StepFlow {
    /// Take another step.
    Continue,
    /// Stop taking steps (loop guard).
    Break,
    /// The run is finished and already in `VERIFYING`.
    Finished,
}

/// Keep the name the model chose on the transcript.
///
/// `resolve` rewrites `mcp.grep` to `grep` so the kernel's gate chain runs;
/// the step still records `mcp.grep`, because that is the catalog row and
/// the prefixed action the harness asked the model to pick.
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
    /// EXECUTE: the executor role calls tools one at a time until `final` or
    /// the budget is exhausted.
    pub async fn execute(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.executing_model.clone().or(req.executing_model.clone());
        // v12.0.0: measured, not guessed — and measured **once** per run, so a
        // step never pays for a probe the previous step already paid for.
        let profile = self.resolve_profile(ctx, model_id.as_deref()).await;
        // **An explicitly named skill is read before the first step** (v12.0.0)
        // — on every dial, because it is the user's instruction rather than a
        // property of the model. See [`Runtime::consult_named_skill`].
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
        // The parse budget, and the **one** extra attempt a byte-identical
        // rejected reply may buy (ported from the deleted
        // `file_generation.orchestration`): the ordinary retry is known to be
        // dead on arrival there — the correction did not change the reply — so
        // the round trip is better spent on a prompt that names the repetition
        // than on a third identical one. A model that never repeats itself is
        // never charged for it.
        let mut parse_budget = profile.parse_failure_budget;
        let mut repeat_escalations = 1u32;
        let mut seen_replies: BTreeSet<String> = BTreeSet::new();
        // The last *tool* failure's `action|args`, and how many times it has
        // come back unchanged — the floor [`super::guided`] has always had,
        // enforced here too since v12.0.0.
        let mut last_failure: Option<String> = None;
        let mut repeated_failures = 0u32;

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
                    // Structure by construction (v12.0.0): under a profile that
                    // asks for it the completion *starts* inside the object, so
                    // a preamble, a fence or a `<|channel|>` frame is not a
                    // thing the model can emit rather than a thing the repair
                    // chain has to undo. Off for `standard`.
                    prefix: if profile.forced_json_prefix {
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
                // The reply is not an action object — but it may still contain
                // the *file* the plan is waiting for, fenced as code. That is
                // the most common ~2B slip on a file task, and refusing the
                // step throws away work the model actually did.
                Err(error) => match self.fence_rescue(ctx, &raw) {
                    Some(rescued) => {
                        ctx.trace.repair("execute", &["fence_rescue".to_string()]);
                        Some(rescued)
                    }
                    None => {
                        parse_failures += 1;
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
                            if self.demote_to_guided(ctx, profile) {
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

            // **Finishing before anything has happened is a format failure**
            // (v12.0.0). A weak model that cannot land a tool call reaches for
            // the one action that always parses: a live 2B answered a search
            // request with `plan`, `execute_step` and then `final`, ending a
            // sixteen-step run on step three with no tool ever dispatched.
            // Verification caught it — a PASS with no evidence is
            // `NEEDS_REVIEW` — but catching it is not doing it, and the run had
            // thirteen steps left.
            //
            // So it spends the format budget the invented action names and the
            // copied example spend, which is what reaches
            // [`Runtime::demote_to_guided`]: the dial that never asks for JSON
            // is the honest next move for a model that has produced none. Three
            // conditions keep it off every run it does not belong to — the
            // planner named work that has not run, nothing has succeeded yet,
            // and the dial is one of the weak ones, so `standard` (every frozen
            // trajectory) and a run whose plan is empty (a greeting, a
            // question) finish exactly as they always did.
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
                    if self.demote_to_guided(ctx, profile) {
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
            // A reply that copied the worked example is a format failure, not a
            // tool failure: the model produced valid JSON and no work. It spends
            // the same budget a parse slip does, so a model that only ever
            // copies the example reaches the same escape hatches.
            // A well-formed object that names no tool this run has (or that
            // copied the example) is the same failure as unparseable JSON:
            // the contract did not hold, no work happened, spend the format
            // budget and demote rather than looping `save_as_file` until
            // the step cap.
            // **And a name this run does not have gets told what it does have**
            // (v12.0.0). The copied-example refusal has always pushed a
            // correction naming what went wrong; an invented action name pushed
            // none, so the only feedback a model got for `{"action": "plan"}`
            // was the seam's `Unknown action: plan` buried in a transcript step
            // — and a live 2B answered that by sending `plan` three more times.
            // The correction is the run's own catalog, which is the one list
            // that makes the next reply choosable, and it is deduped so a model
            // that repeats itself does not fill the correction budget with one
            // sentence.
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
                if parse_failures >= parse_budget {
                    if self.demote_to_guided(ctx, profile) {
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
            // **The same call, the same arguments, the same error, again.**
            // Not a format problem, so none of the budgets above saw it: a
            // live 2B sent `run_command npm run build` thirteen times to a
            // validator that had already refused it twice, and spent the whole
            // step budget doing it. A step that has failed the same way twice
            // is not one more attempt away from working, so the run stops
            // asking and takes the same escape hatch a spent format budget
            // takes — which, for a plan that names a file or a read, is what
            // still produces the deliverable.
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

    /// Should this run stop asking for JSON and finish in `guided`?
    ///
    /// **Demotion only, and only where the dial was measured.** The probe asks
    /// a toy question once; a real task is harder, and a model that passed the
    /// probe and then spent its whole format budget producing nothing has
    /// answered the question again, with better evidence. Switching to the dial
    /// that never asks for JSON is the honest response — the alternative is a
    /// run that ends `NEEDS_REVIEW` having written nothing, which is what the
    /// live 2B did.
    ///
    /// Three conditions, all required:
    ///
    /// * the run's dial was **measured** (`LoopDeps::probe` is wired). A
    ///   caller that injected a profile, or a harness that took the size prior,
    ///   asked for that dial and does not get a different one;
    /// * the dial is not already `guided`;
    /// * nothing has worked yet — a run with execution evidence is making
    ///   progress, and restarting it in another mode would throw that away.
    fn demote_to_guided(&mut self, ctx: &mut AgentRunContext, profile: AgentProfile) -> bool {
        if profile.decomposed
            || self.deps.probe.is_none()
            || self.deps.agent_profile.is_some()
            || Self::has_execution_evidence(ctx)
        {
            return false;
        }
        ctx.trace.decision(
            "execute",
            "profile_demoted",
            &[("from", json!(profile.name)), ("to", json!("guided"))],
        );
        self.emit_step(
            "execute",
            "profile",
            &[("profile", json!({"profile": "guided", "source": "demoted"}))],
        );
        self.set_resolved_profile(crate::kernel::profile::GUIDED);
        true
    }

    /// One chosen action, from `final` through the gates to dispatch.
    ///
    /// Extracted in v12.0.0 so the guided path
    /// ([`super::guided`]) runs the **same** tail rather than a second
    /// implementation of it: the two modes differ in how an action is obtained
    /// and in nothing after that. The order below is the contract (see this
    /// module's header) and is pinned by the frozen trajectories.
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

        // **A call missing a required argument is not a call — on every dial**
        // (v12.0.0). This lived inside [`super::guided`], which is how the
        // `compact` path came to send `mcp.grep` with the plan's `search_term`
        // key and collect the seam's raw `'pattern'` KeyError repr thirty-six
        // times across one live cell's three attempts. It is the catalog's own
        // `required` list either way, so the guard belongs where every dial
        // already meets: the one tail all three of them dispatch through.
        //
        // Two things happen before anything is refused, and their order is the
        // fix. Arguments the **tool itself documents a default for** are filled
        // ([`crate::tools::catalog::fill_documented_defaults`]) — refusing
        // `list_dir` for want of a `path` the tool defaults to `"."` cost a
        // live 2B nine dispatches and three halts over a listing it would have
        // received. Only a genuinely default-less argument is then missing, and
        // that step is recorded with the harness's own honest sentence rather
        // than a Python exception repr the model cannot act on.
        //
        // `final` is the single exception: an absent `message` already has a
        // defined meaning below ("작업을 완료했습니다."), and a run that cannot
        // phrase its own conclusion must still be allowed to reach one.
        // Read once per step and shared with the repair tail below: the host's
        // `entries()` is a scan of the skills directory, and a step must not pay
        // for two of them.
        let catalog = if original == "final" {
            Vec::new()
        } else {
            self.run_catalog(&req.message)
        };
        if original != "final" {
            if let Some(entry) = crate::tools::catalog::entry_for(&catalog, &original) {
                let filled = crate::tools::catalog::fill_documented_defaults(entry, &mut args);
                if !filled.is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "documented_default_filled",
                        &[("action", json!(original)), ("args", json!(filled))],
                    );
                }
                let stated = self.fill_stated_request_args(entry, &mut args, req, &catalog);
                if !stated.is_empty() {
                    ctx.trace.decision(
                        "execute",
                        "request_arg_filled",
                        &[("action", json!(original)), ("args", json!(stated))],
                    );
                }
                if let Some(missing) = crate::tools::catalog::missing_required(entry, &args) {
                    return self.refuse_incomplete_call(ctx, &original, &args, &missing);
                }
            }
        }

        // A prefixed name the run already governs takes the kernel's (stricter)
        // gate chain under its bare name. Only a name the run has no policy
        // for reaches the host catalog, where MCP's own check_governance runs.
        let name = match crate::tools::catalog::resolve(&original, |bare| {
            self.deps.policies.tools.contains_key(bare)
                || self.deps.tool_names.iter().any(|known| known == bare)
        }) {
            crate::tools::catalog::Resolved::Native(bare) => bare.to_string(),
            crate::tools::catalog::Resolved::External(_) => {
                let flow = self
                    .dispatch_external(ctx, req, &original, thoughts, &args)
                    .await;
                // The steer is a sentence about the *request*, so it belongs to
                // every dispatch rather than to the native one: an `mcp.` read
                // of the file this request asks us to create is the same wrong
                // move under a different name. The default-repair below is not
                // duplicated here — a host catalog row carries no documented
                // default to repair with, and writing the re-dispatch for a
                // case that cannot arise would be code nothing has ever run.
                self.steer_to_write_target(ctx, req, &original);
                return flow;
            }
        };

        if self.deps.scoped_knowledge_tools.contains(&name) {
            // Scope is server-owned, never model-owned. Overwrite any
            // claimed values before policy evaluation, audit and dispatch.
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
            // **Proven created artifacts are restored** (v12.0.0, F5).
            // A model that created a file on disk and then said "I did nothing."
            // or named no file has the transcript-proven fact restored first,
            // dropping the disproven bare negation.
            let settled =
                complete_created_files(&said, &ctx.transcript, &self.deps.file_create_actions);
            // **A count question is answered with the count** (v12.0.0). The
            // guided dial already substituted a counted fact when the model's
            // final line carried no digit; the JSON dials did not, so a 2B that
            // ran `list_dir` and then said "폴더를 확인했습니다" reported no
            // number at all and the deliverable of the request was missing from
            // the answer. The number is one a tool returned, appended rather
            // than substituted so nothing the model said is thrown away, and it
            // is only ever added when the request asked *how many* and the
            // answer named no figure at all.
            ctx.final_message = complete_a_count(&settled, &req.message, &ctx.transcript);
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

        // **The example, copied.** A weak model handed a worked example and a
        // task it cannot do answers with the example: a live 2B replied with
        // the prompt's own `content` verbatim, and the run wrote it, passed
        // verification and reported success over a file the user never asked
        // for. Matching our own constant is exact — nobody writes these bytes
        // by accident — so this can only ever refuse our own text coming back.
        // The step is recorded and the correction names what happened; the run
        // then retries with its ordinary budget.
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
            // Python calls the port directly and would raise on failure;
            // the seam reports refusals, and a refusal is recorded rather
            // than crashing the run.
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

    /// The governance chain and the dispatch, for one native call.
    ///
    /// Extracted in v12.0.0 so a *repaired* call runs the identical chain the
    /// first attempt ran — the policy lookup, the governor, the gates, the
    /// pre-write snapshot and `sanitize_write_content` — rather than a shorter
    /// one written beside it. Returns whether the tool was actually reached: a
    /// staged proposal and a gate refusal both answer `false`, and neither is
    /// something a repair may retry, because nothing about them is an argument.
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
}

#[cfg(test)]
mod tests;
#[cfg(test)]
mod tests_dispatch;
