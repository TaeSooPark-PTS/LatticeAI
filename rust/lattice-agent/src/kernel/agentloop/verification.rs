use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::{
    answer_has_ungrounded_claims, answer_owes_a_count, answer_owes_a_summary, artifact_checklist,
    attributed_count, attributed_summary, complete_counted_and_summarized, complete_created_files,
    contains_ascii_word, delivered_answer, format_artifact_checklist, format_requirement_coverage,
    request_asks_for_a_count, request_asks_for_a_summary, requirement_coverage, truncate_strings,
};
use crate::parse::action::extract_verdict_details;
use crate::parse::pystr::py_str;
use crate::seam::worker::{Completion, WorkerError};

const STRICT_HINT: &str = "Your previous verdict was not parseable JSON. \
Reply with EXACTLY ONE JSON object and nothing else — no prose before it, \
no prose after it, no markdown fences, never two objects. \
Never emit <|channel|>, <|message|>, <|start|>, or <|end|> tokens; \
start at the opening brace.\n\
Example:\n\
{\"action\": \"verdict\", \"verdict\": \"PASS\", \"next_state\": \"DONE\", \
\"reason\": \"the requested file was written\", \"corrections\": []}\n\
verdict must be PASS or FAIL; next_state must be one of DONE, EXECUTING, \
ROLLBACK, FAILED.";

const VERDICT_STOP: [&str; 2] = ["\n```", "\nUser:"];

impl Runtime {
    pub fn has_execution_evidence(ctx: &AgentRunContext) -> bool {
        ctx.transcript.iter().any(|step| {
            step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
                && !matches!(
                    step.get("action").and_then(Value::as_str),
                    None | Some("final") | Some("parse_error")
                )
                && step.get("result").is_some_and(Value::is_object)
        })
    }

    pub async fn verify(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.reviewing_model.clone().or(req.reviewing_model.clone());
        let guided_verdict = if self.profile(model_id.as_deref()).decomposed {
            self.guided_verdict(ctx, req, model_id.as_deref()).await?
        } else {
            None
        };
        let verify_transcript = truncate_strings(
            &Value::Array(ctx.transcript.clone()),
            self.deps.transcript_budget.verify_chars,
        );
        let checklist = artifact_checklist(&ctx.transcript, &self.deps.file_create_actions);
        let checklist_hint = if checklist.is_empty() {
            String::new()
        } else {
            format!("\n\n{}", format_artifact_checklist(&checklist))
        };
        let coverage = requirement_coverage(
            &req.message,
            &ctx.transcript,
            &self.deps.file_create_actions,
        );
        let goal = ctx
            .plan
            .get("goal")
            .cloned()
            .unwrap_or_else(|| json!(req.message));
        let answered = if ctx.final_message.trim().is_empty() {
            String::new()
        } else {
            format!(
                "\n\nThe answer this run will give the user:\n{}",
                ctx.final_message.trim()
            )
        };
        let context = format!(
            "{}\n\n[LANGUAGE HINT: {}]\n\nOriginal request: {}\nPlan goal: {}{checklist_hint}{}\
{answered}\n\nFull transcript:\n{}",
            self.deps.prompts.critic_prompt(),
            req.language_hint,
            req.message,
            py_str(&goal),
            format_requirement_coverage(&coverage),
            serde_json::to_string_pretty(&verify_transcript).unwrap_or_default(),
        );

        let mut verdict: Option<Map<String, Value>> = guided_verdict;
        if verdict.is_none() {
            verdict = self
                .ask_for_verdict(ctx, model_id.as_deref(), &context, &coverage)
                .await?;
        }
        self.settle_verdict(ctx, req, verdict, &coverage)
    }

    async fn ask_for_verdict(
        &mut self,
        ctx: &mut AgentRunContext,
        model_id: Option<&str>,
        context: &str,
        coverage: &Value,
    ) -> Result<Option<Map<String, Value>>, WorkerError> {
        let raw = self
            .deps
            .worker
            .llm(Completion {
                model_id,
                message: "Review the execution transcript and return your verdict JSON.",
                context,
                max_tokens: self.deps.phase_budgets.verify_tokens,
                temperature: 0.1,
                stop: &[],
                prefix: "",
            })
            .await?;
        ctx.trace.llm_call("verify", model_id);

        Ok(match extract_verdict_details(&raw) {
            Ok((parsed, repairs)) => {
                ctx.trace.repair("verify", &repairs);
                Some(parsed)
            }
            Err(error) => {
                ctx.trace.parse_error("verify", &error.0, true);
                let strict_context = format!("{context}\n\n{STRICT_HINT}");
                let retry = self
                    .deps
                    .worker
                    .llm(Completion {
                        model_id,
                        message: "Return your verdict as one strict JSON object.",
                        context: &strict_context,
                        max_tokens: self.deps.phase_budgets.verify_tokens,
                        temperature: 0.0,
                        stop: &VERDICT_STOP,
                        prefix: "",
                    })
                    .await?;
                ctx.trace.llm_call("verify", model_id);
                match extract_verdict_details(&retry) {
                    Ok((parsed, repairs)) => {
                        ctx.trace.repair("verify", &repairs);
                        Some(parsed)
                    }
                    Err(retry_error) => {
                        ctx.trace.parse_error("verify", &retry_error.0, false);
                        last_rung_token_verdict(
                            &retry,
                            Self::has_execution_evidence(ctx),
                            coverage["complete"] == json!(true),
                        )
                        .map(|token| {
                            ctx.trace.repair("verify", &["token_verdict".to_string()]);
                            ctx.trace.decision(
                                "verify",
                                "token_verdict",
                                &[("verdict", json!(token.label))],
                            );
                            token.into_verdict()
                        })
                    }
                }
            }
        })
    }

    fn settle_verdict(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        mut verdict: Option<Map<String, Value>>,
        coverage: &Value,
    ) -> Result<(), WorkerError> {
        let has_evidence = Self::has_execution_evidence(ctx);

        let Some(mut verdict) = verdict.take() else {
            ctx.transcript.push(json!({
                "state": AgentState::Verifying.as_str(),
                "verdict": "UNAVAILABLE",
                "reason": "critic output unparseable after strict retry",
                "verifier_available": false,
                "verdict_valid": false,
                "evidence": has_evidence,
            }));
            ctx.trace.decision(
                "verify",
                "verification_unavailable",
                &[
                    ("verifier_available", json!(false)),
                    ("verdict_valid", json!(false)),
                    ("evidence", json!(has_evidence)),
                ],
            );
            self.emit_step("verify", "verdict", &[("verdict", json!("UNAVAILABLE"))]);
            const UNVERIFIED: &str = "검증을 완료하지 못했습니다 — 검증 모델의 응답을 해석할 수 \
없었습니다. 실행 결과를 직접 확인해 주시고, 필요하면 다시 시도해 주세요.";
            ctx.final_message = complete_created_files(
                &ctx.final_message,
                &ctx.transcript,
                &self.deps.file_create_actions,
            );
            ctx.final_message =
                complete_counted_and_summarized(&ctx.final_message, &req.message, &ctx.transcript);
            ctx.final_message = if has_evidence && !ctx.final_message.trim().is_empty() {
                ctx.trace
                    .decision("verify", "needs_review_unverified_answer", &[]);
                format!("{}\n\n{UNVERIFIED}", ctx.final_message.trim())
            } else {
                UNVERIFIED.to_string()
            };
            ctx.state = AgentState::NeedsReview;
            return Ok(());
        };

        ctx.corrections = verdict
            .get("corrections")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let raw_next = verdict
            .get("next_state")
            .cloned()
            .unwrap_or_else(|| json!(""));
        let next_state = match raw_next.as_str() {
            Some("COMPLETE") => "DONE".to_string(),
            Some("RETRY") => "EXECUTING".to_string(),
            _ => py_str(&raw_next),
        };
        let verdict_label = verdict.get("verdict").cloned().unwrap_or_else(|| json!(""));
        if verdict.get("reason").and_then(Value::as_str)
            == Some(crate::prompts::VERDICT_REASON_PLACEHOLDER)
        {
            verdict.insert("reason".into(), json!(""));
        }

        let mut step = json!({
            "state": AgentState::Verifying.as_str(),
            "verdict": verdict_label,
            "reason": verdict.get("reason").cloned().unwrap_or_else(|| json!("")),
            "corrections": ctx.corrections,
            "confidence": verdict.get("confidence").cloned().unwrap_or_else(|| json!(0.9)),
            "next_state": next_state,
            "verifier_available": true,
            "verdict_valid": true,
            "evidence": has_evidence,
        });
        if let Some(source) = verdict
            .get("verdict_source")
            .filter(|value| value.is_string())
        {
            step["verdict_source"] = source.clone();
        }
        ctx.transcript.push(step);
        ctx.trace.decision(
            "verify",
            &py_str(&verdict_label),
            &[
                ("next_state", json!(next_state)),
                ("verifier_available", json!(true)),
                ("verdict_valid", json!(true)),
                ("evidence", json!(has_evidence)),
            ],
        );
        self.emit_step(
            "verify",
            "verdict",
            &[
                ("verdict", json!(py_str(&verdict_label))),
                ("next_state", json!(next_state)),
            ],
        );

        if verdict_label == json!("PASS") {
            if !has_evidence {
                ctx.trace
                    .decision("verify", "needs_review_no_evidence", &[]);
                ctx.final_message = "검증자는 통과를 보고했지만 실제 실행 근거(도구 실행 기록)가 \
없어 완료로 처리하지 않았습니다. 결과를 직접 확인해 주세요."
                    .into();
                ctx.state = AgentState::NeedsReview;
                return Ok(());
            }
            if coverage["complete"] != json!(true) {
                let missing: Vec<String> = coverage["missing_files"]
                    .as_array()
                    .map(|items| items.iter().map(py_str).collect())
                    .unwrap_or_default();
                ctx.trace.decision(
                    "verify",
                    "needs_review_missing_files",
                    &[("missing", json!(missing.len()))],
                );
                ctx.transcript.push(json!({
                    "state": AgentState::Verifying.as_str(),
                    "requirement_coverage": coverage,
                }));
                ctx.final_message = format!(
                    "요청한 파일 중 일부가 만들어지지 않아 완료로 처리하지 않았습니다: {}",
                    missing.join(", ")
                );
                ctx.state = AgentState::NeedsReview;
                return Ok(());
            }
            if ctx.final_message.is_empty() {
                ctx.final_message = match verdict.get("reason") {
                    Some(reason) => py_str(reason),
                    None => "작업이 완료되었습니다.".into(),
                };
            }
            if Self::settle_answer(ctx, req, &self.deps.file_create_actions) {
                return Ok(());
            }
            ctx.state = AgentState::Done;
        } else if next_state == "ROLLBACK" {
            ctx.state = AgentState::Rollback;
        } else if next_state == "EXECUTING" {
            if ctx.retry_count >= req.max_retry {
                let weak_instrument = verdict.get("verdict_source").is_some_and(Value::is_string);
                ctx.final_message = complete_created_files(
                    &ctx.final_message,
                    &ctx.transcript,
                    &self.deps.file_create_actions,
                );
                ctx.final_message = complete_counted_and_summarized(
                    &ctx.final_message,
                    &req.message,
                    &ctx.transcript,
                );
                if weak_instrument
                    && has_evidence
                    && ctx.final_message.trim().is_empty()
                    && coverage["complete"] == json!(true)
                {
                    if let Some(delivered) =
                        delivered_answer(&ctx.transcript, &self.deps.file_create_actions)
                    {
                        ctx.trace.decision("verify", "answer_from_evidence", &[]);
                        ctx.final_message = delivered;
                    }
                }
                if weak_instrument && has_evidence && !ctx.final_message.trim().is_empty() {
                    let doubt =
                        py_str(&verdict.get("reason").cloned().unwrap_or_else(|| json!("")));
                    ctx.trace
                        .decision("verify", "needs_review_unconfirmed_answer", &[]);
                    ctx.final_message = unconfirmed_answer(&ctx.final_message, &doubt);
                    ctx.state = AgentState::NeedsReview;
                } else {
                    ctx.final_message = "처리 중 문제가 발생했습니다. 다시 시도해 주세요.".into();
                    ctx.state = AgentState::Failed;
                }
            } else {
                ctx.retry_count += 1;
                ctx.trace.retry("verify", ctx.retry_count);
                ctx.transcript.push(json!({
                    "state": AgentState::Executing.as_str(),
                    "retry_attempt": ctx.retry_count,
                    "corrections": ctx.corrections,
                }));
                ctx.state = AgentState::Executing;
            }
        } else if next_state == "DONE" {
            ctx.trace
                .decision("verify", "needs_review_inconsistent_verdict", &[]);
            Self::hold_for_review(
                ctx,
                req,
                &self.deps.file_create_actions,
                "검증 결과가 일관되지 않아 완료로 처리하지 않았습니다. \
실행 결과를 직접 확인해 주세요.",
            );
        } else if next_state.is_empty() {
            if implied_done_from_empty_next_state(
                has_evidence,
                coverage["complete"] == json!(true),
                &verdict_label,
            ) {
                ctx.trace
                    .decision("verify", "done_implied_by_evidence", &[]);
                if ctx.final_message.is_empty() {
                    ctx.final_message = match verdict.get("reason") {
                        Some(reason) if !py_str(reason).is_empty() => py_str(reason),
                        _ => "작업이 완료되었습니다.".into(),
                    };
                }
                if Self::settle_answer(ctx, req, &self.deps.file_create_actions) {
                    return Ok(());
                }
                ctx.state = AgentState::Done;
            } else {
                ctx.trace.decision(
                    "verify",
                    "needs_review_empty_next_state",
                    &[
                        ("evidence", json!(has_evidence)),
                        ("coverage_complete", coverage["complete"].clone()),
                        ("verdict", verdict_label.clone()),
                    ],
                );
                Self::hold_for_review(
                    ctx,
                    req,
                    &self.deps.file_create_actions,
                    "검증자가 다음 상태를 비운 채 답했고 완료로 볼 근거가 \
부족해 검토가 필요합니다. 실행 결과를 직접 확인해 주세요.",
                );
            }
        } else {
            let restored = complete_created_files(
                &ctx.final_message,
                &ctx.transcript,
                &self.deps.file_create_actions,
            );
            let restored =
                complete_counted_and_summarized(&restored, &req.message, &ctx.transcript);
            let counted = has_evidence
                && !restored.trim().is_empty()
                && attributed_count(&req.message, &ctx.transcript).is_some()
                && request_asks_for_a_count(&req.message);
            let summarized = has_evidence
                && !restored.trim().is_empty()
                && attributed_summary(&ctx.transcript).is_some()
                && request_asks_for_a_summary(&req.message);
            if counted || summarized {
                let doubt = py_str(&verdict.get("reason").cloned().unwrap_or_else(|| json!("")));
                ctx.trace
                    .decision("verify", "needs_review_counted_answer", &[]);
                ctx.final_message = unconfirmed_answer(&restored, &doubt);
                ctx.state = AgentState::NeedsReview;
            } else {
                ctx.final_message = match verdict.get("reason") {
                    Some(reason) => py_str(reason),
                    None => "검증자가 인식되지 않은 다음 상태를 반환했습니다.".into(),
                };
                ctx.state = AgentState::Failed;
            }
        }
        Ok(())
    }

    fn settle_answer(
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        file_create_actions: &BTreeSet<String>,
    ) -> bool {
        ctx.final_message =
            complete_created_files(&ctx.final_message, &ctx.transcript, file_create_actions);
        ctx.final_message =
            complete_counted_and_summarized(&ctx.final_message, &req.message, &ctx.transcript);
        if answer_owes_a_count(&ctx.final_message, &req.message, &ctx.transcript) {
            ctx.trace.decision("verify", "needs_review_no_count", &[]);
            const CAVEAT: &str =
                "요청하신 개수를 확인하지 못했습니다 — 개수를 센 도구 실행 기록이 \
없어 완료로 처리하지 않았습니다. 결과를 직접 확인해 주세요.";
            Self::hold_for_review(ctx, req, file_create_actions, CAVEAT);
            return true;
        }
        if answer_owes_a_summary(&ctx.final_message, &req.message, &ctx.transcript) {
            ctx.trace
                .decision("verify", "needs_review_thin_summary", &[]);
            const CAVEAT: &str = "요청하신 요약을 파일 내용으로 확인하지 못해 완료로 처리하지 \
않았습니다. 결과를 직접 확인해 주세요.";
            Self::hold_for_review(ctx, req, file_create_actions, CAVEAT);
            return true;
        }
        if let Some(reason) =
            answer_has_ungrounded_claims(&ctx.final_message, &req.message, &ctx.transcript)
        {
            ctx.trace.decision(
                "verify",
                "needs_review_ungrounded_claim",
                &[("reason", json!(reason))],
            );
            let caveat = format!(
                "답변의 파일/식별자 주장이 실행 기록으로 확인되지 않아 완료로 처리하지 않았습니다 ({reason}). 결과를 직접 확인해 주세요."
            );
            Self::hold_for_review(ctx, req, file_create_actions, &caveat);
            return true;
        }
        if crate::kernel::transcript::rag_grounding::transcript_has_evidence(&ctx.transcript) {
            use crate::kernel::transcript::rag_grounding::{
                extract_evidence_passages, verify_grounded_answer, GroundingDefect,
            };
            let passages = extract_evidence_passages(&ctx.transcript);
            let report = verify_grounded_answer(&ctx.final_message, &passages);
            if report.has(GroundingDefect::SupersededFactLeak)
                || report.has(GroundingDefect::NumericMismatch)
                || report.has(GroundingDefect::PolarityMismatch)
                || report.has(GroundingDefect::ZeroActiveEvidenceHallucination)
            {
                ctx.trace.decision(
                    "verify",
                    "needs_review_rag_grounding",
                    &[("report", report.to_detail())],
                );
                let reasons = report
                    .defects
                    .iter()
                    .map(|d| d.as_str())
                    .collect::<Vec<_>>()
                    .join(", ");
                let caveat = format!(
                    "답변이 검색/문서 근거와 일치하지 않거나 대체된(superseded) 사실을 포함하여 완료로 처리하지 않았습니다 ({reasons}). 결과를 직접 확인해 주세요."
                );
                Self::hold_for_review(ctx, req, file_create_actions, &caveat);
                return true;
            }
        }
        false
    }

    fn hold_for_review(
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        file_create_actions: &BTreeSet<String>,
        caveat: &str,
    ) {
        ctx.final_message =
            complete_created_files(&ctx.final_message, &ctx.transcript, file_create_actions);
        ctx.final_message =
            complete_counted_and_summarized(&ctx.final_message, &req.message, &ctx.transcript);
        let said = ctx.final_message.trim();
        ctx.final_message = if said.is_empty() {
            caveat.to_string()
        } else {
            format!("{said}\n\n{caveat}")
        };
        ctx.state = AgentState::NeedsReview;
    }
}

fn unconfirmed_answer(answer: &str, doubt: &str) -> String {
    let caveat = if doubt.trim().is_empty() {
        "검증에서 완료로 확인되지 않아 검토가 필요합니다. 결과를 직접 확인해 주세요.".to_string()
    } else {
        format!(
            "검증에서 완료로 확인되지 않아 검토가 필요합니다 ({}). 결과를 직접 확인해 주세요.",
            doubt.trim()
        )
    };
    format!("{}\n\n{caveat}", answer.trim())
}

fn last_rung_token_verdict(
    raw: &str,
    has_evidence: bool,
    coverage_complete: bool,
) -> Option<TokenVerdict> {
    if !has_evidence || !coverage_complete {
        return None;
    }
    let plain = match crate::parse::channel::strip_channel_frames(raw) {
        Some(stripped) => stripped,
        None => raw.to_string(),
    };
    let pass = contains_ascii_word(&plain, "pass") || plain.contains("통과");
    let fail =
        contains_ascii_word(&plain, "fail") || plain.contains("실패") || plain.contains("불합격");
    match (pass, fail) {
        (true, false) => Some(TokenVerdict {
            label: "PASS",
            next_state: "DONE",
        }),
        (false, true) => Some(TokenVerdict {
            label: "FAIL",
            next_state: "FAILED",
        }),
        _ => None,
    }
}

struct TokenVerdict {
    label: &'static str,
    next_state: &'static str,
}

impl TokenVerdict {
    fn into_verdict(self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("action".into(), json!("verdict"));
        map.insert("verdict".into(), json!(self.label));
        map.insert("next_state".into(), json!(self.next_state));
        map.insert(
            "reason".into(),
            json!(format!(
                "unambiguous {} token after unparseable critic JSON",
                self.label
            )),
        );
        map.insert("corrections".into(), json!([]));
        map.insert("verdict_source".into(), json!("token"));
        map
    }
}

fn implied_done_from_empty_next_state(
    has_evidence: bool,
    coverage_complete: bool,
    verdict_label: &Value,
) -> bool {
    has_evidence && coverage_complete && is_blank_or_pass(verdict_label)
}

fn is_blank_or_pass(verdict_label: &Value) -> bool {
    match verdict_label {
        Value::Null => true,
        Value::String(label) => label.is_empty() || label.eq_ignore_ascii_case("PASS"),
        _ => false,
    }
}

#[cfg(test)]
mod tests;
#[cfg(test)]
mod tests_settle;
