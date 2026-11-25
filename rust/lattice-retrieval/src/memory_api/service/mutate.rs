use std::collections::BTreeSet;

use lattice_auth::OrderedMap;
use serde_json::Value;

use super::report::{nonempty_or, text_or};
use super::snapshot::{json, Snapshot};
use super::WORKSPACE_KINDS;
use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

pub struct PruneOutcome {
    pub removed: Vec<String>,
    pub skipped: Vec<String>,
    pub failed: Vec<(String, String)>,
}

impl PruneOutcome {
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

pub fn compact(state: &BrainState, snapshot: &Snapshot) -> OrderedMap {
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let mut targets: Vec<String> = Vec::new();
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

pub enum ClearPlan {
    ByKind(String),
    Refused(String),
}

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
