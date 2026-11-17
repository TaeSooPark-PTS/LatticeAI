//! Multi-hop Knowledge Graph reasoning chains, bridge concept discovery, and
//! neighborhood contradiction/supersession synthesis (v13.0.0).

use std::collections::{BTreeMap, HashMap};

use lattice_core::{round6, NodeRow};
use serde::Serialize;
use serde_json::{json, Value};

use super::graph_rag::GraphStructuralHit;
use super::sidecar::Bm25SidecarIndex;

/// Maximum reasoning chains emitted in a single GraphRAG response.
pub const MAX_REASONING_PATHS: usize = 16;

/// Raw candidate chain tracked during 1-hop and 2-hop frontier expansion.
#[derive(Debug, Clone)]
pub struct CandidateChain {
    pub seed_id: String,
    pub bridge_id: Option<String>,
    pub target_id: String,
    pub hops: usize,
    pub relations: Vec<String>,
    pub score: f64,
}

/// Resolved, human-readable multi-hop reasoning path from a query seed to a target node.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ReasoningPath {
    pub seed_id: String,
    pub seed_title: String,
    pub bridge_id: Option<String>,
    pub bridge_title: Option<String>,
    pub target_id: String,
    pub target_title: String,
    pub hops: usize,
    pub relations: Vec<String>,
    pub path_score: f64,
    pub is_contradiction: bool,
}

/// Bridge concept/entity summary connecting direct seeds to 2-hop discoveries.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BridgeConcept {
    pub id: String,
    pub title: String,
    pub node_type: String,
    pub bridged_targets: usize,
}

/// Conflicting or superseding edge surfaced in the retrieved neighborhood.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NeighborhoodConflict {
    pub source_id: String,
    pub source_title: String,
    pub target_id: String,
    pub target_title: String,
    pub relation: String,
}

/// Return true if a relation label represents epistemic conflict or supersession.
pub fn is_conflict_or_supersede_relation(rel: &str) -> bool {
    matches!(
        rel.trim().to_ascii_uppercase().as_str(),
        "CONTRADICTS" | "CONFLICTS_WITH" | "REFUTES" | "DISPUTES" | "SUPERSEDES" | "REPLACES"
    )
}

/// Update `best_chains` if `candidate` improves on the existing chain for `target_id`
/// (preferring higher path score, then fewer hops, then lexicographic determinism).
pub fn record_best_chain(
    best_chains: &mut HashMap<String, CandidateChain>,
    candidate: CandidateChain,
) {
    if candidate.seed_id == candidate.target_id {
        return;
    }
    let replace = match best_chains.get(&candidate.target_id) {
        None => true,
        Some(existing) => {
            candidate.score > existing.score + 1e-9
                || ((candidate.score - existing.score).abs() <= 1e-9
                    && (candidate.hops < existing.hops
                        || (candidate.hops == existing.hops
                            && candidate.seed_id < existing.seed_id)))
        }
    };
    if replace {
        best_chains.insert(candidate.target_id.clone(), candidate);
    }
}

fn resolve_title_and_type(
    id: &str,
    sidecar: &Bm25SidecarIndex,
    extra_nodes: &HashMap<String, NodeRow>,
) -> (String, String) {
    if let Some(doc) = sidecar.get_doc(id) {
        let title = doc
            .node
            .title
            .as_deref()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(id)
            .to_string();
        let ntype = doc
            .node
            .node_type
            .as_deref()
            .unwrap_or("Document")
            .to_string();
        return (title, ntype);
    }
    if let Some(row) = extra_nodes.get(id) {
        let title = row
            .title
            .as_deref()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(id)
            .to_string();
        let ntype = row.node_type.as_deref().unwrap_or("Document").to_string();
        return (title, ntype);
    }
    (id.to_string(), "Node".to_string())
}

/// Convert a [`CandidateChain`] into a resolved [`ReasoningPath`].
pub fn resolve_reasoning_path(
    chain: &CandidateChain,
    final_score: f64,
    sidecar: &Bm25SidecarIndex,
    extra_nodes: &HashMap<String, NodeRow>,
) -> ReasoningPath {
    let (seed_title, _) = resolve_title_and_type(&chain.seed_id, sidecar, extra_nodes);
    let (target_title, _) = resolve_title_and_type(&chain.target_id, sidecar, extra_nodes);
    let (bridge_id, bridge_title) = match &chain.bridge_id {
        Some(bid) => {
            let (btitle, _) = resolve_title_and_type(bid, sidecar, extra_nodes);
            (Some(bid.clone()), Some(btitle))
        }
        None => (None, None),
    };
    let is_contradiction = chain
        .relations
        .iter()
        .any(|r| is_conflict_or_supersede_relation(r));
    ReasoningPath {
        seed_id: chain.seed_id.clone(),
        seed_title,
        bridge_id,
        bridge_title,
        target_id: chain.target_id.clone(),
        target_title,
        hops: chain.hops,
        relations: chain.relations.clone(),
        path_score: round6(final_score),
        is_contradiction,
    }
}

/// Build the structured multi-hop `reasoning_paths` list and `synthesis` block
/// from ranked results and expanded graph hits.
pub fn build_multi_hop_synthesis(
    direct_hits: usize,
    multi_hop_hits: usize,
    graph_hits: &[GraphStructuralHit],
    raw_conflicts: &[(String, String, String)],
    sidecar: &Bm25SidecarIndex,
    extra_nodes: &HashMap<String, NodeRow>,
) -> (Vec<ReasoningPath>, Value) {
    let mut paths: Vec<ReasoningPath> = graph_hits
        .iter()
        .filter_map(|hit| hit.best_path.clone())
        .collect();
    paths.sort_by(|a, b| {
        b.path_score
            .total_cmp(&a.path_score)
            .then_with(|| a.hops.cmp(&b.hops))
            .then_with(|| a.target_id.cmp(&b.target_id))
    });
    paths.truncate(MAX_REASONING_PATHS);

    let mut bridge_counts: BTreeMap<String, usize> = BTreeMap::new();
    for hit in graph_hits {
        for bid in &hit.bridge_nodes {
            *bridge_counts.entry(bid.clone()).or_insert(0) += 1;
        }
    }
    let mut bridge_concepts: Vec<BridgeConcept> = bridge_counts
        .into_iter()
        .map(|(bid, count)| {
            let (title, node_type) = resolve_title_and_type(&bid, sidecar, extra_nodes);
            BridgeConcept {
                id: bid,
                title,
                node_type,
                bridged_targets: count,
            }
        })
        .collect();
    bridge_concepts.sort_by(|a, b| {
        b.bridged_targets
            .cmp(&a.bridged_targets)
            .then_with(|| a.id.cmp(&b.id))
    });
    bridge_concepts.truncate(10);

    let mut seen_conflicts = std::collections::BTreeSet::new();
    let mut contradictions: Vec<NeighborhoodConflict> = Vec::new();
    for (src, dst, rel) in raw_conflicts {
        let key = (src.clone(), dst.clone(), rel.to_ascii_uppercase());
        if !seen_conflicts.insert(key) {
            continue;
        }
        let (source_title, _) = resolve_title_and_type(src, sidecar, extra_nodes);
        let (target_title, _) = resolve_title_and_type(dst, sidecar, extra_nodes);
        contradictions.push(NeighborhoodConflict {
            source_id: src.clone(),
            source_title,
            target_id: dst.clone(),
            target_title,
            relation: rel.clone(),
        });
        if contradictions.len() >= 10 {
            break;
        }
    }

    let synthesis = json!({
        "direct_hits": direct_hits,
        "multi_hop_hits": multi_hop_hits,
        "reasoning_path_count": paths.len(),
        "bridge_concepts": bridge_concepts,
        "contradictions": contradictions,
    });
    (paths, synthesis)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_best_chain_prefers_higher_score_then_shorter_hops() {
        let mut best = HashMap::new();
        record_best_chain(
            &mut best,
            CandidateChain {
                seed_id: "seed-b".into(),
                bridge_id: Some("bridge-1".into()),
                target_id: "target-1".into(),
                hops: 2,
                relations: vec!["MENTIONS".into(), "RELATES_TO".into()],
                score: 0.45,
            },
        );
        record_best_chain(
            &mut best,
            CandidateChain {
                seed_id: "seed-a".into(),
                bridge_id: None,
                target_id: "target-1".into(),
                hops: 1,
                relations: vec!["DEPENDS_ON".into()],
                score: 0.72,
            },
        );
        let chosen = best.get("target-1").expect("chosen chain");
        assert_eq!(chosen.seed_id, "seed-a");
        assert_eq!(chosen.hops, 1);
        assert!(is_conflict_or_supersede_relation("CONTRADICTS"));
        assert!(is_conflict_or_supersede_relation("supersedes"));
        assert!(!is_conflict_or_supersede_relation("RELATES_TO"));
    }
}
