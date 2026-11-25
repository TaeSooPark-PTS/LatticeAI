use std::collections::{BTreeMap, BTreeSet, HashMap};

use lattice_core::round6;
use serde_json::{json, Value};

use crate::bm25::reasoning_paths::{
    is_conflict_or_supersede_relation, record_best_chain, BridgeConcept, CandidateChain,
    NeighborhoodConflict, ReasoningPath, MAX_REASONING_PATHS,
};

#[derive(Debug, Clone)]
struct IncidentEdge {
    peer_id: String,
    relation: String,
    weight: f64,
}

fn node_label_and_type(node: &Value, fallback_id: &str) -> (String, String) {
    let title = node
        .get("title")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
        .unwrap_or(fallback_id)
        .to_string();
    let node_type = node
        .get("type")
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("Node")
        .to_string();
    (title, node_type)
}

fn clamped_weight(edge: &Value) -> f64 {
    edge.get("weight")
        .and_then(Value::as_f64)
        .filter(|w| w.is_finite())
        .unwrap_or(0.75)
        .clamp(0.05, 1.0)
}

pub fn synthesize_neighborhood(seed_id: &str, neighborhood: &Value) -> Value {
    let seed = seed_id.trim();
    let nodes = neighborhood
        .get("nodes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let edges = neighborhood
        .get("edges")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    let mut meta_by_id: HashMap<String, (String, String)> = HashMap::with_capacity(nodes.len() + 1);
    for node in &nodes {
        if let Some(id) = node.get("id").and_then(Value::as_str) {
            meta_by_id.insert(id.to_string(), node_label_and_type(node, id));
        }
    }
    meta_by_id
        .entry(seed.to_string())
        .or_insert_with(|| (seed.to_string(), "Node".to_string()));

    let lookup = |id: &str| -> (String, String) {
        meta_by_id
            .get(id)
            .cloned()
            .unwrap_or_else(|| (id.to_string(), "Node".to_string()))
    };

    let mut adjacency: HashMap<String, Vec<IncidentEdge>> = HashMap::new();
    let mut conflicts: Vec<NeighborhoodConflict> = Vec::new();
    let mut seen_conflicts: BTreeSet<(String, String, String)> = BTreeSet::new();

    for edge in &edges {
        let from = edge
            .get("from")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        let to = edge
            .get("to")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        if from.is_empty() || to.is_empty() || from == to {
            continue;
        }
        if !meta_by_id.contains_key(from) || !meta_by_id.contains_key(to) {
            continue;
        }
        let relation = edge
            .get("type")
            .and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty())
            .unwrap_or("RELATES_TO")
            .to_string();
        let weight = clamped_weight(edge);

        adjacency
            .entry(from.to_string())
            .or_default()
            .push(IncidentEdge {
                peer_id: to.to_string(),
                relation: relation.clone(),
                weight,
            });
        adjacency
            .entry(to.to_string())
            .or_default()
            .push(IncidentEdge {
                peer_id: from.to_string(),
                relation: relation.clone(),
                weight,
            });

        if is_conflict_or_supersede_relation(&relation) {
            let key = (from.to_string(), to.to_string(), relation.clone());
            if seen_conflicts.insert(key) {
                let (source_title, _) = lookup(from);
                let (target_title, _) = lookup(to);
                conflicts.push(NeighborhoodConflict {
                    source_id: from.to_string(),
                    source_title,
                    target_id: to.to_string(),
                    target_title,
                    relation,
                });
            }
        }
    }

    let mut best_chains: HashMap<String, CandidateChain> = HashMap::new();
    let mut direct_targets: BTreeSet<String> = BTreeSet::new();
    let mut bridge_targets: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();

    if let Some(first_hop) = adjacency.get(seed) {
        for hop1 in first_hop {
            if hop1.peer_id == seed {
                continue;
            }
            direct_targets.insert(hop1.peer_id.clone());
            record_best_chain(
                &mut best_chains,
                CandidateChain {
                    seed_id: seed.to_string(),
                    bridge_id: None,
                    target_id: hop1.peer_id.clone(),
                    hops: 1,
                    relations: vec![hop1.relation.clone()],
                    score: round6(hop1.weight),
                },
            );

            if let Some(second_hop) = adjacency.get(&hop1.peer_id) {
                for hop2 in second_hop {
                    if hop2.peer_id == seed || hop2.peer_id == hop1.peer_id {
                        continue;
                    }
                    let two_hop_score = round6(hop1.weight * hop2.weight * 0.85);
                    record_best_chain(
                        &mut best_chains,
                        CandidateChain {
                            seed_id: seed.to_string(),
                            bridge_id: Some(hop1.peer_id.clone()),
                            target_id: hop2.peer_id.clone(),
                            hops: 2,
                            relations: vec![hop1.relation.clone(), hop2.relation.clone()],
                            score: two_hop_score,
                        },
                    );
                    bridge_targets
                        .entry(hop1.peer_id.clone())
                        .or_default()
                        .insert(hop2.peer_id.clone());
                }
            }
        }
    }

    let (seed_title, _) = lookup(seed);
    let mut reasoning_paths: Vec<ReasoningPath> = best_chains
        .into_values()
        .map(|chain| {
            let (target_title, _) = lookup(&chain.target_id);
            let (bridge_id, bridge_title) = match &chain.bridge_id {
                Some(bid) => {
                    let (btitle, _) = lookup(bid);
                    (Some(bid.clone()), Some(btitle))
                }
                None => (None, None),
            };
            let is_contradiction = chain
                .relations
                .iter()
                .any(|rel| is_conflict_or_supersede_relation(rel));
            ReasoningPath {
                seed_id: seed.to_string(),
                seed_title: seed_title.clone(),
                bridge_id,
                bridge_title,
                target_id: chain.target_id,
                target_title,
                hops: chain.hops,
                relations: chain.relations,
                path_score: round6(chain.score),
                is_contradiction,
            }
        })
        .collect();

    reasoning_paths.sort_by(|a, b| {
        b.is_contradiction
            .cmp(&a.is_contradiction)
            .then_with(|| {
                b.path_score
                    .partial_cmp(&a.path_score)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| a.hops.cmp(&b.hops))
            .then_with(|| a.target_id.cmp(&b.target_id))
    });
    let multi_hop_hits = reasoning_paths.iter().filter(|p| p.hops >= 2).count();
    reasoning_paths.truncate(MAX_REASONING_PATHS);

    let mut bridge_concepts: Vec<BridgeConcept> = bridge_targets
        .into_iter()
        .map(|(id, targets)| {
            let (title, node_type) = lookup(&id);
            BridgeConcept {
                id,
                title,
                node_type,
                bridged_targets: targets.len(),
            }
        })
        .collect();
    bridge_concepts.sort_by(|a, b| {
        b.bridged_targets
            .cmp(&a.bridged_targets)
            .then_with(|| a.id.cmp(&b.id))
    });
    bridge_concepts.truncate(8);

    json!({
        "reasoning_paths": reasoning_paths,
        "synthesis": {
            "direct_hits": direct_targets.len(),
            "multi_hop_hits": multi_hop_hits,
            "reasoning_path_count": reasoning_paths.len(),
            "bridge_concepts": bridge_concepts,
            "contradictions": conflicts,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthesizes_multi_hop_chains_bridges_and_conflicts_from_neighborhood() {
        let neighborhood = json!({
            "root": "n1",
            "depth": 2,
            "nodes": [
                { "id": "n1", "type": "Decision", "title": "Zero-Copy IPC" },
                { "id": "n2", "type": "Concept", "title": "Shared Memory Ring" },
                { "id": "n3", "type": "Benchmark", "title": "Sub-ms Latency Proof" },
                { "id": "n4", "type": "Note", "title": "Legacy JSON Pipe" }
            ],
            "edges": [
                { "id": "e1", "from": "n1", "to": "n2", "type": "DEPENDS_ON", "weight": 0.9 },
                { "id": "e2", "from": "n2", "to": "n3", "type": "PROVES", "weight": 0.8 },
                { "id": "e3", "from": "n1", "to": "n4", "type": "SUPERSEDES", "weight": 0.95 }
            ]
        });

        let result = synthesize_neighborhood("n1", &neighborhood);
        let paths = result["reasoning_paths"].as_array().expect("paths");
        assert_eq!(paths.len(), 3);
        assert_eq!(paths[0]["target_id"], "n4");
        assert_eq!(paths[0]["is_contradiction"], true);

        let two_hop = paths
            .iter()
            .find(|p| p["target_id"] == "n3")
            .expect("2-hop path");
        assert_eq!(two_hop["hops"], 2);
        assert_eq!(two_hop["bridge_id"], "n2");
        assert_eq!(two_hop["bridge_title"], "Shared Memory Ring");

        let synthesis = &result["synthesis"];
        assert_eq!(synthesis["direct_hits"], 2);
        assert_eq!(synthesis["multi_hop_hits"], 1);
        assert_eq!(synthesis["bridge_concepts"][0]["id"], "n2");
        assert_eq!(synthesis["bridge_concepts"][0]["bridged_targets"], 1);
        assert_eq!(synthesis["contradictions"][0]["relation"], "SUPERSEDES");
    }

    #[test]
    fn handles_empty_or_isolated_neighborhood_gracefully() {
        let empty = json!({ "root": "solo", "depth": 1, "nodes": [], "edges": [] });
        let result = synthesize_neighborhood("solo", &empty);
        assert_eq!(result["reasoning_paths"], json!([]));
        assert_eq!(result["synthesis"]["direct_hits"], 0);
        assert_eq!(result["synthesis"]["multi_hop_hits"], 0);
        assert_eq!(result["synthesis"]["contradictions"], json!([]));
    }
}
