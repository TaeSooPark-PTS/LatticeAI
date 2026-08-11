//! VERIFY — the critic's verdict, and the facts that outrank it.
//!
//! A port of `latticeai.core.agent.verification`. Fail-closed by construction:
//! a critic whose output cannot be parsed (after **one** strict repair retry at
//! temperature 0.0) never fabricates a PASS; a PASS over a transcript with no
//! execution evidence is not a completion; and a PASS that leaves a *requested
//! file* unwritten is a fact, not a judgement, so it is enforced rather than
//! merely reported back to the critic.

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime};
use crate::action::extract_action_details;
use crate::pystr::py_str;
use crate::state::{AgentRunContext, AgentState};
use crate::transcript::{
    artifact_checklist, format_artifact_checklist, format_requirement_coverage,
    requirement_coverage, truncate_strings,
};
use crate::worker::{Completion, WorkerError};

/// The one strict re-ask, verbatim.
const STRICT_HINT: &str = "Your previous verdict was not parseable JSON. Reply with EXACTLY one \
JSON object like {\"action\": \"verdict\", \"verdict\": \"PASS\", \"next_state\": \"DONE\", \
\"reason\": \"...\", \"corrections\": []} and nothing else. verdict must be PASS or FAIL; \
next_state must be one of DONE, EXECUTING, ROLLBACK, FAILED.";

impl Runtime {
    /// Deterministic evidence check: at least one executing step actually
    /// produced a result. `final` / parse-error / blocked steps carry no result
    /// and do not count.
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

    /// VERIFYING: DONE / EXECUTING (retry) / ROLLBACK / NEEDS_REVIEW / FAILED.
    pub async fn verify(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
    ) -> Result<(), WorkerError> {
        let model_id = ctx.reviewing_model.clone().or(req.reviewing_model.clone());
        // The critic must see every step, but not every byte of tool output.
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
        let context = format!(
            "{}\n\n[LANGUAGE HINT: {}]\n\nOriginal request: {}\nPlan goal: {}{checklist_hint}{}\
\n\nFull transcript:\n{}",
            self.deps.prompts.critic,
            req.language_hint,
            req.message,
            py_str(&goal),
            format_requirement_coverage(&coverage),
            serde_json::to_string_pretty(&verify_transcript).unwrap_or_default(),
        );

        let raw = self
            .deps
            .worker
            .llm(Completion {
                model_id: model_id.as_deref(),
                message: "Review the execution transcript and return your verdict JSON.",
                context: &context,
                max_tokens: self.deps.phase_budgets.verify_tokens,
                temperature: 0.1,
            })
            .await?;
        ctx.trace.llm_call("verify", model_id.as_deref());

        let mut verdict: Option<Map<String, Value>> = match extract_action_details(&raw) {
            Ok((parsed, repairs)) => {
                ctx.trace.repair("verify", &repairs);
                Some(parsed)
            }
            Err(error) => {
                // One strict repair retry — re-ask for the exact wire format
                // instead of fabricating a verdict.
                ctx.trace.parse_error("verify", &error.0, true);
                let strict_context = format!("{context}\n\n{STRICT_HINT}");
                let retry = self
                    .deps
                    .worker
                    .llm(Completion {
                        model_id: model_id.as_deref(),
                        message: "Return your verdict as one strict JSON object.",
                        context: &strict_context,
                        max_tokens: self.deps.phase_budgets.verify_tokens,
                        temperature: 0.0,
                    })
                    .await?;
                ctx.trace.llm_call("verify", model_id.as_deref());
                match extract_action_details(&retry) {
                    Ok((parsed, repairs)) => {
                        ctx.trace.repair("verify", &repairs);
                        Some(parsed)
                    }
                    Err(retry_error) => {
                        ctx.trace.parse_error("verify", &retry_error.0, false);
                        None
                    }
                }
            }
        };

        let has_evidence = Self::has_execution_evidence(ctx);

        let Some(verdict) = verdict.take() else {
            // Verifier unavailable — fail closed, never DONE.
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
            ctx.final_message = "검증을 완료하지 못했습니다 — 검증 모델의 응답을 해석할 수 \
없었습니다. 실행 결과를 직접 확인해 주시고, 필요하면 다시 시도해 주세요."
                .into();
            ctx.state = AgentState::NeedsReview;
            return Ok(());
        };

        // A non-list `corrections` is normalised to an empty list rather than
        // sliced as a string, which is what Python's `[-3:]` would do to it.
        ctx.corrections = verdict
            .get("corrections")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let raw_next = verdict
            .get("next_state")
            .cloned()
            .unwrap_or_else(|| json!(""));
        // Normalize legacy verdict next_state strings to current state names.
        let next_state = match raw_next.as_str() {
            Some("COMPLETE") => "DONE".to_string(),
            Some("RETRY") => "EXECUTING".to_string(),
            _ => py_str(&raw_next),
        };
        let verdict_label = verdict.get("verdict").cloned().unwrap_or_else(|| json!(""));

        ctx.transcript.push(json!({
            "state": AgentState::Verifying.as_str(),
            "verdict": verdict_label,
            "reason": verdict.get("reason").cloned().unwrap_or_else(|| json!("")),
            "corrections": ctx.corrections,
            "confidence": verdict.get("confidence").cloned().unwrap_or_else(|| json!(0.9)),
            "next_state": next_state,
            "verifier_available": true,
            "verdict_valid": true,
            "evidence": has_evidence,
        }));
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
            // DONE requires both a validly parsed PASS *and* deterministic
            // execution evidence. A PASS over an evidence-free run is not a
            // completion.
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
            ctx.state = AgentState::Done;
        } else if next_state == "ROLLBACK" {
            ctx.state = AgentState::Rollback;
        } else if next_state == "EXECUTING" {
            if ctx.retry_count >= req.max_retry {
                ctx.final_message = "처리 중 문제가 발생했습니다. 다시 시도해 주세요.".into();
                ctx.state = AgentState::Failed;
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
            // Contradictory verdict: DONE without a PASS. This is a non-success
            // the user must review, not a loose success path.
            ctx.trace
                .decision("verify", "needs_review_inconsistent_verdict", &[]);
            ctx.final_message = "검증 결과가 일관되지 않아 완료로 처리하지 않았습니다. \
실행 결과를 직접 확인해 주세요."
                .into();
            ctx.state = AgentState::NeedsReview;
        } else {
            ctx.final_message = match verdict.get("reason") {
                Some(reason) => py_str(reason),
                None => "검증자가 인식되지 않은 다음 상태를 반환했습니다.".into(),
            };
            ctx.state = AgentState::Failed;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentloop::harness::harness;

    fn executed(path: &str) -> Value {
        json!({"state": "EXECUTING", "action": "write_file",
               "args": {"path": path}, "result": {"path": path, "bytes": 3}})
    }

    fn verdict(body: Value) -> String {
        body.to_string()
    }

    #[tokio::test]
    async fn a_pass_with_evidence_and_full_coverage_is_done() {
        let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
             "next_state": "DONE", "reason": "looks right", "corrections": []}))])
        .await;
        let mut ctx = harness.context();
        ctx.transcript.push(executed("note.md"));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Done);
        assert_eq!(ctx.final_message, "looks right");
        let step = ctx.transcript.last().expect("verdict step");
        assert_eq!(step["state"], "VERIFYING");
        assert_eq!(step["evidence"], true);
        assert_eq!(step["confidence"], 0.9, "the default confidence");
        assert_eq!(step["verifier_available"], true);
    }

    #[tokio::test]
    async fn a_pass_over_an_evidence_free_run_is_needs_review() {
        let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
             "next_state": "DONE", "reason": "all good"}))])
        .await;
        let mut ctx = harness.context();
        // `final` and parse errors carry no result, so they are not evidence.
        ctx.transcript
            .push(json!({"state": "EXECUTING", "action": "final"}));
        ctx.transcript
            .push(json!({"state": "EXECUTING", "action": "parse_error",
                                   "raw": "x", "error": "y"}));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::NeedsReview);
        assert!(ctx.final_message.contains("실행 근거"));
    }

    #[tokio::test]
    async fn a_pass_that_leaves_a_requested_file_unwritten_is_needs_review() {
        let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
             "next_state": "DONE", "reason": "done"}))])
        .await;
        harness.request.message = "todo 앱 html css js 만들어줘".into();
        let mut ctx = harness.context();
        ctx.transcript.push(executed("index.html"));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::NeedsReview);
        assert_eq!(
            ctx.final_message,
            "요청한 파일 중 일부가 만들어지지 않아 완료로 처리하지 않았습니다: style.css, app.js"
        );
        let coverage = ctx.transcript.last().expect("coverage step");
        assert_eq!(coverage["requirement_coverage"]["complete"], false);
    }

    #[tokio::test]
    async fn a_fail_asking_for_execution_retries_until_max_retry() {
        let retry = verdict(json!({"action": "verdict", "verdict": "FAIL",
             "next_state": "EXECUTING", "reason": "try again", "corrections": ["be specific"]}));
        let mut harness = harness(&[&retry, &retry, &retry, &retry]).await;
        let mut ctx = harness.context();
        ctx.transcript.push(executed("a.md"));
        for attempt in 1..=3 {
            harness
                .runtime
                .verify(&mut ctx, &harness.request)
                .await
                .expect("verify");
            assert_eq!(ctx.state, AgentState::Executing, "attempt {attempt}");
            assert_eq!(ctx.retry_count, attempt);
            assert_eq!(ctx.corrections, vec![json!("be specific")]);
        }
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Failed);
        assert_eq!(
            ctx.final_message,
            "처리 중 문제가 발생했습니다. 다시 시도해 주세요."
        );
        assert_eq!(ctx.retry_count, 3, "the fourth attempt does not increment");
    }

    #[tokio::test]
    async fn the_legacy_next_state_aliases_still_mean_what_they_meant() {
        let mut retry = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
             "next_state": "RETRY", "reason": "again"}))])
        .await;
        let mut ctx = retry.context();
        ctx.transcript.push(executed("a.md"));
        retry
            .runtime
            .verify(&mut ctx, &retry.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Executing);
        // The retry appends its own step after the verdict, so the verdict is
        // the one before last.
        assert_eq!(ctx.transcript[1]["next_state"], "EXECUTING");
        assert_eq!(ctx.transcript[2]["retry_attempt"], 1);

        let mut complete = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
             "next_state": "COMPLETE", "reason": "ok"}))])
        .await;
        let mut ctx = complete.context();
        ctx.transcript.push(executed("a.md"));
        complete
            .runtime
            .verify(&mut ctx, &complete.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Done);
        assert_eq!(ctx.transcript.last().expect("step")["next_state"], "DONE");
    }

    #[tokio::test]
    async fn rollback_and_the_contradictory_and_unknown_verdicts() {
        let cases = [
            (
                json!({"action": "v", "verdict": "FAIL", "next_state": "ROLLBACK"}),
                AgentState::Rollback,
            ),
            // DONE without a PASS is a contradiction the user must review.
            (
                json!({"action": "v", "verdict": "FAIL", "next_state": "DONE"}),
                AgentState::NeedsReview,
            ),
            (
                json!({"action": "v", "verdict": "FAIL", "next_state": "SOMETHING"}),
                AgentState::Failed,
            ),
        ];
        for (body, expected) in cases {
            let mut harness = harness(&[&verdict(body.clone())]).await;
            let mut ctx = harness.context();
            ctx.transcript.push(executed("a.md"));
            harness
                .runtime
                .verify(&mut ctx, &harness.request)
                .await
                .expect("verify");
            assert_eq!(ctx.state, expected, "{body}");
        }
    }

    #[tokio::test]
    async fn an_unparseable_critic_gets_exactly_one_strict_retry() {
        let mut harness = harness(&[
            "I think it went fine, honestly.",
            &verdict(
                json!({"action": "verdict", "verdict": "PASS", "next_state": "DONE",
                            "reason": "second time lucky"}),
            ),
        ])
        .await;
        let mut ctx = harness.context();
        ctx.transcript.push(executed("a.md"));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Done);
        assert_eq!(harness.runtime_llm_calls(&ctx), 2);
        // The retry is asked at temperature 0.0, and names the wire format.
        let asks = harness.worker.calls.lock().expect("lock").clone();
        assert_eq!(asks[1]["body"]["temperature"], 0.0);
        assert!(asks[1]["body"]["context"]
            .as_str()
            .expect("context")
            .contains("Your previous verdict was not parseable JSON."));
        assert_eq!(ctx.trace.summary()["parse_errors"], 1);
    }

    #[tokio::test]
    async fn a_critic_that_never_parses_is_unavailable_and_never_done() {
        let mut harness = harness(&["prose", "still prose"]).await;
        let mut ctx = harness.context();
        ctx.transcript.push(executed("a.md"));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::NeedsReview);
        let step = ctx.transcript.last().expect("step");
        assert_eq!(step["verdict"], "UNAVAILABLE");
        assert_eq!(step["verifier_available"], false);
        assert_eq!(step["verdict_valid"], false);
        assert_eq!(step["evidence"], true, "evidence is reported even so");
        assert_eq!(ctx.trace.summary()["parse_errors"], 2);
    }

    #[tokio::test]
    async fn the_critic_prompt_carries_the_deterministic_facts() {
        let mut harness = harness(&[&verdict(json!({"action": "v", "verdict": "PASS",
                                                    "next_state": "DONE"}))])
        .await;
        harness.request.message = "todo 앱 html css 만들어줘\n- 다크모드".into();
        let mut ctx = harness.context();
        let mut step = executed("index.html");
        step["content_sanitize"] = json!({"sanitized": true, "repaired": true});
        ctx.transcript.push(step);
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        let context = harness.worker.calls.lock().expect("lock")[0]["body"]["context"]
            .as_str()
            .expect("context")
            .to_string();
        assert!(
            context.contains("- index.html: auto-REPAIRED scaffold"),
            "{context}"
        );
        assert!(context.contains("- style.css: MISSING"));
        assert!(context.contains("- 다크모드"));
    }

    #[tokio::test]
    async fn a_final_message_already_set_by_execute_is_not_overwritten() {
        let mut harness = harness(&[&verdict(json!({"action": "v", "verdict": "PASS",
             "next_state": "DONE", "reason": "critic prose"}))])
        .await;
        let mut ctx = harness.context();
        ctx.final_message = "the executor already said this".into();
        ctx.transcript.push(executed("a.md"));
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.final_message, "the executor already said this");
    }
}
