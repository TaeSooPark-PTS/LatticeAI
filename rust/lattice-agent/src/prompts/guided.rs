//! The words a decomposed step is asked with (v12.0.0).
//!
//! Every string here obeys one rule the JSON prompts cannot: **the answer is
//! not a data structure.** A menu turn wants a number; an argument turn wants a
//! path or a paragraph. So none of these mention braces, quoting or escaping —
//! a weak model that is shown `{` starts producing `{`, and the whole point of
//! [`crate::kernel::agentloop::guided`] is that it never has to.
//!
//! They are bilingual, Korean then English, for the same reason the product is:
//! the local models this mode exists for follow an instruction in the language
//! the request arrived in, and a Korean request answered in English drifts. The
//! run's own `language_hint` is carried too, so a third language still reaches
//! the model as the answer language.

use serde_json::{Map, Value};

use crate::tools::catalog::{ArgKind, ArgSpec, CatalogEntry, EntryKind};

/// The menu turn's question. One line, one number.
pub const MENU_QUESTION: &str = "다음 행동을 번호로 고르세요. 숫자 하나만 답하세요. \
/ Choose the next action. Reply with ONE number only.";

/// The opening a menu answer is **forced** to begin with (v12.0.0).
///
/// The same instrument [`crate::kernel::profile::EXECUTE_JSON_PREFIX`] is, for
/// the same reason and against a harder case. Asking a reasoning-tuned model
/// "reply with one number" and giving it eight tokens produced, twelve times
/// out of twelve, `Thinking Process:` and nothing else: the model opens every
/// completion by restating the task, the newline stop fired inside the
/// preamble, and the digit the parser needed was never generated. That is not a
/// parser bug and not a model to work around — it is an answer whose *position*
/// was never fixed.
///
/// Prefilling it fixes the position. The worker puts these characters in the
/// model's mouth, so the next token it emits is the token after `NUMBER: ` —
/// and the likeliest continuation of that, for any model, is a digit. It
/// carries no digit of its own, so a reply that adds none still parses as no
/// answer rather than as row one.
pub const MENU_ANSWER_PREFIX: &str = "고른 번호 / CHOSEN NUMBER: ";

/// What a skill row means, said once so the model never has to infer it.
pub const SKILL_NOTE: &str = "skill 항목은 실행이 아니라 안내문입니다 — 고르면 사용법이 표시되고, \
그 다음에 실제 도구를 골라야 합니다. / A skill is guidance, not an executable: choosing one shows \
its instructions, and you then still pick a real tool.";

/// Phrases this crate owns. A file that contains one is our prompt read
/// back, never a document a user asked for. Compared as substrings against
/// constants we wrote, so a genuine greeting cannot collide.
///
/// The English halves of the closed questions joined the list in v12.0.0, and a
/// live 2B is why: asked for a verdict's reason it answered
/// `이유를 다시 한 줄로 쓰세요. / Give the reason in one short line.` — our own
/// question with one word inserted. Line equality missed it (the word broke the
/// match), the run recorded it as the critic's reason, showed it to the user
/// and fed it back to the executor as the next attempt's correction. A phrase
/// this crate wrote is ours wherever it turns up, not only at the front of a
/// line, and these are long enough that no document collides with one.
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

/// Whether `text` contains a sentence we sent the model as an instruction.
pub fn contains_owned_instruction(text: &str) -> bool {
    let text = text.trim();
    if text.is_empty() {
        return false;
    }
    OWNED_INSTRUCTION_MARKERS
        .iter()
        .any(|marker| text.contains(marker))
}

/// The standing context a micro-turn opens with.
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

/// The numbered action list.
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

/// The question a **one-line** argument is asked with.
pub fn argument_question(spec: &ArgSpec) -> String {
    match spec.kind {
        ArgKind::Line => "값을 한 줄로만 쓰세요. 설명은 쓰지 마세요. \
/ Answer with the value on one line. No explanation."
            .to_string(),
        // Never reached: a body argument is asked with [`body_question`], whose
        // whole point is that it carries no instruction a model can copy.
        ArgKind::Text => body_question(""),
    }
}

/// The standing context an **argument** turn opens with.
///
/// Deliberately not [`brief_block`]. An argument turn asks for a value, and the
/// full brief ends with a `DONE SO FAR` list whose lines read
/// `- write_file: ok` — which the first live 0.5B run copied straight into
/// `args.path`, writing files called `ok` and `failed`. What has already
/// happened is what the *menu* turn needs to choose well; it is noise shaped
/// like an answer once the choice is made.
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

/// The question a **free-form body** argument is asked with: **the request**.
///
/// Not the labelled scaffolding [`argument_block`] builds, and the first live
/// 0.5B run is the reason. Asked for `content` under a context ending
/// `[이미 정한 값] - path: notes/hello.md`, the model answered
/// `- path: notes/hello.md`; asked under `[고른 행동] write_file`, it answered
/// `write_file`. A small model continues the nearest label, so a body question
/// must not put one in front of it.
///
/// So the question a body turn asks is the user's own request, and the file it
/// is for is named in the surrounding [`body_block`]. Measured on the acid-test
/// model: asked `notes/hello.md 의 본문:` a 0.5B answered
/// `Body of notes/hello.md:`; asked `메모 파일 notes/hello.md 에 인사말을 써줘`
/// it answered `안녕하세요, 세계의 모든 사람과 함께합니다.`. A model continues
/// what it is given, so it should be given the task.
pub fn body_question(goal: &str) -> String {
    let goal = goal.trim();
    if goal.is_empty() {
        return "본문만 쓰세요. 설명도 코드블록도 쓰지 마세요. \
/ Write the body only — no explanation, no code fence."
            .to_string();
    }
    goal.to_string()
}

/// The context a free-form body argument is asked in.
///
/// No standing instruction. The first live 0.5B run copied
/// `머리말·코드블록 금지` into the file because that sentence sat in front
/// of the request; a small model continues the nearest complete shape. The
/// request *is* the question ([`body_question`]), and this block only names
/// the file and any skill already in force.
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

/// The one sentence a **re-asked** body turn adds (v12.0.0).
///
/// A body turn that came back with a reasoning preamble instead of a file is
/// not re-asked by replaying the same question: every micro-turn runs at
/// temperature zero, so the second identical ask is the first one again — the
/// same lesson [`crate::kernel::agentloop::guided::MENU_RETRY_TOKENS`] learned
/// on the menu. So the re-ask names the thing that went wrong, once, in both
/// languages, and it is registered in [`OWNED_INSTRUCTION_MARKERS`] so that a
/// model which copies it instead of answering has written a sentence the
/// harness will strip rather than a file the user has to read.
pub const BODY_RETRY_NOTE: &str = "생각 과정이나 설명이 아니라, 파일에 들어갈 내용 자체를 \
쓰세요. / Write the file's own content, not your reasoning about it.";

/// The context a **re-asked** free-form body argument is asked in.
///
/// [`body_block`] minus everything a model can copy, plus
/// [`BODY_RETRY_NOTE`]. The skill instructions go — a live 2B answered the
/// checklist question by echoing the `SKILL.md` it had been handed, every line
/// of which was a line we sent, so the echo filter removed all of them and the
/// turn produced nothing at all — and the note goes **first**, so the nearest
/// complete shape to the model's next token is still the user's request.
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

/// The context one argument is asked in.
///
/// Carries what has been decided already — the chosen action and any argument
/// answered before this one — because "write the content" is unanswerable
/// without knowing which file, and a model that has to remember it across turns
/// is a model being asked to hold state again.
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

/// The offered default, rendered once for both argument blocks.
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

/// The question an argument **the tool documents a default for** is asked with
/// (v12.0.0).
///
/// The menu turn's question, one level down, and for the same measured reason:
/// a 0.5B answers a number and cannot type a path. Handed the open question
/// `path — workspace-relative path`, one live 0.5B answered
/// `path/scratchpad/matrix/home_qwen05b/agent_workspace` (the workspace line in
/// its own context, continued) and a live 2B answered `file_list.txt` (a
/// filename its planner had invented); both dispatched, both were told
/// `Directory does not exist.`, and both runs halted. Neither model was asked a
/// question it could answer — and `list_dir(path: str = ".")` documents the
/// answer, so the harness can offer it as row one instead of asking for it.
pub const ARG_CHOICE_QUESTION: &str = "번호로 고르세요. 숫자 하나만 답하세요. \
/ Choose by number. Reply with ONE number only.";

/// The row that declines every offered value and asks for one instead.
///
/// Always last, and always present: the choice must never be a trap. A run that
/// really does want another path says so with the last number and is then asked
/// the ordinary one-line question, unchanged.
pub const ARG_CHOICE_FREE_ROW: &str = "다른 값을 직접 쓰겠습니다 / type a different value myself";

/// How one documented default reads on a choice row.
///
/// Keyed on the value, not on the tool and never on a model: `.` is *the
/// current folder* in any run that has one, and anything else is shown as
/// itself with the word "default" beside it. A value we cannot say anything
/// better about is still shown exactly as it will be sent.
pub fn default_choice_label(value: &str) -> String {
    match value.trim() {
        "." => "현재 폴더 (.) / the current folder".to_string(),
        other => format!("{other} (기본값 / default)"),
    }
}

/// The context a defaulting argument's numbered choice is asked in.
///
/// [`argument_block`]'s shape minus the one thing that turn got wrong — the
/// open value slot — and plus the numbered rows. The already-decided list stays
/// out for [`body_question`]'s reason: a labelled line shaped like an answer is
/// what a small model continues.
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

/// The one sentence a **re-asked** one-line argument turn adds (v12.0.0).
///
/// [`BODY_RETRY_NOTE`]'s twin, one argument kind over, and the same three
/// facts behind it: a micro-turn runs at temperature zero, so an identical
/// second ask is the first one replayed; a small model continues the nearest
/// complete shape; and the nearest shape to a line turn is the
/// `[고른 행동 / CHOSEN ACTION]` label we just printed. Two live cells are the
/// evidence — a 0.5B answered `mcp.grep`'s `pattern` with
/// `mcp.grep --pattern "^LatticeAI` (our label, continued into a command line)
/// and a 2B answered it with the request restated as a sentence. Both searched
/// for that string, both found nothing, and both reported a count of zero.
///
/// Registered in [`OWNED_INSTRUCTION_MARKERS`], like its twin, so a model that
/// copies the note instead of answering has written a sentence the harness
/// strips rather than a value a tool has to run.
pub const LINE_RETRY_NOTE: &str = "행동 이름이나 명령줄이 아니라 값만 한 줄로 쓰세요. \
요청 문장을 다시 쓰지 마세요. / Write only the value itself on one line — not the action name, \
not a command line, not the request restated.";

/// The context a **re-asked** one-line argument is asked in.
///
/// [`argument_block`] minus everything the rejected answer was made of: the
/// chosen action's name and the already-decided list are both label-shaped and
/// both were copied verbatim in live runs, so neither is sent again. The
/// request stays — a `pattern` turn with no request in front of it is
/// unanswerable — and the note goes first, so the nearest complete shape to
/// the model's next token is the correction rather than a label.
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

/// The guided verdict turn: a closed question, then one line of reason.
pub const VERDICT_QUESTION: &str =
    "PASS 또는 FAIL 중 하나만 쓰세요. / Answer with exactly one word: PASS or FAIL.";

/// The opening a verdict answer is **forced** to begin with (v12.0.0).
///
/// [`MENU_ANSWER_PREFIX`]'s twin, against the same defect one phase later. The
/// verdict turn is the menu turn's shape exactly — a closed question, eight
/// tokens, a newline stop — so a model that opens every completion with a
/// reasoning preamble runs out of budget before the word, and the live evidence
/// is the pair: the same four runs whose menu turns produced no digit produced
/// no verdict word either and ended `UNAVAILABLE` over work that had really
/// happened. Fixing the position of one answer and not the other would have
/// been fixing half a defect.
///
/// It carries neither verdict word, so a reply that adds none still parses as
/// no answer rather than as a PASS.
pub const VERDICT_ANSWER_PREFIX: &str = "판정 / VERDICT: ";

/// The reason turn, asked only after a verdict word came back.
pub const REASON_QUESTION: &str = "이유를 한 줄로 쓰세요. / Give the reason in one short line.";

/// The context the verdict turn is asked in.
///
/// `answer` is what the run is about to tell the user, and it is here because
/// without it the closed question is unanswerable for a whole class of request
/// (v12.0.0). Asked "did `이 폴더에 파일이 몇 개인지 알려줘` get carried out?"
/// over evidence that read `- list_dir: ok` and nothing more, a critic is being
/// asked whether a count was reported while being shown no count — and a model
/// with no information to answer with answers FAIL, every time, which is what
/// three models did on every attempt. The deliverable of a question is the
/// answer, so the answer is part of what actually happened.
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
        // The whole design: a model shown `{` starts emitting `{`.
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
        // Measured, not asserted from taste: the acid-test 0.5B answered the
        // meta-question `notes/hello.md 의 본문:` with `Body of notes/hello.md:`
        // and the request itself with an actual greeting.
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
        // A skill in force still reaches it: instructions are not answers.
        assert!(body_block("a.md", "g", "keep it short").contains("keep it short"));
        assert!(!body_block("a.md", "g", "  ").contains("INSTRUCTIONS IN FORCE"));
    }

    #[test]
    fn an_argument_turn_is_never_preceded_by_lines_shaped_like_answers() {
        // The live 0.5B defect: the menu brief's `- write_file: ok` history
        // lines were copied into `args.path`, producing files called `ok`.
        let brief = argument_brief("메모를 써줘", "write notes/hello.md", "/ws", "", "Korean");
        assert!(brief.contains("메모를 써줘"));
        assert!(brief.contains("write notes/hello.md"));
        assert!(brief.contains("/ws"));
        assert!(
            !brief.contains("DONE SO FAR"),
            "an argument turn must not be shown the transcript: {brief}"
        );
        assert!(!brief.contains("- write_file"));
        // Skills in force still reach it — those are instructions, not answers.
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
        // No skills in force means no empty section.
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
        // The count question: the tool row alone cannot answer it.
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
        // A prefix with a digit in it would parse as the model's choice.
        assert!(!MENU_ANSWER_PREFIX.chars().any(|c| c.is_ascii_digit()));
        assert!(MENU_ANSWER_PREFIX.ends_with(": "), "the answer starts next");
        assert!(!MENU_ANSWER_PREFIX.contains('{') && !MENU_ANSWER_PREFIX.contains('}'));
        // And it is a sentence we own, so it can never be written to a file.
        assert!(contains_owned_instruction(&format!(
            "{MENU_ANSWER_PREFIX}3"
        )));
    }
}
