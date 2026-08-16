//! `lattice_brain/graph/proactive.py` — duplicates, contradictions, quality.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::memory_api::shared::BrainState;

use super::pyutil;
use super::quality::{content_signature, dedupe_key, jaccard, EdgeQuality, MemoryQuality};
use super::sampling::{self, Sample};

/// `_DEFAULT_NEAR_THRESHOLD`.
pub const NEAR_THRESHOLD: f64 = 0.75;
/// `_DEFAULT_MAX_PAIRS`.
pub const MAX_PAIRS: usize = 200;
/// `_DEFAULT_CONTRADICTION_NODES`.
pub const CONTRADICTION_NODES: usize = 300;
/// `_DEFAULT_STALE_DAYS` for the quality report (not the health 45-day window).
pub const QUALITY_STALE_DAYS: i64 = 90;
/// `_DEFAULT_HALF_LIFE_DAYS`.
pub const HALF_LIFE_DAYS: f64 = 30.0;

const EPISODIC_TYPES: [&str; 7] = [
    "chat",
    "conversation",
    "message",
    "airesponse",
    "ai_response",
    "event",
    "chunk",
];

/// `ProactiveBrain.find_duplicates` over an already-taken sample.
pub fn find_duplicates(nodes: &[Value], near_threshold: f64, max_pairs: usize) -> OrderedMap {
    let mut by_key: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    let mut signatures: Vec<(&Value, BTreeSet<String>)> = Vec::new();
    for node in nodes {
        let text = sampling::node_text(node);
        if text.chars().count() < 3 {
            continue;
        }
        by_key.entry(dedupe_key(&text)).or_default().push(node);
        signatures.push((node, content_signature(&text)));
    }

    let mut exact_groups: Vec<Value> = Vec::new();
    let mut exact_ids: BTreeSet<String> = BTreeSet::new();
    let mut grouped_together: BTreeSet<(String, String)> = BTreeSet::new();
    for (key, members) in &by_key {
        if members.len() < 2 {
            continue;
        }
        let ids: Vec<String> = members
            .iter()
            .map(|node| pyutil::py_str(node.get("id").unwrap_or(&Value::Null)))
            .collect();
        let slimmed: Vec<Value> = members
            .iter()
            .map(|node| json(&sampling::slim(node)))
            .collect();
        exact_groups.push(serde_json::json!({
            "signature": key,
            "count": members.len() as i64,
            "node_ids": ids,
            "nodes": slimmed,
        }));
        for id in ids.iter().skip(1) {
            exact_ids.insert(id.clone());
        }
        for (i, left) in ids.iter().enumerate() {
            for right in ids.iter().skip(i + 1) {
                let pair = if left <= right {
                    (left.clone(), right.clone())
                } else {
                    (right.clone(), left.clone())
                };
                grouped_together.insert(pair);
            }
        }
    }

    let mut token_index: HashMap<String, Vec<usize>> = HashMap::new();
    for (idx, (_node, sig)) in signatures.iter().enumerate() {
        for token in sig {
            token_index.entry(token.clone()).or_default().push(idx);
        }
    }
    let mut cooccur: HashMap<(usize, usize), i64> = HashMap::new();
    for indices in token_index.values() {
        if indices.len() < 2 || indices.len() > 50 {
            continue;
        }
        for (i, &left_idx) in indices.iter().enumerate() {
            for &right_idx in indices.iter().skip(i + 1) {
                *cooccur.entry((left_idx, right_idx)).or_default() += 1;
            }
        }
    }
    let mut scored: Vec<((usize, usize), i64)> = cooccur.into_iter().collect();
    // HashMap iteration is not PYTHONHASHSEED=0. Tie-break on the index
    // pair so equal-overlap candidates keep a stable order.
    scored.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));

    let mut near_pairs: Vec<Value> = Vec::new();
    for ((li, ri), shared) in scored {
        if shared < 3 {
            continue;
        }
        let (left_node, left_sig) = &signatures[li];
        let (right_node, right_sig) = &signatures[ri];
        let left_id = pyutil::py_str(left_node.get("id").unwrap_or(&Value::Null));
        let right_id = pyutil::py_str(right_node.get("id").unwrap_or(&Value::Null));
        let pair_key = if left_id <= right_id {
            (left_id.clone(), right_id.clone())
        } else {
            (right_id.clone(), left_id.clone())
        };
        if grouped_together.contains(&pair_key) {
            continue;
        }
        let similarity = jaccard(left_sig, right_sig);
        if similarity < near_threshold {
            continue;
        }
        near_pairs.push(serde_json::json!({
            "left": json(&sampling::slim(left_node)),
            "right": json(&sampling::slim(right_node)),
            "similarity": pyutil::round_to(similarity, 4),
        }));
        if near_pairs.len() >= max_pairs {
            break;
        }
    }
    near_pairs.sort_by(|left, right| {
        let ls = left
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        let rs = right
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        rs.partial_cmp(&ls)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let lid = left
                    .pointer("/left/id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let rid = right
                    .pointer("/left/id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                lid.cmp(rid).then_with(|| {
                    let l2 = left
                        .pointer("/right/id")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    let r2 = right
                        .pointer("/right/id")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    l2.cmp(r2)
                })
            })
    });

    let mut out = OrderedMap::new();
    out.insert("nodes_scanned", Value::from(nodes.len() as i64));
    out.insert("exact_groups", Value::Array(exact_groups));
    out.insert("exact_duplicate_nodes", Value::from(exact_ids.len() as i64));
    out.insert("near_pairs", Value::Array(near_pairs));
    out.insert("near_threshold", Value::from(near_threshold));
    out
}

/// `ProactiveBrain.detect_contradictions` over an already-taken sample.
pub fn detect_contradictions(nodes: &[Value], edges: &[Value]) -> OrderedMap {
    let cap = CONTRADICTION_NODES.max(1);
    let mut rows: Vec<Value> = Vec::new();
    for (index, node) in nodes.iter().take(cap).enumerate() {
        let text = sampling::node_text(node);
        if text.is_empty() {
            continue;
        }
        let id = match node.get("id") {
            Some(Value::String(text)) if !text.is_empty() => text.clone(),
            Some(value) if pyutil::truthy(value) => pyutil::py_str(value),
            _ => format!("node-{index}"),
        };
        rows.push(serde_json::json!({
            "id": id,
            "content": text,
            "score": 0.6,
            "source": "graph",
            "timestamp": node.get("updated_at").cloned().unwrap_or(Value::from(0)),
        }));
    }
    let by_id: HashMap<String, &Value> = rows
        .iter()
        .filter_map(|row| {
            row.get("id")
                .and_then(Value::as_str)
                .map(|id| (id.to_string(), row))
        })
        .collect();

    let mut candidates = MemoryQuality::extract_candidates(&rows);
    MemoryQuality::detect_conflicts(&mut candidates);
    let mut node_pairs: Vec<Value> = Vec::new();
    for candidate in &candidates {
        for marker in &candidate.conflicts {
            if !marker.starts_with("conflict:contradicts:") {
                continue;
            }
            let other_id = marker.rsplit(':').next().unwrap_or("");
            if node_pairs.iter().any(|pair| {
                let left = pair.get("left_id").and_then(Value::as_str).unwrap_or("");
                let right = pair.get("right_id").and_then(Value::as_str).unwrap_or("");
                [left, right].contains(&candidate.id.as_str()) && [left, right].contains(&other_id)
            }) {
                continue;
            }
            let other_content = by_id
                .get(other_id)
                .and_then(|row| row.get("content"))
                .and_then(Value::as_str)
                .unwrap_or("");
            node_pairs.push(serde_json::json!({
                "left_id": candidate.id,
                "left_content": pyutil::head(&candidate.content, 200),
                "right_id": other_id,
                "right_content": pyutil::head(other_content, 200),
                "signal": "preference_negation",
            }));
        }
    }

    let temporal: Vec<Value> = MemoryQuality::detect_temporal_contradictions(&rows)
        .into_iter()
        .map(|item| {
            serde_json::json!({
                "id": item.get("id").cloned().unwrap_or(Value::Null),
                "content": pyutil::head(&pyutil::field_text(&item, "content"), 200),
                "signal": item.get("proactive_flag").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();

    let contradiction_edges: Vec<Value> = edges
        .iter()
        .filter(|edge| {
            pyutil::field_text(edge, "type")
                .to_uppercase()
                .contains("CONTRADICT")
        })
        .map(|edge| {
            serde_json::json!({
                "id": edge.get("id").cloned().unwrap_or(Value::Null),
                "source": edge.get("source").cloned().unwrap_or(Value::Null),
                "target": edge.get("target").cloned().unwrap_or(Value::Null),
                "type": edge.get("type").cloned().unwrap_or(Value::Null),
                "signal": "contradicts_edge",
            })
        })
        .collect();

    let count = node_pairs.len() + temporal.len() + contradiction_edges.len();
    let mut out = OrderedMap::new();
    out.insert("nodes_scanned", Value::from(rows.len() as i64));
    out.insert("node_pairs", Value::Array(node_pairs));
    out.insert("temporal", Value::Array(temporal));
    out.insert("contradiction_edges", Value::Array(contradiction_edges));
    out.insert("count", Value::from(count as i64));
    out
}

/// `ProactiveBrain.quality_report` over an already-taken sample.
///
/// `now_utc` is UTC epoch seconds, injected rather than read: the staleness
/// cutoff below is a threshold, and a fixture whose nodes all carry one stamp
/// crosses it all at once. See `BrainState::with_utc_clock`.
pub fn quality_report(sample: &Sample, generated_at: &str, now_utc: f64) -> OrderedMap {
    let duplicates = find_duplicates(&sample.nodes, NEAR_THRESHOLD, MAX_PAIRS);
    let contradictions = detect_contradictions(&sample.nodes, &sample.edges);

    let cutoff = now_utc - (QUALITY_STALE_DAYS.max(1) * 86_400) as f64;
    let mut stale: Vec<&Value> = Vec::new();
    let mut dated = 0i64;
    for node in &sample.nodes {
        let Some(stamp) = pyutil::parse_ts(node.get("updated_at")) else {
            continue;
        };
        dated += 1;
        if stamp < cutoff {
            stale.push(node);
        }
    }
    let samples: Vec<Value> = stale
        .iter()
        .take(10)
        .map(|node| json(&sampling::slim(node)))
        .collect();
    let stale_report = serde_json::json!({
        "count": stale.len() as i64,
        "dated_nodes": dated,
        "threshold_days": QUALITY_STALE_DAYS,
        "samples": samples,
    });

    let quality_edges: Vec<Value> = sample
        .edges
        .iter()
        .map(|edge| {
            let meta = edge.get("metadata").cloned().unwrap_or(Value::Null);
            let mut entry = serde_json::json!({
                "id": edge.get("id").cloned().unwrap_or(Value::Null),
                "source": edge.get("source").cloned().unwrap_or(Value::Null),
                "target": edge.get("target").cloned().unwrap_or(Value::Null),
                "type": edge.get("type").cloned().unwrap_or(Value::Null),
            });
            let confidence = meta
                .get("confidence")
                .cloned()
                .or_else(|| edge.get("confidence").cloned());
            if let Some(Value::Number(number)) = confidence {
                if let Some(map) = entry.as_object_mut() {
                    map.insert("confidence".into(), Value::Number(number));
                }
            }
            let evidence = meta
                .get("evidence")
                .cloned()
                .or_else(|| edge.get("evidence").cloned())
                .unwrap_or(Value::Null);
            if let Some(map) = entry.as_object_mut() {
                map.insert(
                    "evidence".into(),
                    match evidence {
                        Value::Array(items) => Value::Array(items),
                        _ => Value::Array(Vec::new()),
                    },
                );
            }
            entry
        })
        .collect();
    let metrics = EdgeQuality::compute_quality_metrics(&quality_edges);
    let duplicate_edge_ids: Vec<String> = EdgeQuality::detect_duplicate_edges(&quality_edges)
        .into_iter()
        .filter(|id| !id.is_empty())
        .collect();

    let mut edge_quality = OrderedMap::new();
    edge_quality.insert(
        "metrics",
        serde_json::to_value(&metrics).unwrap_or(Value::Null),
    );
    edge_quality.insert(
        "duplicate_edge_ids",
        Value::Array(
            duplicate_edge_ids
                .iter()
                .take(50)
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    edge_quality.insert(
        "duplicate_edge_count",
        Value::from(duplicate_edge_ids.len() as i64),
    );

    let mut summary = OrderedMap::new();
    summary.insert(
        "exact_duplicate_nodes",
        duplicates
            .get("exact_duplicate_nodes")
            .cloned()
            .unwrap_or(Value::from(0)),
    );
    summary.insert(
        "near_duplicate_pairs",
        Value::from(
            duplicates
                .get("near_pairs")
                .and_then(Value::as_array)
                .map(Vec::len)
                .unwrap_or(0) as i64,
        ),
    );
    summary.insert(
        "contradiction_signals",
        contradictions
            .get("count")
            .cloned()
            .unwrap_or(Value::from(0)),
    );
    summary.insert("stale_nodes", Value::from(stale.len() as i64));

    let mut out = OrderedMap::new();
    out.insert("nodes_scanned", Value::from(sample.nodes.len() as i64));
    out.insert("edges_scanned", Value::from(sample.edges.len() as i64));
    out.insert(
        "duplicates",
        serde_json::to_value(&duplicates).unwrap_or(Value::Null),
    );
    out.insert(
        "contradictions",
        serde_json::to_value(&contradictions).unwrap_or(Value::Null),
    );
    out.insert("stale_nodes", stale_report);
    out.insert(
        "edge_quality",
        serde_json::to_value(&edge_quality).unwrap_or(Value::Null),
    );
    out.insert(
        "summary",
        serde_json::to_value(&summary).unwrap_or(Value::Null),
    );
    out.insert("generated_at", Value::String(generated_at.to_string()));
    out
}

/// Access counters for `importance_report`.
///
/// Python reads `nodes_v2.importance_score` (`KnowledgeGraphStore.access_stats`).
/// A row that was never touched still reports `0.0`, and a non-empty map is
/// what flips `access_source` from `"metadata"` to `"store"`.
pub async fn access_stats(state: &BrainState, nodes: &[Value]) -> HashMap<String, f64> {
    let ids: Vec<String> = nodes
        .iter()
        .map(|node| pyutil::py_str(node.get("id").unwrap_or(&Value::Null)))
        .filter(|id| !id.is_empty())
        .collect();
    let from_store = state
        .read(move |conn| {
            let mut out = HashMap::new();
            let Ok(mut stmt) =
                conn.prepare("SELECT id, importance_score FROM nodes_v2 WHERE id = ?1")
            else {
                return Ok(out);
            };
            for id in &ids {
                if let Ok(score) = stmt.query_row([id], |row| row.get::<_, Option<f64>>(1)) {
                    out.insert(id.clone(), score.unwrap_or(0.0));
                }
            }
            Ok(out)
        })
        .await
        .unwrap_or_default();
    let mut out = from_store;
    for node in nodes {
        let id = pyutil::py_str(node.get("id").unwrap_or(&Value::Null));
        if let Some(count) = access_count(node, out.get(&id).copied()) {
            out.insert(id, count);
        }
    }
    out
}

fn access_count(node: &Value, stored: Option<f64>) -> Option<f64> {
    if let Some(meta) = node.get("metadata") {
        for key in ["access_count", "accesses", "access"] {
            if let Some(Value::Number(number)) = meta.get(key) {
                return number.as_f64();
            }
        }
    }
    stored
}

/// `ProactiveBrain.importance_report` over an already-taken sample.
pub fn importance_report(
    sample: &Sample,
    stats: &HashMap<String, f64>,
    generated_at: &str,
    now_utc: f64,
) -> OrderedMap {
    let mut degree: HashMap<String, i64> = HashMap::new();
    for edge in &sample.edges {
        for field in ["source", "target"] {
            let node_id = pyutil::text_of(edge.get(field));
            if !node_id.is_empty() {
                *degree.entry(node_id).or_default() += 1;
            }
        }
    }
    // A smooth decay, not a threshold — the common factor cancels out of the
    // ordering — but `age_days` is printed, so it is injected too rather than
    // left as the one clock read this family still made.
    let now = now_utc;
    let half_life = HALF_LIFE_DAYS.max(0.5);
    let mut scored: Vec<Value> = Vec::new();
    for node in &sample.nodes {
        let node_id = pyutil::py_str(node.get("id").unwrap_or(&Value::Null));
        let accesses = access_count(node, stats.get(&node_id).copied()).unwrap_or(0.0);
        let age_days = match pyutil::parse_ts(node.get("updated_at")) {
            Some(stamp) => ((now - stamp) / 86_400.0).max(0.0),
            None => 0.0,
        };
        let decay = 0.5_f64.powf(age_days / half_life);
        let deg = *degree.get(&node_id).unwrap_or(&0);
        let mut item = sampling::slim(node);
        item.insert("accesses", Value::from(accesses));
        item.insert("degree", Value::from(deg));
        item.insert("age_days", Value::from(pyutil::round_to(age_days, 2)));
        item.insert(
            "score",
            Value::from(pyutil::round_to((1.0 + accesses + deg as f64) * decay, 4)),
        );
        item.insert("episodic", Value::Bool(is_episodic(node)));
        scored.push(json(&item));
    }
    scored.sort_by(|left, right| {
        let ls = left.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let rs = right.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        ls.partial_cmp(&rs)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                left.get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(right.get("id").and_then(Value::as_str).unwrap_or(""))
            })
    });
    let candidates: Vec<Value> = scored
        .iter()
        .filter(|item| item.get("episodic").and_then(Value::as_bool) == Some(true))
        .take(20)
        .cloned()
        .collect();
    let strongest: Vec<Value> = scored.iter().rev().take(5).cloned().collect();

    let mut out = OrderedMap::new();
    out.insert("nodes_scanned", Value::from(sample.nodes.len() as i64));
    out.insert("half_life_days", Value::from(half_life));
    out.insert(
        "access_source",
        Value::String(
            if stats.is_empty() {
                "metadata"
            } else {
                "store"
            }
            .to_string(),
        ),
    );
    out.insert("candidates", Value::Array(candidates.clone()));
    out.insert("candidate_count", Value::from(candidates.len() as i64));
    out.insert("strongest", Value::Array(strongest));
    out.insert("generated_at", Value::String(generated_at.to_string()));
    out
}

fn is_episodic(node: &Value) -> bool {
    let kind = pyutil::field_text(node, "type").to_lowercase();
    EPISODIC_TYPES.iter().any(|name| *name == kind)
}

/// `ProactiveBrain.consolidate_duplicates(..., dry_run=True)`.
pub fn consolidate_duplicates(sample: &Sample, dry_run: bool) -> OrderedMap {
    let duplicates = find_duplicates(&sample.nodes, NEAR_THRESHOLD, MAX_PAIRS);
    let groups: Vec<Value> = duplicates
        .get("exact_groups")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|group| {
            let members = group.get("nodes").and_then(Value::as_array)?.clone();
            if members.is_empty() {
                return None;
            }
            let mut ranked = members.clone();
            ranked.sort_by(|left, right| {
                let left_ts = pyutil::parse_ts(left.get("updated_at")).unwrap_or(0.0);
                let right_ts = pyutil::parse_ts(right.get("updated_at")).unwrap_or(0.0);
                right_ts
                    .partial_cmp(&left_ts)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| {
                        pyutil::text_of(left.get("id")).cmp(&pyutil::text_of(right.get("id")))
                    })
            });
            let keep = ranked.first()?.clone();
            let keep_id = pyutil::text_of(keep.get("id"));
            let remove: Vec<Value> = ranked
                .iter()
                .skip(1)
                .map(|node| Value::String(pyutil::text_of(node.get("id"))))
                .collect();
            Some(serde_json::json!({
                "keep": keep,
                "remove": remove,
                "keep_id": keep_id,
                "signature": group.get("signature").cloned().unwrap_or(Value::Null),
            }))
        })
        .collect();

    let mut out = OrderedMap::new();
    out.insert(
        "mode",
        Value::String(if dry_run { "dry_run" } else { "plan_only" }.to_string()),
    );
    out.insert("apply_supported", Value::Bool(false));
    out.insert("nodes_scanned", Value::from(sample.nodes.len() as i64));
    out.insert("groups", Value::Array(groups.clone()));
    out.insert("group_count", Value::from(groups.len() as i64));
    out.insert("applied", Value::Array(Vec::new()));
    out.insert(
        "near_pairs",
        duplicates
            .get("near_pairs")
            .cloned()
            .unwrap_or(Value::Array(Vec::new())),
    );
    out
}

fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}
