use super::*;
use crate::kernel::agentloop::harness::harness;

fn executed(path: &str) -> Value {
    json!({"state": "EXECUTING", "action": "write_file",
           "args": {"path": path}, "result": {"path": path, "bytes": 3}})
}

fn verdict(body: Value) -> String {
    body.to_string()
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
        // A 2B critic that names no next_state after a real write is DONE.
        (
            json!({"action": "verdict", "confidence": 0.9}),
            AgentState::Done,
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

/// The live gemma_e2b:S2–S5 shape, one branch earlier than the retry
/// exhaustion the same rule already covers: the tools ran, the run had an
/// answer, and two unparseable critic replies threw both away.
#[tokio::test]
async fn an_unavailable_verifier_keeps_an_answer_the_run_already_produced() {
    let mut harness = harness(&["prose", "still prose"]).await;
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "list_dir",
         "args": {"path": "."}, "result": {"items": [{"name": "a"}, {"name": "b"}]}}));
    ctx.final_message = "폴더를 확인했습니다".into();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(
        ctx.state,
        AgentState::NeedsReview,
        "an unreachable verifier is still fail-closed"
    );
    assert!(
        ctx.final_message.starts_with("폴더를 확인했습니다 (2개)"),
        "the answer leads, count restored: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("검증을 완료하지 못했습니다"),
        "and the caveat follows: {}",
        ctx.final_message
    );
    let step = ctx.transcript.last().expect("verdict step");
    assert_eq!(step["verdict"], "UNAVAILABLE", "the record is unchanged");
    assert_eq!(step["verifier_available"], false);
}

/// The other half: no answer, or no evidence, and the caveat stands alone —
/// which is the recorded behaviour every golden row pins.
#[tokio::test]
async fn an_unavailable_verifier_with_nothing_to_keep_is_the_caveat_alone() {
    for (transcript, answer) in [
        (executed("a.md"), ""),
        (
            json!({"state": "EXECUTING", "action": "final"}),
            "an answer",
        ),
    ] {
        let mut harness = harness(&["prose", "still prose"]).await;
        let mut ctx = harness.context();
        ctx.transcript.push(transcript.clone());
        ctx.final_message = answer.into();
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::NeedsReview);
        assert_eq!(
            ctx.final_message,
            "검증을 완료하지 못했습니다 — 검증 모델의 응답을 해석할 수 없었습니다. \
실행 결과를 직접 확인해 주시고, 필요하면 다시 시도해 주세요.",
            "{transcript} / {answer:?}"
        );
    }
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
async fn empty_next_state_without_evidence_is_needs_review() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "confidence": 0.9}))]).await;
    let mut ctx = harness.context();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(ctx.final_message.contains("검토가 필요합니다"));
}

#[tokio::test]
async fn empty_next_state_with_a_fail_verdict_is_needs_review() {
    let mut harness = harness(&[&verdict(json!({
        "action": "verdict", "verdict": "FAIL", "confidence": 0.9
    }))])
    .await;
    let mut ctx = harness.context();
    ctx.transcript.push(executed("a.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
}

#[tokio::test]
async fn empty_next_state_with_missing_files_is_needs_review() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "confidence": 0.9}))]).await;
    harness.request.message = "todo 앱 html css js 만들어줘".into();
    let mut ctx = harness.context();
    ctx.transcript.push(executed("index.html"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
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

#[tokio::test]
async fn a_verdict_object_without_action_still_parses_on_the_first_ask() {
    let mut harness = harness(&[&verdict(json!({
        "verdict": "PASS", "next_state": "DONE", "reason": "no action key"
    }))])
    .await;
    let mut ctx = harness.context();
    ctx.transcript.push(executed("a.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Done);
    assert_eq!(harness.runtime_llm_calls(&ctx), 1);
}

#[tokio::test]
async fn last_rung_clear_pass_is_done() {
    let mut harness = harness(&[
        "I cannot shape this as JSON.",
        "<|channel>thought\nThe file is on disk and the request is met. PASS.",
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
    assert_eq!(ctx.trace.summary()["parse_errors"], 2);
    assert_eq!(ctx.trace.summary()["repairs"]["token_verdict"], 1);
    let step = ctx.transcript.last().expect("verdict step");
    assert_eq!(step["verdict"], "PASS");
    assert_eq!(step["verdict_source"], "token");
    assert_eq!(step["verifier_available"], true);
}

#[tokio::test]
async fn last_rung_clear_fail_is_failed() {
    let mut harness = harness(&[
        "still not JSON",
        "The write did not match the request. FAIL.",
    ])
    .await;
    let mut ctx = harness.context();
    ctx.transcript.push(executed("a.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Failed);
    assert_eq!(ctx.transcript.last().expect("step")["verdict"], "FAIL");
    assert_eq!(ctx.trace.summary()["repairs"]["token_verdict"], 1);
}

#[tokio::test]
async fn last_rung_both_tokens_stays_needs_review() {
    let mut harness = harness(&[
        "prose",
        "I want to PASS this but also FAIL it — cannot decide.",
    ])
    .await;
    let mut ctx = harness.context();
    ctx.transcript.push(executed("a.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert_eq!(
        ctx.transcript.last().expect("step")["verdict"],
        "UNAVAILABLE"
    );
    assert!(ctx.trace.summary()["repairs"]
        .get("token_verdict")
        .is_none());
}

#[tokio::test]
async fn last_rung_pass_token_without_evidence_stays_needs_review() {
    let mut harness = harness(&["prose", "Looks fine. PASS."]).await;
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "final"}));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert_eq!(
        ctx.transcript.last().expect("step")["verdict"],
        "UNAVAILABLE"
    );
    assert!(ctx.trace.summary()["repairs"]
        .get("token_verdict")
        .is_none());
}

#[test]
fn last_rung_condition_is_the_documented_conjunction() {
    // The four gates: token polarity, no opposing token, evidence, coverage.
    assert!(last_rung_token_verdict("PASS", true, true).is_some());
    assert!(last_rung_token_verdict("통과했습니다", true, true).is_some());
    assert!(last_rung_token_verdict("FAIL", true, true).is_some());
    assert!(last_rung_token_verdict("실패", true, true).is_some());
    assert!(last_rung_token_verdict("불합격", true, true).is_some());
    assert!(last_rung_token_verdict("PASS and FAIL", true, true).is_none());
    assert!(last_rung_token_verdict("PASS", false, true).is_none());
    assert!(last_rung_token_verdict("PASS", true, false).is_none());
    assert!(last_rung_token_verdict("just prose", true, true).is_none());
    assert!(
        last_rung_token_verdict("PASSED", true, true).is_none(),
        "PASSED is not the PASS token"
    );
}
// ── v12.0.0 round 4: the counted answer survives every branch ──────────

/// The live `gemma_e2b:S3` body, replayed. `list_dir` succeeded twice and
/// returned two entries; the critic — which could not see the answer at all
/// — failed the run for "not reporting the file count", and this branch
/// then overwrote the answer with that complaint. The user was shown a
/// sentence saying no count was reported, over a run that had counted.
fn gemma_e2b_s3_transcript() -> Vec<Value> {
    let listing = json!({"state": "EXECUTING", "action": "list_dir",
        "args": {"path": "/ws"},
        "result": {"root": "/ws", "path": ".", "items": [
            {"name": "notes", "path": "notes", "type": "directory"},
            {"name": "README.md", "path": "README.md", "type": "file", "size": 154}]}});
    vec![
        listing.clone(),
        listing,
        json!({"state": "EXECUTING", "action": "final",
               "thoughts": "The previous step listed the files in the workspace directory."}),
    ]
}

const S3_REQUEST: &str = "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘";

/// The critic's own words from that run, verbatim.
const S3_DOUBT: &str =
    "The execution finished without reporting the file count or listing the files found.";

#[tokio::test]
async fn a_failed_verdict_never_throws_away_a_count_the_run_established() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "FAILED", "reason": S3_DOUBT,
         "corrections": ["Report the files found and the total count."],
         "confidence": 1.0}))])
    .await;
    harness.request.message = S3_REQUEST.into();
    let mut ctx = harness.context();
    ctx.transcript = gemma_e2b_s3_transcript();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");

    assert!(
        crate::kernel::transcript::contains_ascii_word(&ctx.final_message, "2")
            || ctx.final_message.contains("2개"),
        "the deliverable is the number the tool returned: {:?}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.starts_with("2개"),
        "the answer comes first and whole: {:?}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains(S3_DOUBT),
        "and the critic's doubt is kept beside it: {:?}",
        ctx.final_message
    );
    assert_eq!(
        ctx.state,
        AgentState::NeedsReview,
        "work done, confirmation missing — that is review, not failure"
    );
}

#[tokio::test]
async fn a_failed_verdict_with_nothing_counted_is_the_failure_it_always_was() {
    // The other half, and the one every recorded trajectory takes: no count
    // question, or nothing that counted anything. Byte for byte unchanged.
    let read_step = json!({"state": "EXECUTING", "action": "read_file",
                           "args": {"path": "README.md"}, "result": {"content": "hi"}});
    for (message, transcript, answered) in [
        // Not a count question at all.
        ("README.md를 읽어줘", vec![read_step.clone()], "읽었습니다"),
        // A count question over a run where nothing ran.
        (S3_REQUEST, Vec::new(), ""),
        // A count question where the **model** claimed a figure and no tool
        // counted anything: unattributed, so the FAILED verdict stands and
        // an unverified claim is not dressed in a caveat and surfaced.
        (S3_REQUEST, vec![read_step.clone()], "파일이 7개 있습니다"),
    ] {
        let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
             "next_state": "FAILED", "reason": "nothing was done"}))])
        .await;
        harness.request.message = message.into();
        let mut ctx = harness.context();
        ctx.transcript = transcript;
        ctx.final_message = answered.into();
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Failed, "{message} / {answered}");
        assert_eq!(
            ctx.final_message, "nothing was done",
            "{message} / {answered}"
        );
    }
}

#[tokio::test]
async fn the_json_critic_is_shown_the_answer_the_run_will_give() {
    // The guided closed question has carried the answer since it existed;
    // the JSON critic was judging a count question with no count in front
    // of it, which is what produced the live FAIL above.
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE", "reason": "the count was reported"}))])
    .await;
    harness.request.message = S3_REQUEST.into();
    let mut ctx = harness.context();
    ctx.transcript = gemma_e2b_s3_transcript();
    ctx.final_message = "작업을 완료했습니다. (2개)".into();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    let context = harness
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .find(|call| call["seam"] == json!("llm"))
        .expect("the critic turn")["body"]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(
        context.contains("The answer this run will give the user:\n작업을 완료했습니다. (2개)"),
        "{context}"
    );
    // A run that has phrased no answer yet renders no section at all.
    let mut silent = crate::kernel::agentloop::harness::harness(&[&verdict(
        json!({"action": "verdict", "verdict": "PASS",
               "next_state": "DONE", "reason": "ok"}),
    )])
    .await;
    let mut ctx = silent.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "write_file",
                     "args": {"path": "a.md"}, "result": {"path": "a.md"}}));
    silent
        .runtime
        .verify(&mut ctx, &silent.request)
        .await
        .expect("verify");
    let context = silent
        .worker
        .calls
        .lock()
        .expect("lock")
        .iter()
        .find(|call| call["seam"] == json!("llm"))
        .expect("the critic turn")["body"]["context"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    assert!(
        !context.contains("The answer this run will give"),
        "{context}"
    );
}

#[tokio::test]
async fn a_verdict_over_proven_created_files_drops_bare_negation_and_restores_artifact_facts() {
    // Live qwen05b_S1_a1 shape: write_file(notes/hello.md, 14B) + "I did nothing." with a PASS verdict
    let mut pass_harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE", "reason": "the work was done"}))])
    .await;
    pass_harness.request.message = "notes/hello.md에 인사말을 저장해줘".into();
    let mut ctx = pass_harness.context();
    ctx.transcript = vec![
        json!({
            "state": "EXECUTING",
            "action": "write_file",
            "args": {"path": "notes/hello.md", "content": "notes/hello.md"},
            "result": {"path": "notes/hello.md", "bytes": 14}
        }),
        json!({
            "state": "EXECUTING",
            "action": "final",
            "thoughts": "guided: chose final"
        }),
    ];
    ctx.final_message = "I did nothing.".into();
    pass_harness
        .runtime
        .verify(&mut ctx, &pass_harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Done);
    assert_eq!(
        ctx.final_message,
        "notes/hello.md 파일을 작성했습니다 (14B)."
    );
}
