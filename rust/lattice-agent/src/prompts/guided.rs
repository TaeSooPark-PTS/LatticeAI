use serde_json::{Map, Value};

use crate::tools::catalog::{ArgKind, ArgSpec, CatalogEntry, EntryKind};

pub const MENU_QUESTION: &str = "다음 행동을 번호로 고르세요. 숫자 하나만 답하세요. \
/ Choose the next action. Reply with ONE number only.";

pub const MENU_ANSWER_PREFIX: &str = "고른 번호 / CHOSEN NUMBER: ";

pub const SKILL_NOTE: &str = "skill 항목은 실행이 아니라 안내문입니다 — 고르면 사용법이 표시되고, \
그 다음에 실제 도구를 골라야 합니다. / A skill is guidance, not an executable: choosing one shows \
its instructions, and you then still pick a real tool.";

pub const OWNED_INSTRUCTION_MARKERS: &[&str] = &[
    "머리말·코드블록 금지",
    "no preamble, no code fence",
    "Write only the resulting file body",
    "아래 요청을 그대로 수행한 결과물",
    "본문만 쓰세요",
    "설명도 코드블록도 쓰지 마세요",
    "값을 한 줄로만 쓰세요",
    "숫자 하나만 답하세요",
    "고른 번호 / CHOSEN NUMBER",
    "Choose the next action",
    "Give the reason in one short line",
    "Answer with exactly one word",
    "판정 / VERDICT",
    "Write the file's own content",
    "값만 한 줄로 쓰세요",
    "Write only the value itself",
];

pub fn contains_owned_instruction(text: &str) -> bool {
    let text = text.trim();
    if text.is_empty() {
        return false;
    }
    OWNED_INSTRUCTION_MARKERS
        .iter()
        .any(|marker| text.contains(marker))
}

pub fn brief_block(
    request: &str,
    goal: &str,
    workspace_root: &str,
    done: &str,
    skill_notes: &str,
    language_hint: &str,
) -> String {
    let skills = if skill_notes.trim().is_empty() {
        String::new()
    } else {
        format!("\n\n[안내 / INSTRUCTIONS IN FORCE]\n{}", skill_notes.trim())
    };
    format!(
        "[요청 / REQUEST]\n{request}\n\n\
[목표 / GOAL]\n{goal}\n\n\
[작업 폴더 / WORKSPACE]\n{workspace_root}\n\n\
[지금까지 / DONE SO FAR]\n{done}{skills}\n\n\
[답변 언어 / ANSWER LANGUAGE: {language_hint}]"
    )
}

pub fn menu_block(catalog: &[CatalogEntry]) -> String {
    let mut rows: Vec<String> = Vec::with_capacity(catalog.len());
    let mut has_skill = false;
    for (index, entry) in catalog.iter().enumerate() {
        if entry.kind == EntryKind::Skill {
            has_skill = true;
        }
        let mut row = format!("{}. {}", index + 1, entry.signature());
        if entry.kind != EntryKind::Native {
            row.push_str(&format!(" [{}]", entry.kind.label()));
        }
        if !entry.summary.trim().is_empty() {
            row.push_str(&format!(" — {}", entry.summary.trim()));
        }
        rows.push(row);
    }
    let note = if has_skill {
        format!("\n\n{SKILL_NOTE}")
    } else {
        String::new()
    };
    format!("[행동 / ACTIONS]\n{}{note}", rows.join("\n"))
}

pub fn argument_question(spec: &ArgSpec) -> String {
    match spec.kind {
        ArgKind::Line => "값을 한 줄로만 쓰세요. 설명은 쓰지 마세요. \
/ Answer with the value on one line. No explanation."
            .to_string(),
        ArgKind::Text => body_question(""),
    }
}

pub fn argument_brief(
    request: &str,
    goal: &str,
    workspace_root: &str,
    skill_notes: &str,
    language_hint: &str,
) -> String {
    let skills = if skill_notes.trim().is_empty() {
        String::new()
    } else {
        format!("\n\n[안내 / INSTRUCTIONS IN FORCE]\n{}", skill_notes.trim())
    };
    format!(
        "[요청 / REQUEST]\n{request}\n\n\
[목표 / GOAL]\n{goal}\n\n\
[작업 폴더 / WORKSPACE]\n{workspace_root}{skills}\n\n\
[답변 언어 / ANSWER LANGUAGE: {language_hint}]"
    )
}

pub fn body_question(goal: &str) -> String {
    let goal = goal.trim();
    if goal.is_empty() {
        return "본문만 쓰세요. 설명도 코드블록도 쓰지 마세요. \
/ Write the body only — no explanation, no code fence."
            .to_string();
    }
    goal.to_string()
}

pub fn body_block(target: &str, goal: &str, skill_notes: &str) -> String {
    let named = if target.trim().is_empty() {
        String::new()
    } else {
        format!("파일 이름 / FILE: {}\n\n", target.trim())
    };
    let skills = if skill_notes.trim().is_empty() {
        String::new()
    } else {
        format!("\n\n[안내 / INSTRUCTIONS IN FORCE]\n{}", skill_notes.trim())
    };
    format!("{named}요청 / REQUEST: {}{skills}", goal.trim())
}

pub const BODY_RETRY_NOTE: &str = "생각 과정이나 설명이 아니라, 파일에 들어갈 내용 자체를 \
쓰세요. / Write the file's own content, not your reasoning about it.";

pub fn body_retry_block(target: &str, goal: &str) -> String {
    let named = if target.trim().is_empty() {
        String::new()
    } else {
        format!("파일 이름 / FILE: {}\n\n", target.trim())
    };
    format!(
        "{BODY_RETRY_NOTE}\n\n{named}요청 / REQUEST: {}",
        goal.trim()
    )
}

pub fn argument_block(
    brief: &str,
    entry: &CatalogEntry,
    spec: &ArgSpec,
    suggestion: Option<&str>,
    decided: &Map<String, Value>,
) -> String {
    let mut so_far = String::new();
    for (key, value) in decided {
        let shown = match value.as_str() {
            Some(text) if text.chars().count() > 120 => {
                format!("{}…", crate::parse::pystr::char_slice(text, 120))
            }
            Some(text) => text.to_string(),
            None => value.to_string(),
        };
        so_far.push_str(&format!("\n- {key}: {shown}"));
    }
    let decided_block = if so_far.is_empty() {
        String::new()
    } else {
        format!("\n\n[이미 정한 값 / ALREADY DECIDED]{so_far}")
    };
    let default_block = default_block(suggestion);
    format!(
        "{brief}\n\n[고른 행동 / CHOSEN ACTION]\n{}{decided_block}\n\n\
[지금 필요한 값 / NEEDED NOW]\n{} — {}{default_block}",
        entry.name,
        spec.name,
        spec.hint.trim()
    )
}

fn default_block(suggestion: Option<&str>) -> String {
    match suggestion {
        Some(value) if !value.trim().is_empty() => format!(
            "\n\n[기본값 / DEFAULT]\n{}\n(그대로 쓰려면 이 값을 그대로 답하세요. \
/ Answer with this value to accept it.)",
            value.trim()
        ),
        _ => String::new(),
    }
}

pub const ARG_CHOICE_QUESTION: &str = "번호로 고르세요. 숫자 하나만 답하세요. \
/ Choose by number. Reply with ONE number only.";

pub const ARG_CHOICE_FREE_ROW: &str = "다른 값을 직접 쓰겠습니다 / type a different value myself";

pub fn default_choice_label(value: &str) -> String {
    match value.trim() {
        "." => "현재 폴더 (.) / the current folder".to_string(),
        other => format!("{other} (기본값 / default)"),
    }
}

pub fn default_choice_block(
    brief: &str,
    entry: &CatalogEntry,
    spec: &ArgSpec,
    rows: &[String],
) -> String {
    let numbered: Vec<String> = rows
        .iter()
        .enumerate()
        .map(|(index, row)| format!("{}. {row}", index + 1))
        .collect();
    format!(
        "{brief}\n\n[고른 행동 / CHOSEN ACTION]\n{}\n\n\
[지금 필요한 값 / NEEDED NOW]\n{} — {}\n\n[선택지 / CHOICES]\n{}",
        entry.name,
        spec.name,
        spec.hint.trim(),
        numbered.join("\n"),
    )
}

pub const LINE_RETRY_NOTE: &str = "행동 이름이나 명령줄이 아니라 값만 한 줄로 쓰세요. \
요청 문장을 다시 쓰지 마세요. / Write only the value itself on one line — not the action name, \
not a command line, not the request restated.";

pub fn argument_retry_block(request: &str, spec: &ArgSpec, suggestion: Option<&str>) -> String {
    format!(
        "{LINE_RETRY_NOTE}\n\n요청 / REQUEST: {}\n\n\
[지금 필요한 값 / NEEDED NOW]\n{} — {}{}",
        request.trim(),
        spec.name,
        spec.hint.trim(),
        default_block(suggestion),
    )
}

pub const VERDICT_QUESTION: &str =
    "PASS 또는 FAIL 중 하나만 쓰세요. / Answer with exactly one word: PASS or FAIL.";

pub const VERDICT_ANSWER_PREFIX: &str = "판정 / VERDICT: ";

pub const REASON_QUESTION: &str = "이유를 한 줄로 쓰세요. / Give the reason in one short line.";

pub fn verdict_block(request: &str, evidence: &str, answer: &str) -> String {
    let answered = if answer.trim().is_empty() {
        String::new()
    } else {
        format!(
            "\n\n[사용자에게 할 답변 / THE ANSWER THIS RUN WILL GIVE]\n{}",
            answer.trim()
        )
    };
    format!(
        "[요청 / REQUEST]\n{request}\n\n\
[실제로 한 일 / WHAT ACTUALLY HAPPENED]\n{evidence}{answered}\n\n\
요청한 일이 실제로 끝났으면 PASS, 아니면 FAIL. \
/ PASS only if the request was actually carried out; otherwise FAIL."
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::catalog::{native_entries, ArgSpec};

    fn names(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn the_menu_numbers_every_row_from_one() {
        let catalog = native_entries(&names(&["write_file", "read_file"]));
        let block = menu_block(&catalog);
        assert!(block.contains("1. write_file{path, content}"));
        assert!(block.contains("2. read_file{path}"));
        assert!(block.contains("3. final{message}"));
        assert!(!block.contains("0. "), "numbering starts at one");
        assert!(
            !block.contains("skill 항목은"),
            "the skill note appears only when a skill is offered"
        );
    }

    #[test]
    fn a_skill_row_is_labelled_and_explained() {
        let catalog = vec![CatalogEntry {
            name: "skill.code_review".into(),
            kind: EntryKind::Skill,
            summary: "review a diff".into(),
            required: Vec::new(),
        }];
        let block = menu_block(&catalog);
        assert!(block.contains("1. skill.code_review [skill] — review a diff"));
        assert!(
            block.contains("실행이 아니라 안내문"),
            "the honest semantics are stated, not implied"
        );
        assert!(block.contains("guidance, not an executable"));
    }

    #[test]
    fn no_guided_prompt_ever_shows_the_model_a_brace() {
        let spec = ArgSpec::text("content", "the file's whole content");
        let entry = CatalogEntry {
            name: "write_file".into(),
            kind: EntryKind::Native,
            summary: String::new(),
            required: vec![ArgSpec::line("path", "where"), spec.clone()],
        };
        let mut decided = Map::new();
        decided.insert("path".into(), serde_json::json!("notes/hello.md"));
        let block = argument_block("BRIEF", &entry, &spec, None, &decided);
        assert!(block.contains("notes/hello.md"), "what is decided is shown");
        assert!(block.contains("content — the file's whole content"));
        for text in [
            MENU_QUESTION,
            SKILL_NOTE,
            argument_question(&spec).as_str(),
            argument_question(&ArgSpec::line("path", "")).as_str(),
            body_question("메모 파일에 인사말을 써줘").as_str(),
            body_block("notes/hello.md", "메모 만들기", "").as_str(),
            VERDICT_QUESTION,
            REASON_QUESTION,
            block.as_str(),
        ] {
            assert!(
                !text.contains('{') && !text.contains('}'),
                "a guided prompt must not teach JSON: {text}"
            );
        }
    }

    #[test]
    fn a_default_is_offered_as_a_default_and_not_as_the_answer() {
        let entry = CatalogEntry {
            name: "write_file".into(),
            kind: EntryKind::Native,
            summary: String::new(),
            required: vec![ArgSpec::line("path", "where")],
        };
        let with = argument_block(
            "B",
            &entry,
            &entry.required[0],
            Some("  notes/hello.md  "),
            &Map::new(),
        );
        assert!(with.contains("[기본값 / DEFAULT]\nnotes/hello.md"));
        let without = argument_block("B", &entry, &entry.required[0], None, &Map::new());
        assert!(!without.contains("DEFAULT"));
        let blank = argument_block("B", &entry, &entry.required[0], Some("   "), &Map::new());
        assert!(!blank.contains("DEFAULT"), "a blank default is no default");
    }

    #[test]
    fn a_body_turn_asks_the_request_and_names_the_file_beside_it() {
        assert_eq!(
            body_question("메모 파일 notes/hello.md 에 인사말을 써줘"),
            "메모 파일 notes/hello.md 에 인사말을 써줘"
        );
        assert!(body_question("   ").contains("본문만 쓰세요"));
        let block = body_block("notes/hello.md", "인사말을 써줘", "");
        assert!(block.contains("FILE: notes/hello.md"));
        assert!(block.contains("REQUEST: 인사말을 써줘"));
        assert!(
            !block.contains("머리말·코드블록 금지"),
            "a body context that names our own ban is a file the 0.5B will write: {block}"
        );
        assert!(
            !block.contains("이미 정한 값"),
            "a body turn is shown no labelled values to copy: {block}"
        );
        assert!(body_block("a.md", "g", "keep it short").contains("keep it short"));
        assert!(!body_block("a.md", "g", "  ").contains("INSTRUCTIONS IN FORCE"));
    }

    #[test]
    fn an_argument_turn_is_never_preceded_by_lines_shaped_like_answers() {
        let brief = argument_brief("메모를 써줘", "write notes/hello.md", "/ws", "", "Korean");
        assert!(brief.contains("메모를 써줘"));
        assert!(brief.contains("write notes/hello.md"));
        assert!(brief.contains("/ws"));
        assert!(
            !brief.contains("DONE SO FAR"),
            "an argument turn must not be shown the transcript: {brief}"
        );
        assert!(!brief.contains("- write_file"));
        let guided = argument_brief("r", "g", "/ws", "use short sentences", "English");
        assert!(guided.contains("use short sentences"));
    }

    #[test]
    fn the_brief_carries_the_five_things_a_next_action_depends_on() {
        let brief = brief_block(
            "메모를 써줘",
            "write notes/hello.md",
            "/ws",
            "- write_file: failed",
            "use short sentences",
            "Korean",
        );
        assert!(brief.contains("메모를 써줘"));
        assert!(brief.contains("write notes/hello.md"));
        assert!(brief.contains("/ws"));
        assert!(brief.contains("- write_file: failed"));
        assert!(brief.contains("use short sentences"));
        assert!(brief.contains("ANSWER LANGUAGE: Korean"));
        let bare = brief_block("r", "g", "/ws", "- (nothing yet)", "  ", "English");
        assert!(!bare.contains("INSTRUCTIONS IN FORCE"));
    }

    #[test]
    fn a_long_decided_value_is_shown_shortened_not_whole() {
        let entry = CatalogEntry {
            name: "write_file".into(),
            kind: EntryKind::Native,
            summary: String::new(),
            required: vec![ArgSpec::text("content", "body")],
        };
        let mut decided = Map::new();
        decided.insert("content".into(), serde_json::json!("x".repeat(400)));
        decided.insert("count".into(), serde_json::json!(3));
        let block = argument_block("B", &entry, &entry.required[0], None, &decided);
        assert!(block.contains('…'), "a long value is elided");
        assert!(!block.contains(&"x".repeat(200)));
        assert!(block.contains("- count: 3"), "a non-string still renders");
    }

    #[test]
    fn owned_instruction_markers_are_sentences_we_wrote() {
        assert!(contains_owned_instruction(
            "이미지·머리말·코드블록 금지不予提供。"
        ));
        assert!(contains_owned_instruction(
            "/ Write only the resulting file body for the request below — no explanation, no preamble, no code fence."
        ));
        assert!(!contains_owned_instruction(
            "안녕하세요, 만나서 반갑습니다."
        ));
        assert!(!contains_owned_instruction(""));
    }

    #[test]
    fn the_verdict_turn_is_a_closed_question() {
        let block = verdict_block("메모 파일을 써줘", "- write_file notes/hello.md: ok", "");
        assert!(block.contains("PASS"));
        assert!(block.contains("FAIL"));
        assert!(block.contains("- write_file notes/hello.md: ok"));
        assert!(VERDICT_QUESTION.contains("PASS or FAIL"));
        assert!(
            !block.contains("THE ANSWER THIS RUN WILL GIVE"),
            "no answer yet means no empty section"
        );
    }

    #[test]
    fn the_verdict_turn_is_shown_the_answer_it_is_judging() {
        let block = verdict_block(
            "파일 개수를 알려줘",
            "- list_dir: ok — 항목 2개 / 2 items",
            "  이 폴더에는 파일이 2개 있습니다.  ",
        );
        assert!(block.contains("[사용자에게 할 답변 / THE ANSWER THIS RUN WILL GIVE]"));
        assert!(block.contains("이 폴더에는 파일이 2개 있습니다."));
        assert!(!block.contains("  이 폴더"), "the answer is trimmed");
    }

    #[test]
    fn the_menu_answer_prefix_fixes_the_position_and_carries_no_digit() {
        assert!(!MENU_ANSWER_PREFIX.chars().any(|c| c.is_ascii_digit()));
        assert!(MENU_ANSWER_PREFIX.ends_with(": "), "the answer starts next");
        assert!(!MENU_ANSWER_PREFIX.contains('{') && !MENU_ANSWER_PREFIX.contains('}'));
        assert!(contains_owned_instruction(&format!(
            "{MENU_ANSWER_PREFIX}3"
        )));
    }
}
