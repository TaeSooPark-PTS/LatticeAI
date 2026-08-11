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

use crate::pystr::{char_len, char_slice, is_truthy, py_str};
use crate::state::AgentState;

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

impl PhaseBudgets {
    /// The `from_env` constructor. A misconfigured value floors at one action.
    pub fn from_env() -> Self {
        let default = Self::default();
        let cap = |key: &str, fallback: u32| env_int(key, fallback as i64).max(128) as u32;
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
    let declared: Vec<String> = crate::inference::infer_project_manifest(user_message)
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
mod tests {
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
    fn basenames_follow_pathlib() {
        assert_eq!(path_name("src/main.jsx"), "main.jsx");
        assert_eq!(path_name("index.html"), "index.html");
        assert_eq!(path_name("a/b/"), "b");
        assert_eq!(path_name(""), "");
    }
}
