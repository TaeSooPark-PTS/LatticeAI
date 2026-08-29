//! VERIFY — the critic's verdict, and the facts that outrank it.
//!
//! A port of `latticeai.core.agent.verification`. Fail-closed by construction:
//! a critic whose output cannot be parsed (after **one** strict repair retry at
//! temperature 0.0) never fabricates a PASS; a PASS over a transcript with no
//! execution evidence is not a completion; and a PASS that leaves a *requested
//! file* unwritten is a fact, not a judgement, so it is enforced rather than
//! merely reported back to the critic.

use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

use super::{RunRequest, Runtime};
use crate::kernel::state::{AgentRunContext, AgentState};
use crate::kernel::transcript::{
    answer_owes_a_count, answer_owes_a_summary, artifact_checklist, attributed_count,
    attributed_summary, complete_counted_and_summarized, complete_created_files,
    contains_ascii_word, delivered_answer, format_artifact_checklist, format_requirement_coverage,
    request_asks_for_a_count, request_asks_for_a_summary, requirement_coverage, truncate_strings,
};
use crate::parse::action::extract_verdict_details;
use crate::parse::pystr::py_str;
use crate::surface::worker::{Completion, WorkerError};

/// The one strict re-ask, verbatim. Same contract the executor prompt holds
/// this model to: one object, a worked example, no channel tags.
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

/// The only place in the loop that sends stop strings (v11.9.0).
///
/// The strict re-ask is the last *JSON* chance: after it, only the token
/// last-rung can still recover, and anything ambiguous is `NEEDS_REVIEW`.
/// It is also the one call whose reply is a short, closed object — so a
/// model that keeps talking after `}` is spending the retry on nothing.
/// Everywhere else stop strings are off, because a `content` field routinely
/// contains every sequence one would want to stop on. `\n\n` is deliberately
/// **not** here for the same reason a verdict `reason` may wrap.
const VERDICT_STOP: [&str; 2] = ["\n```", "\nUser:"];

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
        // v12.0.0: under the guided dial the critic is asked a closed question
        // instead of a JSON object, for exactly the reason the executor is. A
        // reviewer too small to emit a verdict object is still perfectly able
        // to answer "PASS or FAIL"; the previous behaviour was two unparseable
        // replies and a fail-closed `NEEDS_REVIEW` on a run that had in fact
        // written the file. When the closed question also fails to produce a
        // word, the ordinary chain below runs unchanged — this rung adds an
        // answer, it never removes one, and every gate the verdict then passes
        // (evidence, requirement coverage, the PASS-without-evidence refusal)
        // is the same gate a JSON verdict passes.
        let guided_verdict = if self.profile(model_id.as_deref()).decomposed {
            self.guided_verdict(ctx, req, model_id.as_deref()).await?
        } else {
            None
        };
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
        // **The critic is shown the answer the run will give** (v12.0.0) — on
        // the JSON dials too. The guided closed question has carried it since
        // it existed, for a stated reason that is not about dials: the
        // deliverable of a question *is* the answer, and a critic asked whether
        // `파일 개수를 알려줘` was carried out over a transcript whose `final`
        // step records only `thoughts` has been asked about a count while being
        // shown none. A live gemma-4-e2b on `compact` answered exactly that —
        // "The execution finished without reporting the file count" — over a
        // run whose answer already carried `(2개)`. An empty answer renders no
        // section, so a run that has not phrased one yet is judged on the
        // transcript exactly as before.
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

    /// Ask the critic for a verdict object, with its one strict retry and its
    /// plain-text last rung.
    ///
    /// Lifted out of [`Runtime::verify`] unchanged in v12.0.0 so the guided
    /// closed question can stand *beside* it rather than inside it. Every
    /// completion, temperature, stop list and trace event is the one this chain
    /// always sent, in the order it always sent them.
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
                // One strict repair retry — re-ask for the exact wire format
                // instead of fabricating a verdict.
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
                        // Last rung: an unambiguous PASS/FAIL token in the
                        // retry's plain text, only when evidence and coverage
                        // already stand on their own. Anything looser stays
                        // unparsed so the fail-closed path below fires.
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

    /// Turn a verdict (however it was obtained) into the run's next state.
    ///
    /// Lifted out of [`Runtime::verify`] unchanged in v12.0.0. Every gate here
    /// is deterministic and applies to every verdict source equally — a guided
    /// PASS with no execution evidence is refused exactly as a JSON one is, so
    /// the cheaper question can never buy a weaker completion.
    fn settle_verdict(
        &mut self,
        ctx: &mut AgentRunContext,
        req: &RunRequest,
        mut verdict: Option<Map<String, Value>>,
        coverage: &Value,
    ) -> Result<(), WorkerError> {
        let has_evidence = Self::has_execution_evidence(ctx);

        let Some(mut verdict) = verdict.take() else {
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
            // **An answer the run already produced outranks the apology here
            // too** (v12.0.0). This is the same defect the retry-exhaustion
            // branch below was fixed for, one branch earlier: a run that
            // dispatched its tools, got real results and wrote a real answer,
            // and then could not get two parseable words out of a critic, was
            // handed "검증을 완료하지 못했습니다" and nothing else. Four live
            // runs ended exactly that way over work that was on the transcript.
            //
            // Nothing about the *state* changes — an unreachable verifier is
            // `NEEDS_REVIEW`, which is the fail-closed answer and stays it. Only
            // the words change, and only when there are words to keep: the
            // answer leads, the caveat follows. `complete_a_count` runs first
            // for the same reason it runs on every other settling path, and is
            // a no-op for every request that did not ask *how many*.
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
        // A weak critic copies the worked example field for field, placeholder
        // included. `<one line: …>` is not a reason and must never be shown as
        // one, so it is blanked here rather than rendered.
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
        // Where the verdict came from, when it did not come from a parsed
        // object: `token` (the plain-text last rung) or `guided` (the closed
        // question). An ordinary parsed verdict carries no source and stamps
        // none, so a reader can tell the three apart on the step itself.
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
            if Self::settle_answer(ctx, req, &self.deps.file_create_actions) {
                return Ok(());
            }
            ctx.state = AgentState::Done;
        } else if next_state == "ROLLBACK" {
            ctx.state = AgentState::Rollback;
        } else if next_state == "EXECUTING" {
            if ctx.retry_count >= req.max_retry {
                // **An answer the run already produced outranks the apology**
                // (v12.0.0). Until now this branch overwrote `final_message`
                // unconditionally, so a run that dispatched its tool, got a real
                // result and wrote a real answer — and then failed to convince a
                // small critic three times — handed the user
                // "처리 중 문제가 발생했습니다" and nothing else. The work, the
                // evidence and the answer were all on the transcript and none of
                // them reached the caller.
                //
                // So the apology is what is said when there is nothing else to
                // say. When there *is* an answer, the transcript has real
                // execution evidence behind it, **and the verdict that asked
                // for another attempt did not come from a parsed verdict
                // object**, the answer survives, the state is NEEDS_REVIEW
                // rather than FAILED — the honest name for "we did the work and
                // could not confirm it" — and the reason is appended so the
                // user knows what was doubted.
                //
                // That third condition is the load-bearing one. A critic that
                // emitted a real verdict object is the strongest instrument the
                // loop has, and three of those saying "try again" is a failure
                // worth reporting as one. The guided closed question is the
                // weakest — it is recorded at confidence 0.6 for exactly that
                // reason — and three of *those* do not outrank an answer the run
                // demonstrably produced. Keying on the instrument rather than on
                // the model is what makes this the same rule for every model,
                // and it is why every recorded trajectory still replays
                // byte-identically: all of them are parsed-object verdicts.
                //
                // The answer is completed *before* it is weighed (v12.0.0), by
                // the same rule the DONE paths run: a run that counted two
                // files and never said so has an answer, and reaching for the
                // apology because the sentence was missing is the same defect
                // one branch further down. `complete_a_count` is a no-op for
                // every request that did not ask *how many*.
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
                // **And a run that delivered has an answer even when it never
                // said one** (v12.0.0). The rule above keys on
                // `ctx.final_message`, which only the `final` action writes — so
                // a run stopped by the loop guard before it reached `final` had
                // nothing to weigh, fell to the `else`, and reported FAILED over
                // a file that was on disk. Two live 2B runs ended exactly that
                // way. The sentence is built from the transcript's own
                // file-create results ([`delivered_answer`]) and only where the
                // deterministic facts already outrank the critic: real
                // execution evidence, and every file the request declared
                // written. It claims nothing the run did not do.
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
            // Contradictory verdict: DONE without a PASS. This is a non-success
            // the user must review, not a loose success path.
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
            // Compact 2B critics often omit next_state. Implied DONE only when
            // every requested file is verifiably written *and* the critic did
            // not name a negative verdict. Anything looser stays NEEDS_REVIEW.
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
            // **A counted fact or created artifact this run established is an answer** (v12.0.0, F5),
            // and this branch used to throw it away. `next_state: FAILED` lands
            // here, and the assignment below overwrote `ctx.final_message`
            // outright — so a live model that ran tools successfully had its
            // answer wiped and was shown the critic's complaint.
            //
            // The restoration runs [`complete_created_files`] and [`complete_a_count`],
            // so it is the same rule and the same attribution on every dial.
            // Where that leaves an answer, the run has delivered what was asked for and
            // could not get it confirmed, which is `NEEDS_REVIEW` and not
            // `FAILED`; the critic's own words become the doubt beside it. Where
            // it does not — no count question, nothing counted, no files, no evidence —
            // every byte of this branch is what it always was, which is every
            // recorded trajectory that reaches it.
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

    /// The last thing done to an answer before the run is called complete.
    ///
    /// Returns `true` when it took the run out of the success path, in which
    /// case the caller must stop — `ctx.state` is already `NEEDS_REVIEW`.
    ///
    /// Both halves concern the same fact and neither reads the model (v12.0.0).
    /// A count question's deliverable is the number, and until now the number
    /// was only ever restored inside the executor's `final` branch — a run that
    /// stalled before `final` had its answer written later, from the critic's
    /// own reason, and that text never passed the rule. So:
    ///
    /// 1. [`complete_created_files`] and [`complete_a_count`] run here, on
    ///    whatever the answer turned out to be and whichever step produced it.
    /// 2. If the answer *still* carries no figure on a count question, no tool
    ///    ever counted anything, and this is a PASS over a deliverable that was
    ///    never delivered. That is deterministic — the same standing a missing
    ///    requested file has — so it is enforced as `NEEDS_REVIEW` rather than
    ///    argued with a critic that already said PASS at confidence 0.9.
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
        false
    }

    /// A non-success the user must review, said **without discarding the run's
    /// own answer** (v12.0.0).
    ///
    /// Three branches settle a run this way — a count nobody reported, a DONE
    /// with no PASS, and a verdict with no `next_state` over evidence too thin
    /// to imply one — and two of them used to *assign* `ctx.final_message`.
    /// Assignment is the defect. Two live 0.5B/2B cells dispatched `mcp.grep`,
    /// got a result, and were shown a bare caveat carrying no count and no
    /// mention of the search: the tool ran, the figure was on the transcript,
    /// and neither reached the caller. The apology was the only thing that did.
    ///
    /// So the shape is [`unconfirmed_answer`]'s, and the completion is
    /// [`complete_created_files`] and [`complete_a_count`]: artifact facts and
    /// counted figures are restored first, then the answer goes first and whole
    /// with the caveat one line after it.
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
        // The run's own words first and whole, the caveat after — and nothing
        // but the caveat when the run never produced any words at all.
        let said = ctx.final_message.trim();
        ctx.final_message = if said.is_empty() {
            caveat.to_string()
        } else {
            format!("{said}\n\n{caveat}")
        };
        ctx.state = AgentState::NeedsReview;
    }
}

/// The run's own answer, plus the note that verification never confirmed it.
///
/// Kept as a function so the shape is asserted once: the answer comes **first**
/// and whole — it is the only thing in the string the user asked for — and the
/// caveat is one line after it, naming the critic's reason when there was one.
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

/// Last-rung condition, verbatim:
///
/// if the reply's plain text contains an unambiguous verdict token
/// (PASS/통과 with no FAIL/실패/불합격 tokens anywhere, or vice versa)
/// AND execution evidence exists AND requirement_coverage is complete
/// → map to that verdict; anything ambiguous stays NEEDS_REVIEW
/// exactly as today.
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

/// Empty `next_state` becomes DONE only when every requested file was written,
/// the transcript has a real tool result, and the critic did not name a
/// negative verdict. A malformed critic with no evidence stays NEEDS_REVIEW.
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
