use serde_json::{json, Value};

use super::execution::{Chosen, StepFlow};
use super::{RunRequest, Runtime};
use crate::kernel::profile::AgentProfile;
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::seam::worker::WorkerError;

mod answers;
mod args;
mod ranking;
mod suggestions;
mod verdict;
use args::*;
pub(super) use ranking::*;

pub use answers::{
    looks_like_a_path, named_choice, parse_choice, parse_line, parse_verdict_word,
    strip_control_tokens, strip_echoed_lines,
};

pub const MENU_TOKENS: u32 = 8;

pub const MENU_RETRY_TOKENS: u32 = 64;

pub const LINE_TOKENS: u32 = 192;

pub const MENU_LIMIT: usize = 9;

const LINE_STOP: [&str; 1] = ["\n"];

const MICRO_TEMPERATURE: f64 = 0.0;

pub(super) const REPEAT_FAILURE_LIMIT: u32 = 2;

pub(super) const ECHO_PREFIX_FLOOR: usize = 20;

pub(super) const TRAILING_PUNCTUATION: [char; 8] = ['.', ',', ':', ';', '—', '-', '…', '。'];

impl Runtime {
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
                    && step.get("action").and_then(Value::as_str) != Some("parse_error")
                    && !step
                        .get("error")
                        .and_then(Value::as_str)
                        .is_some_and(|err| {
                            err.starts_with("Unknown action")
                                || err.starts_with("COPIED_")
                                || err.starts_with("PREMATURE_FINAL")
                        })
            })
            .count() as u32;
        let budget = req.max_steps.saturating_sub(executed).max(1);
        let mut misreads = 0u32;
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
