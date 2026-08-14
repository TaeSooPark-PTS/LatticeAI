//! Staging a change proposal, natively (v11.6.0, WP-P1c).
//!
//! Under `strict` the loop does not apply a mutation: it *stages* it as a
//! review item the user approves in the Review Center, and nothing touches the
//! file until they do. Until this module existed that staging was one
//! `POST /agent/change-proposal` to the Python worker, whose
//! `ChangeProposalService.review` classified the call, computed the diff and
//! wrote the review item. WP-P1a retired that route, so the whole verdict is
//! computed here now — [`Governor::review`] is a port of that method, and the
//! payload it stages is the payload the Review Center's approve path reads
//! (`lattice_platform::change_proposals`).
//!
//! ## Where a staged proposal is written
//!
//! The review-item store belongs to `lattice-platform` (WP-R7's
//! `GovernanceState`, over `workspace_os.json`), and this crate cannot depend
//! on it — the dependency runs the other way (`platform → agent`). So staging
//! goes through a port, [`ProposalStore`], exactly as the hook lifecycle does:
//!
//! * **In the product**, `lattice-platform` implements this trait for
//!   `GovernanceState` and the host injects it. That is the only correct wiring
//!   when the Review Center runs in the same process, because `GovernanceState`
//!   holds the document in memory and mirrors it into SQLite: a second writer
//!   appending to the JSON file alone would be invisible to it, and its next
//!   save would overwrite the appended item.
//! * **Standalone** (this crate's own loop routes, with no Review Center),
//!   [`JsonProposalStore`] appends to the same `workspace_os.json` in the same
//!   shape, so a proposal staged by a bare `lattice-agent` is listed by a
//!   Review Center opened over that data directory afterwards. The format's
//!   owner is `lattice_platform::review_queue` — every field, the id
//!   derivation and the file layout are that module's, mirrored here.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::governor::{classify_tool_call, Classification, CHANGE_ADDITIVE};
use crate::policy::ToolPolicy;
use crate::pydiff::{splitlines, unified_diff};
use crate::pystr::{char_slice, is_truthy, py_str};
use crate::sandbox::Workspace;

/// `_MAX_STAGED_BYTES` — the cap on the content a proposal carries.
///
/// Python slices a `str` with it, so it counts **characters**, and the same
/// constant is applied to the base snapshot as a **byte** truncation before
/// decoding. Both are reproduced where they are used.
pub const MAX_STAGED_BYTES: usize = 400_000;
/// `_MAX_DIFF_LINES` — a staged diff is truncated, never summarised.
pub const MAX_DIFF_LINES: usize = 400;
/// `_SMALL_TIER_DIFF_LINES` — at most this many diff lines is a `small` change.
pub const SMALL_TIER_DIFF_LINES: usize = 40;

/// `ChangeProposalService.governed_tools` — the two tools it stages for.
pub const GOVERNED_TOOLS: [&str; 2] = ["edit_file", "write_file"];

/// The file the review items live in (`lattice_core::db::state_files::WORKSPACE_OS`).
const WORKSPACE_OS_FILE: &str = "workspace_os.json";
/// The workspace a review item belongs to when the caller names none.
const DEFAULT_WORKSPACE_ID: &str = "personal";

/// One review item to create — `ReviewQueueService.create`'s arguments.
#[derive(Debug, Clone)]
pub struct NewReviewItem {
    pub title: String,
    pub summary: String,
    pub source: String,
    pub kind: String,
    pub payload: Value,
    pub provenance: Value,
    pub user_email: Option<String>,
    pub workspace_id: Option<String>,
}

/// Where a staged proposal is persisted.
///
/// `create` answers the **stored item**, id included, because the loop puts
/// that id on the transcript and in the audit trail: "제안으로 저장했습니다"
/// with no id is a claim the user cannot check.
pub trait ProposalStore: std::fmt::Debug + Send + Sync {
    /// Append one review item; `Err` is a staging failure, never a panic.
    fn create(&self, item: &NewReviewItem) -> Result<Value, String>;
}

/// The standalone store: `<data_dir>/workspace_os.json`.
///
/// Byte-compatible with `lattice_platform::review_queue` — see the module
/// docs for when this is the wrong writer.
#[derive(Debug, Clone)]
pub struct JsonProposalStore {
    data_dir: PathBuf,
    /// One writer at a time. Appending is read-modify-write over a whole
    /// document, so two runs staging at once would otherwise keep whichever
    /// wrote last and silently drop the other's proposal. Shared across clones;
    /// it does not cover a second *process* (nor did Python's store).
    lock: Arc<Mutex<()>>,
}

impl JsonProposalStore {
    /// A store over an explicit data directory.
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            data_dir: data_dir.into(),
            lock: Arc::new(Mutex::new(())),
        }
    }

    /// `LATTICEAI_DATA_DIR`, or `$HOME/.ltcai` — [`crate::runs::default_runs_dir`]'s rule.
    pub fn from_env() -> Self {
        let configured = std::env::var("LATTICEAI_DATA_DIR").unwrap_or_default();
        if !configured.trim().is_empty() {
            return Self::new(PathBuf::from(configured.trim()));
        }
        let base = std::env::var_os("HOME")
            .map(PathBuf::from)
            .filter(|home| !home.as_os_str().is_empty())
            .map_or_else(|| PathBuf::from(".ltcai"), |home| home.join(".ltcai"));
        Self::new(base)
    }

    /// The document this store appends to.
    pub fn state_path(&self) -> PathBuf {
        self.data_dir.join(WORKSPACE_OS_FILE)
    }
}

impl ProposalStore for JsonProposalStore {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        if item.title.trim().is_empty() {
            // `WorkspaceReviewItems.create_review_item`'s own ValueError.
            return Err("title is required".into());
        }
        let path = self.state_path();
        let _guard = self
            .lock
            .lock()
            .map_err(|_| "proposal store lock poisoned")?;
        let mut state = read_state(&path);
        let existing: Vec<String> = state
            .get("review_items")
            .and_then(Value::as_array)
            .map(|rows| {
                rows.iter()
                    .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let stored = build_review_item(item, &existing);
        let rows = state
            .as_object_mut()
            .ok_or("workspace_os.json is not an object")?
            .entry("review_items")
            .or_insert_with(|| json!([]));
        rows.as_array_mut()
            .ok_or("workspace_os.json review_items is not a list")?
            .push(stored.clone());
        write_state(&path, &mut state)?;
        Ok(stored)
    }
}

/// One review item, exactly as `lattice_platform::review_queue` builds it.
fn build_review_item(item: &NewReviewItem, existing: &[String]) -> Value {
    let now = now_iso();
    let user_email = item
        .user_email
        .clone()
        .map_or(Value::Null, |email| json!(email));
    let mut item_id = review_id(item, &user_email, &now, None);
    let mut seq = 0u32;
    while existing.iter().any(|id| id == &item_id) {
        seq += 1;
        item_id = review_id(item, &user_email, &now, Some(seq));
    }
    json!({
        "id": item_id,
        "status": "pending",
        "title": item.title,
        "summary": item.summary,
        "source": item.source,
        "kind": item.kind,
        "payload": item.payload,
        "provenance": item.provenance,
        "snoozed_until": null,
        "user_email": user_email,
        "workspace_id": item.workspace_id.as_deref().unwrap_or(DEFAULT_WORKSPACE_ID),
        "created_at": now,
        "updated_at": now,
    })
}

/// `review-{_json_hash([title, source, kind, user_email, now, seq?])[:16]}`.
fn review_id(item: &NewReviewItem, user_email: &Value, now: &str, seq: Option<u32>) -> String {
    let mut parts = vec![
        json!(item.title),
        json!(item.source),
        json!(item.kind),
        user_email.clone(),
        json!(now),
    ];
    if let Some(seq) = seq {
        parts.push(json!(seq));
    }
    let payload = serde_json::to_string(&Value::Array(parts)).unwrap_or_else(|_| "null".into());
    let digest: String = Sha256::digest(payload.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    format!("review-{}", &digest[..16])
}

fn read_state(path: &Path) -> Value {
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if value.is_object() {
                return value;
            }
        }
    }
    default_workspace_os()
}

/// The document `GovernanceState::open` starts from when there is no file.
fn default_workspace_os() -> Value {
    let now = now_iso();
    json!({
        "version": crate::VERSION,
        "identity": "AI Workspace OS",
        "created_at": now,
        "updated_at": now,
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": {
            "personal": {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "id": DEFAULT_WORKSPACE_ID,
                "name": "Personal Workspace",
                "type": "personal",
                "owner_user_id": null,
                "members": [],
                "status": "active",
                "created_at": now,
                "updated_at": now
            }
        },
        "review_items": [],
        "workflows": [],
        "workflow_runs": [],
        "agent_runs": [],
        "timeline": []
    })
}

/// `save_workspace_os`: stamp the version and `updated_at`, then replace.
///
/// The SQLite mirror `lattice-platform` also writes is **not** written here —
/// this crate has no SQLite dependency, and a store that mirrored half of the
/// pair would be worse than one that mirrors none. It is the reason the
/// in-process Review Center must inject its own store; the module docs say so.
fn write_state(path: &Path, state: &mut Value) -> Result<(), String> {
    if let Some(object) = state.as_object_mut() {
        object.insert("version".into(), json!(crate::VERSION));
        object.insert("updated_at".into(), json!(now_iso()));
    }
    let text = serde_json::to_string_pretty(state).map_err(|error| error.to_string())?;
    atomic_write(path, &format!("{text}\n"))
}

/// Temp file + rename, as `lattice_auth::atomic::write_text` (its owner).
fn atomic_write(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let mut temp = path.as_os_str().to_os_string();
    temp.push(".tmp");
    let temp = PathBuf::from(temp);
    if let Err(error) = std::fs::write(&temp, text) {
        let _ = std::fs::remove_file(&temp);
        return Err(error.to_string());
    }
    std::fs::rename(&temp, path).map_err(|error| {
        let _ = std::fs::remove_file(&temp);
        error.to_string()
    })
}

/// `now_iso()` — naive, second resolution, no offset (the store's format).
fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = (secs / 86_400) as i64;
    let rest = secs % 86_400;
    let (hour, min, sec) = (rest / 3600, (rest % 3600) / 60, rest % 60);
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

/// Howard Hinnant's `civil_from_days`, as `lattice_platform::review_queue` uses.
fn civil_from_days(mut days: i64) -> (i32, u32, u32) {
    days += 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let doe = (days - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (year as i32, month as u32, day as u32)
}

/// What the governor decided about one tool call.
///
/// `Silent` is Python's `{"decision": "none"}` — "I have nothing to say about
/// this call, fall through to the gates" — and it collapses the same three
/// situations the service collapsed: not a governed tool, no proposal required,
/// or an edit that cannot be computed deterministically.
#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    /// Nothing to say; the ordinary gates decide.
    Silent,
    /// An additive create: execute it without the extra approval prompt.
    AllowAdditive(Classification),
    /// Staged. The step must not execute.
    Proposed {
        classification: Classification,
        proposal: Value,
    },
    /// Staging was required and did not happen.
    ///
    /// Python logged the exception and answered `None`, which fell through to
    /// the gates — where a `strict` run is blocked by the approval gate anyway,
    /// so the failure was invisible. A change that had to be reviewed and was
    /// not is a fact about the run, so it is an error step here instead.
    Failed(String),
}

/// The governor port — `ChangeProposalService.review` and the staging it does.
#[derive(Debug, Clone, Copy)]
pub struct Governor<'a> {
    /// The sandbox the target path resolves in (`resolve_workspace_path`).
    pub workspace: &'a Workspace,
    /// Where the staged item is written.
    pub store: &'a dyn ProposalStore,
    pub user_email: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
    pub conversation_id: Option<&'a str>,
}

impl Governor<'_> {
    /// `review(name, args, policy=…)` — the verdict for one tool call.
    pub fn review(&self, name: &str, args: &Map<String, Value>, policy: &ToolPolicy) -> Verdict {
        if !crate::in_set(&GOVERNED_TOOLS, name) {
            return Verdict::Silent;
        }
        let exists = |candidate: &str| self.path_is_file(candidate);
        let classification = classify_tool_call(name, args, policy, &exists);
        if classification.change_class == CHANGE_ADDITIVE {
            return Verdict::AllowAdditive(classification);
        }
        if !classification.proposal_required {
            return Verdict::Silent;
        }
        let path = str_arg(args, "path");
        let Some(after) = self.staged_content(name, args) else {
            // The edit cannot be computed deterministically (`old_string` is
            // absent or ambiguous) — let the normal tool path surface the real
            // error rather than staging a proposal for a change we cannot make.
            return Verdict::Silent;
        };
        match self.propose_file_update(&path, &after, &classification, policy) {
            Ok(proposal) => Verdict::Proposed {
                classification,
                proposal,
            },
            Err(error) => Verdict::Failed(error),
        }
    }

    /// `propose_file_update` — the staged payload, and the review item for it.
    pub fn propose_file_update(
        &self,
        path: &str,
        new_content: &str,
        classification: &Classification,
        policy: &ToolPolicy,
    ) -> Result<Value, String> {
        let (base_exists, before) = self.snapshot(path);
        let mut diff = unified_diff(
            &splitlines(&before),
            &splitlines(new_content),
            &format!("a/{path}"),
            &format!("b/{path}"),
            3,
        );
        diff.truncate(MAX_DIFF_LINES);
        let tier = if diff.len() <= SMALL_TIER_DIFF_LINES {
            "small"
        } else {
            "large"
        };
        // `{"proposed_by": …, "reason": …}` first, then the context entries
        // whose value is not None and whose key is not already present.
        let mut provenance = Map::new();
        provenance.insert("proposed_by".into(), json!("agent"));
        provenance.insert("reason".into(), json!(classification.reason));
        provenance.insert("tool".into(), json!(classification.tool));
        provenance.insert("change_class".into(), json!(classification.change_class));
        if !policy.risk.is_empty() {
            provenance.insert("risk".into(), json!(policy.risk));
        }
        if let Some(conversation_id) = self.conversation_id {
            provenance.insert("conversation_id".into(), json!(conversation_id));
        }
        provenance.insert("source_detail".into(), json!("agent change governor"));

        let payload = json!({
            "path": path,
            "diff": diff,
            "new_content": char_slice(new_content, MAX_STAGED_BYTES),
            "tier": tier,
            "before_bytes": before.len(),
            "after_bytes": new_content.len(),
            // An empty `base_sha256` with `base_exists = false` means "proposed
            // against a missing file", never "the hash of the empty string".
            "base_exists": base_exists,
            "base_sha256": if base_exists { sha256_text(&before) } else { String::new() },
        });
        self.store.create(&NewReviewItem {
            title: format!("파일 수정 제안: {path}"),
            summary: if classification.reason.is_empty() {
                "기존 파일을 변경하는 작업이라 검토 후 적용됩니다.".into()
            } else {
                classification.reason.clone()
            },
            source: "change_proposal".into(),
            kind: "file_update".into(),
            payload,
            provenance: Value::Object(provenance),
            user_email: self.user_email.map(str::to_string),
            workspace_id: self.workspace_id.map(str::to_string),
        })
    }

    /// `_staged_content` — the exact bytes the proposal would apply.
    fn staged_content(&self, name: &str, args: &Map<String, Value>) -> Option<String> {
        match name {
            "write_file" => {
                Some(char_slice(&str_arg(args, "content"), MAX_STAGED_BYTES).to_string())
            }
            "edit_file" => {
                let before = self.snapshot(&str_arg(args, "path")).1;
                let old = str_arg(args, "old_string");
                let new = str_arg(args, "new_string");
                if old.is_empty() || !before.contains(&old) {
                    return None;
                }
                if args.get("replace_all").is_some_and(is_truthy) {
                    return Some(
                        char_slice(&before.replace(&old, &new), MAX_STAGED_BYTES).to_string(),
                    );
                }
                if before.matches(&old).count() != 1 {
                    return None;
                }
                Some(char_slice(&before.replacen(&old, &new, 1), MAX_STAGED_BYTES).to_string())
            }
            _ => None,
        }
    }

    /// `_path_exists` — the classifier's question, over the sandbox.
    fn path_is_file(&self, path: &str) -> bool {
        self.workspace
            .resolve(path)
            .map(|target| target.is_file())
            .unwrap_or(false)
    }

    /// `_snapshot` — `(exists, content)` as the proposal pipeline sees it.
    ///
    /// Truncate the **bytes**, then decode with replacement, so an unchanged
    /// file hashes identically at staging time and at approve time (where
    /// `lattice_platform::change_proposals` repeats this normalisation).
    pub fn snapshot(&self, path: &str) -> (bool, String) {
        let Ok(target) = self.workspace.resolve(path) else {
            return (false, String::new());
        };
        if !target.is_file() {
            return (false, String::new());
        }
        match std::fs::read(&target) {
            Ok(bytes) => {
                let clipped = if bytes.len() > MAX_STAGED_BYTES {
                    &bytes[..MAX_STAGED_BYTES]
                } else {
                    &bytes[..]
                };
                (true, String::from_utf8_lossy(clipped).into_owned())
            }
            Err(_) => (false, String::new()),
        }
    }
}

/// `str(args.get(key) or "")`.
fn str_arg(args: &Map<String, Value>, key: &str) -> String {
    match args.get(key) {
        Some(value) if is_truthy(value) => py_str(value),
        _ => String::new(),
    }
}

/// `sha256_hex` — the digest both sides of the conflict check compare.
pub fn sha256_text(content: &str) -> String {
    Sha256::digest(content.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Debug, Default)]
    struct Recorder {
        items: Mutex<Vec<NewReviewItem>>,
        fail: bool,
    }

    impl ProposalStore for Recorder {
        fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
            if self.fail {
                return Err("store is read-only".into());
            }
            self.items.lock().expect("lock").push(item.clone());
            Ok(json!({"id": "review-test", "title": item.title}))
        }
    }

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        (dir, workspace)
    }

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    fn write_policy() -> ToolPolicy {
        ToolPolicy {
            risk: "write".into(),
            ..ToolPolicy::default()
        }
    }

    #[test]
    fn an_ungoverned_tool_and_an_additive_write_never_stage() {
        let (_dir, workspace) = workspace();
        let store = Recorder::default();
        let governor = Governor {
            workspace: &workspace,
            store: &store,
            user_email: None,
            workspace_id: None,
            conversation_id: None,
        };
        assert_eq!(
            governor.review("run_command", &Map::new(), &write_policy()),
            Verdict::Silent
        );
        let verdict = governor.review(
            "write_file",
            &args(json!({"path": "new.md", "content": "hi"})),
            &write_policy(),
        );
        match verdict {
            Verdict::AllowAdditive(classification) => {
                assert_eq!(classification.change_class, "additive");
            }
            other => panic!("expected allow_additive, got {other:?}"),
        }
        assert!(store.items.lock().expect("lock").is_empty());
    }

    #[test]
    fn an_overwrite_stages_the_payload_the_review_center_applies() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.md"), "old\n").expect("seed");
        let store = Recorder::default();
        let governor = Governor {
            workspace: &workspace,
            store: &store,
            user_email: Some("owner@example.com"),
            workspace_id: Some("personal"),
            conversation_id: Some("conv-1"),
        };
        let verdict = governor.review(
            "write_file",
            &args(json!({"path": "a.md", "content": "new\n"})),
            &write_policy(),
        );
        assert!(matches!(verdict, Verdict::Proposed { .. }));
        let staged = store.items.lock().expect("lock").remove(0);
        assert_eq!(staged.title, "파일 수정 제안: a.md");
        assert_eq!(staged.source, "change_proposal");
        assert_eq!(staged.kind, "file_update");
        assert_eq!(staged.user_email.as_deref(), Some("owner@example.com"));
        let payload = &staged.payload;
        assert_eq!(payload["path"], json!("a.md"));
        assert_eq!(payload["new_content"], json!("new\n"));
        assert_eq!(payload["tier"], json!("small"));
        assert_eq!(payload["before_bytes"], json!(4));
        assert_eq!(payload["after_bytes"], json!(4));
        assert_eq!(payload["base_exists"], json!(true));
        assert_eq!(payload["base_sha256"], json!(sha256_text("old\n")));
        assert_eq!(
            payload["diff"],
            json!(["--- a/a.md", "+++ b/a.md", "@@ -1 +1 @@", "-old", "+new"])
        );
        assert_eq!(staged.provenance["proposed_by"], json!("agent"));
        assert_eq!(staged.provenance["change_class"], json!("mutation"));
        assert_eq!(staged.provenance["conversation_id"], json!("conv-1"));
        assert_eq!(
            staged.provenance["source_detail"],
            json!("agent change governor")
        );
        // Nothing was written: staging is the whole point.
        assert_eq!(
            std::fs::read_to_string(workspace.root().join("a.md")).expect("read"),
            "old\n"
        );
    }

    #[test]
    fn an_undecidable_edit_falls_through_instead_of_staging_a_guess() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.md"), "x\nx\n").expect("seed");
        let store = Recorder::default();
        let governor = Governor {
            workspace: &workspace,
            store: &store,
            user_email: None,
            workspace_id: None,
            conversation_id: None,
        };
        // `old_string` absent, not present, and ambiguous — all three are the
        // same `Silent`, exactly as Python collapses them.
        for call in [
            json!({"path": "a.md", "new_string": "y"}),
            json!({"path": "a.md", "old_string": "zzz", "new_string": "y"}),
            json!({"path": "a.md", "old_string": "x", "new_string": "y"}),
        ] {
            assert_eq!(
                governor.review("edit_file", &args(call), &write_policy()),
                Verdict::Silent
            );
        }
        // …and `replace_all` makes the ambiguous one decidable again.
        let verdict = governor.review(
            "edit_file",
            &args(
                json!({"path": "a.md", "old_string": "x", "new_string": "y", "replace_all": true}),
            ),
            &write_policy(),
        );
        assert!(matches!(verdict, Verdict::Proposed { .. }));
        assert_eq!(
            store.items.lock().expect("lock")[0].payload["new_content"],
            json!("y\ny\n")
        );
    }

    #[test]
    fn a_store_that_refuses_is_a_failure_not_a_silent_fallthrough() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.md"), "old\n").expect("seed");
        let store = Recorder {
            fail: true,
            ..Recorder::default()
        };
        let governor = Governor {
            workspace: &workspace,
            store: &store,
            user_email: None,
            workspace_id: None,
            conversation_id: None,
        };
        assert_eq!(
            governor.review(
                "write_file",
                &args(json!({"path": "a.md", "content": "new\n"})),
                &write_policy()
            ),
            Verdict::Failed("store is read-only".into())
        );
    }

    #[test]
    fn a_path_outside_the_workspace_is_no_file_and_stages_as_a_create() {
        // `_snapshot` swallows the sandbox refusal exactly as Python's does,
        // so the proposal is staged against "no base" rather than crashing the
        // run. Approving it is what re-checks the path.
        let (_dir, workspace) = workspace();
        let store = Recorder::default();
        let governor = Governor {
            workspace: &workspace,
            store: &store,
            user_email: None,
            workspace_id: None,
            conversation_id: None,
        };
        assert_eq!(governor.snapshot("../escape.md"), (false, String::new()));
        assert!(!governor.path_is_file("../escape.md"));
    }

    #[test]
    fn the_json_store_writes_the_shape_the_review_center_reads() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = JsonProposalStore::new(dir.path());
        let item = NewReviewItem {
            title: "파일 수정 제안: a.md".into(),
            summary: "s".into(),
            source: "change_proposal".into(),
            kind: "file_update".into(),
            payload: json!({"path": "a.md"}),
            provenance: json!({"proposed_by": "agent"}),
            user_email: Some("owner@example.com".into()),
            workspace_id: None,
        };
        let stored = store.create(&item).expect("create");
        assert!(stored["id"].as_str().expect("id").starts_with("review-"));
        assert_eq!(stored["id"].as_str().expect("id").len(), 7 + 16);
        assert_eq!(stored["status"], json!("pending"));
        assert_eq!(stored["snoozed_until"], Value::Null);
        assert_eq!(stored["workspace_id"], json!("personal"));
        assert_eq!(stored["created_at"], stored["updated_at"]);

        let raw = std::fs::read_to_string(store.state_path()).expect("read");
        assert!(raw.ends_with("\n"), "the file ends with a newline");
        let document: Value = serde_json::from_str(&raw).expect("json");
        assert_eq!(document["version"], json!(crate::VERSION));
        assert_eq!(document["active_workspace"], json!("personal"));
        assert_eq!(document["review_items"].as_array().expect("rows").len(), 1);
        assert_eq!(document["review_items"][0]["id"], stored["id"]);
        // A second item appends rather than replacing, and gets its own id
        // even when every hashed field is identical within the same second.
        let second = store.create(&item).expect("create");
        assert_ne!(second["id"], stored["id"]);
        let document: Value =
            serde_json::from_str(&std::fs::read_to_string(store.state_path()).expect("read"))
                .expect("json");
        assert_eq!(document["review_items"].as_array().expect("rows").len(), 2);
        // A blank title is the store's own refusal.
        assert!(store
            .create(&NewReviewItem {
                title: "  ".into(),
                ..item
            })
            .is_err());
    }

    #[test]
    fn the_env_store_prefers_the_data_dir_over_home() {
        // No env mutation: the rule is asserted through the two constructors it
        // is built from, because a test that sets a process-wide variable races
        // every other test in the binary.
        assert_eq!(
            JsonProposalStore::new("/tmp/data").state_path(),
            PathBuf::from("/tmp/data/workspace_os.json")
        );
        assert!(JsonProposalStore::from_env()
            .state_path()
            .ends_with("workspace_os.json"));
    }
}
