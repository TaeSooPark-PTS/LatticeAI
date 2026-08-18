use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::result_digest;
use crate::parse::pystr::py_str;
use crate::surface::worker::{Completion, WorkerError};

impl Runtime {
    /// VERIFY, decomposed: one closed question, then one line of reason.
    ///
    /// Returns `None` when no verdict word came back at all, and the ordinary
    /// JSON critic chain then runs — this is a *first* rung, never a
    /// replacement. What it produces is an ordinary verdict map, so
    /// [`Runtime::settle_verdict`]'s gates judge it exactly as they judge a
    /// parsed object: a guided PASS over a run with no execution evidence is
    /// still `NEEDS_REVIEW`, and a guided PASS that left a requested file
    /// unwritten still is too.
    pub(in crate::kernel::agentloop) async fn guided_verdict(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
    ) -> Result<Option<Map<String, Value>>, WorkerError> {
        let evidence = self.verdict_evidence(ctx);
        let context =
            crate::prompts::guided::verdict_block(&req.message, &evidence, &ctx.final_message);
        // The menu turn's instrument, applied to the menu turn's twin: the
        // answer's *position* is forced with a prefix, and a turn that came
        // back with no verdict word is re-asked with room and no line stop
        // rather than replayed identically at temperature zero. See
        // [`crate::prompts::guided::VERDICT_ANSWER_PREFIX`] and
        // [`MENU_RETRY_TOKENS`].
        let mut passed = None;
        for attempt in 0..2 {
            let (max_tokens, stop): (u32, &[&str]) = if attempt == 0 {
                (MENU_TOKENS, &LINE_STOP)
            } else {
                (MENU_RETRY_TOKENS, &[])
            };
            let reply = self
                .deps
                .worker
                .llm(Completion {
                    model_id,
                    message: crate::prompts::guided::VERDICT_QUESTION,
                    context: &context,
                    max_tokens,
                    temperature: MICRO_TEMPERATURE,
                    stop,
                    prefix: crate::prompts::guided::VERDICT_ANSWER_PREFIX,
                })
                .await?;
            ctx.trace.llm_call("verify", model_id);
            passed = parse_verdict_word(&clean_reply(&reply));
            if passed.is_some() {
                break;
            }
        }
        let Some(passed) = passed else {
            return Ok(None);
        };
        let reason_reply = self
            .deps
            .worker
            .llm(Completion {
                model_id,
                message: crate::prompts::guided::REASON_QUESTION,
                context: &format!(
                    "{context}\n\n[판정 / VERDICT]\n{}",
                    if passed { "PASS" } else { "FAIL" }
                ),
                max_tokens: LINE_TOKENS,
                temperature: MICRO_TEMPERATURE,
                stop: &LINE_STOP,
                prefix: "",
            })
            .await?;
        ctx.trace.llm_call("verify", model_id);
        let reason = {
            // The one place a control frame reached the *user* (v12.0.0). The
            // reason is shown, and it is fed back to the executor as the next
            // attempt's correction, so a raw `<|channel>thought` here was both
            // the answer a live run displayed and the instruction it then tried
            // to follow. Cleaned first; what survives is judged by the same
            // empty/echo test as before, so a frame-only reply now falls to the
            // stated default instead of being recorded as a reason.
            let line = parse_line(&clean_reply(&reason_reply));
            // A weak model handed its own verdict in the context echoes it: the
            // first live run's reasons were all the literal word `FAIL`. That
            // is not a reason, and recording it as one would put it in front of
            // the executor as a correction on the next attempt.
            //
            // **And so does a model that echoes the question** (v12.0.0). A
            // live 2B answered this very turn with
            // `이유를 다시 한 줄로 쓰세요. / Give the reason in one short line.`
            // — our own words with one inserted — and the run showed that to
            // the user as the critic's reason and fed it back as the next
            // attempt's correction. The sentences this crate wrote are known
            // ([`crate::prompts::guided::contains_owned_instruction`]); one
            // coming back is not a reason, whatever a model did to it on the
            // way.
            if line.is_empty()
                || parse_verdict_word(&line).is_some()
                || crate::prompts::guided::contains_owned_instruction(&line)
            {
                if passed {
                    "guided verdict: the transcript shows the work was done".to_string()
                } else {
                    "guided verdict: the transcript does not show the work was done".to_string()
                }
            } else {
                line
            }
        };
        ctx.trace.decision(
            "verify",
            "guided_verdict",
            &[("verdict", json!(if passed { "PASS" } else { "FAIL" }))],
        );

        let mut verdict = Map::new();
        verdict.insert("action".into(), json!("verdict"));
        verdict.insert(
            "verdict".into(),
            json!(if passed { "PASS" } else { "FAIL" }),
        );
        verdict.insert(
            "next_state".into(),
            json!(if passed { "DONE" } else { "EXECUTING" }),
        );
        verdict.insert("reason".into(), json!(reason));
        verdict.insert(
            "corrections".into(),
            if passed {
                json!([])
            } else {
                json!([verdict["reason"].clone()])
            },
        );
        // Below the 0.9 a parsed object gets: a closed question is a weaker
        // instrument than a verdict object, and saying so on the step is the
        // honest record of how the answer was obtained.
        verdict.insert("confidence".into(), json!(0.6));
        verdict.insert("verdict_source".into(), json!("guided"));
        Ok(Some(verdict))
    }

    /// The one thing the closed question is asked about: what actually ran.
    ///
    /// Deliberately not the transcript. A model asked "did this succeed?" over
    /// four hundred lines of JSON answers about the JSON; asked over six lines
    /// of `- write_file notes/hello.md: ok` it answers about the work.
    ///
    /// **But `ok` alone is not what happened** (v12.0.0). `- list_dir: ok` says
    /// a call returned; it does not say what it returned, and a critic asked
    /// whether the run reported a file count over that line has been given
    /// nothing to answer with — three models, every attempt, said FAIL over a
    /// `list_dir` that had returned two real entries. So each successful row
    /// carries [`crate::kernel::transcript::result_digest`]: the one fact the
    /// result actually established,
    /// in a handful of characters. Still not the transcript, and still one line
    /// per step.
    fn verdict_evidence(&self, ctx: &AgentRunContext) -> String {
        let rows: Vec<String> = ctx
            .transcript
            .iter()
            .filter(|step| {
                step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
            })
            .filter_map(|step| {
                let action = step.get("action").and_then(Value::as_str)?;
                if action == "parse_error" {
                    return None;
                }
                let path = step
                    .get("args")
                    .and_then(|args| args.get("path"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let outcome = match (step.get("result"), step.get("error")) {
                    (Some(result), _) => match result_digest(result) {
                        Some(digest) => format!("ok — {digest}"),
                        None => "ok".to_string(),
                    },
                    (None, Some(error)) => {
                        format!(
                            "failed ({})",
                            crate::parse::pystr::char_slice(&py_str(error), 80)
                        )
                    }
                    _ => "-".to_string(),
                };
                Some(if path.is_empty() {
                    format!("- {action}: {outcome}")
                } else {
                    format!("- {action} {path}: {outcome}")
                })
            })
            .collect();
        if rows.is_empty() {
            "- (아무 도구도 실행되지 않았습니다 / no tool ran)".to_string()
        } else {
            rows.join("\n")
        }
    }

    /// Instructions from any skill this run has loaded, newest last.
    ///
    /// Read back out of the transcript rather than held in a field: a resumed
    /// run restores its transcript, so the skill a user approved before the
    /// pause is still in force after it.
    pub(super) fn skill_notes(&self, ctx: &AgentRunContext) -> String {
        let mut notes: Vec<String> = Vec::new();
        for step in &ctx.transcript {
            let Some(action) = step.get("action").and_then(Value::as_str) else {
                continue;
            };
            if !action.starts_with(crate::tools::catalog::SKILL_PREFIX) {
                continue;
            }
            if let Some(text) = step
                .get("result")
                .and_then(|result| result.get("text"))
                .and_then(Value::as_str)
            {
                notes.push(crate::parse::pystr::char_slice(text, 1200).to_string());
            }
        }
        notes.join("\n\n")
    }
}
