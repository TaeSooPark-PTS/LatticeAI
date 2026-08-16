//! What the reply says about the files a turn wrote.
//!
//! The payload is the **agent's** shape, not a second one: `status`, `steps`,
//! `state_history`, `final_state`, `created_files`, `artifacts`,
//! `routed_to_agent`, `action_route`, `brain_ingest`. Every client already
//! parses it — the SPA joins `created_files` with `artifacts` by path
//! (`agentPayloadFiles`), the VS Code extension reads `created_files`, and the
//! bridges read `response`. Keeping the shape is what let this branch add real
//! generation without a client change.
//!
//! Two keys carry the honesty this module exists for:
//!
//! * `artifacts[].valid` / `artifacts[].repaired` — the validator's verdict on
//!   the bytes on disk and whether repair produced them. Both were literals
//!   (`true` / `false`) until v11.9.0.
//! * `generation.repaired` — the same fact where the SPA's badge reads it, plus
//!   `generation.attempts`, one record per model attempt. Absent entirely when
//!   the user typed the content: a reply that never generated anything must not
//!   report a generation.

use lattice_agent::state::AgentState;
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::intents::previewable;

use super::{Written, MAX_PROJECT_FILES};

/// The agent-shaped payload every client already parses.
///
/// `authored` is what decides whether a `generation` key exists at all;
/// `complete` is false when a file the user asked for could not be written, and
/// turns the terminal state into `NEEDS_REVIEW` so a partial bundle never
/// renders as a clean success.
pub(super) fn payload(
    workspace: &std::path::Path,
    files: &[Written],
    answer: &str,
    attempts: &[Value],
    authored: bool,
    complete: bool,
) -> Value {
    let steps: Vec<Value> = files
        .iter()
        .map(|file| {
            json!({
                "state": AgentState::Executing.as_str(),
                "action": file.action,
                "args": {"path": file.requested},
                "result": {"path": file.path, "bytes": file.bytes},
            })
        })
        .collect();
    let mut history: Vec<Value> = files
        .iter()
        .map(|_| json!(AgentState::Executing.as_str()))
        .collect();
    let final_state = if complete {
        AgentState::Done
    } else {
        // A file the user asked for that never appeared must not render as
        // a clean success; the SPA shows NEEDS_REVIEW as a warning.
        AgentState::NeedsReview
    };
    history.push(json!(final_state.as_str()));
    let created: Vec<Value> = files
        .iter()
        .map(|file| {
            json!({
                "path": file.path,
                "filename": file.filename,
                "bytes": file.bytes,
                "action": file.action,
            })
        })
        .collect();
    let artifacts: Vec<Value> = files
        .iter()
        .map(|file| {
            json!({
                "kind": "file",
                "path": file.path,
                "filename": file.filename,
                "bytes": file.bytes,
                "previewable": previewable(&file.path),
                "valid": file.valid,
                "repaired": file.repaired,
            })
        })
        .collect();

    let mut payload = OrderedMap::new();
    payload.insert("status", json!("ok"));
    payload.insert("response", json!(answer));
    payload.insert("workspace", json!(workspace.to_string_lossy().into_owned()));
    payload.insert("steps", json!(steps));
    payload.insert("state_history", json!(history));
    payload.insert("final_state", json!(final_state.as_str()));
    payload.insert("created_files", json!(created));
    payload.insert("artifacts", json!(artifacts));
    if authored {
        // Where the SPA's "자동 보정됨" badge reads from — the same key the
        // agent loop's own direct-file fallback sets.
        payload.insert(
            "generation",
            json!({
                "repaired": files.iter().any(|file| file.repaired),
                "attempts": attempts,
            }),
        );
    }
    payload.insert("routed_to_agent", json!(true));
    payload.insert(
        "action_route",
        json!(if authored {
            "chat_file_generation"
        } else {
            "direct_write_file"
        }),
    );
    if files.len() == 1 {
        if let Some(ingest) = files[0].ingest.clone() {
            payload.insert("brain_ingest", ingest);
        }
    } else {
        // The bundle shape the SPA joins by path.
        let receipts: Vec<Value> = files
            .iter()
            .filter_map(|file| {
                file.ingest.clone().map(|mut receipt| {
                    if let Some(object) = receipt.as_object_mut() {
                        object.insert("path".into(), json!(file.path));
                    }
                    receipt
                })
            })
            .collect();
        if !receipts.is_empty() {
            payload.insert("brain_ingest", json!(receipts));
        }
    }
    serde_json::to_value(payload).unwrap_or(Value::Null)
}

pub(super) fn file_name_of(path: &str) -> String {
    std::path::Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(path)
        .to_string()
}

/// "…를 만들었습니다." — the sentence the fixtures pin, in the caller's language.
pub(super) fn created_sentence(lang: &str, path: &str, renamed: bool) -> String {
    let mut answer = if lang == "en" {
        format!("Created {path}.")
    } else {
        format!("{path} 파일을 만들었습니다.")
    };
    if renamed {
        answer.push_str(if lang == "en" {
            " (A file with that name already existed, so it was saved under a new one.)"
        } else {
            " (같은 이름의 파일이 있어 새 이름으로 저장했습니다.)"
        });
    }
    answer
}

/// The multi-file sentence: what was written, what was deferred, what failed.
pub(super) fn project_sentence(
    lang: &str,
    written: &[Written],
    deferred: &[String],
    failed: &[String],
) -> String {
    let names = |paths: &[String]| paths.join(", ");
    let made = written
        .iter()
        .map(|file| file.path.clone())
        .collect::<Vec<_>>()
        .join(", ");
    let mut answer = if lang == "en" {
        format!("Created {made}.")
    } else {
        format!("{made} 파일을 만들었습니다.")
    };
    if !failed.is_empty() {
        // Deliberately not "the model refused": the same list holds a file
        // whose write failed, and a sentence that names the wrong cause is a
        // worse answer than one that names only the fact.
        answer.push_str(&if lang == "en" {
            format!(" {} could not be written, so nothing was.", names(failed))
        } else {
            format!(" {} 은(는) 만들지 못해 건너뛰었습니다.", names(failed))
        });
    }
    if !deferred.is_empty() {
        answer.push_str(&if lang == "en" {
            format!(
                " This surface writes {MAX_PROJECT_FILES} files per turn, so {} {} not written yet — ask again to continue.",
                names(deferred),
                if deferred.len() == 1 { "was" } else { "were" }
            )
        } else {
            format!(
                " 한 번에 {MAX_PROJECT_FILES}개까지 만들기 때문에 {} 은(는) 아직 만들지 않았습니다. 이어서 요청해 주세요.",
                names(deferred)
            )
        });
    }
    answer
}
