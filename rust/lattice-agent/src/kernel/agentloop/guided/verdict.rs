use serde_json::{json, Map, Value};

use super::*;
use crate::kernel::agentloop::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::result_digest;
use crate::parse::pystr::py_str;
use crate::seam::worker::{Completion, WorkerError};

impl Runtime {
    pub(in crate::kernel::agentloop) async fn guided_verdict(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        model_id: Option<&str>,
    ) -> Result<Option<Map<String, Value>>, WorkerError> {
        let evidence = self.verdict_evidence(ctx);
        let context =
            crate::prompts::guided::verdict_block(&req.message, &evidence, &ctx.final_message);
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
            let line = parse_line(&clean_reply(&reason_reply));
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
        verdict.insert("confidence".into(), json!(0.6));
        verdict.insert("verdict_source".into(), json!("guided"));
        Ok(Some(verdict))
    }

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
