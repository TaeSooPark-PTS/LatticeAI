use super::*;

#[test]
fn language_detection_counts_characters() {
    assert_eq!(detect_language("무엇을 기억하고 있나요?"), "ko");
    assert_eq!(detect_language("what do you remember?"), "en");
    assert_eq!(detect_language(""), "en");
    assert_eq!(detect_language("aaaaaaaaaaaaaaaaaa가"), "ko");
    assert_eq!(detect_language("aaaaaaaaaaaaaaaaaaaa가"), "en");
    assert_eq!(
        language_hint("ko"),
        "Respond in Korean (한국어로 답변하세요)."
    );
    assert_eq!(language_hint("de"), "Respond in English.");
}

#[test]
fn the_network_intent_matches_phrases_and_both_patterns() {
    assert!(is_network_status_request("네트워크 상태 알려줘"));
    assert!(is_network_status_request("IPCONFIG"));
    assert!(is_network_status_request("내 ip 주소"));
    assert!(is_network_status_request("아이피 확인"));
    assert!(!is_network_status_request("무엇을 기억하고 있나요?"));
    assert!(!is_network_status_request(""));
}

#[test]
fn the_current_url_intent_matches_phrases_and_both_patterns() {
    assert!(is_current_url_request("현재 url 알려줘"));
    assert!(is_current_url_request("이 페이지 주소 알려줘"));
    assert!(is_current_url_request("링크 보여줘"));
    assert!(is_current_url_request("Page URL"));
    assert!(!is_current_url_request("hello"));
}

#[test]
fn the_clear_command_takes_exactly_two_spellings() {
    assert!(is_clear_command("/clear"));
    assert!(is_clear_command("  /CLEAR_ALL \n"));
    assert!(!is_clear_command("/clearall"));
    assert!(!is_clear_command(""));
}

#[test]
fn file_intent_needs_an_action_and_a_target_or_a_file_word() {
    assert!(is_file_action_request("fixture-note.md 파일 만들어줘"));
    assert!(is_file_action_request("create report.txt"));
    assert!(is_file_action_request("html 페이지 만들어줘"));
    assert!(!is_file_action_request("report.txt"), "no action verb");
    assert!(!is_file_action_request("how do i create a file?"));
    assert!(
        is_file_action_request("how do i actually create report.txt"),
        "the override phrase re-opens the gate"
    );
    assert!(!is_file_action_request("   "));
    assert!(
        !is_file_action_request("만들어줘"),
        "no target, no file word"
    );
}

#[test]
fn the_target_regex_keeps_single_token_paths_and_trims_punctuation() {
    assert_eq!(
        file_action_target("fixture-note.md 파일 만들어줘"),
        Some("fixture-note.md".into())
    );
    assert_eq!(
        file_action_target("create `docs/notes.md`."),
        Some("docs/notes.md".into())
    );
    assert_eq!(file_action_target("no target here"), None);
}

#[test]
fn inline_content_needs_an_explicit_binder() {
    assert_eq!(
        inline_file_action_content("fixture-note.md 파일 만들어줘. 내용: Hello Lattice"),
        Some("Hello Lattice".into())
    );
    assert_eq!(
        inline_file_action_content("내용은 두 줄\n입니다"),
        Some("두 줄\n입니다".into())
    );
    assert_eq!(
        inline_file_action_content("report.md with the content \"a b\""),
        Some("a b".into())
    );
    assert_eq!(inline_file_action_content("body is  x "), Some("x".into()));
    assert_eq!(
        inline_file_action_content("create a text file report.txt"),
        None,
        "an ambiguous word without a binder captures nothing"
    );
}

#[test]
fn generated_content_loses_its_fence() {
    assert_eq!(strip_generated_file_content("```md\n# hi\n```"), "# hi");
    assert_eq!(strip_generated_file_content("```\nplain\n```"), "plain");
    assert_eq!(strip_generated_file_content("  no fence  "), "no fence");
}

#[test]
fn network_status_formats_every_optional_block() {
    let info: Map<String, Value> = serde_json::from_value(json!({
        "local_ip": "10.0.0.2", "public_ip": null, "hostname": "box",
        "local_ips": {"en0": "10.0.0.2"}, "note": "partial",
    }))
    .unwrap();
    assert_eq!(
        format_network_status(&info),
        "내부 IP: 10.0.0.2\n외부 IP: 확인 안 됨\n호스트명: box\n\n인터페이스:\n- en0: 10.0.0.2\n\npartial"
    );
    let bare: Map<String, Value> = Map::new();
    assert_eq!(
        format_network_status(&bare),
        "내부 IP: 확인 안 됨\n외부 IP: 확인 안 됨\n호스트명: 확인 안 됨"
    );
}

#[test]
fn grounding_reports_no_context_when_the_trace_is_empty() {
    let verdict = assess_answer_grounding("answer", None, None);
    assert_eq!(verdict["status"], "no_context");
    assert_eq!(verdict["reason"], "검색된 출처가 없습니다");
    let verdict = assess_answer_grounding("answer", None, Some(&json!({"mode": "hybrid"})));
    assert_eq!(verdict["reason"], "출처 후보를 답변에 연결하지 못했습니다");
}

#[test]
fn grounding_binds_by_overlap_and_by_explicit_title() {
    let trace = json!({
        "graph_nodes": [
            {"id": "n1", "title": "리트리벌 가중치", "summary": "가중치 정리 문서",
             "metadata": {"filename": "weights.md"}},
            {"id": "n1", "title": "duplicate"},
            {"not": "an object"},
            {"title": "no id"},
        ],
        "source_files": [
            {"node_id": "n1", "node_title": "already seen"},
            {"source": "orphan.md", "node_title": "Orphan Document"},
            "not an object",
        ],
    });
    let verdict = assess_answer_grounding(
        "리트리벌 가중치 문서를 정리했습니다",
        Some(&trace),
        Some(&json!({"mode": "hybrid"})),
    );
    assert_eq!(verdict["status"], "supported");
    assert_eq!(verdict["source_ids"][0], "n1");
    assert_eq!(verdict["cited"][0]["explicit"], true);
    assert!(
        verdict["claim_verification"].is_object(),
        "claim_verification telemetry should be attached"
    );
    assert!(
        verdict["claim_verification"]["claim_support_ratio"].is_number(),
        "claim_support_ratio should be numeric"
    );

    let verdict = assess_answer_grounding("완전히 다른 이야기", Some(&trace), None);
    assert_eq!(verdict["status"], "unsupported");
    assert_eq!(verdict["overlap"], 0.0);
    assert!(verdict["claim_verification"].is_object());

    let verdict = assess_answer_grounding("", Some(&trace), None);
    assert_eq!(verdict["status"], "unsupported");
    assert_eq!(verdict["reason"], "답변이 비어 있습니다");
}

#[test]
fn grounding_skips_sources_with_no_usable_tokens() {
    let trace = json!({"graph_nodes": [{"id": "n", "title": "", "summary": ""}]});
    let verdict = assess_answer_grounding("something", Some(&trace), None);
    assert_eq!(verdict["status"], "unsupported");
    assert!(
        grounding_tokens("the and for").is_empty(),
        "stop tokens drop"
    );
    assert_eq!(grounding_tokens("your You"), Vec::<String>::new());
}
