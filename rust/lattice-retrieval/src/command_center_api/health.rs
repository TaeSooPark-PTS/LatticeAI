use std::collections::{BTreeSet, HashMap};

use lattice_auth::OrderedMap;
use rusqlite::Connection;
use serde_json::Value;

use super::store::{parse_ts, py_round_int, py_text};
use crate::memory_api::kg;
use crate::shape::{py_str, truthy};

const STALE_DAYS: i64 = 45;
pub(crate) const GRAPH_SAMPLE_LIMIT: i64 = 800;

pub(crate) struct GraphSample {
    pub(crate) nodes: Vec<Value>,
    pub(crate) edges: Vec<Value>,
    pub(crate) available: bool,
}

impl GraphSample {
    fn unavailable() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            available: false,
        }
    }
}

pub(crate) fn graph_sample(
    conn: &Connection,
    workspace_id: Option<&str>,
    enable_graph: bool,
) -> GraphSample {
    if !enable_graph {
        return GraphSample::unavailable();
    }
    let allowed = workspace_id.map(|id| BTreeSet::from([id.to_string()]));
    let Ok(slice) = kg::graph_slice(conn, GRAPH_SAMPLE_LIMIT, allowed.as_ref()) else {
        return GraphSample::unavailable();
    };
    let edges = slice
        .edges
        .into_iter()
        .map(|mut edge| {
            if let Some(map) = edge.as_object_mut() {
                let from = map.get("from").cloned().unwrap_or(Value::Null);
                let to = map.get("to").cloned().unwrap_or(Value::Null);
                map.entry("source").or_insert(from);
                map.entry("target").or_insert(to);
            }
            edge
        })
        .collect();
    GraphSample {
        nodes: slice.nodes,
        edges,
        available: true,
    }
}

pub(crate) struct HealthReport {
    pub(crate) overall_score: Option<i64>,
    pub(crate) grade: Option<&'static str>,
    pub(crate) actions: Vec<Value>,
}

struct Dimension {
    score: Option<i64>,
    detail: i64,
    flag: bool,
}

impl Dimension {
    fn unavailable() -> Self {
        Self {
            score: None,
            detail: 0,
            flag: false,
        }
    }
}

pub(crate) fn health_report(
    conn: &Connection,
    db_path: &str,
    sample: &GraphSample,
    enable_graph: bool,
    now_utc: f64,
) -> HealthReport {
    let freshness = freshness_dimension(sample, now_utc);
    let connectivity = connectivity_dimension(sample);
    let embedding = embedding_dimension(conn, db_path, enable_graph);
    let consistency = consistency_dimension(sample);

    let scores: Vec<i64> = [&freshness, &connectivity, &embedding, &consistency]
        .iter()
        .filter_map(|dimension| dimension.score)
        .collect();
    let overall = if scores.is_empty() {
        None
    } else {
        Some(py_round_int(
            scores.iter().sum::<i64>() as f64 / scores.len() as f64,
        ))
    };
    let grade = overall.map(grade_of);

    let mut actions: Vec<Value> = Vec::new();
    if embedding.flag {
        actions.push(action(
            "rebuild_vector_index",
            &format!(
                "{} items are missing or stale in the vector index.",
                embedding.detail
            ),
        ));
    }
    if connectivity.score.is_some_and(|score| score < 70) {
        actions.push(action(
            "review_orphans",
            &format!("{} nodes have no relationships.", connectivity.detail),
        ));
    }
    if freshness.score.is_some_and(|score| score < 60) {
        actions.push(action(
            "refresh_stale_knowledge",
            &format!(
                "{} nodes untouched for over {STALE_DAYS} days.",
                freshness.detail
            ),
        ));
    }
    if consistency.detail != 0 {
        actions.push(action(
            "resolve_contradictions",
            &format!(
                "{} contradiction edges recorded in the graph.",
                consistency.detail
            ),
        ));
    }
    HealthReport {
        overall_score: overall,
        grade,
        actions,
    }
}

fn grade_of(score: i64) -> &'static str {
    if score >= 85 {
        "excellent"
    } else if score >= 70 {
        "good"
    } else if score >= 50 {
        "attention"
    } else {
        "critical"
    }
}

fn action(id: &str, reason: &str) -> Value {
    let mut entry = OrderedMap::new();
    entry.insert("id", Value::String(id.to_string()));
    entry.insert("reason", Value::String(reason.to_string()));
    serde_json::to_value(&entry).unwrap_or(Value::Null)
}

fn freshness_dimension(sample: &GraphSample, now_utc: f64) -> Dimension {
    if !sample.available || sample.nodes.is_empty() {
        return Dimension::unavailable();
    }
    let cutoff = now_utc - (STALE_DAYS as f64) * 86_400.0;
    let known: Vec<f64> = sample
        .nodes
        .iter()
        .filter_map(|node| parse_ts(node.get("updated_at").unwrap_or(&Value::Null)))
        .collect();
    let stale = known.iter().filter(|stamp| **stamp < cutoff).count();
    let fresh_ratio = if known.is_empty() {
        0.0
    } else {
        1.0 - (stale as f64 / known.len() as f64)
    };
    Dimension {
        score: Some(py_round_int(fresh_ratio * 100.0)),
        detail: stale as i64,
        flag: false,
    }
}

fn connectivity_dimension(sample: &GraphSample) -> Dimension {
    if !sample.available || sample.nodes.is_empty() {
        return Dimension::unavailable();
    }
    let mut connected: BTreeSet<String> = BTreeSet::new();
    for edge in &sample.edges {
        for (primary, fallback) in [("source", "from_node"), ("target", "to_node")] {
            let endpoint = edge
                .get(primary)
                .filter(|value| truthy(value))
                .or_else(|| edge.get(fallback))
                .cloned()
                .unwrap_or(Value::Null);
            connected.insert(py_text(Some(&endpoint)));
        }
    }
    let orphans = sample
        .nodes
        .iter()
        .filter(|node| !connected.contains(&py_str_of(node.get("id"))))
        .count();
    let ratio = 1.0 - (orphans as f64 / sample.nodes.len() as f64);
    Dimension {
        score: Some(py_round_int(ratio * 100.0)),
        detail: orphans as i64,
        flag: false,
    }
}

fn py_str_of(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".to_string(),
        Some(value) => py_str(value),
    }
}

fn embedding_dimension(conn: &Connection, db_path: &str, enable_graph: bool) -> Dimension {
    if !enable_graph {
        return Dimension::unavailable();
    }
    let Ok(status) = kg::index_status(conn, db_path) else {
        return Dimension::unavailable();
    };
    let status = serde_json::to_value(&status).unwrap_or(Value::Null);
    let scale = status.get("scale").cloned().unwrap_or(Value::Null);
    let Some(coverage) = scale.get("coverage_ratio").and_then(Value::as_f64) else {
        return Dimension::unavailable();
    };
    let indexable = scale
        .get("source_items")
        .or_else(|| status.get("source_items"))
        .and_then(Value::as_i64);
    if indexable == Some(0) {
        return Dimension::unavailable();
    }
    Dimension {
        score: Some(py_round_int(coverage * 100.0)),
        detail: scale
            .get("pending_items")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        flag: status.get("status").and_then(Value::as_str) == Some("needs_reindex"),
    }
}

fn consistency_dimension(sample: &GraphSample) -> Dimension {
    if !sample.available || sample.edges.is_empty() {
        return Dimension::unavailable();
    }
    let total = sample.edges.len();
    let mut seen: HashMap<String, ()> = HashMap::new();
    let mut duplicates = 0_usize;
    for edge in &sample.edges {
        let key = format!(
            "{}\u{1f}{}\u{1f}{}",
            edge.get("source").unwrap_or(&Value::Null),
            edge.get("target").unwrap_or(&Value::Null),
            edge.get("type").unwrap_or(&Value::Null),
        );
        if seen.insert(key, ()).is_some() {
            duplicates += 1;
        }
    }
    let dup_rate = lattice_core::pytext::round_to(duplicates as f64 / total as f64, 3);
    let contradictions = sample
        .edges
        .iter()
        .filter(|edge| {
            py_text(edge.get("type"))
                .to_uppercase()
                .contains("CONTRADICT")
        })
        .count();
    let pressure = (dup_rate + contradictions as f64 / total as f64).min(1.0);
    Dimension {
        score: Some(py_round_int((1.0 - pressure) * 100.0)),
        detail: contradictions as i64,
        flag: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::pytext::parse_iso;
    use serde_json::json;

    fn sample(nodes: Vec<Value>, edges: Vec<Value>) -> GraphSample {
        GraphSample {
            nodes,
            edges,
            available: true,
        }
    }

    fn at(stamp: &str) -> f64 {
        parse_iso(Some(stamp)).expect("stamp")
    }

    #[test]
    fn an_unavailable_graph_measures_nothing_rather_than_scoring_a_perfect_100() {
        let empty = GraphSample::unavailable();
        assert!(freshness_dimension(&empty, 0.0).score.is_none());
        assert!(connectivity_dimension(&empty).score.is_none());
        assert!(consistency_dimension(&empty).score.is_none());
        assert!(
            consistency_dimension(&sample(vec![json!({"id": "a"})], Vec::new()))
                .score
                .is_none()
        );
    }

    #[test]
    fn freshness_scores_only_the_nodes_whose_stamp_parses() {
        let measured = freshness_dimension(
            &sample(
                vec![
                    json!({"id": "a", "updated_at": "2026-08-13T00:00:00"}),
                    json!({"id": "b", "updated_at": "2026-01-01T00:00:00"}),
                    json!({"id": "c", "updated_at": null}),
                ],
                Vec::new(),
            ),
            at("2026-08-14T00:00:00"),
        );
        assert_eq!(
            measured.score,
            Some(50),
            "one of the two readable stamps is over 45 days old"
        );
        assert_eq!(measured.detail, 1);
    }

    #[test]
    fn connectivity_counts_a_node_no_edge_names() {
        let measured = connectivity_dimension(&sample(
            vec![json!({"id": "a"}), json!({"id": "b"}), json!({"id": "c"})],
            vec![json!({"source": "a", "target": "b", "type": "MENTIONS"})],
        ));
        assert_eq!(measured.detail, 1, "c is the orphan");
        assert_eq!(measured.score, Some(67), "round(2/3 * 100)");
    }

    #[test]
    fn an_edge_that_lost_its_source_key_falls_back_to_from_node() {
        let measured = connectivity_dimension(&sample(
            vec![json!({"id": "a"}), json!({"id": "b"})],
            vec![json!({"from_node": "a", "to_node": "b"})],
        ));
        assert_eq!(measured.detail, 0);
        assert_eq!(measured.score, Some(100));
    }

    #[test]
    fn consistency_charges_duplicates_and_contradictions() {
        let edges = vec![
            json!({"source": "a", "target": "b", "type": "MENTIONS"}),
            json!({"source": "a", "target": "b", "type": "MENTIONS"}),
            json!({"source": "a", "target": "c", "type": "CONTRADICTS"}),
            json!({"source": "b", "target": "c", "type": "MENTIONS"}),
        ];
        let measured = consistency_dimension(&sample(vec![json!({"id": "a"})], edges));
        assert_eq!(measured.score, Some(50));
        assert_eq!(measured.detail, 1);
    }

    #[test]
    fn the_grade_words_sit_on_their_thresholds() {
        assert_eq!(grade_of(85), "excellent");
        assert_eq!(grade_of(84), "good");
        assert_eq!(grade_of(70), "good");
        assert_eq!(grade_of(69), "attention");
        assert_eq!(grade_of(50), "attention");
        assert_eq!(grade_of(49), "critical");
    }
}
