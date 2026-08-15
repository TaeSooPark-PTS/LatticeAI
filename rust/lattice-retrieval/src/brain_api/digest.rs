//! `GET /api/brain/insights` and `GET /api/brain/garden`.
//!
//! Two read-only views over the same graph slice. `insights` is the proactive
//! digest — recent growth, trending types, stale knowledge, orphans, and
//! questions grounded in real node titles. `garden_overview` is the same
//! knowledge arranged as four beds a gardener tends, because "how healthy is my
//! knowledge?" in aggregate is not a question anyone acts on.
//!
//! Three orderings are load-bearing and all rest on `sorted()` being **stable**:
//! trending types keep the order their type was first seen in when counts tie,
//! and the recent/stale beds keep sample order when two nodes carry the same
//! `updated_at`. `sort_by` is stable in Rust too; `sort_unstable_by` is not, and
//! would reorder every tie.
//!
//! Honest when empty: an unavailable graph produces empty beds and
//! `available: false`, never invented plants.

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
    clippy::useless_format,
    clippy::collapsible_str_replace,
    clippy::manual_repeat_n,
    clippy::module_inception
)]
use lattice_auth::OrderedMap;
use serde_json::Value;

use crate::memory_api::shared::BrainState;

use super::pyutil;
use super::sampling::{self, Sample, RECENT_DAYS, STALE_DAYS};
use super::{consistency, proactive};

/// `BrainIntelligenceService.insights`.
pub async fn insights(state: &BrainState, workspace_id: Option<&str>) -> OrderedMap {
    let sample = sampling::graph_sample(state, workspace_id).await;
    build_insights(&sample, &state.now(), state.now_utc())
}

/// The pure digest, so the windows can be tested without a store.
///
/// `now_utc` is UTC epoch seconds, injected: both windows below are thresholds,
/// and the 7-day one is the tightest in the crate. See
/// `BrainState::with_utc_clock`.
pub fn build_insights(sample: &Sample, generated_at: &str, now_utc: f64) -> OrderedMap {
    let now = now_utc;
    let recent_cutoff = now - (RECENT_DAYS * 86_400) as f64;
    let stale_cutoff = now - (STALE_DAYS * 86_400) as f64;

    let mut recent: Vec<&Value> = Vec::new();
    let mut stale: Vec<&Value> = Vec::new();
    let mut types = pyutil::Counter::new();
    for node in &sample.nodes {
        let node_type = match node.get("type") {
            Some(value) if pyutil::truthy(value) => pyutil::py_str(value),
            _ => "node".to_string(),
        };
        let Some(stamp) = pyutil::parse_ts(node.get("updated_at")) else {
            continue;
        };
        if stamp >= recent_cutoff {
            recent.push(node);
            types.bump(&node_type);
        } else if stamp < stale_cutoff {
            stale.push(node);
        }
    }
    let orphans = orphan_nodes(sample);

    let mut activity = OrderedMap::new();
    activity.insert("recent_nodes", Value::from(recent.len() as i64));
    activity.insert("recent_samples", slim_list(&recent, 8));
    activity.insert(
        "trending_types",
        Value::Array(
            types
                .ranked()
                .into_iter()
                .take(5)
                .map(|(name, count)| {
                    let mut entry = OrderedMap::new();
                    entry.insert("type", Value::String(name));
                    entry.insert("count", Value::from(count));
                    json_of(&entry)
                })
                .collect(),
        ),
    );

    let mut attention = OrderedMap::new();
    attention.insert("stale_nodes", Value::from(stale.len() as i64));
    attention.insert("stale_samples", slim_list(&stale, 8));
    attention.insert("orphan_nodes", Value::from(orphans.len() as i64));
    attention.insert("orphan_samples", slim_list(&orphans, 8));

    let questions: Vec<Value> = recent
        .iter()
        .take(3)
        .filter_map(|node| {
            let title = lattice_core::pytext::strip(&pyutil::field_text(node, "title"));
            if title.is_empty() {
                return None;
            }
            Some(Value::String(format!(
                "{}에 대해 지금까지 알고 있는 것을 정리해줘",
                pyutil::head(&title, 60)
            )))
        })
        .collect();

    let mut out = OrderedMap::new();
    out.insert("window_days", Value::from(RECENT_DAYS));
    out.insert("activity", json_of(&activity));
    out.insert("attention", json_of(&attention));
    out.insert("suggested_questions", Value::Array(questions));
    out.insert("graph_available", Value::Bool(sample.available));
    out.insert("generated_at", Value::String(generated_at.to_string()));
    out
}

/// Nodes no edge in the slice names, by the connectivity reading.
fn orphan_nodes(sample: &Sample) -> Vec<&Value> {
    let mut connected: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for edge in &sample.edges {
        connected.insert(sampling::endpoint(edge, "source", "from_node"));
        connected.insert(sampling::endpoint(edge, "target", "to_node"));
    }
    sample
        .nodes
        .iter()
        .filter(|node| {
            let id = node
                .get("id")
                .map(pyutil::py_str)
                .unwrap_or_else(|| "None".to_string());
            !connected.contains(&id)
        })
        .collect()
}

fn slim_list(nodes: &[&Value], limit: usize) -> Value {
    Value::Array(
        nodes
            .iter()
            .take(limit)
            .map(|node| json_of(&sampling::slim(node)))
            .collect(),
    )
}

fn json_of(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

/// `BrainIntelligenceService.garden_overview` — the knowledge garden in four beds.
pub async fn garden_overview(
    state: &BrainState,
    user_email: &str,
    workspace_id: Option<&str>,
    limit: i64,
) -> OrderedMap {
    let sample = sampling::graph_sample(state, workspace_id).await;
    let found = consistency::contradictions(state, user_email, workspace_id).await;
    let items = found
        .get("items")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    build_garden(&sample, &items, limit, &state.now(), state.now_utc())
}

/// The pure four beds.
///
/// `now_utc` is UTC epoch seconds; the `recent` and `stale` beds are the same
/// two thresholds `build_insights` uses. See `BrainState::with_utc_clock`.
pub fn build_garden(
    sample: &Sample,
    contradictions: &[Value],
    limit: i64,
    generated_at: &str,
    now_utc: f64,
) -> OrderedMap {
    // An explicit 0 clamps to 1 — `limit or 8` would silently re-expand it.
    let limit = limit.clamp(1, 50).max(1) as usize;
    let now = now_utc;
    let recent_cutoff = now - (RECENT_DAYS * 86_400) as f64;
    let stale_cutoff = now - (STALE_DAYS * 86_400) as f64;

    let mut recent: Vec<&Value> = Vec::new();
    let mut stale: Vec<&Value> = Vec::new();
    for node in &sample.nodes {
        // Chunks are retrieval plumbing, not knowledge a gardener tends.
        if pyutil::field_text(node, "type") == "Chunk" {
            continue;
        }
        let Some(stamp) = pyutil::parse_ts(node.get("updated_at")) else {
            continue;
        };
        if stamp >= recent_cutoff {
            recent.push(node);
        } else if stamp < stale_cutoff {
            stale.push(node);
        }
    }
    let key = |node: &&Value| pyutil::field_text(node, "updated_at");
    recent.sort_by(|left, right| key(right).cmp(&key(left)));
    stale.sort_by(|left, right| key(left).cmp(&key(right)));

    // "Frequent" is degree, not a guess: how many relations point at a node.
    let mut degree = pyutil::Counter::new();
    for edge in &sample.edges {
        for field in ["source", "target"] {
            let node_id = pyutil::text_of(edge.get(field));
            if !node_id.is_empty() {
                degree.bump(&node_id);
            }
        }
    }
    let mut by_id: std::collections::HashMap<String, &Value> = std::collections::HashMap::new();
    for node in &sample.nodes {
        by_id.insert(
            node.get("id")
                .map(pyutil::py_str)
                .unwrap_or_else(|| "None".to_string()),
            node,
        );
    }
    let frequent: Vec<Value> = degree
        .ranked()
        .into_iter()
        .filter_map(|(node_id, count)| {
            let node = by_id.get(&node_id)?;
            if pyutil::field_text(node, "type") == "Chunk" {
                return None;
            }
            let mut slimmed = sampling::slim(node);
            slimmed.insert("degree", Value::from(count));
            Some(json_of(&slimmed))
        })
        .take(limit)
        .collect();

    let bed = |count: usize, items: Value| -> Value {
        let mut out = OrderedMap::new();
        out.insert("count", Value::from(count as i64));
        out.insert("items", items);
        json_of(&out)
    };

    let mut beds = OrderedMap::new();
    beds.insert("recent", bed(recent.len(), slim_list(&recent, limit)));
    beds.insert(
        "contradictions",
        bed(
            contradictions.len(),
            Value::Array(contradictions.iter().take(limit).cloned().collect()),
        ),
    );
    beds.insert("stale", bed(stale.len(), slim_list(&stale, limit)));
    beds.insert("frequent", bed(frequent.len(), Value::Array(frequent)));

    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(sample.available));
    out.insert("window_days", Value::from(RECENT_DAYS));
    out.insert("stale_threshold_days", Value::from(STALE_DAYS));
    out.insert("beds", json_of(&beds));
    out.insert("generated_at", Value::String(generated_at.to_string()));
    out
}

/// `GET /api/brain/duplicates` — duplicate graph nodes, read only.
pub async fn graph_duplicates(state: &BrainState, workspace_id: Option<&str>) -> OrderedMap {
    let now = state.now();
    if !state.graph_enabled() {
        return duplicates_unavailable(&now);
    }
    let sample = sampling::graph_sample(state, workspace_id).await;
    if !sample.available {
        return duplicates_unavailable(&now);
    }
    let mut result = proactive::find_duplicates(
        &sample.nodes,
        proactive::NEAR_THRESHOLD,
        proactive::MAX_PAIRS,
    );
    result.insert("available", Value::Bool(true));
    result.insert("generated_at", Value::String(now));
    result
}

fn duplicates_unavailable(now: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("exact_groups", Value::Array(Vec::new()));
    out.insert("near_pairs", Value::Array(Vec::new()));
    out.insert("exact_duplicate_nodes", Value::from(0));
    out.insert("nodes_scanned", Value::from(0));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// `GET /api/brain/importance` — importance and decay, read only.
pub async fn importance_report(state: &BrainState, workspace_id: Option<&str>) -> OrderedMap {
    let now = state.now();
    if !state.graph_enabled() {
        return importance_unavailable(&now);
    }
    let sample = sampling::graph_sample(state, workspace_id).await;
    if !sample.available {
        return importance_unavailable(&now);
    }
    let stats = proactive::access_stats(state, &sample.nodes).await;
    let mut report = proactive::importance_report(&sample, &stats, &now, state.now_utc());
    report.insert("available", Value::Bool(true));
    report
}

fn importance_unavailable(now: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("candidates", Value::Array(Vec::new()));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

/// `GET /api/brain/quality-report` — duplicates, contradictions, stale nodes,
/// edge quality, and what the Brain would tidy away.
pub async fn quality_report(state: &BrainState, workspace_id: Option<&str>) -> OrderedMap {
    let now = state.now();
    if !state.graph_enabled() {
        return quality_unavailable(&now);
    }
    let sample = sampling::graph_sample(state, workspace_id).await;
    if !sample.available {
        return quality_unavailable(&now);
    }
    let mut result = proactive::quality_report(&sample, &now, state.now_utc());
    result.insert("available", Value::Bool(true));

    // v11.1.0: decay is part of quality, and "the Brain is tidying up" is a
    // state the user is entitled to see rather than a background surprise.
    let importance = importance_report(state, workspace_id).await;
    let candidates = importance
        .get("candidates")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    result.insert("importance", json_of(&importance));
    result.insert(
        "tidying",
        Value::Bool(
            importance
                .get("available")
                .map(pyutil::truthy)
                .unwrap_or(false)
                && candidates > 0,
        ),
    );
    let mut summary: OrderedMap = result
        .get("summary")
        .and_then(|value| serde_json::from_value(value.clone()).ok())
        .unwrap_or_default();
    summary.insert("consolidation_candidates", Value::from(candidates as i64));
    result.insert("summary", json_of(&summary));
    result.insert("generated_at", Value::String(now));
    result
}

fn quality_unavailable(now: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("available", Value::Bool(false));
    out.insert("generated_at", Value::String(now.to_string()));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, kind: &str, title: &str, updated_at: &str) -> Value {
        serde_json::json!({"id": id, "type": kind, "title": title, "updated_at": updated_at})
    }

    fn edge(from: &str, to: &str) -> Value {
        serde_json::json!({
            "id": format!("{from}->{to}"), "from": from, "to": to, "type": "RELATED_TO",
            "source": from, "target": to,
        })
    }

    fn stamp(days_ago: f64) -> String {
        let secs = sampling::now_utc_secs() - days_ago * 86_400.0;
        format!("{}", iso_of(secs))
    }

    fn iso_of(secs: f64) -> String {
        let days = (secs / 86_400.0).floor() as i64;
        let rest = secs - days as f64 * 86_400.0;
        let mut civil = days + 719_468;
        let era = if civil >= 0 { civil } else { civil - 146_096 } / 146_097;
        civil -= era * 146_097;
        let yoe = (civil - civil / 1460 + civil / 36524 - civil / 146_096) / 365;
        let doy = civil - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = doy - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let year = yoe + era * 400 + i64::from(month <= 2);
        format!(
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
            (rest / 3600.0) as i64,
            ((rest % 3600.0) / 60.0) as i64,
            (rest % 60.0) as i64
        )
    }

    fn sample() -> Sample {
        Sample {
            nodes: vec![
                node("a", "Concept", "Alpha", &stamp(1.0)),
                node("b", "Concept", "Beta", &stamp(2.0)),
                node("c", "Document", "Gamma", &stamp(3.0)),
                node("old", "Document", "Ancient", &stamp(400.0)),
                node("chunk", "Chunk", "plumbing", &stamp(1.0)),
                node("lonely", "Concept", "", &stamp(1.0)),
            ],
            edges: vec![edge("a", "b"), edge("b", "c"), edge("a", "c")],
            available: true,
        }
    }

    #[test]
    fn the_digest_counts_windows_and_grounds_its_questions_in_real_titles() {
        let digest = build_insights(&sample(), "@ts", sampling::now_utc_secs());
        assert_eq!(digest.get("window_days"), Some(&Value::from(7)));
        let activity = digest.get("activity").expect("activity");
        assert_eq!(activity["recent_nodes"], 5);
        assert_eq!(
            activity["trending_types"],
            serde_json::json!([
                {"type": "Concept", "count": 3},
                {"type": "Document", "count": 1},
                {"type": "Chunk", "count": 1},
            ])
        );
        let attention = digest.get("attention").expect("attention");
        assert_eq!(attention["stale_nodes"], 1);
        assert_eq!(attention["orphan_nodes"], 3);
        assert_eq!(
            digest.get("suggested_questions"),
            Some(&serde_json::json!([
                "Alpha에 대해 지금까지 알고 있는 것을 정리해줘",
                "Beta에 대해 지금까지 알고 있는 것을 정리해줘",
                "Gamma에 대해 지금까지 알고 있는 것을 정리해줘",
            ]))
        );
    }

    #[test]
    fn an_untitled_recent_node_asks_no_question() {
        let sample = Sample {
            nodes: vec![node("lonely", "Concept", "   ", &stamp(1.0))],
            edges: Vec::new(),
            available: true,
        };
        let digest = build_insights(&sample, "@ts", sampling::now_utc_secs());
        assert_eq!(
            digest.get("suggested_questions"),
            Some(&serde_json::json!([]))
        );
        assert_eq!(digest.get("graph_available"), Some(&Value::Bool(true)));
        let empty = build_insights(&Sample::default(), "@ts", sampling::now_utc_secs());
        assert_eq!(empty.get("graph_available"), Some(&Value::Bool(false)));
        assert_eq!(empty.get("activity").expect("activity")["recent_nodes"], 0);
    }

    #[test]
    fn the_garden_keeps_chunks_out_of_every_bed() {
        let garden = build_garden(
            &sample(),
            &[serde_json::json!({"kind": "x"})],
            5,
            "@ts",
            sampling::now_utc_secs(),
        );
        let beds = garden.get("beds").expect("beds");
        assert_eq!(beds["recent"]["count"], 4, "the Chunk is not a plant");
        assert_eq!(beds["stale"]["count"], 1);
        assert_eq!(beds["contradictions"]["count"], 1);
        assert_eq!(beds["frequent"]["count"], 3);
        let frequent = beds["frequent"]["items"].as_array().expect("items");
        assert_eq!(frequent[0]["id"], "a");
        assert_eq!(frequent[0]["degree"], 2);
        assert_eq!(garden.get("available"), Some(&Value::Bool(true)));
        assert_eq!(garden.get("stale_threshold_days"), Some(&Value::from(45)));
        // Newest first in `recent`, oldest first in `stale`.
        let recent = beds["recent"]["items"].as_array().expect("items");
        assert_eq!(recent[0]["id"], "a");
    }

    #[test]
    fn an_explicit_zero_limit_clamps_to_one_rather_than_re_expanding() {
        let garden = build_garden(&sample(), &[], 0, "@ts", sampling::now_utc_secs());
        let beds = garden.get("beds").expect("beds");
        assert_eq!(beds["recent"]["items"].as_array().expect("items").len(), 1);
        let wide = build_garden(&sample(), &[], 500, "@ts", sampling::now_utc_secs());
        assert_eq!(
            wide.get("beds").expect("beds")["recent"]["items"]
                .as_array()
                .expect("items")
                .len(),
            4
        );
        let negative = build_garden(&sample(), &[], -9, "@ts", sampling::now_utc_secs());
        assert_eq!(
            negative.get("beds").expect("beds")["stale"]["items"]
                .as_array()
                .expect("items")
                .len(),
            1
        );
    }

    #[test]
    fn an_unavailable_graph_produces_empty_beds_and_says_so() {
        let garden = build_garden(&Sample::default(), &[], 8, "@ts", sampling::now_utc_secs());
        assert_eq!(garden.get("available"), Some(&Value::Bool(false)));
        let beds = garden.get("beds").expect("beds");
        for name in ["recent", "contradictions", "stale", "frequent"] {
            assert_eq!(beds[name]["count"], 0, "{name} should be empty");
        }
        let unavailable = duplicates_unavailable("@ts");
        assert_eq!(unavailable.get("available"), Some(&Value::Bool(false)));
        assert_eq!(unavailable.get("nodes_scanned"), Some(&Value::from(0)));
        assert_eq!(
            importance_unavailable("@ts").get("candidates"),
            Some(&Value::Array(Vec::new()))
        );
        assert_eq!(
            quality_unavailable("@ts").get("generated_at"),
            Some(&Value::from("@ts"))
        );
    }
}
