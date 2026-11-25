use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::memory_api::service::{self, Snapshot};
use crate::memory_api::shared::BrainState;

use super::proactive;
use super::pyutil;
use super::quality::{EdgeQuality, MemoryQuality};
use super::sampling;

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

    let (entity_resolution, temporal_timeline) =
        run_epistemic_consolidation(state, apply, workspace_id).await;

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
    out.insert("entity_resolution", entity_resolution);
    out.insert("temporal_timeline", temporal_timeline);
    out.insert("generated_at", Value::String(state.now()));
    out
}

async fn run_epistemic_consolidation(
    state: &BrainState,
    apply: bool,
    workspace_id: Option<&str>,
) -> (Value, Value) {
    let ws = workspace_id.map(str::to_string);
    let now = state.now();
    let entity_resolution = if apply {
        if let Some(graph) = state.graph().cloned() {
            let ws_apply = ws.clone();
            let now_apply = now.clone();
            tokio::task::spawn_blocking(move || {
                let alias_book = lattice_core::entity_resolution::EntityAliasBook::default();
                graph.store().with_write_txn(|txn| {
                    lattice_core::entity_resolution::apply_entity_consolidation(
                        txn,
                        ws_apply.as_deref(),
                        &alias_book,
                        &now_apply,
                    )
                })
            })
            .await
            .ok()
            .and_then(Result::ok)
            .map(|summary| {
                serde_json::json!({
                    "applied": true,
                    "total_entities_scanned": summary.total_entities_scanned,
                    "clusters_with_merges": summary.clusters_with_merges,
                    "duplicate_nodes_linked": summary.duplicate_nodes_linked,
                    "same_as_edges_upserted": summary.same_as_edges_upserted,
                })
            })
            .unwrap_or(Value::Null)
        } else {
            Value::Null
        }
    } else {
        let ws_read = ws.clone();
        state
            .read(move |conn| {
                let alias_book = lattice_core::entity_resolution::EntityAliasBook::default();
                lattice_core::entity_resolution::resolve_entities_in_conn(
                    conn,
                    ws_read.as_deref(),
                    &alias_book,
                )
            })
            .await
            .ok()
            .map(|clusters| {
                let clusters_with_merges = clusters
                    .iter()
                    .filter(|c| c.member_node_ids.len() > 1)
                    .count();
                let duplicate_nodes = clusters
                    .iter()
                    .map(|c| c.member_node_ids.len().saturating_sub(1))
                    .sum::<usize>();
                serde_json::json!({
                    "applied": false,
                    "total_entities_scanned": clusters.iter().map(|c| c.member_node_ids.len()).sum::<usize>(),
                    "clusters_with_merges": clusters_with_merges,
                    "duplicate_nodes_candidate": duplicate_nodes,
                })
            })
            .unwrap_or(Value::Null)
    };

    let ws_temporal = ws;
    let temporal_timeline = state
        .read(move |conn| {
            lattice_core::temporal::query_temporal_timeline(conn, ws_temporal.as_deref(), "", None)
        })
        .await
        .ok()
        .map(|summary| {
            serde_json::json!({
                "active_facts": summary.active_facts.len(),
                "historical_facts": summary.historical_facts.len(),
                "unreached_facts": summary.unreached_facts.len(),
                "has_active_contradiction": summary.has_active_contradiction(),
            })
        })
        .unwrap_or(Value::Null);

    (entity_resolution, temporal_timeline)
}
