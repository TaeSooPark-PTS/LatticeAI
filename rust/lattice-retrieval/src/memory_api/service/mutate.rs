//! Memory maintenance: prune, compact, and clear planning.

use std::collections::BTreeSet;

use lattice_auth::OrderedMap;
use serde_json::Value;

use super::report::{nonempty_or, text_or};
use super::snapshot::{json, Snapshot};
use super::WORKSPACE_KINDS;
use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

// ── the mutating half ───────────────────────────────────────────────────────

/// The answer `prune` builds, before it becomes a response body.
pub struct PruneOutcome {
    /// Ids that were removed, in the order they were targeted.
    pub removed: Vec<String>,
    /// Ids the caller asked for but does not own.
    pub skipped: Vec<String>,
    /// Ids whose deletion failed, with the reason.
    pub failed: Vec<(String, String)>,
}

impl PruneOutcome {
    /// `{"removed", "count"}` plus the two optional blocks.
    pub fn to_body(&self) -> OrderedMap {
        let mut out = OrderedMap::new();
        out.insert(
            "removed",
            Value::Array(
                self.removed
                    .iter()
                    .map(|id| Value::String(id.clone()))
                    .collect(),
            ),
        );
        out.insert("count", Value::from(self.removed.len() as i64));
        if !self.skipped.is_empty() {
            out.insert(
                "skipped",
                Value::Array(
                    self.skipped
                        .iter()
                        .map(|id| Value::String(id.clone()))
                        .collect(),
                ),
            );
        }
        if !self.failed.is_empty() {
            out.insert("failed", failed_rows(&self.failed));
            out.insert(
                "status",
                Value::String(
                    if self.removed.is_empty() {
                        "error"
                    } else {
                        "partial"
                    }
                    .to_string(),
                ),
            );
        }
        out
    }
}

fn failed_rows(failed: &[(String, String)]) -> Value {
    Value::Array(
        failed
            .iter()
            .map(|(id, detail)| {
                let mut row = OrderedMap::new();
                row.insert("id", Value::String(id.clone()));
                row.insert("detail", Value::String(detail.clone()));
                json(&row)
            })
            .collect(),
    )
}

/// `MemoryMaintenanceMixin.prune`, ownership guard included.
///
/// The guard is the point: both the explicit-id path and the by-kind path are
/// intersected with the caller's *own* memories, so a forged id belonging to
/// someone else is reported `skipped` rather than silently deleted.
pub fn prune(
    state: &BrainState,
    snapshot: &Snapshot,
    ids: &[String],
    kind: Option<&str>,
) -> PruneOutcome {
    let owned: BTreeSet<String> = snapshot
        .owned_memories
        .iter()
        .filter_map(|item| item.get("id").and_then(Value::as_str))
        .filter(|id| !id.is_empty())
        .map(str::to_string)
        .collect();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut targets: Vec<String> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();
    for id in ids {
        if !seen.insert(id.clone()) {
            continue;
        }
        if owned.contains(id) {
            targets.push(id.clone());
        } else {
            skipped.push(id.clone());
        }
    }
    if let Some(kind) = kind.filter(|value| !value.is_empty()) {
        for item in &snapshot.owned_memories {
            let matches = item.get("kind").and_then(Value::as_str) == Some(kind);
            let id = item.get("id").and_then(Value::as_str).unwrap_or_default();
            if matches && !id.is_empty() && seen.insert(id.to_string()) {
                targets.push(id.to_string());
            }
        }
    }
    delete_all(state, &targets, skipped)
}

/// `MemoryMaintenanceMixin.compact` — drop repeats of one `(kind, content)`.
pub fn compact(state: &BrainState, snapshot: &Snapshot) -> OrderedMap {
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let mut targets: Vec<String> = Vec::new();
    // Oldest first, so the first occurrence — the oldest — is the one kept.
    for item in snapshot.owned_memories.iter().rev() {
        let key = (
            nonempty_or(item, "kind", ""),
            lattice_core::pytext::strip(text_or(item, "content", "")),
        );
        if seen.contains(&key) {
            if let Some(id) = item
                .get("id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                targets.push(id.to_string());
            }
        } else {
            seen.insert(key);
        }
    }
    let outcome = delete_all(state, &targets, Vec::new());
    let mut out = OrderedMap::new();
    out.insert("compacted", Value::from(outcome.removed.len() as i64));
    out.insert(
        "removed",
        Value::Array(
            outcome
                .removed
                .iter()
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    out.insert("remaining", Value::from(seen.len() as i64));
    out.insert("failed", failed_rows(&outcome.failed));
    out.insert(
        "status",
        Value::String(
            if !outcome.failed.is_empty() && !outcome.removed.is_empty() {
                "partial"
            } else if !outcome.failed.is_empty() {
                "error"
            } else {
                "ok"
            }
            .to_string(),
        ),
    );
    out
}

fn delete_all(state: &BrainState, targets: &[String], skipped: Vec<String>) -> PruneOutcome {
    let mut removed = Vec::new();
    let mut failed = Vec::new();
    for id in targets {
        match wsos::delete_memory(state.store(), state.data_dir(), id) {
            Ok(true) => removed.push(id.clone()),
            // `WorkspaceOSStore.delete_memory` raises FileNotFoundError for an
            // id that vanished between the read and the write; Python's
            // `except Exception` files that as a failure, not a silent skip.
            Ok(false) => failed.push((id.clone(), id.clone())),
            Err(error) => failed.push((id.clone(), error.to_string())),
        }
    }
    PruneOutcome {
        removed,
        skipped,
        failed,
    }
}

/// `MemoryMaintenanceMixin.clear`'s scope router, as a decision.
pub enum ClearPlan {
    /// `scope` is one of `WORKSPACE_KINDS` — prune by kind.
    ByKind(String),
    /// Every other scope is refused with this exact sentence.
    Refused(String),
}

/// `clear(scope, confirm)` before anything is deleted.
pub fn clear_plan(scope: &str, confirm: bool) -> ClearPlan {
    if !confirm {
        return ClearPlan::Refused("clear requires confirm=true".to_string());
    }
    if WORKSPACE_KINDS.contains(&scope) {
        return ClearPlan::ByKind(scope.to_string());
    }
    if scope == "graph" {
        return ClearPlan::Refused(
            "graph clear is disabled from Memory Manager because it is not workspace-scoped"
                .to_string(),
        );
    }
    ClearPlan::Refused(format!("unsupported clear scope: {scope}"))
}
