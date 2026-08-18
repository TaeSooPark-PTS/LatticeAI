use super::*;
use serde_json::Value;
use std::collections::BTreeSet;

/// The run's answer, with transcript-proven artifact facts restored (F5).
///
/// 1. When the transcript proves created files, the answer must carry the fact:
///    `<path> 파일을 작성했습니다 (<N>B).`
/// 2. If the model's answer already names the created file(s), leave it untouched (idempotent).
/// 3. If the model's answer is a negation contradicting proven work ("I did nothing.", "아무것도 하지 않았습니다"),
///    the fact goes first and the bare disproven negation is dropped. Other model text
///    stays after the fact sentence.
/// 4. No files created -> nothing changes, byte-identical.
pub fn complete_created_files(
    said: &str,
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> String {
    let proven = proven_created_files(transcript, file_create_actions);
    if proven.is_empty() {
        return said.to_string();
    }
    if proven.iter().all(|f| answer_names_file(said, &f.path)) {
        return said.to_string();
    }
    let trimmed = said.trim();
    if is_bare_negation(trimmed) {
        let facts: Vec<String> = proven
            .iter()
            .map(|f| match f.bytes {
                Some(b) => format!("{} 파일을 작성했습니다 ({}B).", f.path, b),
                None => format!("{} 파일을 작성했습니다.", f.path),
            })
            .collect();
        return facts.join("\n");
    }
    if is_negation_sentence(trimmed) {
        let missing_files: Vec<&ProvenFile> = proven
            .iter()
            .filter(|f| !answer_names_file(said, &f.path))
            .collect();
        let facts: Vec<String> = missing_files
            .iter()
            .map(|f| match f.bytes {
                Some(b) => format!("{} 파일을 작성했습니다 ({}B).", f.path, b),
                None => format!("{} 파일을 작성했습니다.", f.path),
            })
            .collect();
        let facts_text = facts.join("\n");
        return format!("{facts_text}\n\n{trimmed}");
    }
    said.to_string()
}

/// The run's answer, with a counted fact restored when the request wanted one.
///
/// Three conditions, all required, all cheap: the request asked *how many*, the
/// answer names no figure at all, and a tool in this run actually returned a
/// count. Miss any one and the words are returned untouched — which is every
/// run that ever existed before this, including every recorded one.
///
/// **Keyed on the answer, not on the step that produced it** (v12.0.0). This
/// lived in [`crate::kernel::agentloop::execution`] and ran only inside the
/// `final` branch, so it covered the one path where a model *chose* to finish.
/// A run that stalls instead — the budget runs out, the loop guard fires, the
/// model spends its last steps naming a tool that does not exist — never
/// reaches `final`, and its answer is filled in later from the critic's own
/// reason. A live 2B did exactly that over `list_dir`: four real listings, four
/// invented actions, no `final`, and a fluent PASS whose text carried no number
/// anywhere. The count question was answered by nobody. Which step wrote the
/// sentence is not a property of the request, so the rule is attached to the
/// answer and every path that settles one runs it.
pub fn complete_a_count(said: &str, request: &str, transcript: &[Value]) -> String {
    if !request_asks_for_a_count(request) {
        return said.to_string();
    }
    let Some(count) = attributed_count(request, transcript) else {
        return said.to_string();
    };
    if answer_carries(said, &count) {
        return said.to_string();
    }
    if said.trim().is_empty() {
        count
    } else {
        format!("{} ({count})", said.trim())
    }
}

/// A count question whose answer still does not carry the counted fact
/// (v12.0.0).
///
/// The deterministic half of the same fact [`complete_a_count`] repairs: after
/// the repair has had its chance, an answer that still does not report the
/// number is a PASS over an undelivered deliverable. That is a fact rather than
/// a judgement — the same standing a missing requested file has in
/// [`requirement_coverage`] — so it is enforced rather than argued with the
/// critic.
///
/// **"Carries a number" is not "contains a digit"** (v12.0.0). It was, and a
/// live 0.5B passed this gate on the strength of `501` and `0de64199` inside
/// the absolute workspace path its answer happened to quote — over a run whose
/// `list_dir` had succeeded at step two with the items sitting in the
/// transcript, unreported, for the rest of the run. See [`answer_carries`].
pub fn answer_owes_a_count(answer: &str, request: &str, transcript: &[Value]) -> bool {
    if !request_asks_for_a_count(request) {
        return false;
    }
    match attributed_count(request, transcript) {
        // A tool counted: the answer must report *that* number.
        Some(count) => !answer_carries(answer, &count),
        // Nothing in this run counted anything a caller may attribute. The
        // answer must at least state a figure of its own — the model's claim,
        // which is all there is — and a digit inside a path is not one.
        None => standalone_numbers(answer).next().is_none(),
    }
}

/// The counted fact this run may put in front of a user, or `None`.
///
/// **Attribution is the whole of this function** (v12.0.0), and the release it
/// gates is why. [`count_from_transcript`] took the first countable field it
/// found in reverse order with no check of where it came from; two live cells
/// searched for the wrong string, got `files_with_matches: 0`, and reported an
/// unhedged **`0개`, `DONE`** over a workspace whose README contains the word
/// they were asked to find. A caveat is recoverable; a confident wrong number
/// marked done is not.
///
/// So a surfaced count must come from **the tool the question is about**:
///
/// * when the request names a tool this run actually ran (`list_dir로 확인하고`,
///   `mcp.grep으로 찾아주고` — bare or prefixed, whole-token), only that tool's
///   most recent **successful** call may supply the number. It counted the
///   thing that was asked about; nothing else in the transcript did;
/// * when the request names no tool at all — `이 폴더에 파일이 몇 개야` — the
///   most recent countable result stands, which is every run this rule existed
///   for before attribution was possible;
/// * anything else is **unattributable** and returns `None`, at which point the
///   caller keeps its caveat and the run is held for review. No caller may
///   invent a number, and no caller may borrow one from a tool the user was not
///   asking about.
pub fn attributed_count(request: &str, transcript: &[Value]) -> Option<String> {
    let named: Vec<&str> = transcript
        .iter()
        .filter_map(|step| step.get("action").and_then(Value::as_str))
        .filter(|action| !action.is_empty() && names_action(request, action))
        .collect();
    if named.is_empty() {
        return count_from_transcript(transcript);
    }
    transcript
        .iter()
        .rev()
        .filter(|step| {
            step.get("error").is_none()
                && step
                    .get("action")
                    .and_then(Value::as_str)
                    .is_some_and(|action| named.contains(&action))
        })
        .find_map(|step| step.get("result").and_then(countable))
}

/// Whether the request names this action — its own spelling or its bare form.
///
/// `mcp.grep으로` names `mcp.grep`; `list_dir로` names `list_dir` and the
/// `mcp.list_dir` row that resolves to it. One rule, shared with the guided
/// dial's menu ranking ([`crate::kernel::agentloop::guided`]), because "does
/// the user's sentence name this row" must have one answer in the crate.
pub fn names_action(request: &str, action: &str) -> bool {
    if names_token(request, action) {
        return true;
    }
    action
        .split_once('.')
        .is_some_and(|(_, bare)| !bare.is_empty() && names_token(request, bare))
}

/// `token` as a whole token in `text`, case-insensitively.
///
/// A preceding `.` is part of a qualifier (`mcp.grep` is not the native
/// `grep`); a following alphanumeric is a longer word (`final` is not
/// `final_message`).
pub fn names_token(text: &str, token: &str) -> bool {
    let hay = text.to_lowercase();
    let needle = token.to_lowercase();
    if needle.is_empty() {
        return false;
    }
    let mut start = 0;
    while let Some(offset) = hay[start..].find(&needle) {
        let index = start + offset;
        let before_ok = match hay[..index].chars().next_back() {
            None => true,
            Some(character)
                if character.is_ascii_alphanumeric() || character == '_' || character == '.' =>
            {
                false
            }
            Some(_) => true,
        };
        let after_ok = match hay[index + needle.len()..].chars().next() {
            None => true,
            Some(character) if character.is_ascii_alphanumeric() || character == '_' => false,
            Some(_) => true,
        };
        if before_ok && after_ok {
            return true;
        }
        start = index + 1;
    }
    false
}

/// The countable fact one tool result carries, as `"<n>개"`.
fn countable(result: &Value) -> Option<String> {
    if let Some(items) = result.get("items").and_then(Value::as_array) {
        return Some(format!("{}개", items.len()));
    }
    let count = result
        .get("count")
        .or_else(|| result.get("matches"))
        .or_else(|| result.get("hits"))?;
    if let Some(number) = count.as_u64() {
        return Some(format!("{number}개"));
    }
    count.as_array().map(|rows| format!("{}개", rows.len()))
}

/// Every **standalone** number in a text, as it is written.
///
/// A maximal run of ASCII digits whose neighbours are not alphanumerics and not
/// the characters a path is made of (`/`, `-`, `_`, `.`). That exclusion list
/// is the rule: `claude-501`, `0de64199-8a92`, `pass8_r2` and `qwen05b` are the
/// four shapes a live answer quoted a workspace path with, and not one of them
/// is a figure anybody reported. `2개`, `there are 2 files` and `(2)` all are.
pub(super) fn standalone_numbers(text: &str) -> impl Iterator<Item = &str> {
    let bytes = text.as_bytes();
    let edge = |index: usize| -> bool {
        match bytes.get(index) {
            None => true,
            Some(byte) => {
                !(byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'-' | b'_' | b'.'))
            }
        }
    };
    let mut start = 0usize;
    std::iter::from_fn(move || {
        while start < bytes.len() {
            if !bytes[start].is_ascii_digit() {
                start += 1;
                continue;
            }
            let from = start;
            while start < bytes.len() && bytes[start].is_ascii_digit() {
                start += 1;
            }
            let leading = from == 0 || edge(from - 1);
            if leading && edge(start) {
                return Some(&text[from..start]);
            }
        }
        None
    })
}

/// Whether an answer already reports this counted fact.
fn answer_carries(answer: &str, count: &str) -> bool {
    let digits: String = count.chars().filter(char::is_ascii_digit).collect();
    !digits.is_empty() && standalone_numbers(answer).any(|number| number == digits)
}

/// The most recent countable fact a tool actually returned, as `"<n>개"`.
///
/// Deterministic and transcript-only: the number is one a tool reported, never
/// one the harness inferred. `None` when nothing counted anything, at which
/// point no caller may invent a number.
///
/// **Unattributed**, which is why every product caller goes through
/// [`attributed_count`] instead: this answers "did anything count something",
/// not "did the thing the user asked about count something".
pub fn count_from_transcript(transcript: &[Value]) -> Option<String> {
    transcript
        .iter()
        .rev()
        .find_map(|step| step.get("result").and_then(countable))
}
