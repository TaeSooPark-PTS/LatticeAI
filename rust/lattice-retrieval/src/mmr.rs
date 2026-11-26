use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use crate::bm25::tokenize_bm25_cjk;
use crate::context_parents::parent_id_of;
use crate::shape::parent_node_id;

pub const MMR_ENV: &str = "LATTICEAI_MMR";
pub const MMR_LAMBDA_ENV: &str = "LATTICEAI_MMR_LAMBDA";
pub const PER_DOCUMENT_ENV: &str = "LATTICEAI_CHUNKS_PER_DOCUMENT";
pub const DEFAULT_LAMBDA: f64 = 0.7;
pub const DEFAULT_PER_DOCUMENT: usize = 2;
pub const MIN_PER_DOCUMENT: usize = 1;
pub const MAX_PER_DOCUMENT: usize = 3;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DiversityConfig {
    pub enabled: bool,
    pub lambda: f64,
    pub per_document: usize,
}

impl Default for DiversityConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            lambda: DEFAULT_LAMBDA,
            per_document: DEFAULT_PER_DOCUMENT,
        }
    }
}

impl DiversityConfig {
    pub fn from_env() -> Self {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Self {
        let enabled = lookup(MMR_ENV).is_none_or(|raw| {
            !matches!(
                raw.trim().to_ascii_lowercase().as_str(),
                "0" | "false" | "no" | "off"
            )
        });
        let lambda = lookup(MMR_LAMBDA_ENV)
            .and_then(|raw| raw.trim().parse::<f64>().ok())
            .filter(|value| value.is_finite())
            .unwrap_or(DEFAULT_LAMBDA)
            .clamp(0.0, 1.0);
        let per_document = lookup(PER_DOCUMENT_ENV)
            .and_then(|raw| raw.trim().parse::<usize>().ok())
            .unwrap_or(DEFAULT_PER_DOCUMENT)
            .clamp(MIN_PER_DOCUMENT, MAX_PER_DOCUMENT);
        Self {
            enabled,
            lambda,
            per_document,
        }
    }
}

struct Unit {
    members: Vec<usize>,
    document: String,
    tokens: BTreeSet<String>,
}

fn tokens_of(item: &Value) -> BTreeSet<String> {
    let text = ["title", "summary", "snippet"]
        .iter()
        .filter_map(|key| item.get(*key).and_then(Value::as_str))
        .collect::<Vec<_>>()
        .join(" ");
    tokenize_bm25_cjk(&text).into_iter().collect()
}

fn similarity(left: &BTreeSet<String>, right: &BTreeSet<String>) -> f64 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    let shared = left.intersection(right).count();
    shared as f64 / (left.len() + right.len() - shared) as f64
}

fn units_of(matches: &[Value]) -> Vec<Unit> {
    let mut units: Vec<Unit> = Vec::new();
    let mut parents: BTreeMap<String, usize> = BTreeMap::new();
    for (index, item) in matches.iter().enumerate() {
        let parent = parent_id_of(item).map(str::to_string);
        if let Some(position) = parent.as_ref().and_then(|id| parents.get(id)) {
            units[*position].members.push(index);
            continue;
        }
        if let Some(parent) = parent {
            parents.insert(parent, units.len());
        }
        units.push(Unit {
            members: vec![index],
            document: item.as_object().map(parent_node_id).unwrap_or_default(),
            tokens: tokens_of(item),
        });
    }
    units
}

pub fn diversify(
    mut matches: Vec<Value>,
    limit: usize,
    config: &DiversityConfig,
) -> (Vec<Value>, Value) {
    let candidates = matches.len();
    if !config.enabled {
        matches.truncate(limit);
        return (matches, json!({"applied": false, "candidates": candidates}));
    }
    let units = units_of(&matches);
    let total = units.len();
    let mut chosen: Vec<usize> = Vec::new();
    let mut per_document: BTreeMap<&str, usize> = BTreeMap::new();
    while chosen.len() < limit {
        let mut best: Option<(usize, f64)> = None;
        for (position, unit) in units.iter().enumerate() {
            let used = per_document
                .get(unit.document.as_str())
                .copied()
                .unwrap_or(0);
            if chosen.contains(&position)
                || (!unit.document.is_empty() && used >= config.per_document)
            {
                continue;
            }
            let relevance = 1.0 - position as f64 / total as f64;
            let redundancy = chosen
                .iter()
                .map(|other| similarity(&unit.tokens, &units[*other].tokens))
                .fold(0.0, f64::max);
            let score = config.lambda * relevance - (1.0 - config.lambda) * redundancy;
            if best.is_none_or(|(_, top)| score > top + 1e-12) {
                best = Some((position, score));
            }
        }
        let Some((position, _)) = best else {
            break;
        };
        chosen.push(position);
        *per_document
            .entry(units[position].document.as_str())
            .or_insert(0) += 1;
    }
    let keep: BTreeSet<usize> = chosen
        .iter()
        .flat_map(|position| units[*position].members.iter().copied())
        .collect();
    let selected: Vec<Value> = matches
        .into_iter()
        .enumerate()
        .filter(|(index, _)| keep.contains(index))
        .map(|(_, item)| item)
        .collect();
    let report = json!({
        "applied": true,
        "lambda": config.lambda,
        "per_document_cap": config.per_document,
        "candidates": candidates,
        "selected_units": chosen.len(),
        "returned": selected.len(),
    });
    (selected, report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(id: &str, document: &str, parent: &str, text: &str) -> Value {
        json!({
            "id": id,
            "type": "Chunk",
            "title": text,
            "summary": text,
            "metadata": {"source_node": document, "parent_id": parent},
        })
    }

    fn ids(items: &[Value]) -> Vec<&str> {
        items
            .iter()
            .filter_map(|item| item["id"].as_str())
            .collect()
    }

    #[test]
    fn one_document_is_capped_and_the_incoming_order_survives() {
        let matches = vec![
            chunk("a1", "doc-a", "", "alpha one rollout"),
            chunk("a2", "doc-a", "", "alpha two budget"),
            chunk("a3", "doc-a", "", "alpha three owners"),
            chunk("b1", "doc-b", "", "beta schedule"),
        ];
        let (out, report) = diversify(matches, 5, &DiversityConfig::default());
        assert_eq!(ids(&out), vec!["a1", "a2", "b1"]);
        assert_eq!(report["selected_units"], 3);
    }

    #[test]
    fn near_duplicates_lose_to_a_distinct_passage() {
        let matches = vec![
            chunk("a", "d1", "", "phoenix rollback restart the host"),
            chunk("b", "d2", "", "phoenix rollback restart the host"),
            chunk("c", "d3", "", "budget approval owners schedule"),
        ];
        let config = DiversityConfig {
            lambda: 0.5,
            ..Default::default()
        };
        let (out, _) = diversify(matches, 2, &config);
        assert_eq!(ids(&out), vec!["a", "c"]);
    }

    #[test]
    fn children_of_one_parent_count_once_and_travel_together() {
        let matches = vec![
            chunk("c1", "d1", "parent:1", "restart the host"),
            chunk("c2", "d1", "parent:1", "check the port"),
            chunk("c3", "d1", "parent:2", "rotate keys"),
            chunk("c4", "d1", "parent:3", "archive logs"),
            chunk("e1", "d2", "", "unrelated budget"),
        ];
        let (out, report) = diversify(matches, 5, &DiversityConfig::default());
        assert_eq!(ids(&out), vec!["c1", "c2", "c3", "e1"]);
        assert_eq!(report["selected_units"], 3);
    }

    #[test]
    fn the_switch_and_bounds_come_from_the_environment() {
        let off = DiversityConfig::from_lookup(|key| (key == MMR_ENV).then(|| "off".into()));
        let matches = vec![chunk("a", "d", "", "x"), chunk("b", "d", "", "y")];
        assert_eq!(ids(&diversify(matches, 1, &off).0), vec!["a"]);
        let wide = DiversityConfig::from_lookup(|_| Some("9".into()));
        assert_eq!(wide.per_document, MAX_PER_DOCUMENT);
        assert_eq!(wide.lambda, 1.0);
        assert_eq!(
            DiversityConfig::from_lookup(|_| None),
            DiversityConfig::default()
        );
    }
}
