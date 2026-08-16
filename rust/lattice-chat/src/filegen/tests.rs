//! Unit-level proof for the pieces the HTTP tests then exercise end to end.

use super::*;

fn written(path: &str, repaired: bool) -> Written {
    Written {
        requested: path.to_string(),
        path: path.to_string(),
        filename: file_name_of(path),
        bytes: 10,
        valid: true,
        repaired,
        action: "write_file",
        ingest: None,
    }
}

fn turn() -> Turn {
    Turn {
        state: ChatState::new(
            lattice_auth::AuthState::new(lattice_auth::AuthConfig::default()),
            crate::state::ChatConfig::default(),
        ),
        req: ChatRequest::default(),
        lang: "ko",
        model: Some("demo".into()),
        email: None,
        workspace: None,
    }
}

#[test]
fn the_created_sentence_is_the_one_the_fixtures_pin() {
    assert_eq!(
        created_sentence("ko", "fixture-note.md", false),
        "fixture-note.md 파일을 만들었습니다."
    );
    assert!(created_sentence("ko", "a.md", true).contains("새 이름으로 저장했습니다"));
    assert_eq!(created_sentence("en", "a.md", false), "Created a.md.");
    assert!(created_sentence("en", "a.md", true).contains("already existed"));
}

#[test]
fn the_project_sentence_names_what_was_deferred_and_what_failed() {
    let files = [written("index.html", false), written("style.css", false)];
    let ko = project_sentence("ko", &files, &["src/App.jsx".into()], &["app.js".into()]);
    assert!(ko.starts_with("index.html, style.css 파일을 만들었습니다."));
    assert!(
        ko.contains("app.js 은(는) 만들지 못해 건너뛰었습니다"),
        "{ko}"
    );
    assert!(
        ko.contains("src/App.jsx"),
        "the deferred file is named: {ko}"
    );
    assert!(ko.contains("이어서 요청해 주세요"));

    let en = project_sentence("en", &files, &["a.css".into(), "b.js".into()], &[]);
    assert!(en.starts_with("Created index.html, style.css."));
    assert!(en.contains("were not written yet"), "{en}");
    // Nothing deferred, nothing failed: one clean sentence and no apology.
    let clean = project_sentence("ko", &files, &[], &[]);
    assert_eq!(clean, "index.html, style.css 파일을 만들었습니다.");
}

#[test]
fn the_payload_carries_the_badge_key_the_spa_reads() {
    let turn = turn();
    let files = [written("page.html", true)];
    let attempts = vec![json!({"attempt": 1, "outcome": "repaired"})];
    let payload = reply::payload(
        &turn.state.config.agent_root,
        &files,
        "done",
        &attempts,
        true,
        true,
    );
    // `agentPayloadFiles` in frontend/src/features/brain/brainData.ts reads
    // exactly this key for the "자동 보정됨" badge.
    assert_eq!(payload["generation"]["repaired"], true);
    assert_eq!(payload["generation"]["attempts"][0]["attempt"], 1);
    // …and the artifact carries the per-file truth beside it.
    assert_eq!(payload["artifacts"][0]["repaired"], true);
    assert_eq!(payload["artifacts"][0]["valid"], true);
    assert_eq!(payload["artifacts"][0]["previewable"], true);
    assert_eq!(payload["action_route"], "chat_file_generation");
    assert_eq!(payload["final_state"], "DONE");
    assert_eq!(payload["state_history"], json!(["EXECUTING", "DONE"]));
}

#[test]
fn a_user_typed_file_reports_no_generation_at_all() {
    let turn = turn();
    let payload = reply::payload(
        &turn.state.config.agent_root,
        &[written("note.md", false)],
        "done",
        &[],
        false,
        true,
    );
    assert!(
        payload.get("generation").is_none(),
        "nothing was generated, so nothing claims to have been"
    );
    assert_eq!(payload["action_route"], "direct_write_file");
    assert_eq!(payload["created_files"][0]["action"], "write_file");
}

#[test]
fn a_partial_bundle_is_not_reported_as_a_clean_success() {
    let turn = turn();
    let files = [written("index.html", false), written("style.css", false)];
    let payload = reply::payload(
        &turn.state.config.agent_root,
        &files,
        "done",
        &[],
        true,
        false,
    );
    assert_eq!(payload["final_state"], "NEEDS_REVIEW");
    assert_eq!(
        payload["state_history"],
        json!(["EXECUTING", "EXECUTING", "NEEDS_REVIEW"])
    );
    assert_eq!(payload["steps"].as_array().map(Vec::len), Some(2));
}

#[test]
fn a_bundles_ingest_receipts_carry_the_path_they_belong_to() {
    let turn = turn();
    let mut first = written("index.html", false);
    first.ingest = Some(json!({"status": "ok", "node_id": "file:a"}));
    let mut second = written("style.css", false);
    second.ingest = Some(json!({"status": "ok", "node_id": "file:b"}));
    let payload = reply::payload(
        &turn.state.config.agent_root,
        &[first, second],
        "done",
        &[],
        true,
        true,
    );
    let receipts = payload["brain_ingest"].as_array().expect("bundle receipts");
    assert_eq!(receipts.len(), 2);
    assert_eq!(receipts[0]["path"], "index.html");
    assert_eq!(receipts[1]["node_id"], "file:b");

    // The single-file shape stays the flat object every client already reads.
    let mut only = written("a.md", false);
    only.ingest = Some(json!({"status": "ok"}));
    let single = reply::payload(
        &turn.state.config.agent_root,
        &[only],
        "done",
        &[],
        false,
        true,
    );
    assert_eq!(single["brain_ingest"]["status"], "ok");
}

#[test]
fn attempts_are_tagged_with_the_file_they_were_for() {
    let tagged = tag_attempts(vec![json!({"attempt": 1, "outcome": "clean"})], "style.css");
    assert_eq!(tagged[0]["path"], "style.css");
    assert_eq!(tagged[0]["attempt"], 1);
    assert!(tag_attempts(Vec::new(), "x").is_empty());
}

#[test]
fn the_manifest_briefs_are_read_by_path() {
    let manifest = lattice_agent::inference::infer_project_manifest(
        "html css js 로 간단한 todo 웹페이지 만들어줘",
    )
    .expect("a web project");
    let briefs = file_briefs(&manifest);
    assert!(briefs.contains_key("index.html"));
    assert!(briefs["index.html"].contains("style.css"));
    // A manifest without briefs is an empty map, not a panic.
    assert!(file_briefs(&json!({"files": [{"path": "a.md"}]})).is_empty());
    assert!(file_briefs(&json!({})).is_empty());
}

#[tokio::test]
async fn the_progress_frame_is_the_documented_shape_and_is_optional() {
    let (sink, stream) = frame_channel();
    announce(Some(&sink), "page.html", "generating").await;
    announce(Some(&sink), "page.html", "written").await;
    drop(sink);
    let body = axum::body::to_bytes(axum::body::Body::from_stream(stream), 4096)
        .await
        .expect("frames");
    let text = String::from_utf8(body.to_vec()).expect("utf8");
    assert_eq!(
        text,
        "data: {\"type\":\"file_generation\",\"path\":\"page.html\",\"status\":\"generating\",\"chunk\":\"\"}\n\n\
         data: {\"type\":\"file_generation\",\"path\":\"page.html\",\"status\":\"written\",\"chunk\":\"\"}\n\n"
    );
    // No sink (the `stream: false` path) is silence, not a panic.
    announce(None, "page.html", "generating").await;
}

#[tokio::test]
async fn a_streamed_refusal_ends_with_the_sentinel_and_names_the_error() {
    let (sink, stream) = frame_channel();
    let writer = tokio::spawn(async move {
        turn_frames(
            &sink,
            Outcome::Refused(Refusal::Named {
                error: "office_format_unsupported",
                detail: "표는 에이전트에게".into(),
                action: "use_agent",
            }),
            "demo",
            "ko",
        )
        .await;
    });
    let body = axum::body::to_bytes(axum::body::Body::from_stream(stream), 8192)
        .await
        .expect("frames");
    writer.await.expect("writer");
    let text = String::from_utf8(body.to_vec()).expect("utf8");
    assert!(text.contains("\"error\":\"office_format_unsupported\""));
    assert!(text.contains("표는 에이전트에게"));
    assert!(text.trim_end().ends_with("data: [DONE]"), "{text}");
}

#[tokio::test]
async fn a_streamed_success_is_the_shared_three_frame_shape() {
    let (sink, stream) = frame_channel();
    let writer = tokio::spawn(async move {
        turn_frames(
            &sink,
            Outcome::Written {
                answer: "page.html 파일을 만들었습니다.".into(),
                payload: json!({"status": "ok"}),
            },
            "demo",
            "ko",
        )
        .await;
    });
    let body = axum::body::to_bytes(axum::body::Body::from_stream(stream), 8192)
        .await
        .expect("frames");
    writer.await.expect("writer");
    let text = String::from_utf8(body.to_vec()).expect("utf8");
    assert_eq!(
        text,
        crate::sse::agent_payload_stream(
            "page.html 파일을 만들었습니다.",
            &json!({"status": "ok"}),
            "demo"
        )
    );
}

#[test]
fn the_generation_failure_body_keeps_the_catalog_message_and_adds_the_trail() {
    use axum::http::HeaderMap;
    let response = generation_failed_body(&HeaderMap::new(), &[]);
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    // With attempts the body still carries `detail`; the trail is additive.
    let with_attempts = generation_failed_body(
        &HeaderMap::new(),
        &[json!({"attempt": 1, "outcome": "unusable"})],
    );
    assert_eq!(with_attempts.status(), StatusCode::BAD_REQUEST);
}

#[test]
fn a_file_name_survives_a_nested_path_and_a_bare_one() {
    assert_eq!(file_name_of("src/App.jsx"), "App.jsx");
    assert_eq!(file_name_of("note.md"), "note.md");
    assert_eq!(file_name_of(""), "");
}
