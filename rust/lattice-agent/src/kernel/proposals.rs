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

pub const MAX_STAGED_BYTES: usize = 400_000;
pub const MAX_DIFF_LINES: usize = 400;
pub const SMALL_TIER_DIFF_LINES: usize = 40;

pub const GOVERNED_TOOLS: [&str; 2] = ["edit_file", "write_file"];

#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    Silent,
    AllowAdditive(Classification),
    Proposed {
        classification: Classification,
        proposal: Value,
    },
    Failed(String),
}

#[derive(Debug, Clone, Copy)]
pub struct Governor<'a> {
    pub workspace: &'a Workspace,
    pub store: &'a dyn ProposalStore,
    pub user_email: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
    pub conversation_id: Option<&'a str>,
}

impl Governor<'_> {
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

    fn path_is_file(&self, path: &str) -> bool {
        self.workspace
            .resolve(path)
            .map(|target| target.is_file())
            .unwrap_or(false)
    }

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

fn str_arg(args: &Map<String, Value>, key: &str) -> String {
    match args.get(key) {
        Some(value) if is_truthy(value) => py_str(value),
        _ => String::new(),
    }
}

pub fn sha256_text(content: &str) -> String {
    Sha256::digest(content.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
#[path = "proposals_tests.rs"]
mod tests;
