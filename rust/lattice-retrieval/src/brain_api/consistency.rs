//! Contradiction and consolidation scans.

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
use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::memory_api::service::{self, Snapshot};
use crate::memory_api::shared::BrainState;

use super::proactive;
use super::pyutil;
use super::quality::{EdgeQuality, MemoryQuality};
use super::sampling;

/// `BrainIntelligenceService.contradictions`.
pub async fn contradictions(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let memories = sampling::workspace_memories(state, user_email, workspace_id);
    let memory_rows: Vec<Value> = memories
        .iter()
        .enumerate()
        .filter_map(|(index, memory)| {
            let content = pyutil::field_text(memory, "content");
            if lattice_core::pytext::strip(&content).is_empty() {
                return None;
            }
            let id = match memory.get("id") {
                Some(Value::String(text)) if !text.is_empty() => text.clone(),
                Some(value) if pyutil::truthy(value) => pyutil::py_str(value),
                _ => format!("mem-{index}"),
            };
            Some(serde_json::json!({
                "id": id,
                "content": content,
                "score": 0.6,
                "source": "workspace",
                "timestamp": memory.get("created_at").cloned()
                    .or_else(|| memory.get("timestamp").cloned())
                    .unwrap_or(Value::from(0)),
            }))
        })
        .collect();

    let mut candidates = MemoryQuality::extract_candidates(&memory_rows);
    MemoryQuality::detect_conflicts(&mut candidates);
    let mut conflicts: Vec<Value> = Vec::new();
    for candidate in &candidates {
        for marker in &candidate.conflicts {
            if !marker.starts_with("conflict:contradicts:") {
                continue;
            }
            let other_id = marker.rsplit(':').next().unwrap_or("");
            if conflicts.iter().any(|item| {
                item.get("kind").and_then(Value::as_str) == Some("memory_pair") && {
                    let left = item.get("left_id").and_then(Value::as_str).unwrap_or("");
                    let right = item.get("right_id").and_then(Value::as_str).unwrap_or("");
                    [left, right].contains(&candidate.id.as_str())
                        && [left, right].contains(&other_id)
                }
            }) {
                continue;
            }
            let other = memory_rows
                .iter()
                .find(|row| row.get("id").and_then(Value::as_str) == Some(other_id));
            let right_content = other
                .and_then(|row| row.get("content"))
                .and_then(Value::as_str)
                .unwrap_or("");
            conflicts.push(serde_json::json!({
                "kind": "memory_pair",
                "left_id": candidate.id,
                "left_content": pyutil::head(&candidate.content, 200),
                "right_id": other_id,
                "right_content": pyutil::head(right_content, 200),
                "signal": "preference_negation",
            }));
        }
    }

    let temporal_items: Vec<Value> = MemoryQuality::detect_temporal_contradictions(&memory_rows)
        .into_iter()
        .map(|item| {
            serde_json::json!({
                "kind": "temporal",
                "id": item.get("id").cloned().unwrap_or(Value::Null),
                "content": pyutil::head(&pyutil::field_text(&item, "content"), 200),
                "signal": item.get("proactive_flag").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();

    let sample = sampling::graph_sample(state, workspace_id).await;
    let mut edge_items: Vec<Value> = Vec::new();
    for edge in &sample.edges {
        if pyutil::field_text(edge, "type")
            .to_uppercase()
            .contains("CONTRADICT")
        {
            edge_items.push(serde_json::json!({
                "kind": "graph_edge",
                "id": edge.get("id").cloned().unwrap_or(Value::Null),
                "source": edge.get("source").cloned().or_else(|| edge.get("from_node").cloned()).unwrap_or(Value::Null),
                "target": edge.get("target").cloned().or_else(|| edge.get("to_node").cloned()).unwrap_or(Value::Null),
                "signal": "contradicts_edge",
            }));
        }
    }

    let graph_pairs = if sample.available {
        let found = proactive::detect_contradictions(&sample.nodes, &sample.edges);
        found
            .get("node_pairs")
            .and_then(Value::as_array)
            .map(|pairs| {
                pairs
                    .iter()
                    .map(|pair| {
                        let mut out = serde_json::Map::new();
                        out.insert("kind".into(), Value::String("graph_node_pair".into()));
                        if let Value::Object(map) = pair {
                            for (key, value) in map {
                                out.insert(key.clone(), value.clone());
                            }
                        }
                        Value::Object(out)
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };

    let mut items = conflicts.clone();
    items.extend(temporal_items.iter().cloned());
    items.extend(edge_items.iter().cloned());
    items.extend(graph_pairs.iter().cloned());

    let mut sources = OrderedMap::new();
    sources.insert("memory_pairs", Value::from(conflicts.len() as i64));
    sources.insert("temporal", Value::from(temporal_items.len() as i64));
    sources.insert("graph_edges", Value::from(edge_items.len() as i64));
    sources.insert("graph_node_pairs", Value::from(graph_pairs.len() as i64));

    let mut out = OrderedMap::new();
    out.insert("items", Value::Array(items.clone()));
    out.insert("count", Value::from(items.len() as i64));
    out.insert(
        "sources",
        serde_json::to_value(&sources).unwrap_or(Value::Null),
    );
    out.insert("memories_scanned", Value::from(memory_rows.len() as i64));
    out.insert("generated_at", Value::String(state.now()));
    out
}

/// `BrainIntelligenceService.consolidate`.
pub async fn consolidate(
    state: &BrainState,
    apply: bool,
    user_email: &str,
    workspace_id: Option<&str>,
) -> OrderedMap {
    let memories = sampling::workspace_memories(state, user_email, workspace_id);
    let memory_rows: Vec<Value> = memories
        .iter()
        .enumerate()
        .filter_map(|(index, memory)| {
            let content = pyutil::field_text(memory, "content");
            if lattice_core::pytext::strip(&content).is_empty() {
                return None;
            }
            let id = match memory.get("id") {
                Some(Value::String(text)) if !text.is_empty() => text.clone(),
                Some(value) if pyutil::truthy(value) => pyutil::py_str(value),
                _ => format!("mem-{index}"),
            };
            Some(serde_json::json!({
                "id": id,
                "content": content,
                "score": 0.6,
                "source": "workspace",
            }))
        })
        .collect();
    let candidates = MemoryQuality::extract_candidates(&memory_rows);
    let kept = MemoryQuality::dedupe(&candidates);
    let kept_ids: std::collections::BTreeSet<&str> = kept.iter().map(String::as_str).collect();
    let duplicate_memory_ids: Vec<String> = candidates
        .iter()
        .filter(|candidate| !kept_ids.contains(candidate.id.as_str()))
        .map(|candidate| candidate.id.clone())
        .collect();

    let sample = sampling::graph_sample(state, workspace_id).await;
    let duplicate_edge_ids: Vec<String> = EdgeQuality::detect_duplicate_edges(&sample.edges)
        .into_iter()
        .filter(|id| !id.is_empty())
        .collect();

    let mut pruned = 0i64;
    if apply && !duplicate_memory_ids.is_empty() {
        let data_dir = state.data_dir().to_path_buf();
        let graph = state.graph_enabled();
        let email = user_email.to_string();
        let scope = workspace_id.map(str::to_string);
        if let Ok(snapshot) = state
            .read({
                let data_dir = data_dir.clone();
                let email = email.clone();
                let scope = scope.clone();
                move |conn| Snapshot::read(conn, &data_dir, graph, &email, scope.as_deref())
            })
            .await
        {
            let owned = state.clone();
            let ids = duplicate_memory_ids.clone();
            if let Ok(outcome) =
                tokio::task::spawn_blocking(move || service::prune(&owned, &snapshot, &ids, None))
                    .await
            {
                pruned = outcome
                    .to_body()
                    .get("count")
                    .and_then(Value::as_i64)
                    .unwrap_or(0);
            }
        }
    }

    let graph_consolidation = if sample.available {
        Some(proactive::consolidate_duplicates(&sample, true))
    } else {
        None
    };

    let mut out = OrderedMap::new();
    out.insert(
        "mode",
        Value::String(if apply { "applied" } else { "dry_run" }.to_string()),
    );
    out.insert("memories_scanned", Value::from(memory_rows.len() as i64));
    out.insert(
        "duplicate_memories",
        Value::Array(
            duplicate_memory_ids
                .iter()
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    out.insert(
        "duplicate_memory_count",
        Value::from(duplicate_memory_ids.len() as i64),
    );
    out.insert("pruned", Value::from(pruned));
    out.insert(
        "duplicate_edges",
        Value::Array(
            duplicate_edge_ids
                .iter()
                .take(50)
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    out.insert(
        "duplicate_edge_count",
        Value::from(duplicate_edge_ids.len() as i64),
    );
    out.insert(
        "graph_consolidation",
        graph_consolidation
            .map(|map| serde_json::to_value(&map).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
    );
    out.insert("generated_at", Value::String(state.now()));
    out
}
