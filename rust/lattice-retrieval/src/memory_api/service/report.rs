//! Cross-tier memory reports (`manager`, `inspect`, `tiers`).

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeSet;
use std::path::Path;

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::snapshot::{brain_readiness, json, source_row, sum_counts, Snapshot};
use super::{TIERS, WORKSPACE_KINDS};
use crate::memory_api::kg;
use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

/// `MemoryManagerMixin.manager` — the report every other surface rests on.
pub fn manager(snapshot: &Snapshot, graph_enabled: bool, now: &str) -> OrderedMap {
    let node_total = snapshot
        .stats
        .as_ref()
        .and_then(|stats| sum_counts(stats.get("nodes")));
    let edge_total = snapshot
        .stats
        .as_ref()
        .and_then(|stats| sum_counts(stats.get("edges")));
    // `vector_counts` is not a key `index_status()` produces, and neither are
    // `indexed` / `ready` — so a live store answers `None` here. Reproduced as
    // it is, not as it reads: the Vector row's `count` is genuinely null.
    let vector_total: Option<i64> = snapshot.index.as_ref().and_then(|index| {
        match index.get("vector_counts").and_then(Value::as_object) {
            Some(counts) => Some(counts.values().filter_map(Value::as_i64).sum()),
            None => index
                .get("indexed")
                .and_then(Value::as_i64)
                .or_else(|| index.get("ready").and_then(Value::as_i64)),
        }
    });
    let conv_bytes = snapshot.graph_bytes;
    let sources = vec![
        source_row(
            "workspace",
            "Workspace Memory",
            Value::from(snapshot.workspace_memories.len() as i64),
            if snapshot.workspace_memories.is_empty() {
                0
            } else {
                snapshot.workspace_bytes
            },
            "ok",
            "Personal workspace knowledge, by kind.",
            None,
        ),
        source_row(
            "project",
            "Project Memory",
            Value::from(snapshot.project_memories.len() as i64),
            0,
            "ok",
            "Memory scoped to organization workspaces.",
            None,
        ),
        source_row(
            "agent",
            "Agent Memory",
            Value::from(snapshot.snapshots.len() as i64),
            0,
            "ok",
            "Per-run agent memory snapshots.",
            None,
        ),
        source_row(
            "conversation",
            "Conversation Memory",
            Value::from(snapshot.scoped_conversations.len() as i64),
            conv_bytes,
            // The durable conversation store is always wired in this build, so
            // the tier is `ok` even with nothing in it — the JSON-file fallback
            // is the only branch that could read `empty`.
            "ok",
            "Historical interaction memory from chat.",
            None,
        ),
        source_row(
            "graph",
            "Graph Memory",
            node_total.map(Value::from).unwrap_or(Value::Null),
            snapshot.graph_bytes,
            if snapshot.stats.is_some() {
                "ok"
            } else {
                "unavailable"
            },
            if snapshot.stats.is_some() {
                "Knowledge Graph entities and relations."
            } else {
                "Knowledge graph disabled or unavailable."
            },
            Some(edge_total.map(Value::from).unwrap_or(Value::Null)),
        ),
        source_row(
            "vector",
            "Vector Memory",
            vector_total.map(Value::from).unwrap_or(Value::Null),
            0,
            if snapshot.index.is_some() {
                "ok"
            } else {
                "unavailable"
            },
            if snapshot.index.is_some() {
                "Local embedding vector index."
            } else {
                "Vector index unavailable."
            },
            None,
        ),
    ];
    let total_items: i64 = sources
        .iter()
        .map(|row| row.get("count").and_then(Value::as_i64).unwrap_or(0))
        .sum();
    let healthy = sources
        .iter()
        .filter(|row| row.get("health") == Some(&Value::String("ok".to_string())))
        .count() as i64;
    let overall = if healthy >= 4 {
        "ok"
    } else if healthy >= 1 {
        "degraded"
    } else {
        "unavailable"
    };
    let mut memory_ids: BTreeSet<String> = BTreeSet::new();
    for item in snapshot
        .workspace_memories
        .iter()
        .chain(snapshot.project_memories.iter())
    {
        if let Some(id) = item
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            memory_ids.insert(id.to_string());
        }
    }
    let memory_count = memory_ids.len() as i64
        + snapshot.snapshots.len() as i64
        + snapshot.scoped_conversations.len() as i64;

    let mut usage = OrderedMap::new();
    usage.insert("total_items", Value::from(total_items));
    usage.insert(
        "total_bytes",
        Value::from(snapshot.workspace_bytes + snapshot.graph_bytes + conv_bytes),
    );
    usage.insert("sources", Value::from(sources.len() as i64));

    let mut out = OrderedMap::new();
    out.insert(
        "sources",
        Value::Array(sources.iter().map(json).collect::<Vec<_>>()),
    );
    out.insert("recent_memories", recent_memories(snapshot, 8));
    out.insert(
        "tiers",
        Value::Array(TIERS.iter().map(|t| Value::String(t.to_string())).collect()),
    );
    out.insert("usage", json(&usage));
    out.insert(
        "brain_readiness",
        json(&brain_readiness(
            memory_count,
            node_total,
            edge_total,
            healthy,
        )),
    );
    out.insert("health", Value::String(overall.to_string()));
    out.insert("graph_enabled", Value::Bool(graph_enabled));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// `_manager_recent_memories` — the first `limit` of workspace + project.
fn recent_memories(snapshot: &Snapshot, limit: usize) -> Value {
    let mut rows = Vec::new();
    for item in snapshot
        .workspace_memories
        .iter()
        .chain(snapshot.project_memories.iter())
        .take(limit.max(1))
    {
        let mut row = OrderedMap::new();
        row.insert("id", Value::String(text_or(item, "id", "").to_string()));
        row.insert("kind", Value::String(nonempty_or(item, "kind", "memory")));
        row.insert(
            "content",
            Value::String(lattice_core::truncate_chars(
                text_or(item, "content", ""),
                320,
            )),
        );
        row.insert(
            "tags",
            match item.get("tags") {
                Some(Value::Array(tags)) => Value::Array(tags.clone()),
                _ => Value::Array(Vec::new()),
            },
        );
        row.insert(
            "metadata",
            match item.get("metadata") {
                Some(value @ Value::Object(_)) => value.clone(),
                _ => Value::Object(serde_json::Map::new()),
            },
        );
        row.insert(
            "workspace_id",
            Value::String(nonempty_or(
                item,
                "workspace_id",
                wsos::DEFAULT_WORKSPACE_ID,
            )),
        );
        row.insert(
            "created_at",
            item.get("created_at").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "updated_at",
            item.get("updated_at").cloned().unwrap_or(Value::Null),
        );
        rows.push(json(&row));
    }
    Value::Array(rows)
}

/// `str(item.get(key) or "")` — the reading Python takes everywhere here.
pub fn text_or<'a>(item: &'a Value, key: &str, default: &'a str) -> &'a str {
    item.get(key).and_then(Value::as_str).unwrap_or(default)
}

/// `item.get(key) or fallback` on a string field.
pub fn nonempty_or(item: &Value, key: &str, fallback: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
        .to_string()
}

/// `MemoryRecallMixin.tiers`.
pub fn tiers() -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert(
        "tiers",
        Value::Array(TIERS.iter().map(|t| Value::String(t.to_string())).collect()),
    );
    out.insert(
        "workspace_kinds",
        Value::Array(
            WORKSPACE_KINDS
                .iter()
                .map(|t| Value::String(t.to_string()))
                .collect(),
        ),
    );
    out
}

/// `MemoryRecallMixin.inspect` — `None` for a source the service does not know.
pub fn inspect(snapshot: &Snapshot, source: &str, limit: i64) -> Option<OrderedMap> {
    let cap = limit.max(0) as usize;
    let mut out = OrderedMap::new();
    out.insert("source", Value::String(source.to_string()));
    match source {
        "workspace" | "project" | "agent" => {
            let items: Vec<Value> = match source {
                "workspace" => snapshot
                    .workspace_memories
                    .iter()
                    .take(cap)
                    .cloned()
                    .collect(),
                "project" => snapshot
                    .project_memories
                    .iter()
                    .take(cap)
                    .cloned()
                    .collect(),
                _ => snapshot.snapshots.iter().take(cap).cloned().collect(),
            };
            let count = items.len() as i64;
            out.insert("items", Value::Array(items));
            out.insert("count", Value::from(count));
        }
        "conversation" => {
            let items: Vec<Value> = snapshot
                .scoped_conversations
                .iter()
                .take(cap)
                .map(|conversation| {
                    let id = conversation.get("id").cloned().unwrap_or(Value::Null);
                    let mut row = OrderedMap::new();
                    row.insert("id", id.clone());
                    row.insert(
                        "title",
                        match conversation.get("title") {
                            Some(value @ Value::String(text)) if !text.is_empty() => value.clone(),
                            _ => id,
                        },
                    );
                    row.insert(
                        "messages",
                        Value::from(
                            conversation
                                .get("messages")
                                .and_then(Value::as_array)
                                .map(Vec::len)
                                .unwrap_or(0) as i64,
                        ),
                    );
                    json(&row)
                })
                .collect();
            out.insert("items", Value::Array(items));
            out.insert(
                "count",
                Value::from(snapshot.scoped_conversations.len() as i64),
            );
        }
        "graph" => {
            out.insert(
                "stats",
                snapshot
                    .stats
                    .as_ref()
                    .map(json)
                    .unwrap_or(Value::Object(serde_json::Map::new())),
            );
            out.insert("available", Value::Bool(snapshot.stats.is_some()));
        }
        "vector" => {
            out.insert(
                "index",
                snapshot
                    .index
                    .as_ref()
                    .map(json)
                    .unwrap_or(Value::Object(serde_json::Map::new())),
            );
            out.insert("available", Value::Bool(snapshot.index.is_some()));
        }
        _ => return None,
    }
    Some(out)
}
