//! **GUIDED** — a step a 0.5B model cannot get structurally wrong (v12.0.0).
//!
//! The standard and compact executors ask one question: *emit a JSON object
//! naming a tool and its arguments.* That question has a large answer space and
//! exactly one acceptable region, and a model small enough to run on a laptop
//! spends its whole correction budget failing to land in it — not because it
//! does not know what to do, but because it cannot hold `{`, `"`, `\n` and `}`
//! in the right order for four hundred tokens.
//!
//! This module asks smaller questions instead:
//!
//! 1. **"Which action? Answer with one number."** The menu is the run's real
//!    catalog ([`crate::tools::catalog`]), numbered. The answer space is
//!    `1..=n`. Parsing it is [`parse_choice`], which cannot fail on a reply
//!    that contains a digit anywhere.
//! 2. **"What is `path`?"** One line. No quoting, no braces. When the plan
//!    already names a file, that is offered as the default and an empty reply
//!    takes it.
//! 3. **"Write the content."** Free-form prose — the one thing weak models are
//!    *good* at — with no JSON escaping anywhere near it. A leading code fence
//!    is stripped by the same extractor the direct chat path uses.
//!
//! Then **the harness builds the action struct** and hands it to
//! [`Runtime::perform_action`], the same tail every other mode runs: scoped-arg
//! forcing, the loop guard, the governor, the gate chain, the pre-write
//! snapshot, `sanitize_write_content`, dispatch. Nothing here bypasses
//! anything; the only thing that changed is who assembled the JSON, and the
//! answer is now "us".
//!
//! ## The honest limits
//!
//! * **A step costs more calls.** One JSON turn becomes two to four short ones.
//!   They are short by construction (a menu turn is capped at
//!   [`MENU_TOKENS`] tokens) and the transcript window is two steps, so the
//!   *tokens* are comparable; the round trips are not, and a model fast enough
//!   to be worth guiding is fast enough to pay them.
//! * **Nonsense still ends the step.** [`AgentProfile::micro_turn_cap`] bounds
//!   the retries; past it, the slip is recorded through the same
//!   [`Runtime::note_parse_failure`] the JSON loop uses and the direct-path
//!   fallback applies. Guided is a better question, not a guarantee that an
//!   answer exists.
//! * **A skill is guidance, not an executable.** Choosing one returns its
//!   `SKILL.md`; the run still has to choose a real tool afterwards, and the
//!   menu says so.

use serde_json::{json, Value};

use super::execution::{Chosen, StepFlow};
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::surface::worker::WorkerError;

mod answers;
mod args;
mod ranking;
mod verdict;
use args::*;
pub(super) use ranking::*;

pub use answers::{
    looks_like_a_path, named_choice, parse_choice, parse_line, parse_verdict_word,
    strip_control_tokens, strip_echoed_lines,
};

/// Tokens a menu turn may spend. The answer is one number.
pub const MENU_TOKENS: u32 = 8;

/// Tokens a **re-asked** menu turn may spend (v12.0.0).
///
/// The first turn is prefilled with [`crate::prompts::guided::MENU_ANSWER_PREFIX`]
/// and eight tokens is a whole answer. A turn that came back with no number at
/// all is a different situation: something in this model's register needs room
/// before it will commit, and re-asking the identical question at temperature
/// zero is not a second attempt — it is the first one, replayed. The live
/// evidence is exactly that shape: three menu turns, three identical
/// `parse_error` steps, never one and never two. So the retry buys room and
/// drops the newline stop, and the answer prefix keeps the number first.
pub const MENU_RETRY_TOKENS: u32 = 64;

/// Tokens a one-line argument turn may spend.
///
/// Generous for a line, and deliberately so: the first live 0.5B run was
/// offered an absolute path as the default, copied it, and ran out of budget
/// four characters from the end — writing a *directory* where a file was
/// wanted. A truncated path is silent corruption, and the cheapest guard
/// against it is a ceiling no realistic path reaches.
pub const LINE_TOKENS: u32 = 192;

/// Rows a menu may carry. A numbered list a small model can hold in one glance;
/// past this the catalog is truncated (keeping the plan's own tools and
/// `final`) rather than rendered in full, because a menu nobody can read is the
/// same failure the JSON object was.
pub const MENU_LIMIT: usize = 9;

/// Menu answers are one number, so the first newline ends the reply.
const LINE_STOP: [&str; 1] = ["\n"];

/// Every micro-turn runs at zero temperature. A guided turn has one right
/// answer and no room for style; entropy here only buys a wrong number.
const MICRO_TEMPERATURE: f64 = 0.0;

/// Identical failing dispatches a run tolerates before it stops.
///
/// The first live 0.5B run chose the same tool with the same arguments fifteen
/// times, each one erroring, because "answer 1" is what a confused small model
/// answers. The menu ranking is the fix for *why* it chose that row; this is
/// the floor under it — a step that has failed the same way twice is not one
/// more attempt away from working.
///
/// `pub(super)` since v12.0.0: the JSON dials enforce the same floor
/// ([`super::execution`]), because a run that has stopped making progress has
/// stopped making progress whichever dial it is on. A live 2B on `compact`
/// sent `run_command npm run build` thirteen times to a validator that had
/// already refused it twice — a *tool* error, so none of the format budgets
/// counted it and nothing else was watching.
pub(super) const REPEAT_FAILURE_LIMIT: u32 = 2;

/// Shortest leading fragment [`strip_echoed_lines`] will treat as an echo.
///
/// Twenty characters. Below it a "prefix of one of our sentences" is a phrase
/// a real document could legitimately open with; above it, it is our text.
pub(super) const ECHO_PREFIX_FLOOR: usize = 20;

/// Punctuation a truncated echo ends with where our sentence continued.
pub(super) const TRAILING_PUNCTUATION: [char; 8] = ['.', ',', ':', ';', '—', '-', '…', '。'];

impl Runtime {
    /// EXECUTE, decomposed: a menu turn and one turn per required argument.
    pub(super) async fn execute_guided(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        profile: AgentProfile,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.executing_model.clone().or(req.executing_model.clone());
        let executed = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .count() as u32;
        let budget = req.max_steps.saturating_sub(executed).max(1);
        // Menu turns that produced no choice at all, this run. Not a loop
        // counter: a step that *did* choose spends none of it, so the budget
        // measures confusion rather than progress.
        let mut misreads = 0u32;
        // The last dispatch's `action|args`, and how many times it has come
        // back unchanged after failing — see [`REPEAT_FAILURE_LIMIT`].
        let mut last_signature: Option<String> = None;
        let mut repeats = 0u32;

        #[allow(clippy::explicit_counter_loop)]
        for _step in 0..budget {
            let catalog = self.rank_catalog(ctx, &req.message);
            let Some(chosen) = self
                .choose_action(ctx, req, model_id.as_deref(), &catalog, profile)
                .await?
            else {
                misreads += 1;
                let slip = super::fallback::ParseSlip {
                    failures: misreads,
                    budget: profile.parse_failure_budget,
                    repeated: false,
                };
                if self.note_parse_failure(
                    ctx,
                    "(no menu choice)",
                    "the model did not answer the action menu with a number",
                    slip,
                    profile,
                ) {
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
            };

            let entry = catalog[chosen - 1].clone();
            ctx.trace.decision(
                "execute",
                "guided_choice",
                &[
                    ("action", json!(entry.name)),
                    ("kind", json!(entry.kind.label())),
                ],
            );
            self.emit_step(
                "execute",
                "guided_choice",
                &[
                    ("action", json!(entry.name)),
                    ("kind", json!(entry.kind.label())),
                ],
            );

            let args = self
                .collect_args(ctx, req, model_id.as_deref(), &entry, profile)
                .await?;
            let final_message = args.get("message").cloned();
            let thoughts = format!("guided: chose {}", entry.name);
            // The same call, the same arguments, the same error, again. Not a
            // parse problem and not something another turn fixes; the run has
            // stopped making progress and the transcript already says so.
            let signature = format!("{}|{}", entry.name, Value::Object(args.clone()));
            let failed_before = ctx
                .transcript
                .last()
                .is_some_and(|step| step.get("error").is_some());
            if failed_before && last_signature.as_deref() == Some(signature.as_str()) {
                repeats += 1;
                if repeats >= REPEAT_FAILURE_LIMIT {
                    ctx.transcript.push(json!({
                        "state": AgentState::Executing.as_str(),
                        "action": entry.name,
                        "error": "LOOP_DETECTED: identical action+args failed repeatedly — halted.",
                    }));
                    ctx.trace
                        .decision("execute", "loop_detected", &[("tool", json!(entry.name))]);
                    self.emit_step(
                        "execute",
                        "blocked",
                        &[
                            ("action", json!(entry.name)),
                            ("reason", json!("loop_detected")),
                        ],
                    );
                    // Same hatch a spent menu budget takes (v12.0.0): a run
                    // that has stopped making progress has stopped, and the
                    // plan may still name a step it never ran or a file it
                    // never wrote. Nothing is written when it does not.
                    if self
                        .direct_fallback(ctx, req, model_id.as_deref(), profile)
                        .await?
                    {
                        ctx.state = AgentState::Verifying;
                        return Ok(());
                    }
                    break;
                }
            } else {
                repeats = 0;
            }
            last_signature = Some(signature);

            // **A call missing a required argument is not a call**, and that
            // rule now lives one level down in [`Runtime::perform_action`]
            // (v12.0.0) so it applies to every dial rather than to this one.
            // The `compact` path had no equivalent, and a live gemma-4-e2b
            // collected the seam's raw `'pattern'` KeyError repr thirty-six
            // times across one cell's three attempts because of it.
            match self
                .perform_action(
                    ctx,
                    req,
                    Chosen {
                        name: &entry.name,
                        thoughts: &thoughts,
                        args,
                        final_message,
                    },
                )
                .await
            {
                StepFlow::Continue => continue,
                StepFlow::Break => break,
                StepFlow::Finished => return Ok(()),
            }
        }

        ctx.state = AgentState::Verifying;
        Ok(())
    }
}

#[cfg(test)]
mod tests_args;
#[cfg(test)]
mod tests_menu;
#[cfg(test)]
mod tests_verify;
