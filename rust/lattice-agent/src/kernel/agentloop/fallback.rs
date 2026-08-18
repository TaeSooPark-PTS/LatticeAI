//! When the tool-call protocol runs out: rescue, corrections, then the direct
//! path.
//!
//! Everything `latticeai.core.agent.execution` does with a model that cannot
//! hold the JSON contract, in the order the loop tries it:
//!
//! 1. [`Runtime::fence_rescue`] (v11.9.0) — the reply is not an action but it
//!    contains the *file* the plan is waiting for, so it becomes the write;
//! 2. [`Runtime::note_parse_failure`] spends the profile's slip budget,
//!    escalates the hint before it runs out, and lets a byte-identical repeat
//!    buy exactly one extra attempt with a prompt that names the repetition;
//! 3. [`Runtime::direct_fallback`] is the weak profiles' escape hatch and the
//!    one place in the loop that stops asking for JSON entirely. It has two
//!    halves, in this order: [`Runtime::direct_plan_path`] runs the plan's own
//!    **non-write** steps, then [`Runtime::direct_file_path`] writes the plan's
//!    files with what those steps found in front of it.
//!
//! None of them ever fabricates evidence. A fence with no plan step to write it
//! to, a hint that does not land, a plan with no paths, a staged proposal or a
//! tool error all leave the run to end exactly as it would have — and every
//! write here goes through the same policy, gates and sanitize pass as one the
//! model asked for correctly.

use serde_json::{json, Map, Value};

use super::execution::{Chosen, StepFlow};
use super::gates::Call;
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::parse::inference::infer_file_target;
use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::surface::worker::{Completion, WorkerError};
use crate::tools::catalog::EntryKind;

/// The fixed part of the correction hint a format slip earns.
pub(super) const FORMAT_HINT: &str = "Your last reply was not a single JSON action \
object. Reply with EXACTLY one JSON object like {\"thoughts\": \"...\", \"action\": \
\"tool_name\", \"args\": {...}} and nothing else.";

/// The extra sentence a **byte-identical** rejected reply earns.
///
/// From the deleted `file_generation.orchestration`: a small model handed the
/// same corrective feedback replays the same reply verbatim, and the only
/// signal left that has any chance of moving it is saying so.
pub(super) const REPEAT_HINT: &str = " You already sent exactly this reply and it was \
rejected for the same reason — do not repeat it. Send a different reply, starting at \
the opening brace.";

/// Plan steps the direct path may run for a model that never chose one.
///
/// A ceiling, not a target: the escape hatch exists because the model stopped
/// steering, and a harness that then ran twenty steps on its own would be
/// driving rather than rescuing. Four covers every plan shape the live matrix
/// produced (a read then a write; a single search; a skill then a write).
const DIRECT_PLAN_LIMIT: usize = 4;

/// Characters of one earlier tool result the direct write turn is shown.
const FINDING_CHARS: usize = 1_200;

/// What the transcript records a harness-run plan step as.
const DIRECT_PLAN_THOUGHTS: &str = "direct plan path";

/// One executor parse slip, with the run-level facts the response depends on.
#[derive(Debug, Clone, Copy)]
pub(super) struct ParseSlip {
    /// Slips so far, this one included.
    pub failures: u32,
    /// The budget in force, which a repeat may already have widened by one.
    pub budget: u32,
    /// Whether this reply is byte-identical to one already rejected.
    pub repeated: bool,
}

impl Runtime {
    /// Record one executor parse slip; `true` when the run should stop retrying.
    pub(super) fn note_parse_failure(
        &mut self,
        ctx: &mut AgentRunContext,
        raw: &str,
        error: &str,
        slip: ParseSlip,
        profile: AgentProfile,
    ) -> bool {
        let mut step = json!({
            "state": AgentState::Executing.as_str(),
            "action": "parse_error",
            "raw": char_slice(raw, 400),
            "error": error,
        });
        if slip.repeated {
            // Visible in the trace, so two rounds of the same reply do not read
            // as two genuine attempts.
            step["repeated"] = json!(true);
        }
        ctx.transcript.push(step);
        if slip.failures >= slip.budget {
            ctx.trace.parse_error("execute", error, false);
            self.emit_step("execute", "parse_error", &[("recovered", json!(false))]);
            return true;
        }
        ctx.trace.parse_error("execute", error, true);
        self.emit_step("execute", "parse_error", &[("recovered", json!(true))]);
        let mut hint = FORMAT_HINT.to_string();
        if slip.failures >= profile.escalate_after {
            // Escalate: name the valid tools so the model stops inventing
            // action names or prose. The compact profile escalates earlier.
            //
            // Never an empty list: a run whose caller sent no policy table used
            // to be told `Valid action values are: , final.`, which names
            // nothing and reads as a bug the model then imitates.
            // The **same** list the prompt named, so a model that reached for a
            // skill or an `mcp.` row is not now told it does not exist
            // (v12.0.0). Unranked here: a correction is about what is valid,
            // and the prompt beside it is what says which one is likely.
            hint = format!(
                "{hint} Valid action values are: {}. \
Use {{\"action\": \"final\", \"message\": \"...\"}} to finish.",
                crate::prompts::action_list(&self.prompt_action_names("")).join(", ")
            );
        }
        if slip.repeated {
            hint.push_str(REPEAT_HINT);
        }
        if !ctx.corrections.iter().any(|known| py_str(known) == hint) {
            ctx.corrections.push(json!(hint));
            ctx.trace.correction("execute", &hint);
        }
        false
    }

    /// The plan's file steps, in order, minus what this run already wrote.
    pub(super) fn pending_plan_paths(&self, ctx: &AgentRunContext) -> Vec<String> {
        let written = crate::kernel::transcript::files_written(
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let mut pending: Vec<String> = Vec::new();
        for step in ctx.steps() {
            let action = step.get("action").and_then(Value::as_str).unwrap_or("");
            if !self.deps.file_create_actions.contains(action) {
                continue;
            }
            let path = step
                .get("args")
                .and_then(|args| args.get("path"))
                .filter(|value| is_truthy(value))
                .map(py_str)
                .unwrap_or_default();
            let path = path.trim().to_string();
            if path.is_empty() || pending.contains(&path) {
                continue;
            }
            let name = crate::kernel::transcript::path_name(&path);
            if written
                .iter()
                .any(|done| done == &path || crate::kernel::transcript::path_name(done) == name)
            {
                continue;
            }
            pending.push(path);
        }
        pending
    }

    /// Turn a reply that fenced the *file* instead of the tool call into one.
    ///
    /// The ~2B failure this catches is not a format slip — it is the model
    /// doing the work and framing it as chat: `Here is your page:` followed by
    /// a ```html block holding the whole document. The parse chain cannot help
    /// (there is no JSON to find), and the ordinary answer is a `parse_error`
    /// step that throws a finished file away.
    ///
    /// Four conditions, all required, so this can never invent a write:
    /// the reply has a fenced block, the block is not itself JSON (which the
    /// parse chain owns), the **plan** names a file that has not been written
    /// yet, and — when the fence carries a language tag — that file is of the
    /// kind the fence holds. The last one is what stops the useful case from
    /// becoming a destructive one: a plan for `index.html` and `style.css`
    /// answered with a ```css block must not write the stylesheet into the
    /// page, where `sanitize_write_content` would then "repair" the CSS into an
    /// HTML scaffold and lose it. An untagged fence has nothing to disagree
    /// with, so it takes the first pending path.
    ///
    /// The synthesized call goes through [`Runtime::dispatch_step`] like any
    /// other — same policy, same gates, same sanitize pass — so nothing here is
    /// a shortcut past governance, only past the JSON.
    pub(super) fn fence_rescue(
        &self,
        ctx: &AgentRunContext,
        raw: &str,
    ) -> Option<Map<String, Value>> {
        let (language, body) = fenced_code(raw)?;
        if body.trim_start().starts_with('{') {
            // JSON in a fence is the parse chain's rung, not this one's.
            return None;
        }
        let pending = self.pending_plan_paths(ctx);
        let path = match fence_extensions(&language) {
            Some(extensions) => pending.into_iter().find(|path| {
                extensions.contains(&crate::content::sanitize::ext_of(path).as_str())
            })?,
            None => pending.into_iter().next()?,
        };
        let mut args = Map::new();
        args.insert("path".into(), json!(path));
        args.insert("content".into(), json!(body));
        let mut action = Map::new();
        action.insert(
            "thoughts".into(),
            json!("recovered from a fenced code block"),
        );
        action.insert("action".into(), json!("write_file"));
        action.insert("args".into(), Value::Object(args));
        Some(action)
    }

    /// The whole escape hatch, in the order the two halves depend on.
    ///
    /// Every caller ran only the second half before v12.0.0, and the live
    /// matrix showed what that costs. Three of four failing runs on the weakest
    /// model had a plan whose first step was a *read* — `read_file README.md`,
    /// `list_dir .`, `mcp.grep LatticeAI` — and the file half only knows how to
    /// write, so two of them wrote nothing at all and the third wrote a summary
    /// of a file nobody had opened ("README content not provided"). The plan is
    /// the run's own statement of what to do; running the reads in it before
    /// writing anything is not a new capability, it is the order the plan
    /// already declared.
    ///
    /// Returns `true` when the run did something — a dispatched step or a
    /// written file — which is the caller's signal to go and be verified rather
    /// than to keep asking a model that has stopped answering.
    pub(super) async fn direct_fallback(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        profile: AgentProfile,
    ) -> Result<bool, WorkerError> {
        if !profile.direct_path_fallback {
            return Ok(false);
        }
        let ran = self.direct_plan_path(ctx, req).await;
        let wrote = self.direct_file_path(ctx, req, model_id, profile).await?;
        Ok(ran || wrote)
    }

    /// Run the plan's own non-write steps, for a model that never chose one.
    ///
    /// **Nothing here is invented.** Every name and every argument comes from
    /// the plan this run already produced and accepted, or — for the one skill
    /// case — from the user's own words; a name the run's catalog does not
    /// carry is skipped, a step that already succeeded is skipped, and the
    /// dispatch goes through [`Runtime::perform_action`], which is the same
    /// tail the JSON and guided dials run: `resolve`, the scoped-arg forcing,
    /// the loop guard, the governor and the full gate chain. The harness
    /// assembling the call is exactly what [`super::guided`] already does; the
    /// only difference is that here the *choice* also comes from the plan,
    /// because the model declined to make one.
    ///
    /// The skill row is first and deliberate. A request that names a skill
    /// (`code_review 스킬을 참고해서 …`) is asking for guidance that shapes
    /// everything after it, and a skill is guidance rather than an executable —
    /// choosing one writes nothing, runs nothing and returns its `SKILL.md`. So
    /// a run whose model could not answer the menu still gets the instructions
    /// the user asked for in front of the write that follows, which is what the
    /// scenario was testing and what the file half alone silently skipped.
    /// One skill, never a sweep of the catalog: the request named it.
    pub(super) async fn direct_plan_path(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> bool {
        let calls = self.direct_plan_calls(ctx, req);
        if calls.is_empty() {
            return false;
        }
        // Only what actually returned something is claimed: a refusal, a staged
        // proposal or a tool error leaves the run exactly where it was, which
        // is the rule every rung in this file obeys.
        let mut ran: Vec<String> = Vec::new();
        for (name, args) in calls {
            let before = ctx.transcript.len();
            let flow = self
                .perform_action(
                    ctx,
                    req,
                    Chosen {
                        name: &name,
                        thoughts: DIRECT_PLAN_THOUGHTS,
                        args,
                        final_message: None,
                    },
                )
                .await;
            if ctx.transcript.len() > before {
                let step = ctx.transcript.last_mut().expect("just appended");
                step["direct_plan"] = json!(true);
                let applied = step
                    .get("result")
                    .filter(|result| result.is_object())
                    .is_some_and(|result| !result.get("proposed").is_some_and(is_truthy));
                if applied {
                    ran.push(name.clone());
                }
            }
            if matches!(flow, StepFlow::Break | StepFlow::Finished) {
                break;
            }
        }
        if !ran.is_empty() {
            ctx.trace.decision(
                "execute",
                "direct_plan_path",
                &[("steps", json!(ran.len()))],
            );
            self.emit_step("execute", "direct_plan", &[("steps", json!(ran.len()))]);
            if ctx.final_message.trim().is_empty() {
                // Said only when the run would otherwise say nothing, and said
                // plainly: the harness ran the plan because the model stopped
                // steering. The count a tool returned is restored on top of
                // this by `complete_a_count` in VERIFY, so a "how many"
                // question still gets its number.
                ctx.final_message = format!(
                    "도구 호출 형식을 계속 벗어나서, 계획에 있던 단계를 직접 실행했습니다: {}. \
결과를 확인해 주세요.",
                    ran.join(", ")
                );
            }
        }
        !ran.is_empty()
    }

    /// What [`Runtime::direct_plan_path`] will run, in order.
    ///
    /// **A rescue that replays the step that just failed is not a rescue**
    /// (v12.0.0). `LOOP_DETECTED` halts a run and then calls straight into here,
    /// and until now this handed back the plan's step with the plan's arguments
    /// — the same `action|args` the loop had just halted for. A live
    /// gemma-4-e2b spent twelve dispatches per attempt, thirty-six across the
    /// cell, on `mcp.grep` with the planner's `search_term` key and no
    /// `pattern`: fail, halt, replay, fail, halt, replay. The halt was undone by
    /// its own escape hatch.
    ///
    /// So two rules, in this order, and the order is the point:
    ///
    /// 1. **repair the arguments first.** The catalog knows what this call takes
    ///    and what the tool documents a default for, and the plan's own map
    ///    usually carries the value under another key — that is the keyword rule
    ///    ([`crate::tools::catalog::adopt_named_args`]), the same one the guided
    ///    dial reads a plan with. A repaired call is a *different* call and is
    ///    allowed to run;
    /// 2. **never re-send a signature that already failed.** What is left after
    ///    the repair — a call the harness could not complete, or one whose exact
    ///    `action|args` is already on the transcript with an error against it —
    ///    is dropped. Respecting the halt is the honest outcome: the run ends
    ///    with what it has and VERIFY says so.
    fn direct_plan_calls(
        &self,
        ctx: &AgentRunContext,
        req: &RunRequest,
    ) -> Vec<(String, Map<String, Value>)> {
        let catalog = self.run_catalog(&req.message);
        let mut calls: Vec<(String, Map<String, Value>)> = Vec::new();
        if let Some(skill) = catalog.iter().find(|entry| {
            entry.kind == EntryKind::Skill
                && super::guided::request_names(&req.message, entry)
                && !super::guided::action_succeeded(ctx, &entry.name)
        }) {
            calls.push((skill.name.clone(), Map::new()));
        }
        // Every `action|args` this run has already been refused, exactly as the
        // executor's own repeat guard spells one.
        let failed: std::collections::BTreeSet<String> = ctx
            .transcript
            .iter()
            .filter(|step| step.get("error").is_some())
            .filter_map(|step| {
                let action = step.get("action").and_then(Value::as_str)?;
                let args = step.get("args").cloned().unwrap_or_else(|| json!({}));
                Some(format!("{action}|{args}"))
            })
            .collect();
        for step in ctx.steps() {
            let action = step
                .get("action")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_string();
            if action.is_empty() || action == "final" {
                continue;
            }
            // The write half of the plan belongs to `direct_file_path`, which
            // knows how to obtain content; this half only re-runs what the plan
            // said to *look at*.
            if self.deps.file_create_actions.contains(&action) {
                continue;
            }
            // A planner may name anything; only a row this run actually offers
            // may be dispatched without a model having chosen it.
            let Some(entry) = crate::tools::catalog::entry_for(&catalog, &action) else {
                continue;
            };
            if super::guided::action_succeeded(ctx, &action) {
                continue;
            }
            if calls.iter().any(|(known, _)| known == &action) {
                continue;
            }
            let planned = step
                .get("args")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut args = planned.clone();
            crate::tools::catalog::adopt_named_args(entry, &planned, &mut args);
            crate::tools::catalog::fill_documented_defaults(entry, &mut args);
            if crate::tools::catalog::missing_required(entry, &args).is_some() {
                continue;
            }
            if failed.contains(&format!("{action}|{}", Value::Object(args.clone()))) {
                continue;
            }
            calls.push((action, args));
        }
        calls.truncate(DIRECT_PLAN_LIMIT);
        calls
    }

    /// What this run has already found, for a turn that has to write about it.
    ///
    /// Only successful tool results, only what the tool itself returned, each
    /// one capped. The direct write turn used to be handed the goal and nothing
    /// else, so a plan of "read README.md, then summarise it into
    /// notes/summary.md" reached the write with no README in sight and the live
    /// model wrote its own reasoning trace ending *"README content not
    /// provided"* — a fabricated 2.8KB file that looked like an artefact and
    /// was not one. The run's own results are the only honest way to close that:
    /// nothing is inferred, nothing is summarised, and a run with no results
    /// yet renders the empty string, which is exactly the prompt this turn
    /// always had.
    fn run_findings(&self, ctx: &AgentRunContext) -> String {
        let rows: Vec<String> = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .filter_map(|step| {
                let action = step.get("action").and_then(Value::as_str)?;
                let result = step.get("result").filter(|value| value.is_object())?;
                let rendered = serde_json::to_string(result).unwrap_or_default();
                // `content` is what `read_file` calls its payload and `text` is
                // what a skill calls its instructions; anything else is a small
                // structured result and renders as itself.
                let body = match result
                    .get("content")
                    .or_else(|| result.get("text"))
                    .and_then(Value::as_str)
                {
                    Some(text) if !text.trim().is_empty() => char_slice(text.trim(), FINDING_CHARS),
                    _ => char_slice(&rendered, 400),
                };
                let path = step
                    .get("args")
                    .and_then(|args| args.get("path"))
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                Some(if path.is_empty() {
                    format!("[{action}]\n{body}")
                } else {
                    format!("[{action} {path}]\n{body}")
                })
            })
            .collect();
        if rows.is_empty() {
            String::new()
        } else {
            format!(
                "\n\nWhat this run already found (use it; do not invent \
anything it does not say):\n{}",
                rows.join("\n\n")
            )
        }
    }

    /// Write the plan's file steps without asking the model for JSON.
    ///
    /// Deviation, stated rather than hidden: Python routed the content through
    /// `generate_file_content`, whose generation *loop* lived with the document
    /// generators. v11.7.0 ported that pipeline's pure stages and left the loop
    /// behind; v11.9.0 brings back the two parts of it that were load-bearing
    /// for weak models, and only for the profile that needs them
    /// ([`AgentProfile::regeneration_retries`], zero on `standard`):
    ///
    /// * **candidate scoring.** Content that would have to be repaired is asked
    ///   for again, and the *better* of the two is written — better by
    ///   [`crate::content::sanitize::salvage_score`], which ranks a short real document
    ///   above a long apology.
    /// * **repeated-reply detection.** A byte-identical second answer is told
    ///   so, exactly as [`Runtime::note_parse_failure`] tells the executor.
    ///
    /// The write itself is unchanged: [`Runtime::dispatch_step`] runs the same
    /// extract → validate → repair pass ([`crate::content::sanitize::sanitize_write_content`])
    /// and `generation.repaired` stays this loop's own verdict.
    pub(super) async fn direct_file_path(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
        profile: AgentProfile,
    ) -> Result<bool, WorkerError> {
        let mut planned = self.pending_plan_paths(ctx);
        if planned.is_empty() {
            if let Some(inferred) = infer_file_target(&req.message) {
                planned.push(inferred);
            }
        }
        if planned.is_empty() {
            return Ok(false);
        }
        let goal = match ctx.plan.get("goal") {
            Some(value) if is_truthy(value) => py_str(value),
            _ => req.message.clone(),
        };

        let mut wrote = false;
        for path in planned.iter().take(6) {
            let content = self
                .generate_direct_content(ctx, model_id, path, &goal, profile)
                .await?;
            // **A write with no body is not a write** (v12.0.0), here for the
            // same reason [`super::guided`] enforces it on the menu path — and
            // here it is the only thing standing between a silent model and a
            // 0-byte file the run then reports as a delivered artifact.
            // `sanitize_write_content` cannot catch it: an empty reply is
            // "untouched, nothing to repair", which reads as valid content to
            // the caller above. Skipped rather than written, so the plan's next
            // path still gets its turn and the run ends as it would have.
            if content.trim().is_empty() {
                ctx.trace.decision(
                    "execute",
                    "direct_path_empty_content",
                    &[("path", json!(path))],
                );
                continue;
            }
            ctx.trace
                .repair("execute", &["direct_path_fallback".to_string()]);
            let mut args = Map::new();
            args.insert("path".into(), json!(path));
            args.insert("content".into(), json!(content));
            let policy = self.deps.policies.policy_for("write_file", &args);
            let before = ctx.transcript.len();
            self.dispatch_step(
                ctx,
                req,
                Call {
                    name: "write_file",
                    thoughts: "direct path fallback",
                    args: &args,
                    policy: &policy,
                },
            )
            .await;
            if ctx.transcript.len() <= before {
                continue;
            }
            let last = ctx.transcript.last_mut().expect("just appended");
            let applied = last
                .get("result")
                .filter(|result| result.is_object())
                .is_some_and(|result| !result.get("proposed").is_some_and(is_truthy));
            if applied {
                wrote = true;
                last["direct_path"] = json!(true);
                let repaired = last
                    .get("content_sanitize")
                    .and_then(|meta| meta.get("repaired"))
                    .is_some_and(is_truthy);
                last["generation"] = json!({"repaired": repaired});
            }
        }
        if wrote {
            ctx.trace.decision(
                "execute",
                "direct_path_fallback",
                &[("files", json!(planned.len()))],
            );
            self.emit_step("execute", "direct_path", &[("files", json!(planned.len()))]);
            ctx.final_message = "도구 호출 형식을 계속 벗어나서, 계획에 있던 파일을 직접 \
생성했습니다. 내용을 확인해 주세요."
                .into();
        }
        Ok(wrote)
    }

    /// Ask the worker for one file's contents, spending the profile's
    /// regeneration budget on content that would otherwise be repaired.
    ///
    /// The first answer that **validates** is returned immediately — a run that
    /// gets it right the first time costs exactly one call, which is what
    /// `standard` (`regeneration_retries: 0`) always does. Otherwise the retry
    /// carries the reason the last answer was rejected, and the best candidate
    /// by [`crate::content::sanitize::salvage_score`] is what gets written.
    ///
    /// One call **beyond** the budget is spent, at most once per file, when the
    /// model has returned a byte-identical rejected reply — Python's rule, kept
    /// because the reason for it is unchanged: the corrective feedback did not
    /// move the model, so a third identical round trip is worth less than one
    /// prompt that says "you already sent this". A model that never repeats
    /// itself is never charged for it, and `standard` cannot reach the branch
    /// at all: the first reply has nothing to be identical to.
    async fn generate_direct_content(
        &mut self,
        ctx: &mut AgentRunContext,
        model_id: Option<&str>,
        path: &str,
        goal: &str,
        profile: AgentProfile,
    ) -> Result<String, WorkerError> {
        let mut feedback = String::new();
        let mut best: Option<(u8, usize, String)> = None;
        let mut seen: Vec<String> = Vec::new();
        let mut budget = profile.regeneration_retries;
        let mut escalations_left = 1u32;
        let mut attempt = 0u32;
        // What the run has read, searched or been given as skill guidance, so a
        // write that depends on an earlier step is written from that step's own
        // output rather than from the model's memory of it. Empty for every run
        // that had nothing yet, which is the prompt this turn always sent.
        let findings = self.run_findings(ctx);
        loop {
            let context = format!(
                "Write the complete contents of {path} for this request, and nothing else \
(no prose, no code fences).\n\nRequest: {goal}{findings}{feedback}"
            );
            let content = self
                .deps
                .worker
                .llm(Completion {
                    model_id,
                    message: "Write the file content.",
                    context: &context,
                    max_tokens: self.deps.phase_budgets.execute_tokens,
                    temperature: 0.2,
                    stop: &[],
                    prefix: "",
                })
                .await?;
            ctx.trace.llm_call("execute", model_id);
            // The predicate is `sanitize_write_content`'s own, run here rather
            // than re-derived: content it can *extract* into something valid
            // (a fenced document, a chat-wrapped one) is not content that has
            // to be repaired, and spending a regeneration on it would be a
            // second opinion about a question already answered. Only the
            // verdict is used — [`Runtime::dispatch_step`] does the real pass,
            // and its meta is what the transcript reports.
            let (_, meta) = crate::content::sanitize::sanitize_write_content(path, &content, goal);
            if !meta.repaired {
                return Ok(content);
            }
            let reason = meta.reason;
            let (tier, length) = crate::content::sanitize::salvage_score(&content, path);
            if best
                .as_ref()
                .is_none_or(|(best_tier, best_len, _)| (tier, length) > (*best_tier, *best_len))
            {
                best = Some((tier, length, content.clone()));
            }
            let fingerprint = content.trim().to_string();
            let repeated = !fingerprint.is_empty() && seen.contains(&fingerprint);
            seen.push(fingerprint);
            if repeated && escalations_left > 0 && attempt >= budget {
                escalations_left -= 1;
                budget += 1;
            }
            if attempt >= budget {
                break;
            }
            attempt += 1;
            ctx.trace.repair(
                "execute",
                &[if repeated {
                    "direct_path_repeated".to_string()
                } else {
                    "direct_path_regenerate".to_string()
                }],
            );
            feedback = if repeated {
                format!("\n\nYour last reply was rejected: {reason}.{REPEAT_HINT}")
            } else {
                format!(
                    "\n\nYour last reply was rejected: {reason}. Output the file itself, \
starting at its first character."
                )
            };
        }
        Ok(best.map(|(_, _, content)| content).unwrap_or_default())
    }
}

/// The language tag and body of the first fenced code block, closed or not.
///
/// A reply that ran out of tokens mid-file has an opening fence and no closing
/// one, and that half-file is still the most useful thing in the reply — the
/// write-side validator will say whether it survived, and repair will finish it
/// if it did not.
fn fenced_code(raw: &str) -> Option<(String, String)> {
    let start = raw.find("```")?;
    let after = &raw[start + 3..];
    // The language tag is the rest of the opening line.
    let body_start = after.find('\n')? + 1;
    let language = after[..body_start].trim().to_lowercase();
    let body = &after[body_start..];
    let body = match body.find("```") {
        Some(end) => &body[..end],
        None => body,
    };
    let body = body.trim_matches(['\r', '\n']);
    if body.trim().is_empty() {
        None
    } else {
        Some((language, body.to_string()))
    }
}

/// The file extensions a fence language tag claims, or `None` for a tag that
/// says nothing about the file type (including no tag at all).
///
/// Deliberately short: only the tags whose meaning is unambiguous. An unknown
/// tag is treated as no tag rather than as a mismatch, so a model writing
/// ```jinja into an `.html` plan step is not refused over vocabulary.
fn fence_extensions(language: &str) -> Option<&'static [&'static str]> {
    Some(match language {
        "html" | "htm" => &[".html", ".htm"],
        "css" => &[".css"],
        "js" | "javascript" | "jsx" => &[".js", ".jsx", ".mjs"],
        "ts" | "typescript" | "tsx" => &[".ts", ".tsx"],
        "json" => &[".json"],
        "md" | "markdown" => &[".md", ".markdown"],
        "py" | "python" => &[".py"],
        "sh" | "bash" | "shell" => &[".sh"],
        "sql" => &[".sql"],
        "yaml" | "yml" => &[".yaml", ".yml"],
        _ => return None,
    })
}

#[cfg(test)]
mod tests;
