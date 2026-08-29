//! Transcript shaping and the deterministic facts the critic is handed.
//!
//! Ports of `latticeai.core.agent_helpers`: `_truncate_strings`,
//! `compact_transcript`, `files_written`, `artifact_checklist`,
//! `requirement_coverage`, `filter_learnings`, plus the two budget records.
//!
//! One thing worth naming, because the brief for this port assumed otherwise:
//! **none of these functions touch the filesystem.** `requirement_coverage`
//! answers "was every requested file written?" from the *transcript*, matching
//! declared manifest paths against the basenames of successful write steps. The
//! loop's only real disk reads are the pre-write snapshot and the fail-closed
//! overwrite guard's existence check, and both live in `execution`.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

use crate::kernel::state::AgentState;
use crate::parse::pystr::{char_len, char_slice, is_truthy, py_str};

mod counts;
pub use counts::*;
mod summaries;
pub use summaries::*;

/// Executor/critic prompt shaping caps.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TranscriptBudget {
    pub window: usize,
    pub result_chars: usize,
    pub verify_chars: usize,
}

impl Default for TranscriptBudget {
    fn default() -> Self {
        Self {
            window: 8,
            result_chars: 700,
            verify_chars: 1200,
        }
    }
}

/// Per-phase token budgets for the agent loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PhaseBudgets {
    pub plan_tokens: u32,
    pub execute_tokens: u32,
    pub verify_tokens: u32,
    pub memory_tokens: u32,
}

impl Default for PhaseBudgets {
    fn default() -> Self {
        Self {
            plan_tokens: 1024,
            execute_tokens: 4096,
            verify_tokens: 512,
            memory_tokens: 256,
        }
    }
}

/// `latticeai.core.config._int`: blank or unparseable falls back to the default.
fn env_int(key: &str, default: i64) -> i64 {
    match std::env::var(key) {
        Ok(raw) if !raw.trim().is_empty() => raw.trim().parse::<i64>().unwrap_or(default),
        _ => default,
    }
}

impl TranscriptBudget {
    /// The `from_env` constructor, floors included.
    pub fn from_env() -> Self {
        let default = Self::default();
        let cap = |key: &str, fallback: usize, floor: i64| {
            env_int(key, fallback as i64).max(floor) as usize
        };
        Self {
            window: cap("LATTICEAI_AGENT_TRANSCRIPT_WINDOW", default.window, 2),
            result_chars: cap(
                "LATTICEAI_AGENT_TRANSCRIPT_CHARS",
                default.result_chars,
                120,
            ),
            verify_chars: cap("LATTICEAI_AGENT_VERIFY_CHARS", default.verify_chars, 200),
        }
    }
}

/// The smallest budget worth asking for — one action's worth of tokens.
pub const MIN_PHASE_TOKENS: u32 = 128;

/// The largest `max_tokens` the worker's completion seam accepts.
///
/// The worker answers `422 agent_seam.max_tokens_out_of_range` above this, and
/// the loop has no way to explain that refusal: by the time it arrives, the
/// phase is already running and all the user sees is a step that failed with a
/// status code. `LATTICEAI_AGENT_EXECUTE_TOKENS=20000` used to break every
/// execute phase that way. Clamping here turns a misconfiguration into the
/// largest completion the seam will actually serve, and says so on stderr once,
/// at startup, where an operator can act on it.
pub const MAX_PHASE_TOKENS: u32 = 8192;

/// What a configured phase budget actually becomes, and the one warning worth
/// printing on the way.
///
/// Pure apart from the `eprintln!`, so the clamp can be tested without touching
/// the process environment — `PhaseBudgets::from_env`'s own test asserts that an
/// unset environment is the default record, and a sibling test that set
/// variables would race it.
fn clamp_phase_tokens(key: &str, asked: i64) -> u32 {
    if asked > MAX_PHASE_TOKENS as i64 {
        eprintln!(
            "lattice-agent: {key}={asked} is above the worker's max_tokens \
             ceiling ({MAX_PHASE_TOKENS}); using {MAX_PHASE_TOKENS}. Anything \
             higher is refused by the completion seam with a 422 the loop \
             cannot explain."
        );
    }
    asked.clamp(MIN_PHASE_TOKENS as i64, MAX_PHASE_TOKENS as i64) as u32
}

impl PhaseBudgets {
    /// The `from_env` constructor. A misconfigured value floors at one action
    /// ([`MIN_PHASE_TOKENS`]) and is capped at what the worker seam accepts
    /// ([`MAX_PHASE_TOKENS`]).
    pub fn from_env() -> Self {
        let default = Self::default();
        let cap = |key: &str, fallback: u32| clamp_phase_tokens(key, env_int(key, fallback as i64));
        Self {
            plan_tokens: cap("LATTICEAI_AGENT_PLAN_TOKENS", default.plan_tokens),
            execute_tokens: cap("LATTICEAI_AGENT_EXECUTE_TOKENS", default.execute_tokens),
            verify_tokens: cap("LATTICEAI_AGENT_VERIFY_TOKENS", default.verify_tokens),
            memory_tokens: cap("LATTICEAI_AGENT_MEMORY_TOKENS", default.memory_tokens),
        }
    }
}

/// Deep copy with every string capped at `limit` **characters**.
pub fn truncate_strings(value: &Value, limit: usize) -> Value {
    match value {
        Value::String(text) => {
            let length = char_len(text);
            if length <= limit {
                return Value::String(text.clone());
            }
            Value::String(format!(
                "{}…[+{} chars]",
                char_slice(text, limit),
                length - limit
            ))
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, item)| (key.clone(), truncate_strings(item, limit)))
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| truncate_strings(item, limit))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// Bounded executor view of a transcript: `window` recent steps in full, the
/// rest reduced to one line each behind a count.
pub fn compact_transcript(transcript: &[Value], window: usize, result_chars: usize) -> Vec<Value> {
    if transcript.len() <= window {
        return transcript
            .iter()
            .map(|step| truncate_strings(step, result_chars))
            .collect();
    }
    let split = transcript.len() - window;
    let (older, recent) = transcript.split_at(split);
    let mut out = vec![json!({
        "summarized_older_steps": older.len(),
        "note": "older steps compacted — full detail retained in the run record",
    })];
    for step in older {
        let mut entry = Map::new();
        entry.insert(
            "state".into(),
            step.get("state").cloned().unwrap_or(Value::Null),
        );
        for key in ["action", "verdict", "retry_attempt"] {
            match step.get(key) {
                Some(Value::Null) | None => {}
                Some(value) => {
                    entry.insert(key.into(), value.clone());
                }
            }
        }
        if step.get("error").is_some_and(is_truthy) {
            let error = py_str(&step["error"]);
            entry.insert("error".into(), json!(char_slice(&error, 160)));
        } else if let Some(result) = step.get("result").filter(|value| value.is_object()) {
            entry.insert("ok".into(), json!(true));
            let path = result
                .get("path")
                .filter(|value| is_truthy(value))
                .or_else(|| step.get("args").and_then(|args| args.get("path")))
                .filter(|value| is_truthy(value));
            if let Some(path) = path {
                entry.insert("path".into(), json!(py_str(path)));
            }
        }
        out.push(Value::Object(entry));
    }
    out.extend(
        recent
            .iter()
            .map(|step| truncate_strings(step, result_chars)),
    );
    out
}

fn is_executing(step: &Value) -> bool {
    step.get("state").and_then(Value::as_str) == Some(AgentState::Executing.as_str())
}

fn is_file_create(step: &Value, file_create_actions: &BTreeSet<String>) -> bool {
    step.get("action")
        .and_then(Value::as_str)
        .is_some_and(|action| file_create_actions.contains(action))
}

/// `result["path"] or args["path"]`, as a string, when either is truthy.
fn written_path(step: &Value) -> Option<String> {
    let from_result = step
        .get("result")
        .and_then(|result| result.get("path"))
        .filter(|value| is_truthy(value));
    let from_args = step
        .get("args")
        .and_then(|args| args.get("path"))
        .filter(|value| is_truthy(value));
    from_result.or(from_args).map(py_str)
}

/// Ordered unique paths of files this run successfully wrote.
pub fn files_written(transcript: &[Value], file_create_actions: &BTreeSet<String>) -> Vec<String> {
    let mut seen: Vec<String> = Vec::new();
    for step in transcript {
        if !is_executing(step) || !is_file_create(step, file_create_actions) {
            continue;
        }
        if !step.get("result").is_some_and(Value::is_object) {
            continue;
        }
        if let Some(path) = written_path(step) {
            if !seen.contains(&path) {
                seen.push(path);
            }
        }
    }
    seen
}

/// A file creation proven by the transcript, with its size in bytes if recorded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProvenFile {
    pub path: String,
    pub bytes: Option<u64>,
}

/// Ordered unique files this run successfully created, with byte size if recorded.
pub fn proven_created_files(
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> Vec<ProvenFile> {
    let mut files: Vec<ProvenFile> = Vec::new();
    for step in transcript {
        if !is_executing(step) || !is_file_create(step, file_create_actions) {
            continue;
        }
        if step.get("error").is_some() {
            continue;
        }
        let Some(result) = step.get("result").filter(|value| value.is_object()) else {
            continue;
        };
        if let Some(created) = result.get("created_files").and_then(Value::as_array) {
            for path in created {
                let path = py_str(path);
                if !files.iter().any(|f| f.path == path) {
                    files.push(ProvenFile { path, bytes: None });
                }
            }
            continue;
        }
        if let Some(path) = written_path(step) {
            let bytes = result
                .get("bytes")
                .and_then(|v| v.as_u64().or_else(|| v.as_i64().map(|n| n as u64)));
            if let Some(existing) = files.iter_mut().find(|f| f.path == path) {
                if bytes.is_some() {
                    existing.bytes = bytes;
                }
            } else {
                files.push(ProvenFile { path, bytes });
            }
        }
    }
    files
}

/// Whether an answer is a bare negation contradicting proven work.
///
/// Disproven bare negations ("I did nothing.", "아무것도 하지 않았습니다")
/// are dropped when the transcript proves artifacts were created.
pub fn is_bare_negation(text: &str) -> bool {
    let cleaned: String = text
        .trim()
        .trim_matches(|c: char| {
            c.is_ascii_punctuation()
                || c == '.'
                || c == '!'
                || c == '?'
                || c == ','
                || c == '"'
                || c == '\''
                || c == '`'
                || c == '“'
                || c == '”'
                || c == '‘'
                || c == '’'
        })
        .trim()
        .to_lowercase();
    if cleaned.is_empty() {
        return false;
    }
    matches!(
        cleaned.as_str(),
        "i did nothing"
            | "i have done nothing"
            | "i did not do anything"
            | "i didn't do anything"
            | "i have not done anything"
            | "i haven't done anything"
            | "nothing was done"
            | "nothing done"
            | "nothing has been done"
            | "no action was taken"
            | "no action taken"
            | "no actions were taken"
            | "no actions taken"
            | "no changes were made"
            | "no changes made"
            | "no files were created"
            | "no file was created"
            | "no file created"
            | "no files created"
            | "did nothing"
            | "done nothing"
            | "nothing"
            | "아무것도 하지 않았습니다"
            | "아무것도 하지 않았다"
            | "아무것도 하지 못했습니다"
            | "아무것도 하지 못했다"
            | "아무것도 안 했습니다"
            | "아무것도 안 했다"
            | "아무것도 안했습니다"
            | "아무것도 안했다"
            | "아무 작업도 하지 않았습니다"
            | "아무 작업도 하지 않았다"
            | "아무런 작업도 하지 않았습니다"
            | "아무런 작업도 하지 않았다"
            | "수행한 작업이 없습니다"
            | "수행된 작업이 없습니다"
            | "아무것도 수행하지 않았습니다"
            | "아무것도 수행하지 않았다"
            | "한 일이 없습니다"
            | "한 게 없습니다"
            | "한게 없습니다"
            | "작업을 수행하지 않았습니다"
            | "작업을 하지 않았습니다"
            | "아무것도 변경하지 않았습니다"
            | "아무것도 생성하지 않았습니다"
            | "파일을 생성하지 않았습니다"
            | "파일을 작성하지 않았습니다"
            | "아무것도 하지 않음"
            | "수행하지 않음"
    )
}

/// Whether the answer already mentions this file path or its basename.
pub fn answer_names_file(answer: &str, path: &str) -> bool {
    if path.is_empty() {
        return false;
    }
    let hay = answer.to_lowercase();
    let p_lower = path.to_lowercase();
    let n_lower = path_name(path).to_lowercase();
    if !p_lower.is_empty() && hay.contains(&p_lower) {
        return true;
    }
    if !n_lower.is_empty() && hay.contains(&n_lower) {
        return true;
    }
    false
}

/// Whether the answer contains a negation phrase contradicting proven work.
pub fn is_negation_sentence(text: &str) -> bool {
    let hay = text.to_lowercase();
    hay.contains("i did nothing")
        || hay.contains("i have done nothing")
        || hay.contains("i didn't do anything")
        || hay.contains("i did not do anything")
        || hay.contains("nothing was done")
        || hay.contains("no action was taken")
        || hay.contains("no actions were taken")
        || hay.contains("no files were created")
        || hay.contains("아무것도 하지 않았")
        || hay.contains("아무것도 하지 못했")
        || hay.contains("아무것도 안 했")
        || hay.contains("아무것도 안했")
        || hay.contains("아무 작업도 하지")
        || hay.contains("아무런 작업도 하지")
        || hay.contains("수행한 작업이 없")
        || hay.contains("수행된 작업이 없")
        || hay.contains("아무것도 수행하지")
        || hay.contains("한 일이 없")
        || hay.contains("작업을 수행하지 않았")
        || hay.contains("작업을 하지 않았")
}

/// The one fact a successful tool result established, in a few characters.
///
/// Shared since v12.0.0, because two callers need the same sentence: the
/// guided critic's evidence lines, which is where it started, and
/// [`delivered_answer`] — a run that never reached `final` still did
/// something, and what it did is a fact this function already knows how to
/// state. Two implementations of "what did this call establish" would drift
/// into two different answers to one question.
///
/// Deliberately tiny and deliberately **only** what the result itself carries —
/// a count, a size, a path, a match tally. Nothing here summarises, infers or
/// rephrases: every branch reads a field the tool wrote, so a digest can never
/// claim something the run did not do. `None` when the result says nothing
/// countable, at which point the row stays the bare `ok` it always was.
pub fn result_digest(result: &Value) -> Option<String> {
    if let Some(items) = result.get("items").and_then(Value::as_array) {
        return Some(format!("항목 {}개 / {} items", items.len(), items.len()));
    }
    for key in ["matches", "hits", "count", "files_with_matches"] {
        match result.get(key) {
            Some(Value::Array(rows)) => {
                return Some(format!("{key} {}개 / {} {key}", rows.len(), rows.len()))
            }
            Some(Value::Number(number)) => return Some(format!("{key} {number}")),
            _ => {}
        }
    }
    if let Some(bytes) = result.get("bytes").and_then(Value::as_u64) {
        let path = result
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default();
        return Some(if path.is_empty() {
            format!("{bytes} bytes")
        } else {
            format!("{path}, {bytes} bytes")
        });
    }
    if let Some(text) = result.get("text").and_then(Value::as_str) {
        let text = text.trim();
        if !text.is_empty() {
            return Some(format!("\"{}\"", crate::parse::pystr::char_slice(text, 80)));
        }
    }
    result
        .get("path")
        .and_then(Value::as_str)
        .filter(|path| !path.is_empty())
        .map(str::to_string)
}

/// What this run delivered, said in one line, **read only off the transcript**.
///
/// `None` when the run wrote no file, which is the honest answer to "what did
/// you deliver" for a run that delivered nothing. Nothing here summarises,
/// infers or rephrases: the paths are the ones the file-create steps recorded
/// with a result, so this sentence can never claim work that did not happen.
///
/// It exists because [`crate::kernel::agentloop`]'s "an answer the run already
/// produced outranks the apology" rule had a hole (v12.0.0): it keys on
/// `ctx.final_message`, and that string is only ever written by the `final`
/// action. A run whose executor never reached `final` — stopped by the loop
/// guard, or by a step budget — therefore had *no* answer to outrank anything
/// with, and two live 2B runs that had written the requested file, and had it
/// on disk, ended `FAILED` with "처리 중 문제가 발생했습니다" because a
/// 0.6-confidence critic said FAIL four times. The work was real; only the
/// sentence was missing.
pub fn delivered_answer(
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> Option<String> {
    let written = files_written(transcript, file_create_actions);
    if !written.is_empty() {
        return Some(format!("{} 파일을 저장했습니다.", written.join(", ")));
    }
    // No file, but something still ran and established something — a count, a
    // match tally, a size. That is what the run has to report, and reporting it
    // is the difference between "we searched and found none" and an apology
    // that mentions neither the search nor the result.
    for step in transcript.iter().rev() {
        if !is_executing(step) {
            continue;
        }
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        if matches!(action, "final" | "parse_error") {
            continue;
        }
        let Some(result) = step.get("result").filter(|value| value.is_object()) else {
            continue;
        };
        if let Some(digest) = result_digest(result) {
            return Some(format!("{action} 실행 결과: {digest}"));
        }
    }
    None
}

/// Deterministic artifact facts for the critic: one row per written file with
/// its sanitize/repair honesty flags.
pub fn artifact_checklist(
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> Vec<Value> {
    let mut checklist = Vec::new();
    for step in transcript {
        if !is_executing(step) || !is_file_create(step, file_create_actions) {
            continue;
        }
        if !step.get("result").is_some_and(Value::is_object) {
            continue;
        }
        let Some(path) = written_path(step) else {
            continue;
        };
        let meta = step
            .get("content_sanitize")
            .filter(|value| is_truthy(value));
        let flag = |key: &str| meta.and_then(|meta| meta.get(key)).is_some_and(is_truthy);
        checklist.push(json!({
            "path": path,
            "sanitized": flag("sanitized"),
            "repaired": flag("repaired"),
        }));
    }
    checklist
}

/// `Path(path).name` for the `/`-separated paths the loop carries.
pub fn path_name(path: &str) -> &str {
    path.trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or(path)
}

fn requirement_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        // `re.MULTILINE`; `\s` is Unicode whitespace in both engines and `.`
        // still excludes newlines, which is what bounds a requirement to a line.
        Regex::new(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+(.{3,120})$").expect("ported pattern must compile")
    })
}

/// Did the run produce what the request actually asked for?
pub fn requirement_coverage(
    user_message: &str,
    transcript: &[Value],
    file_create_actions: &BTreeSet<String>,
) -> Value {
    let written = files_written(transcript, file_create_actions);
    let written_names: Vec<String> = written
        .iter()
        .map(|path| path_name(path).to_lowercase())
        .collect();
    // `str(spec.get("path") or "")` — a manifest is our own data, but the
    // coercion is the original's and costs nothing to keep.
    let mut declared: Vec<String> = crate::parse::inference::infer_project_manifest(user_message)
        .and_then(|manifest| manifest["files"].as_array().cloned())
        .map(|files| {
            files
                .iter()
                .map(|spec| match spec.get("path") {
                    Some(path) if is_truthy(path) => py_str(path),
                    _ => String::new(),
                })
                .collect()
        })
        .unwrap_or_default();
    // **The second source of a declared file** (v12.0.0). The manifest is only
    // ever populated for a multi-file *scaffold* request; `infer_project_manifest`
    // returns `None` the moment the message names a filename itself, which is
    // the shape of every single-file request there is. So for
    // `notes/summary.md로 저장해줘` the declared list was empty, the missing
    // list was empty, and this function reported `complete: true` over a run
    // that wrote nothing — the one deterministic gate that outranks a critic's
    // PASS was unreachable exactly where a weak model needs it.
    //
    // Only a filename the request marked as a *destination* is added
    // ([`crate::parse::inference::requested_output_paths`]), so a file the run
    // was asked to read stays out and a request that named no destination
    // declares exactly what it declared before.
    for path in crate::parse::inference::requested_output_paths(user_message) {
        let already = declared
            .iter()
            .any(|known| path_name(known).eq_ignore_ascii_case(path_name(&path)));
        if !already {
            declared.push(path);
        }
    }
    let missing: Vec<String> = declared
        .iter()
        .filter(|path| !path.is_empty() && !written_names.contains(&path_name(path).to_lowercase()))
        .cloned()
        .collect();
    let requirements: Vec<String> = requirement_line()
        .captures_iter(user_message)
        .filter_map(|captures| captures.ok())
        .filter_map(|captures| {
            captures
                .get(1)
                .map(|group| group.as_str().trim().to_string())
        })
        .take(10)
        .collect();
    json!({
        "files": {"declared": declared, "written": written},
        "missing_files": missing,
        "requirements": requirements,
        "complete": missing.is_empty(),
    })
}

/// Does the request ask *how many* of something there are?
///
/// Lifted out of [`crate::kernel::agentloop::guided`] in v12.0.0 so both dials
/// read one rule. A count question's whole deliverable is the number in the
/// final message, and a model that ran the tool and then answered in prose has
/// done the work and not reported it — which is a harness gap, not a refusal.
///
/// `count` is matched as a **whole word**. As a bare substring it fires on
/// `account`, `discount`, `countries` and `encounter`, which was harmless while
/// the only consequence was an offered default — and stops being harmless the
/// moment a verdict gate keys on it ([`answer_owes_a_count`]): "create an
/// account page" would have been held back for want of a number nobody asked
/// for. The Korean markers need no such guard; `개수` and `몇 개` are not
/// fragments of other words.
pub fn request_asks_for_a_count(request: &str) -> bool {
    let hay = request.to_lowercase();
    hay.contains("개수")
        || hay.contains("몇 개")
        || hay.contains("how many")
        || contains_ascii_word(&hay, "count")
}

/// `word` as a whole ASCII token in `text`, case-insensitively.
///
/// One implementation for the two places that needed it — this rule and the
/// critic's plain-text last rung ([`crate::kernel::agentloop::verification`]),
/// which had its own byte-identical copy. A word boundary here is
/// "not an ASCII alphanumeric", which is what keeps `PASS` out of `passed`
/// and `count` out of `account` while leaving `개수` — where the neighbours are
/// Hangul, not ASCII — matching as it always did.
pub fn contains_ascii_word(text: &str, word: &str) -> bool {
    let hay = text.as_bytes();
    let needle = word.as_bytes();
    if needle.is_empty() || needle.len() > hay.len() {
        return false;
    }
    (0..=hay.len() - needle.len()).any(|start| {
        hay[start..start + needle.len()].eq_ignore_ascii_case(needle)
            && (start == 0 || !hay[start - 1].is_ascii_alphanumeric())
            && {
                let after = start + needle.len();
                after == hay.len() || !hay[after].is_ascii_alphanumeric()
            }
    })
}

/// Requirement facts for the critic prompt, or `""` when there is nothing.
pub fn format_requirement_coverage(coverage: &Value) -> String {
    let mut lines: Vec<String> = Vec::new();
    let declared = coverage["files"]["declared"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if !declared.is_empty() {
        let written: Vec<String> = coverage["files"]["written"]
            .as_array()
            .map(|items| items.iter().map(py_str).collect())
            .unwrap_or_default();
        lines.push("Requested files (deterministic, from the request):".into());
        for path in &declared {
            let path = py_str(path);
            let got = written
                .iter()
                .any(|item| path_name(item).to_lowercase() == path_name(&path).to_lowercase());
            lines.push(format!(
                "- {path}: {}",
                if got { "written" } else { "MISSING" }
            ));
        }
    }
    let requirements = coverage["requirements"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if !requirements.is_empty() {
        lines.push(
            "Requirements the user listed explicitly — check each one against \
the artifacts, not against the plan:"
                .into(),
        );
        lines.extend(
            requirements
                .iter()
                .map(|item| format!("- {}", py_str(item))),
        );
    }
    if lines.is_empty() {
        String::new()
    } else {
        format!("\n\n{}", lines.join("\n"))
    }
}

/// The artifact checklist as the critic prompt renders it.
pub fn format_artifact_checklist(checklist: &[Value]) -> String {
    let lines: Vec<String> = checklist
        .iter()
        .map(|item| {
            let state = if item["repaired"] == json!(true) {
                "auto-REPAIRED scaffold"
            } else if item["sanitized"] == json!(true) {
                "sanitized model output"
            } else {
                "written as produced"
            };
            format!("- {}: {state}", py_str(&item["path"]))
        })
        .collect();
    format!(
        "Artifact checklist (deterministic, from the transcript):\n{}\nVerify each \
artifact actually fulfills the user's request. An auto-repaired scaffold is NOT \
completion unless its content satisfies what was asked.",
        lines.join("\n")
    )
}

fn trivial_learning() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(concat!(
            r"(?i)^(파일(을|이)?\s*(만들|생성|작성|저장)|작업(을|이)?\s*(완료|성공)|성공적으로",
            r"|task\s+(was\s+)?complet|file\s+(was\s+)?(creat|written|saved)",
            r"|(successfully\s+)?(created|completed|finished|done)\b)",
        ))
        .expect("ported pattern must compile")
    })
}

/// Drop trivial/duplicate learnings before they enter the brain.
pub fn filter_learnings(learnings: &[Value]) -> Vec<String> {
    let mut kept: Vec<String> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for raw in learnings {
        let text = match raw {
            value if is_truthy(value) => py_str(value).trim().to_string(),
            _ => String::new(),
        };
        let length = char_len(&text);
        if length < 12 {
            continue;
        }
        if length < 48 && trivial_learning().is_match(&text).unwrap_or(false) {
            continue;
        }
        let key = text.to_lowercase();
        if seen.insert(key) {
            kept.push(text);
        }
    }
    kept
}

#[cfg(test)]
mod tests;
