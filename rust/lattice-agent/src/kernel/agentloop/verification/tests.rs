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
async fn a_count_question_that_never_reached_final_still_reports_the_count() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE",
         "reason": "The transcript shows the requested files were found."}))])
    .await;
    harness.request.message = "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 \
파일 개수를 알려줘"
        .into();
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "list_dir",
         "args": {"path": "."},
         "result": {"items": [{"name": "notes"}, {"name": "README.md"}]}}));
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "plan",
         "error": "Unknown action: plan"}));
    assert!(ctx.final_message.is_empty(), "no `final` step ran");
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Done);
    assert_eq!(
        ctx.final_message, "The transcript shows the requested files were found. (2개)",
        "the count is appended to the answer, never substituted for it"
    );
}

#[tokio::test]
async fn a_count_question_no_tool_ever_answered_is_needs_review() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE", "reason": "확인했습니다"}))])
    .await;
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript.push(executed("note.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("확인했습니다"),
        "the run's own answer leads: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("개수를 확인하지 못했습니다"),
        "and the reason is named: {}",
        ctx.final_message
    );
}

#[tokio::test]
async fn a_count_gate_with_no_answer_at_all_is_the_caveat_alone() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE", "reason": ""}))])
    .await;
    harness.request.message = "파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript.push(executed("note.md"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("요청하신 개수를"),
        "no leading blank line: {:?}",
        ctx.final_message
    );
}

#[tokio::test]
async fn retry_exhaustion_over_a_count_question_keeps_the_count_as_the_answer() {
    let guided = verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "근거가 부족합니다",
         "verdict_source": "guided", "confidence": 0.6}));
    let mut harness = harness(&[&guided]).await;
    harness.request.message = "이 폴더의 파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.retry_count = harness.request.max_retry;
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "list_dir",
         "args": {"path": "."}, "result": {"items": [{"name": "a"}, {"name": "b"}]}}));
    assert!(ctx.final_message.is_empty(), "the run never said anything");
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("2개"),
        "the counted fact leads: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("근거가 부족합니다"),
        "and the doubt is named: {}",
        ctx.final_message
    );
}

#[tokio::test]
async fn a_word_that_merely_contains_count_is_not_a_count_question() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "next_state": "DONE", "reason": "the account page was written"}))])
    .await;
    harness.request.message = "create an account page".into();
    let mut ctx = harness.context();
    ctx.transcript.push(executed("account.html"));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Done);
    assert_eq!(ctx.final_message, "the account page was written");
}

#[tokio::test]
async fn an_incoherent_verdict_keeps_the_answer_and_the_figure_a_tool_returned() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "DONE", "reason": "the pattern was not found"}))])
    .await;
    harness.request.message = "워크스페이스에서 LatticeAI를 mcp.grep으로 찾고 \
개수를 알려줘"
        .into();
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "mcp.grep",
         "args": {"pattern": "LatticeAI"},
         "result": {"matches": [{"path": "README.md"}], "files_scanned": 4}}));
    ctx.final_message = "LatticeAI를 찾았습니다".into();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("LatticeAI를 찾았습니다"),
        "the run's own answer leads: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("(1개)"),
        "and carries the figure the tool returned: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("검증 결과가 일관되지 않아"),
        "with the caveat after it: {}",
        ctx.final_message
    );
}

#[tokio::test]
async fn an_empty_next_state_still_reports_what_the_search_counted() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "", "reason": "오류가 있었습니다"}))])
    .await;
    harness.request.message = "LatticeAI라는 단어를 찾고 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "mcp.grep",
         "args": {"pattern": "LatticeAI"}, "result": {"matches": [], "files_scanned": 4}}));
    assert!(ctx.final_message.is_empty(), "no `final` step ran");
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("0개"),
        "the searched-for count leads: {}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("검증자가 다음 상태를 비운 채"),
        "with the caveat after it: {}",
        ctx.final_message
    );
}

#[tokio::test]
async fn an_incoherent_verdict_with_nothing_to_report_is_the_caveat_alone() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "DONE", "reason": "because"}))])
    .await;
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
        "검증 결과가 일관되지 않아 완료로 처리하지 않았습니다. 실행 결과를 직접 확인해 주세요."
    );
}

#[tokio::test]
async fn an_implied_done_over_a_count_question_carries_the_count_too() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "PASS",
         "reason": "폴더를 확인했습니다"}))])
    .await;
    harness.request.message = "파일 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "list_dir",
         "args": {"path": "."}, "result": {"items": [1, 2, 3]}}));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::Done);
    assert_eq!(ctx.final_message, "폴더를 확인했습니다 (3개)");
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
async fn retry_exhaustion_keeps_an_answer_the_run_already_produced() {
    let retry = verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "the count was not reported",
         "verdict_source": "guided"}));
    let mut harness = harness(&[&retry]).await;
    let mut ctx = harness.context();
    ctx.retry_count = harness.request.max_retry;
    ctx.transcript.push(executed("a.md"));
    ctx.final_message = "이 폴더에는 파일이 2개 있습니다.".into();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(
        ctx.state,
        AgentState::NeedsReview,
        "work was done and could not be confirmed — that is review, not failure"
    );
    assert!(
        ctx.final_message
            .starts_with("이 폴더에는 파일이 2개 있습니다."),
        "{}",
        ctx.final_message
    );
    assert!(ctx.final_message.contains("the count was not reported"));
    assert!(!ctx.final_message.contains("처리 중 문제가 발생했습니다"));
}

#[tokio::test]
async fn retry_exhaustion_with_no_evidence_nothing_delivered_or_a_real_verdict_fails_plainly() {
    let guided = json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "no", "verdict_source": "guided"});
    let parsed = json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "give up"});
    let opaque = json!({"state": "EXECUTING", "action": "todo_write", "result": {}});
    for (body, transcript, message) in [
        (
            &guided,
            json!({"state": "EXECUTING", "action": "final"}),
            "an answer",
        ),
        (&guided, opaque, ""),
        (&parsed, executed("a.md"), "done"),
    ] {
        let mut harness = harness(&[&verdict(body.clone())]).await;
        let mut ctx = harness.context();
        ctx.retry_count = harness.request.max_retry;
        ctx.transcript.push(transcript.clone());
        ctx.final_message = message.into();
        harness
            .runtime
            .verify(&mut ctx, &harness.request)
            .await
            .expect("verify");
        assert_eq!(ctx.state, AgentState::Failed, "{body} over {transcript}");
        assert_eq!(
            ctx.final_message,
            "처리 중 문제가 발생했습니다. 다시 시도해 주세요."
        );
    }
}

#[tokio::test]
async fn a_delivered_file_outranks_a_weak_critic_that_never_got_an_answer() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "저는 Lattice AI입니다.",
         "verdict_source": "guided"}))])
    .await;
    let mut ctx = harness.context();
    ctx.retry_count = harness.request.max_retry;
    ctx.transcript.push(executed("notes/hello.md"));
    ctx.final_message = String::new();
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(
        ctx.state,
        AgentState::NeedsReview,
        "the file exists — that is review, not failure"
    );
    assert!(
        ctx.final_message
            .starts_with("notes/hello.md 파일을 저장했습니다."),
        "{}",
        ctx.final_message
    );
    assert!(
        ctx.final_message.contains("검증에서 완료로 확인되지 않아"),
        "the doubt is still reported: {}",
        ctx.final_message
    );
    assert!(!ctx.final_message.contains("처리 중 문제가 발생했습니다"));
}

#[tokio::test]
async fn a_search_that_found_something_is_reported_rather_than_apologised_for() {
    let mut harness = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "0 matches", "verdict_source": "guided"}))])
    .await;
    harness.request.message = "LatticeAI를 mcp.grep으로 찾아주고 개수를 알려줘".into();
    let mut ctx = harness.context();
    ctx.retry_count = harness.request.max_retry;
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "mcp.grep",
         "args": {"pattern": "LatticeAI"},
         "result": {"matches": [{"line": 1}], "files_scanned": 4}}));
    harness
        .runtime
        .verify(&mut ctx, &harness.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("1개"),
        "{}",
        ctx.final_message
    );
    assert!(ctx.final_message.contains("검증에서 완료로 확인되지 않아"));
    assert!(!ctx.final_message.contains("처리 중 문제가 발생했습니다"));
}

#[tokio::test]
async fn a_finding_is_the_answer_when_the_request_asked_for_no_number() {
    let mut plain = harness(&[&verdict(json!({"action": "verdict", "verdict": "FAIL",
         "next_state": "EXECUTING", "reason": "0 matches", "verdict_source": "guided"}))])
    .await;
    plain.request.message = "LatticeAI를 mcp.grep으로 찾아줘".into();
    let mut ctx = plain.context();
    ctx.retry_count = plain.request.max_retry;
    ctx.transcript
        .push(json!({"state": "EXECUTING", "action": "mcp.grep",
         "args": {"pattern": "LatticeAI"},
         "result": {"matches": [{"line": 1}], "files_scanned": 4}}));
    plain
        .runtime
        .verify(&mut ctx, &plain.request)
        .await
        .expect("verify");
    assert_eq!(ctx.state, AgentState::NeedsReview);
    assert!(
        ctx.final_message.starts_with("mcp.grep 실행 결과:"),
        "{}",
        ctx.final_message
    );
    assert!(!ctx.final_message.contains("처리 중 문제가 발생했습니다"));
}

#[test]
fn the_unconfirmed_answer_leads_with_the_answer() {
    let with = unconfirmed_answer("  2개입니다.  ", " 근거 부족 ");
    assert!(with.starts_with("2개입니다.\n\n"));
    assert!(with.contains("(근거 부족)"));
    let without = unconfirmed_answer("2개입니다.", "   ");
    assert!(without.starts_with("2개입니다.\n\n"));
    assert!(!without.contains('('));
}
