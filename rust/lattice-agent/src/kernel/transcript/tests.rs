use super::*;

fn actions() -> BTreeSet<String> {
    ["write_file", "edit_file", "create_pdf"]
        .into_iter()
        .map(String::from)
        .collect()
}

fn executing(action: &str, path: &str) -> Value {
    json!({
        "state": "EXECUTING", "action": action,
        "args": {"path": path}, "result": {"path": path, "bytes": 12},
    })
}

#[test]
fn truncation_counts_characters_and_names_what_it_dropped() {
    let value = json!({"body": "가".repeat(10), "keep": "short", "n": [1, "나".repeat(4)]});
    let capped = truncate_strings(&value, 5);
    assert_eq!(capped["body"], "가가가가가…[+5 chars]");
    assert_eq!(capped["keep"], "short");
    assert_eq!(capped["n"], json!([1, "나나나나"]));
}

#[test]
fn a_short_transcript_is_only_truncated() {
    let steps = vec![
        executing("write_file", "a.md"),
        executing("edit_file", "b.md"),
    ];
    let compact = compact_transcript(&steps, 8, 700);
    assert_eq!(compact, steps);
}

#[test]
fn an_older_step_becomes_one_line_and_none_disappear() {
    let mut steps: Vec<Value> = (0..5)
        .map(|index| executing("write_file", &format!("f{index}.md")))
        .collect();
    steps[1] = json!({"state": "EXECUTING", "action": "run_command", "error": "boom"});
    let compact = compact_transcript(&steps, 2, 700);
    assert_eq!(compact.len(), 1 + 3 + 2, "header + summaries + window");
    assert_eq!(compact[0]["summarized_older_steps"], 3);
    assert_eq!(
        compact[1],
        json!({"state": "EXECUTING", "action": "write_file", "ok": true, "path": "f0.md"})
    );
    assert_eq!(
        compact[2],
        json!({"state": "EXECUTING", "action": "run_command", "error": "boom"})
    );
    assert_eq!(compact[4], steps[3]);
}

#[test]
fn files_written_is_ordered_unique_and_only_counts_results() {
    let steps = vec![
        executing("write_file", "a.md"),
        json!({"state": "EXECUTING", "action": "write_file", "args": {"path": "blocked.md"},
               "error": "BLOCKED"}),
        executing("write_file", "a.md"),
        executing("edit_file", "b.md"),
        json!({"state": "VERIFYING", "action": "write_file", "result": {"path": "c.md"}}),
        json!({"state": "EXECUTING", "action": "read_file", "result": {"path": "d.md"}}),
    ];
    assert_eq!(files_written(&steps, &actions()), vec!["a.md", "b.md"]);
}

#[test]
fn the_checklist_carries_the_honesty_flags_per_file() {
    let mut repaired = executing("write_file", "a.md");
    repaired["content_sanitize"] = json!({"sanitized": true, "repaired": true});
    let steps = vec![repaired, executing("edit_file", "b.md")];
    assert_eq!(
        artifact_checklist(&steps, &actions()),
        json!([
            {"path": "a.md", "sanitized": true, "repaired": true},
            {"path": "b.md", "sanitized": false, "repaired": false},
        ])
        .as_array()
        .expect("rows")
        .clone()
    );
    let rendered = format_artifact_checklist(&artifact_checklist(&steps, &actions()));
    assert!(rendered.contains("- a.md: auto-REPAIRED scaffold"));
    assert!(rendered.contains("- b.md: written as produced"));
}

#[test]
fn coverage_is_complete_only_when_every_declared_file_exists() {
    let request = "todo 앱 html css js 만들어줘";
    let none = requirement_coverage(request, &[], &actions());
    assert_eq!(none["complete"], false);
    assert_eq!(
        none["missing_files"],
        json!(["index.html", "style.css", "app.js"])
    );

    let steps = vec![
        executing("write_file", "index.html"),
        executing("write_file", "sub/STYLE.CSS"),
        executing("write_file", "app.js"),
    ];
    let full = requirement_coverage(request, &steps, &actions());
    assert_eq!(full["complete"], true, "basenames match case-insensitively");
    assert_eq!(full["missing_files"], json!([]));
}

#[test]
fn a_request_with_no_manifest_is_complete_by_construction() {
    let coverage = requirement_coverage("무슨 파일이 있어?", &[], &actions());
    assert_eq!(coverage["complete"], true);
    assert_eq!(coverage["files"]["declared"], json!([]));
    assert_eq!(format_requirement_coverage(&coverage), "");
}

#[test]
fn a_file_the_request_named_as_its_destination_is_declared() {
    // The gate that used to be structurally unreachable: no manifest is
    // inferred once the message names a filename, so `complete` was true
    // over a run that wrote nothing at all.
    let request = "README.md 첫 문단을 요약해 notes/summary.md로 저장해줘";
    let none = requirement_coverage(request, &[], &actions());
    assert_eq!(none["complete"], false);
    assert_eq!(none["missing_files"], json!(["notes/summary.md"]));
    assert_eq!(
        none["files"]["declared"],
        json!(["notes/summary.md"]),
        "the file the run only reads is not an output"
    );

    let written = vec![executing("write_file", "notes/summary.md")];
    let full = requirement_coverage(request, &written, &actions());
    assert_eq!(full["complete"], true);
    assert_eq!(full["missing_files"], json!([]));
}

#[test]
fn a_request_that_marks_no_destination_declares_nothing_new() {
    // The conservative half — and what keeps the frozen coverage rows and
    // the compact trajectory byte-identical.
    for request in [
        "make a note",
        "index.html 소개 페이지 만들어줘",
        "README.md에서 첫 문단을 읽어서 정리해 저장해줘",
    ] {
        let coverage = requirement_coverage(request, &[], &actions());
        assert_eq!(coverage["files"]["declared"], json!([]), "{request}");
        assert_eq!(coverage["complete"], true, "{request}");
    }
    // A manifest request is unchanged: the manifest is still the source.
    let manifest = requirement_coverage("todo 앱 html css js 만들어줘", &[], &actions());
    assert_eq!(
        manifest["missing_files"],
        json!(["index.html", "style.css", "app.js"])
    );
}

#[test]
fn a_counted_fact_is_read_back_out_of_the_transcript_only() {
    assert!(request_asks_for_a_count("파일 개수를 알려줘"));
    assert!(request_asks_for_a_count("how many files are there"));
    assert!(!request_asks_for_a_count("인사말을 저장해줘"));
    assert_eq!(count_from_transcript(&[]), None);
    assert_eq!(
        count_from_transcript(&[json!({"result": {"items": [1, 2, 3]}})]).as_deref(),
        Some("3개")
    );
    assert_eq!(
        count_from_transcript(&[json!({"result": {"matches": 7}})]).as_deref(),
        Some("7개")
    );
    // The newest countable fact wins; a step with no result is skipped.
    assert_eq!(
        count_from_transcript(&[
            json!({"result": {"items": [1]}}),
            json!({"action": "final"}),
            json!({"result": {"hits": [1, 2]}}),
        ])
        .as_deref(),
        Some("2개")
    );
}

/// `count` used to be a bare substring test, which fires on four ordinary
/// English words. Harmless while it only offered a default; not harmless
/// once [`answer_owes_a_count`] can hold a run back on it.
#[test]
fn a_count_question_is_the_whole_word_and_not_a_fragment_of_another() {
    assert!(request_asks_for_a_count("count the files"));
    assert!(request_asks_for_a_count("Count them please"));
    for innocent in [
        "create an account page",
        "add a discount field",
        "list the countries",
        "we encountered an error",
    ] {
        assert!(
            !request_asks_for_a_count(innocent),
            "{innocent} asked for no number"
        );
    }
    // The Korean markers are unaffected: their neighbours are Hangul.
    assert!(request_asks_for_a_count("이 폴더의 파일 개수를 알려줘"));
    assert!(request_asks_for_a_count("파일이 몇 개 있어?"));
}

#[test]
fn a_whole_ascii_word_ignores_case_and_respects_its_neighbours() {
    assert!(contains_ascii_word("verdict: PASS", "pass"));
    assert!(contains_ascii_word("pass", "PASS"));
    assert!(!contains_ascii_word("passed the check", "pass"));
    assert!(!contains_ascii_word("bypass", "pass"));
    assert!(!contains_ascii_word("", "pass"));
    assert!(
        !contains_ascii_word("no", "verdict"),
        "needle longer than hay"
    );
}

#[test]
fn a_count_is_completed_from_the_transcript_and_never_invented() {
    let listed = vec![json!({"result": {"items": [1, 2, 3]}})];
    assert_eq!(
        complete_a_count("폴더를 확인했습니다", "파일 개수를 알려줘", &listed),
        "폴더를 확인했습니다 (3개)"
    );
    assert_eq!(
        complete_a_count("", "파일 개수를 알려줘", &listed),
        "3개",
        "an empty answer becomes the fact itself"
    );
    assert_eq!(
        complete_a_count("파일이 3개 있습니다", "파일 개수를 알려줘", &listed),
        "파일이 3개 있습니다",
        "an answer that already names a figure is left alone"
    );
    assert_eq!(
        complete_a_count("메모를 저장했습니다", "인사말을 저장해줘", &listed),
        "메모를 저장했습니다",
        "a request that asked for no count is left alone"
    );
    assert_eq!(
        complete_a_count("확인했습니다", "파일 개수를 알려줘", &[]),
        "확인했습니다",
        "no tool counted anything, so the harness invents nothing"
    );
}

#[test]
fn an_answer_owes_a_count_only_when_one_was_asked_for_and_none_was_given() {
    assert!(answer_owes_a_count(
        "확인했습니다",
        "파일 개수를 알려줘",
        &[]
    ));
    assert!(!answer_owes_a_count("파일 2개", "파일 개수를 알려줘", &[]));
    assert!(!answer_owes_a_count(
        "확인했습니다",
        "인사말을 저장해줘",
        &[]
    ));
    assert!(
        !answer_owes_a_count(
            "the account page was written",
            "create an account page",
            &[]
        ),
        "the fail-closed gate inherits the whole-word rule"
    );
}

/// The `qwen05b:S3` defect, at the level it was caused: `list_dir` had
/// succeeded at step two with a real `items` array, and the gate concluded
/// the count was already reported because the answer quoted a tmp path
/// containing `501` and `0de64199`. "Contains a digit" is not "carries the
/// count that was asked for".
#[test]
fn a_digit_inside_a_path_is_not_the_count_that_was_asked_for() {
    let listed = vec![json!({
        "action": "list_dir",
        "args": {"path": "."},
        "result": {"items": [{"name": "a"}, {"name": "b"}]},
    })];
    let request = "이 폴더에 어떤 파일이 있는지 list_dir로 확인하고 파일 개수를 알려줘";
    let quoted_path =
        "list_dir /private/tmp/claude-501/-Users-x/0de64199-8a92-4294-a294-f78de19997ac/\
pass8_r2/home_qwen05b/agent_workspace";
    assert!(
        answer_owes_a_count(quoted_path, request, &listed),
        "every digit here belongs to a path, not to an answer"
    );
    assert_eq!(
        complete_a_count(quoted_path, request, &listed),
        format!("{quoted_path} (2개)"),
        "and the fact the tool returned is restored"
    );
    // A real report of the same fact is left exactly as the model said it.
    for answered in ["파일이 2개 있습니다", "there are 2 files", "(2)"] {
        assert!(
            !answer_owes_a_count(answered, request, &listed),
            "{answered}"
        );
        assert_eq!(complete_a_count(answered, request, &listed), answered);
    }
    // A number that is not the counted one does not discharge the question.
    assert!(answer_owes_a_count("파일이 9개 있습니다", request, &listed));
}

/// The release-gating defect: a count surfaced from a tool the question was
/// not about. `mcp.grep` failed; `list_dir` happened to succeed; the answer
/// would have been "2개" and `DONE` to a question about how many matches
/// there were.
#[test]
fn a_count_must_come_from_the_tool_the_question_is_about() {
    let request = "워크스페이스에서 LatticeAI를 mcp.grep으로 찾아주고, 찾은 개수를 알려줘";
    let mixed = vec![
        json!({
            "action": "list_dir", "args": {"path": "."},
            "result": {"items": [{"name": "a"}, {"name": "b"}]},
        }),
        json!({"action": "mcp.grep", "args": {"pattern": "LatticeAI"}, "error": "'pattern'"}),
    ];
    assert_eq!(
        attributed_count(request, &mixed),
        None,
        "the tool the user asked about never returned a count"
    );
    assert_eq!(
        complete_a_count("검색했습니다", request, &mixed),
        "검색했습니다",
        "so nothing is surfaced…"
    );
    assert!(
        answer_owes_a_count("검색했습니다", request, &mixed),
        "…and the run is held for review rather than answered with a number"
    );
    // The same transcript with the named tool succeeding: attributable, and
    // the number is that tool's, not the listing's.
    let mut searched = mixed.clone();
    searched.push(json!({
        "action": "mcp.grep", "args": {"pattern": "LatticeAI"},
        "result": {"matches": [{"line": 1}], "files_with_matches": 1},
    }));
    assert_eq!(attributed_count(request, &searched).as_deref(), Some("1개"));
    // A request that names no tool keeps the rule it always had.
    assert_eq!(
        attributed_count("이 폴더에 파일이 몇 개야", &mixed).as_deref(),
        Some("2개")
    );
}

#[test]
fn a_standalone_number_is_one_no_path_could_have_produced() {
    fn numbers(text: &str) -> Vec<&str> {
        standalone_numbers(text).collect()
    }
    assert_eq!(numbers("파일이 2개 있습니다"), vec!["2"]);
    assert_eq!(numbers("(2) and 30 more"), vec!["2", "30"]);
    assert!(numbers("/tmp/claude-501/0de64199-8a92/pass8_r2/qwen05b").is_empty());
    assert!(numbers("v11.9.0").is_empty(), "a version is not a count");
    assert!(numbers("").is_empty());
    assert!(names_action("mcp.grep으로 찾아줘", "mcp.grep"));
    assert!(
        names_action("list_dir로 확인하고", "mcp.list_dir"),
        "bare form"
    );
    assert!(!names_action("파일을 읽어줘", "read_file"));
    assert!(!names_token("bypass", "pass") && names_token("verdict: PASS", "pass"));
}

#[test]
fn only_bullet_and_numbered_lines_become_requirements() {
    let message =
        "만들어줘:\n- 다크모드\n* dark mode\n1. 검색 기능\n2) 필터\nfree prose here\n- ab";
    let coverage = requirement_coverage(message, &[], &actions());
    assert_eq!(
        coverage["requirements"],
        json!(["다크모드", "dark mode", "검색 기능"]),
        "prose is not parsed, and `필터` / `ab` are under the 3-character floor"
    );
}

#[test]
fn at_most_ten_requirements_are_reported() {
    let message: String = (0..15)
        .map(|index| format!("- item number {index}\n"))
        .collect();
    let coverage = requirement_coverage(&message, &[], &actions());
    assert_eq!(coverage["requirements"].as_array().expect("list").len(), 10);
}

#[test]
fn the_coverage_block_names_missing_files_in_capitals() {
    let steps = vec![executing("write_file", "index.html")];
    let coverage = requirement_coverage("todo 앱 html css 만들어줘", &steps, &actions());
    let block = format_requirement_coverage(&coverage);
    assert!(block.starts_with("\n\nRequested files"));
    assert!(block.contains("- index.html: written"));
    assert!(block.contains("- style.css: MISSING"));
}

#[test]
fn learnings_drop_the_short_the_trivial_and_the_duplicated() {
    let learnings = json!([
        "short",
        "파일을 만들었습니다",
        "Successfully created the file",
        "Vite needs the entry script tag before </body> or the app never mounts",
        "vite needs THE entry script tag before </body> or the app never mounts".to_uppercase(),
        null,
    ]);
    let kept = filter_learnings(learnings.as_array().expect("list"));
    assert_eq!(kept.len(), 1, "{kept:?}");
    assert!(kept[0].starts_with("Vite needs"));
}

#[test]
fn a_long_completion_sentence_survives_because_it_carries_information() {
    let long = "Successfully created the file, but the CSS never loaded because the \
manifest path was wrong";
    let kept = filter_learnings(&[json!(long)]);
    assert_eq!(kept, vec![long.to_string()]);
}

#[test]
fn budgets_read_the_environment_with_floors() {
    assert_eq!(PhaseBudgets::default().execute_tokens, 4096);
    assert_eq!(TranscriptBudget::default().window, 8);
    assert_eq!(env_int("LATTICEAI_AGENT_DEFINITELY_UNSET", 9), 9);
    // `from_env` with nothing set is the default record.
    assert_eq!(PhaseBudgets::from_env(), PhaseBudgets::default());
    assert_eq!(TranscriptBudget::from_env(), TranscriptBudget::default());
}

#[test]
fn a_phase_budget_is_held_between_one_action_and_what_the_seam_will_serve() {
    let key = "LATTICEAI_AGENT_EXECUTE_TOKENS";
    // Every default is already inside the band and passes through unchanged.
    let default = PhaseBudgets::default();
    for tokens in [
        default.plan_tokens,
        default.execute_tokens,
        default.verify_tokens,
        default.memory_tokens,
    ] {
        assert_eq!(clamp_phase_tokens(key, tokens as i64), tokens);
    }
    // The floor: one action's worth, never zero or negative.
    assert_eq!(clamp_phase_tokens(key, 0), MIN_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, -5), MIN_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, 127), MIN_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, 128), 128);
    // The ceiling. Without it, `=20000` reached the worker and every
    // execute phase came back 422 `agent_seam.max_tokens_out_of_range`.
    assert_eq!(clamp_phase_tokens(key, 8192), MAX_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, 8193), MAX_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, 20_000), MAX_PHASE_TOKENS);
    assert_eq!(clamp_phase_tokens(key, i64::MAX), MAX_PHASE_TOKENS);
    // A floor above the ceiling would make `clamp` panic rather than clamp.
    const { assert!(MIN_PHASE_TOKENS < MAX_PHASE_TOKENS) };
}

#[test]
fn basenames_follow_pathlib() {
    assert_eq!(path_name("src/main.jsx"), "main.jsx");
    assert_eq!(path_name("index.html"), "index.html");
    assert_eq!(path_name("a/b/"), "b");
    assert_eq!(path_name(""), "");
}

fn creates() -> BTreeSet<String> {
    ["write_file".to_string()].into_iter().collect()
}

#[test]
fn a_run_that_never_said_anything_still_has_an_answer_on_its_transcript() {
    // The two live 2B runs this exists for: the requested file was written,
    // the executor was stopped by the loop guard before it reached `final`,
    // and the run reported "처리 중 문제가 발생했습니다" over work that was
    // on disk. Nothing here is invented — every word comes off a step.
    let wrote = vec![json!({"state": "EXECUTING", "action": "write_file",
         "args": {"path": "notes/hello.md"},
         "result": {"path": "notes/hello.md", "bytes": 59}})];
    assert_eq!(
        delivered_answer(&wrote, &creates()).as_deref(),
        Some("notes/hello.md 파일을 저장했습니다.")
    );

    // No file, but a search that established something: the count is the
    // deliverable of a counting question, and an apology reports neither
    // the search nor its result.
    let searched = vec![json!({"state": "EXECUTING", "action": "mcp.grep",
         "args": {"pattern": "LatticeAI"},
         "result": {"matches": [{"line": 1}, {"line": 2}], "files_scanned": 4}})];
    assert_eq!(
        delivered_answer(&searched, &creates()).as_deref(),
        Some("mcp.grep 실행 결과: matches 2개 / 2 matches")
    );

    // And a run that established nothing has nothing to say, which is the
    // honest answer rather than a sentence about a step that told us
    // nothing. `final` and `parse_error` are never the delivery.
    let nothing = vec![
        json!({"state": "EXECUTING", "action": "todo_write", "result": {}}),
        json!({"state": "EXECUTING", "action": "final"}),
    ];
    assert_eq!(delivered_answer(&nothing, &creates()), None);
    assert_eq!(delivered_answer(&[], &creates()), None);
}

#[test]
fn a_created_file_is_completed_from_the_transcript_and_bare_negation_is_dropped() {
    // 1. Live qwen05b_S1_a1 shape: write_file(notes/hello.md, 14B) + "I did nothing."
    let s1_step = vec![json!({
        "state": "EXECUTING",
        "action": "write_file",
        "args": {"path": "notes/hello.md", "content": "hello world"},
        "result": {"path": "notes/hello.md", "bytes": 14}
    })];
    assert_eq!(
        complete_created_files("I did nothing.", &s1_step, &creates()),
        "notes/hello.md 파일을 작성했습니다 (14B)."
    );

    // Live qwen05b_S2_a1 shape: write_file(notes/summary.md, 319B) + "I did nothing."
    let s2_step = vec![json!({
        "state": "EXECUTING",
        "action": "write_file",
        "args": {"path": "notes/summary.md"},
        "result": {"path": "notes/summary.md", "bytes": 319}
    })];
    assert_eq!(
        complete_created_files("I did nothing.", &s2_step, &creates()),
        "notes/summary.md 파일을 작성했습니다 (319B)."
    );

    // Live qwen05b_S4_a1 shape: write_file(notes/review_note.md, 648B) + "I did nothing."
    let s4_step = vec![json!({
        "state": "EXECUTING",
        "action": "write_file",
        "args": {"path": "notes/review_note.md"},
        "result": {"path": "notes/review_note.md", "bytes": 648}
    })];
    assert_eq!(
        complete_created_files("I did nothing.", &s4_step, &creates()),
        "notes/review_note.md 파일을 작성했습니다 (648B)."
    );

    // Korean bare negation dropped
    assert_eq!(
        complete_created_files("아무것도 하지 않았습니다.", &s1_step, &creates()),
        "notes/hello.md 파일을 작성했습니다 (14B)."
    );

    // 2. An answer that already names the file passes through byte-identical (idempotent)
    let named_full = "The file notes/hello.md was successfully written to disk.";
    assert_eq!(
        complete_created_files(named_full, &s1_step, &creates()),
        named_full
    );
    let named_base = "hello.md 파일을 작성했습니다.";
    assert_eq!(
        complete_created_files(named_base, &s1_step, &creates()),
        named_base
    );

    // 3. Negation with extra text: facts go first, extra text follows
    let extra_negation = "I did nothing. Please check.";
    assert_eq!(
        complete_created_files(extra_negation, &s1_step, &creates()),
        "notes/hello.md 파일을 작성했습니다 (14B).\n\nI did nothing. Please check."
    );

    // Non-negation model text without file name is not a negation, so it passes through
    let other_text = "작업을 완료했습니다.";
    assert_eq!(
        complete_created_files(other_text, &s1_step, &creates()),
        other_text
    );

    // 4. No files created -> untouched (byte-identical passthrough)
    assert_eq!(
        complete_created_files("I did nothing.", &[], &creates()),
        "I did nothing."
    );

    // Multiple files created: joined sentences for bare negation
    let multi_step = vec![
        json!({
            "state": "EXECUTING",
            "action": "write_file",
            "args": {"path": "notes/a.md"},
            "result": {"path": "notes/a.md", "bytes": 10}
        }),
        json!({
            "state": "EXECUTING",
            "action": "write_file",
            "args": {"path": "notes/b.md"},
            "result": {"path": "notes/b.md", "bytes": 20}
        }),
    ];
    assert_eq!(
        complete_created_files("I did nothing.", &multi_step, &creates()),
        "notes/a.md 파일을 작성했습니다 (10B).\nnotes/b.md 파일을 작성했습니다 (20B)."
    );
}
