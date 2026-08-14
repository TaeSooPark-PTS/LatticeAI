//! `GET /api/brain/health` and `GET /api/brain/vector-freshness`.
//!
//! The health report grades four dimensions and is deliberate about what it
//! refuses to grade. Three rules in `health.py` decide its numbers and are
//! reproduced exactly:
//!
//! * **an empty index is not perfect coverage.** `embedding_coverage` is
//!   `unavailable` when the store reports no `coverage_ratio` **or** when it has
//!   zero indexable items — scoring 100% of nothing is how a brand-new Brain
//!   used to grade itself "excellent" off its only measurable dimension.
//! * **`round()` is banker's rounding** (see [`super::pyutil::py_round`]).
//! * **`if cons_dim.get("contradiction_edges"):` is falsy at zero**, so a graph
//!   with no contradictions raises no action — a null check would raise one
//!   saying "0 contradiction edges recorded".
//!
//! The composite averages only what could be measured, reports that as
//! `coverage`, and when nothing could be measured says why rather than leaving
//! a bare null (the 9.9.7 rule that a "—" always states its reason).

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

use crate::memory_api::kg;
use crate::memory_api::shared::BrainState;

use super::pyutil::{self, py_round};
use super::quality::EdgeQuality;
use super::sampling::{self, Sample, STALE_DAYS};

/// `BrainIntelligenceService.health_report`.
pub async fn health_report(state: &BrainState, workspace_id: Option<&str>) -> OrderedMap {
    let sample = sampling::graph_sample(state, workspace_id).await;
    let index_status = read_index_status(state).await;
    build_health_report(&sample, index_status.as_ref(), &state.now())
}

/// The pure half, so the scoring can be tested without a store.
pub fn build_health_report(
    sample: &Sample,
    index_status: Option<&OrderedMap>,
    generated_at: &str,
) -> OrderedMap {
    let mut dimensions = OrderedMap::new();
    dimensions.insert("freshness", freshness_dimension(sample));
    dimensions.insert("connectivity", connectivity_dimension(sample));
    dimensions.insert("embedding_coverage", embedding_dimension(index_status));
    dimensions.insert("consistency", consistency_dimension(sample));

    let names = [
        "freshness",
        "connectivity",
        "embedding_coverage",
        "consistency",
    ];
    let scores: Vec<i64> = names
        .iter()
        .filter_map(|name| dimensions.get(*name).and_then(|dim| dim.get("score")))
        .filter_map(Value::as_i64)
        .collect();
    let overall = if scores.is_empty() {
        None
    } else {
        Some(py_round(
            scores.iter().sum::<i64>() as f64 / scores.len() as f64,
        ))
    };
    let grade = overall.map(|score| match score {
        s if s >= 85 => "excellent",
        s if s >= 70 => "good",
        s if s >= 50 => "attention",
        _ => "critical",
    });

    // `sorted(...)` over the dimension names whose score is null.
    let mut unmeasured: Vec<&str> = names
        .iter()
        .copied()
        .filter(|name| {
            dimensions
                .get(*name)
                .and_then(|dim| dim.get("score"))
                .map(Value::is_null)
                .unwrap_or(false)
        })
        .collect();
    unmeasured.sort_unstable();

    let mut coverage = OrderedMap::new();
    coverage.insert("measured", Value::from(scores.len() as i64));
    coverage.insert("total", Value::from(names.len() as i64));
    coverage.insert(
        "unavailable",
        Value::Array(unmeasured.iter().map(|name| Value::from(*name)).collect()),
    );
    coverage.insert("partial", Value::Bool(!unmeasured.is_empty()));

    let reason = overall.is_none().then(|| {
        let parts: Vec<String> = unmeasured
            .iter()
            .map(|name| {
                let stated = dimensions
                    .get(*name)
                    .and_then(|dim| dim.get("reason"))
                    .filter(|value| pyutil::truthy(value))
                    .map(pyutil::py_str)
                    .unwrap_or_else(|| "unavailable".to_string());
                format!("{name}: {stated}")
            })
            .collect();
        format!(
            "no health dimension could be measured yet — {}",
            parts.join("; ")
        )
    });

    let actions = recommended_actions(&dimensions);

    let mut report = OrderedMap::new();
    report.insert(
        "overall_score",
        overall.map(Value::from).unwrap_or(Value::Null),
    );
    report.insert("grade", grade.map(Value::from).unwrap_or(Value::Null));
    report.insert("dimensions", json_of(&dimensions));
    report.insert("coverage", json_of(&coverage));
    report.insert("recommended_actions", Value::Array(actions));
    report.insert("graph_available", Value::Bool(sample.available));
    report.insert("generated_at", Value::String(generated_at.to_string()));
    if let Some(reason) = reason {
        report.insert("reason", Value::String(reason));
    }
    report
}

fn json_of(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

fn unavailable(reason: &str) -> Value {
    let mut dim = OrderedMap::new();
    dim.insert("status", Value::from("unavailable"));
    dim.insert("score", Value::Null);
    dim.insert("reason", Value::from(reason));
    json_of(&dim)
}

/// How much of the sampled knowledge saw a recent update.
fn freshness_dimension(sample: &Sample) -> Value {
    if !(sample.available && !sample.nodes.is_empty()) {
        return unavailable(sampling::no_graph_reason(sample.available));
    }
    let cutoff = sampling::now_utc_secs() - (STALE_DAYS * 86_400) as f64;
    let known: Vec<f64> = sample
        .nodes
        .iter()
        .filter_map(|node| pyutil::parse_ts(node.get("updated_at")))
        .collect();
    let stale = known.iter().filter(|stamp| **stamp < cutoff).count();
    let fresh_ratio = if known.is_empty() {
        0.0
    } else {
        1.0 - (stale as f64 / known.len() as f64)
    };
    let mut dim = OrderedMap::new();
    dim.insert("status", Value::from("ok"));
    dim.insert("score", Value::from(py_round(fresh_ratio * 100.0)));
    dim.insert("sampled", Value::from(sample.nodes.len() as i64));
    dim.insert("stale_nodes", Value::from(stale as i64));
    dim.insert("stale_threshold_days", Value::from(STALE_DAYS));
    json_of(&dim)
}

/// Orphan nodes are knowledge the Brain cannot reason across.
fn connectivity_dimension(sample: &Sample) -> Value {
    if !(sample.available && !sample.nodes.is_empty()) {
        return unavailable(sampling::no_graph_reason(sample.available));
    }
    let mut connected: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for edge in &sample.edges {
        connected.insert(sampling::endpoint(edge, "source", "from_node"));
        connected.insert(sampling::endpoint(edge, "target", "to_node"));
    }
    let orphans = sample
        .nodes
        .iter()
        .filter(|node| !connected.contains(&node_id_str(node)))
        .count();
    let ratio = 1.0 - (orphans as f64 / sample.nodes.len() as f64);
    let mut dim = OrderedMap::new();
    dim.insert("status", Value::from("ok"));
    dim.insert("score", Value::from(py_round(ratio * 100.0)));
    dim.insert("sampled", Value::from(sample.nodes.len() as i64));
    dim.insert("orphan_nodes", Value::from(orphans as i64));
    dim.insert("edges", Value::from(sample.edges.len() as i64));
    json_of(&dim)
}

/// `str(node.get("id"))` — an absent id stringifies to `"None"`, not `""`.
fn node_id_str(node: &Value) -> String {
    node.get("id")
        .map(pyutil::py_str)
        .unwrap_or_else(|| "None".to_string())
}

/// Semantic recall only works for indexed items — and only where it was measured.
fn embedding_dimension(index_status: Option<&OrderedMap>) -> Value {
    let empty = OrderedMap::new();
    let status = index_status.unwrap_or(&empty);
    let scale = status
        .get("scale")
        .filter(|value| pyutil::truthy(value))
        .cloned()
        .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
    let indexable = scale
        .get("source_items")
        .or_else(|| status.get("source_items"))
        .cloned();
    if scale.get("coverage_ratio").is_none() {
        return unavailable("this knowledge store does not report vector index coverage");
    }
    if indexable.as_ref().and_then(Value::as_f64) == Some(0.0) {
        return unavailable("no indexable items yet");
    }
    let ratio = scale
        .get("coverage_ratio")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let mut dim = OrderedMap::new();
    dim.insert("status", Value::from("ok"));
    dim.insert("score", Value::from(py_round(ratio * 100.0)));
    dim.insert(
        "ready_items",
        scale.get("ready_items").cloned().unwrap_or(Value::Null),
    );
    dim.insert(
        "pending_items",
        scale.get("pending_items").cloned().unwrap_or(Value::Null),
    );
    dim.insert(
        "needs_reindex",
        Value::Bool(status.get("status") == Some(&Value::from("needs_reindex"))),
    );
    json_of(&dim)
}

/// Duplicate-edge rate plus contradiction pressure, from the quality layer.
fn consistency_dimension(sample: &Sample) -> Value {
    if !(sample.available && !sample.edges.is_empty()) {
        let reason = if !(sample.available && !sample.nodes.is_empty()) {
            sampling::no_graph_reason(sample.available)
        } else {
            "no relationships recorded yet"
        };
        return unavailable(reason);
    }
    let metrics = EdgeQuality::compute_quality_metrics(&sample.edges);
    let contradictions = sample
        .edges
        .iter()
        .filter(|edge| is_contradiction(edge))
        .count();
    let dup_rate = metrics
        .get("dup_rate")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let pressure = (dup_rate + contradictions as f64 / sample.edges.len().max(1) as f64).min(1.0);
    let mut dim = OrderedMap::new();
    dim.insert("status", Value::from("ok"));
    dim.insert("score", Value::from(py_round((1.0 - pressure) * 100.0)));
    dim.insert("edge_metrics", json_of(&metrics));
    dim.insert("contradiction_edges", Value::from(contradictions as i64));
    json_of(&dim)
}

/// `"CONTRADICT" in str(edge.get("type") or "").upper()`.
pub fn is_contradiction(edge: &Value) -> bool {
    pyutil::field_text(edge, "type")
        .to_uppercase()
        .contains("CONTRADICT")
}

/// The four rules that turn a diagnosis into something to do.
fn recommended_actions(dimensions: &OrderedMap) -> Vec<Value> {
    let mut actions = Vec::new();
    let read = |name: &str, key: &str| -> Option<Value> {
        dimensions.get(name).and_then(|dim| dim.get(key)).cloned()
    };
    let action = |id: &str, reason: String| -> Value {
        let mut entry = OrderedMap::new();
        entry.insert("id", Value::from(id));
        entry.insert("reason", Value::String(reason));
        json_of(&entry)
    };
    if read("embedding_coverage", "needs_reindex")
        .map(|value| pyutil::truthy(&value))
        .unwrap_or(false)
    {
        let pending = read("embedding_coverage", "pending_items")
            .map(|value| pyutil::py_str(&value))
            .unwrap_or_else(|| "0".to_string());
        actions.push(action(
            "rebuild_vector_index",
            format!("{pending} items are missing or stale in the vector index."),
        ));
    }
    if read("connectivity", "score")
        .and_then(|value| value.as_i64())
        .is_some_and(|score| score < 70)
    {
        let orphans = read("connectivity", "orphan_nodes")
            .map(|value| pyutil::py_str(&value))
            .unwrap_or_else(|| "0".to_string());
        actions.push(action(
            "review_orphans",
            format!("{orphans} nodes have no relationships."),
        ));
    }
    if read("freshness", "score")
        .and_then(|value| value.as_i64())
        .is_some_and(|score| score < 60)
    {
        let stale = read("freshness", "stale_nodes")
            .map(|value| pyutil::py_str(&value))
            .unwrap_or_else(|| "0".to_string());
        actions.push(action(
            "refresh_stale_knowledge",
            format!("{stale} nodes untouched for over {STALE_DAYS} days."),
        ));
    }
    // Truthiness, not a null check: zero contradiction edges raises nothing.
    if let Some(edges) = read("consistency", "contradiction_edges") {
        if pyutil::truthy(&edges) {
            actions.push(action(
                "resolve_contradictions",
                format!(
                    "{} contradiction edges recorded in the graph.",
                    pyutil::py_str(&edges)
                ),
            ));
        }
    }
    actions
}

/// `self._kg.index_status()`, or `None` when the store could not answer.
pub async fn read_index_status(state: &BrainState) -> Option<OrderedMap> {
    if !state.graph_enabled() {
        return None;
    }
    let db_path = state.store().path().to_string_lossy().into_owned();
    state
        .read(move |conn| Ok(kg::index_status(conn, &db_path).ok()))
        .await
        .unwrap_or(None)
}

/// `BrainIntelligenceService.vector_freshness` — four keys, plus a breakdown.
pub async fn vector_freshness(state: &BrainState) -> OrderedMap {
    if !state.graph_enabled() {
        return freshness_unavailable("knowledge graph is disabled; no vector index is configured");
    }
    let db_path = state.store().path().to_string_lossy().into_owned();
    let measured = state
        .read(move |conn| {
            Ok(kg::index_status(conn, &db_path).ok().map(|status| {
                let summary = kg::vector_freshness_summary(conn, &status);
                let breakdown = kg::vector_freshness_breakdown(conn, &status, &summary);
                (summary, breakdown)
            }))
        })
        .await
        .unwrap_or(None);
    let Some((summary, breakdown)) = measured else {
        // Python renders the exception text here; the exception is the store's,
        // so the sentence names the failure without claiming Python's wording.
        return freshness_unavailable("vector freshness read failed: knowledge store unavailable");
    };
    let mut payload = contract_of(&summary);
    payload.insert("breakdown", json_of(&breakdown));
    payload
}

/// `_unavailable(detail)` — the honest zero, never a fake reading.
fn freshness_unavailable(detail: &str) -> OrderedMap {
    let mut out = OrderedMap::new();
    out.insert("status", Value::from("unavailable"));
    out.insert("pending_items", Value::from(0));
    out.insert("total_items", Value::from(0));
    out.insert("detail", Value::from(detail));
    out
}

/// `_vector_freshness_contract` over the store's own freshness answer.
fn contract_of(raw: &OrderedMap) -> OrderedMap {
    let mut status = raw
        .get("status")
        .filter(|value| pyutil::truthy(value))
        .map(pyutil::py_str)
        .unwrap_or_else(|| "unavailable".to_string());
    if status == "needs_reindex" {
        status = "pending".to_string();
    }
    if !matches!(status.as_str(), "ready" | "pending" | "unavailable") {
        status = "unavailable".to_string();
    }
    let count = |key: &str| -> i64 {
        raw.get(key)
            .filter(|value| pyutil::truthy(value))
            .and_then(Value::as_f64)
            .map(|value| value.trunc() as i64)
            .unwrap_or(0)
    };
    let mut out = OrderedMap::new();
    out.insert("status", Value::String(status));
    out.insert("pending_items", Value::from(count("pending_items")));
    out.insert("total_items", Value::from(count("total_items")));
    out.insert(
        "detail",
        Value::String(
            raw.get("detail")
                .filter(|value| pyutil::truthy(value))
                .map(pyutil::py_str)
                .unwrap_or_default(),
        ),
    );
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, updated_at: &str) -> Value {
        serde_json::json!({"id": id, "type": "Concept", "title": id, "updated_at": updated_at})
    }

    fn edge(id: &str, from: &str, to: &str, kind: &str) -> Value {
        serde_json::json!({
            "id": id, "from": from, "to": to, "type": kind,
            "source": from, "target": to,
        })
    }

    fn status(
        coverage: f64,
        source_items: i64,
        ready: i64,
        pending: i64,
        state: &str,
    ) -> OrderedMap {
        let mut scale = OrderedMap::new();
        scale.insert("coverage_ratio", Value::from(coverage));
        scale.insert("source_items", Value::from(source_items));
        scale.insert("ready_items", Value::from(ready));
        scale.insert("pending_items", Value::from(pending));
        let mut out = OrderedMap::new();
        out.insert("status", Value::from(state));
        out.insert("source_items", Value::from(source_items));
        out.insert("scale", json_of(&scale));
        out
    }

    fn now_stamp(offset_days: f64) -> String {
        let secs = sampling::now_utc_secs() - offset_days * 86_400.0;
        let days = (secs / 86_400.0).floor() as i64;
        let rest = secs - days as f64 * 86_400.0;
        // A crude civil rendering is enough: only the parsed epoch matters.
        let (year, month, day) = civil_from_days(days);
        format!(
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
            (rest / 3600.0) as i64,
            ((rest % 3600.0) / 60.0) as i64,
            (rest % 60.0) as i64
        )
    }

    fn civil_from_days(mut days: i64) -> (i64, u32, u32) {
        days += 719_468;
        let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
        let doe = days - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
        let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
        (if m <= 2 { y + 1 } else { y }, m, d)
    }

    #[test]
    fn a_healthy_graph_grades_every_dimension() {
        let sample = Sample {
            nodes: vec![node("a", &now_stamp(1.0)), node("b", &now_stamp(2.0))],
            edges: vec![edge("e1", "a", "b", "RELATED_TO")],
            available: true,
        };
        let report = build_health_report(&sample, Some(&status(1.0, 10, 10, 0, "ready")), "@ts");
        assert_eq!(report.get("overall_score"), Some(&Value::from(100)));
        assert_eq!(report.get("grade"), Some(&Value::from("excellent")));
        assert_eq!(
            report.get("recommended_actions"),
            Some(&Value::Array(vec![]))
        );
        assert_eq!(report.get("reason"), None);
        let coverage = report.get("coverage").expect("coverage");
        assert_eq!(coverage["measured"], 4);
        assert_eq!(coverage["partial"], false);
        assert_eq!(coverage["unavailable"], serde_json::json!([]));
        let dims = report.get("dimensions").expect("dimensions");
        assert_eq!(dims["freshness"]["stale_nodes"], 0);
        assert_eq!(dims["connectivity"]["orphan_nodes"], 0);
        assert_eq!(dims["consistency"]["edge_metrics"]["avg_conf"], 0.5);
    }

    #[test]
    fn an_empty_index_is_unavailable_rather_than_a_perfect_hundred() {
        let empty = Sample::default();
        let report = build_health_report(&empty, Some(&status(1.0, 0, 0, 0, "ready")), "@ts");
        assert_eq!(report.get("overall_score"), Some(&Value::Null));
        assert_eq!(report.get("grade"), Some(&Value::Null));
        let dims = report.get("dimensions").expect("dimensions");
        assert_eq!(
            dims["embedding_coverage"]["reason"],
            "no indexable items yet"
        );
        assert_eq!(
            dims["freshness"]["reason"],
            "the knowledge graph could not be read"
        );
        let reason = report
            .get("reason")
            .and_then(Value::as_str)
            .expect("reason");
        assert!(reason.starts_with("no health dimension could be measured yet — "));
        assert!(reason.contains("connectivity: the knowledge graph could not be read"));
        assert!(reason.contains("embedding_coverage: no indexable items yet"));
        assert_eq!(report.get("coverage").expect("coverage")["partial"], true);
    }

    #[test]
    fn a_store_that_reports_no_coverage_ratio_is_not_graded() {
        let mut status = OrderedMap::new();
        status.insert("status", Value::from("ready"));
        let report = build_health_report(&Sample::default(), Some(&status), "@ts");
        assert_eq!(
            report.get("dimensions").expect("dimensions")["embedding_coverage"]["reason"],
            "this knowledge store does not report vector index coverage"
        );
        let none = build_health_report(&Sample::default(), None, "@ts");
        assert_eq!(
            none.get("dimensions").expect("dimensions")["embedding_coverage"]["status"],
            "unavailable"
        );
    }

    #[test]
    fn every_recommended_action_rule_fires_on_its_own_evidence() {
        let stale: Vec<Value> = (0..10)
            .map(|i| node(&format!("n{i}"), &now_stamp(400.0)))
            .collect();
        let sample = Sample {
            nodes: stale,
            edges: vec![
                edge("e1", "n0", "n1", "CONTRADICTS"),
                edge("e2", "n0", "n1", "CONTRADICTS"),
            ],
            available: true,
        };
        let report = build_health_report(
            &sample,
            Some(&status(0.5, 10, 5, 5, "needs_reindex")),
            "@ts",
        );
        let actions = report
            .get("recommended_actions")
            .and_then(Value::as_array)
            .expect("actions");
        let ids: Vec<&str> = actions.iter().filter_map(|a| a["id"].as_str()).collect();
        assert_eq!(
            ids,
            vec![
                "rebuild_vector_index",
                "review_orphans",
                "refresh_stale_knowledge",
                "resolve_contradictions"
            ]
        );
        assert_eq!(
            actions[0]["reason"],
            "5 items are missing or stale in the vector index."
        );
        assert_eq!(actions[2]["reason"], "10 nodes untouched for over 45 days.");
        assert_eq!(
            actions[3]["reason"],
            "2 contradiction edges recorded in the graph."
        );
        let dims = report.get("dimensions").expect("dimensions");
        assert_eq!(
            dims["consistency"]["score"], 0,
            "both edges contradict (and they are a duplicate pair)"
        );
        assert_eq!(dims["freshness"]["score"], 0);
    }

    #[test]
    fn a_graph_with_nodes_but_no_edges_says_so() {
        let sample = Sample {
            nodes: vec![node("a", &now_stamp(1.0))],
            edges: Vec::new(),
            available: true,
        };
        let report = build_health_report(&sample, None, "@ts");
        let dims = report.get("dimensions").expect("dimensions");
        assert_eq!(
            dims["consistency"]["reason"],
            "no relationships recorded yet"
        );
        assert_eq!(dims["connectivity"]["score"], 0, "one node, no edges");
        assert_eq!(report.get("grade"), Some(&Value::from("attention")));
        assert!(is_contradiction(
            &serde_json::json!({"type": "contradicts"})
        ));
        assert!(!is_contradiction(&serde_json::json!({"type": null})));
    }

    #[test]
    fn grade_thresholds_sit_where_python_puts_them() {
        for (score, grade) in [
            (85, "excellent"),
            (84, "good"),
            (70, "good"),
            (69, "attention"),
            (50, "attention"),
            (49, "critical"),
        ] {
            let mut scale = OrderedMap::new();
            scale.insert("coverage_ratio", Value::from(score as f64 / 100.0));
            scale.insert("source_items", Value::from(10));
            let mut status = OrderedMap::new();
            status.insert("status", Value::from("ready"));
            status.insert("scale", json_of(&scale));
            let report = build_health_report(&Sample::default(), Some(&status), "@ts");
            assert_eq!(
                report.get("grade"),
                Some(&Value::from(grade)),
                "score {score} should grade {grade}"
            );
        }
    }

    #[test]
    fn the_freshness_contract_normalises_whatever_the_store_answers() {
        let mut raw = OrderedMap::new();
        raw.insert("status", Value::from("needs_reindex"));
        raw.insert("pending_items", Value::from(3));
        raw.insert("total_items", Value::from(9));
        raw.insert("detail", Value::from("3 of 9 items are missing"));
        let contract = contract_of(&raw);
        assert_eq!(contract.get("status"), Some(&Value::from("pending")));
        assert_eq!(contract.get("pending_items"), Some(&Value::from(3)));
        let mut odd = OrderedMap::new();
        odd.insert("status", Value::from("stale_embedder"));
        let contract = contract_of(&odd);
        assert_eq!(contract.get("status"), Some(&Value::from("unavailable")));
        assert_eq!(contract.get("total_items"), Some(&Value::from(0)));
        assert_eq!(contract.get("detail"), Some(&Value::from("")));
        let unavailable = freshness_unavailable("no index");
        assert_eq!(unavailable.get("detail"), Some(&Value::from("no index")));
    }
}
