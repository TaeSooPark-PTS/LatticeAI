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
//! (`lattice_platform::governance::change_proposals`).
//!
//! ## Where a staged proposal is written
//!
//! The review-item store belongs to `lattice-platform` (WP-R7's
//! `GovernanceState`, over `workspace_os.json`), and this crate cannot depend
//! on it — the dependency runs the other way (`platform → agent`). So staging
//! goes through a port, [`ProposalStore`], exactly as the hook lifecycle does:
//!
//! * **In the product**, `lattice-platform` implements this trait for
//!   `GovernanceState` and the host injects it. That is the correct wiring when
//!   the Review Center runs in the same process, because the document has an
//!   owner there — one write lock over the `workspace_os_state` SQLite row and
//!   the JSON file it mirrors, and `load_state` reads the row first.
//! * **Standalone** (this crate's own loop routes, with no Review Center),
//!   [`JsonProposalStore`] appends to the same `workspace_os.json` in the same
//!   shape, so a proposal staged by a bare `lattice-agent` is listed by a
//!   Review Center opened over that data directory afterwards. The format's
//!   owner is `lattice_platform::governance::review_queue` — every field, the id
//!   derivation and the file layout are that module's, mirrored here.
//!
//! Since v11.7.0 the second bullet is no longer a *hazard* in the first
//! bullet's process: [`DocumentWriter`] is a process-wide port the host
//! installs, and a `JsonProposalStore` built where an owner exists writes
//! through it. What used to depend on every caller injecting the right store
//! is now true by construction.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::content::pydiff::{splitlines, unified_diff};
use crate::kernel::governor::{classify_tool_call, Classification, CHANGE_ADDITIVE};
use crate::kernel::policy::ToolPolicy;
use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::tools::sandbox::Workspace;

#[path = "proposals_store.rs"]
mod store;
pub use store::{
    document_writer, install_document_writer, DocumentWriter, JsonProposalStore, NewReviewItem,
    ProposalStore,
};

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
        if !crate::kernel::policy::in_set(&GOVERNED_TOOLS, name) {
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
    /// `lattice_platform::governance::change_proposals` repeats this normalisation).
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
#[path = "proposals_tests.rs"]
mod tests;
