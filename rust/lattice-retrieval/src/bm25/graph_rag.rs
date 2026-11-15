//! Knowledge Graph 1-hop & 2-hop structural expansion (GraphRAG).

use std::collections::{HashMap, HashSet};

use lattice_core::{filter_scoped_nodes, CoreError, NodeRow};
use rusqlite::Connection;

use crate::service::Scope;

use super::scoring::row_to_node;
use super::tokenizer::extract_wikilinks_and_tags;

/// Configuration for Knowledge Graph structural neighborhood traversal & boosting.
#[derive(Debug, Clone)]
pub struct GraphRagConfig {
    /// Maximum traversal depth (1 or 2 hops; defaults to 2).
    pub max_hops: usize,
    /// Number of top seed hits from BM25F / Vector to expand from.
    pub max_seeds: usize,
    /// Maximum expanded neighbor nodes to admit.
    pub max_neighbors: usize,
    /// Decay factor for 1-hop neighbors.
    pub hop1_decay: f64,
    /// Decay factor for 2-hop neighbors (and shared concept/entity/wikilink/tag bridges).
    pub hop2_decay: f64,
}

impl Default for GraphRagConfig {
    fn default() -> Self {
        Self {
            max_hops: 2,
            max_seeds: 12,
            max_neighbors: 40,
            hop1_decay: 0.68,
            hop2_decay: 0.38,
        }
    }
}

/// Structural graph evidence for a node in the Knowledge Graph.
#[derive(Debug, Clone)]
pub struct GraphStructuralHit {
    pub node: NodeRow,
    pub graph_score: f64,
    pub min_hop: usize,
    pub connected_seeds: Vec<String>,
    pub relations: Vec<String>,
    pub degree: usize,
}

fn relation_type_weight(relation_type: &str) -> f64 {
    match relation_type.trim().to_ascii_uppercase().as_str() {
        "PART_OF" => 1.00,
        "RELATES_TO" => 0.95,
        "DEPENDS_ON" => 0.92,
        "USES" | "IMPLEMENTS" => 0.90,
        "MENTIONS" | "REFERENCES" => 0.85,
        "DERIVED_FROM" | "SUMMARIZES" => 0.82,
        "HAS_TAG" | "WIKILINK" | "SHARED_CONCEPT" => 0.80,
        _ => 0.68,
    }
}

/// Expand seed nodes across 1-hop and 2-hop Knowledge Graph edges (`RELATES_TO`,
/// `MENTIONS`, `PART_OF`, etc.), shared Concept/Entity bridge nodes, `[[wikilinks]]`,
/// and `#tags`, returning structurally scored nodes ordered by `graph_score`.
pub fn expand_knowledge_graph(
    conn: &Connection,
    seeds: &[(String, f64)],
    corpus: &[NodeRow],
    scope: &Scope,
    cfg: &GraphRagConfig,
) -> Result<Vec<GraphStructuralHit>, CoreError> {
    if seeds.is_empty() {
        return Ok(Vec::new());
    }

    let mut node_map: HashMap<String, NodeRow> =
        corpus.iter().map(|n| (n.id.clone(), n.clone())).collect();

    let active_seeds: Vec<(String, f64)> = seeds
        .iter()
        .take(cfg.max_seeds.max(1))
        .filter(|(_, s)| *s > 0.0)
        .cloned()
        .collect();
    if active_seeds.is_empty() {
        return Ok(Vec::new());
    }

    let max_seed_score = active_seeds
        .iter()
        .map(|(_, s)| *s)
        .fold(0.0f64, f64::max)
        .max(1e-6);

    // Track structural evidence per node_id
    let mut raw_scores: HashMap<String, f64> = HashMap::new();
    let mut min_hops: HashMap<String, usize> = HashMap::new();
    let mut seed_links: HashMap<String, HashSet<String>> = HashMap::new();
    let mut relation_kinds: HashMap<String, HashSet<String>> = HashMap::new();
    let mut degree_map: HashMap<String, usize> = HashMap::new();

    // Load edges connected to seeds (1-hop) and their neighbors (2-hop)
    let mut frontier_ids: Vec<String> = active_seeds.iter().map(|(id, _)| id.clone()).collect();
    let mut visited_frontier: HashSet<String> = frontier_ids.iter().cloned().collect();

    // Map from seed_id -> normalized seed strength in (0, 1]
    let seed_strength: HashMap<String, f64> = active_seeds
        .iter()
        .map(|(id, s)| (id.clone(), (*s / max_seed_score).clamp(0.15, 1.0)))
        .collect();

    // Track 1-hop intermediate nodes: node_id -> (best_seed_id, path_score, relation_type)
    let mut hop1_paths: HashMap<String, Vec<(String, f64, String)>> = HashMap::new();

    for hop in 1..=cfg.max_hops.clamp(1, 2) {
        if frontier_ids.is_empty() {
            break;
        }
        let placeholders = (1..=frontier_ids.len())
            .map(|i| format!("?{i}"))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT source_id, target_id, relation_type, weight
             FROM edges
             WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(frontier_ids.iter()), |row| {
            let source_id: String = row.get(0)?;
            let target_id: String = row.get(1)?;
            let relation_type: Option<String> = row.get(2)?;
            let weight: Option<f64> = row.get(3)?;
            Ok((
                source_id,
                target_id,
                relation_type.unwrap_or_else(|| "RELATES_TO".to_string()),
                weight.unwrap_or(1.0).clamp(0.05, 5.0),
            ))
        })?;

        let mut next_frontier: Vec<String> = Vec::new();
        for edge in rows {
            let (src, dst, rel, weight) = edge?;
            *degree_map.entry(src.clone()).or_insert(0) += 1;
            *degree_map.entry(dst.clone()).or_insert(0) += 1;

            let rel_w = relation_type_weight(&rel) * weight.sqrt();

            if hop == 1 {
                for (from, to) in [(&src, &dst), (&dst, &src)] {
                    if let Some(&s_strength) = seed_strength.get(from) {
                        let contrib = s_strength * rel_w * cfg.hop1_decay;
                        *raw_scores.entry(to.clone()).or_insert(0.0) += contrib;
                        min_hops
                            .entry(to.clone())
                            .and_modify(|h| *h = (*h).min(1))
                            .or_insert(1);
                        seed_links
                            .entry(to.clone())
                            .or_default()
                            .insert(from.clone());
                        relation_kinds
                            .entry(to.clone())
                            .or_default()
                            .insert(rel.clone());

                        // Credit the seed itself for verified structural edges
                        *raw_scores.entry(from.clone()).or_insert(0.0) += 0.22 * rel_w;
                        min_hops.entry(from.clone()).or_insert(0);
                        seed_links
                            .entry(from.clone())
                            .or_default()
                            .insert(from.clone());
                        relation_kinds
                            .entry(from.clone())
                            .or_default()
                            .insert(rel.clone());

                        hop1_paths.entry(to.clone()).or_default().push((
                            from.clone(),
                            s_strength * rel_w,
                            rel.clone(),
                        ));

                        if !visited_frontier.contains(to) {
                            visited_frontier.insert(to.clone());
                            next_frontier.push(to.clone());
                        }
                    }
                }
            } else {
                // Hop 2: expand from 1-hop intermediates (especially Concept/Entity/Topic bridges)
                for (from, to) in [(&src, &dst), (&dst, &src)] {
                    if let Some(paths) = hop1_paths.get(from).cloned() {
                        let bridge_mult = node_map
                            .get(from)
                            .and_then(|n| n.node_type.as_deref())
                            .map(|nt| match nt {
                                "Concept" | "Entity" | "Topic" | "Decision" => 1.25,
                                _ => 1.0,
                            })
                            .unwrap_or(1.0);
                        for (origin_seed, p_score, rel1) in paths {
                            if &origin_seed == to {
                                continue;
                            }
                            let contrib = p_score * rel_w * cfg.hop2_decay * bridge_mult;
                            *raw_scores.entry(to.clone()).or_insert(0.0) += contrib;
                            min_hops
                                .entry(to.clone())
                                .and_modify(|h| *h = (*h).min(2))
                                .or_insert(2);
                            seed_links
                                .entry(to.clone())
                                .or_default()
                                .insert(origin_seed);
                            relation_kinds
                                .entry(to.clone())
                                .or_default()
                                .insert(format!("{rel1}->{rel}"));
                        }
                    }
                }
            }
        }
        next_frontier.truncate(cfg.max_neighbors * 2);
        frontier_ids = next_frontier;
    }

    // Fetch any missing node rows referenced by edges
    let missing_ids: Vec<String> = raw_scores
        .keys()
        .filter(|id| !node_map.contains_key(*id))
        .cloned()
        .collect();
    if !missing_ids.is_empty() {
        let placeholders = (1..=missing_ids.len())
            .map(|i| format!("?{i}"))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT id, type, title, summary, metadata_json, updated_at
             FROM nodes
             WHERE id IN ({placeholders})"
        );
        let mut stmt = conn.prepare(&sql)?;
        let iter = stmt.query_map(rusqlite::params_from_iter(missing_ids.iter()), row_to_node)?;
        let mut fetched = Vec::new();
        for item in iter {
            fetched.push(item?);
        }
        let scoped_fetched = filter_scoped_nodes(
            conn,
            fetched,
            scope.allowed_workspaces.as_ref(),
            scope.include_legacy_global,
            |r| r.id.clone(),
        )?;
        for node in scoped_fetched {
            node_map.insert(node.id.clone(), node);
        }
    }

    // Link nodes via implicit wikilinks `[[...]]` and `#tags` shared with seeds
    let mut seed_wikilinks: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    let mut seed_tags: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    for (seed_id, &strength) in &seed_strength {
        if let Some(seed_node) = node_map.get(seed_id) {
            let s_title = seed_node.title.as_deref().unwrap_or_default();
            let s_sum = seed_node.summary.as_deref().unwrap_or_default();
            let s_meta = seed_node.metadata_json.as_deref().unwrap_or_default();
            let combined = format!("{s_title} {s_sum} {s_meta}");
            let (wls, tgs) = extract_wikilinks_and_tags(&combined);
            let seed_title_norm = s_title.trim().to_lowercase();
            if !seed_title_norm.is_empty() {
                seed_wikilinks
                    .entry(seed_title_norm)
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
            for wl in wls {
                seed_wikilinks
                    .entry(wl)
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
            for tg in tgs {
                seed_tags
                    .entry(tg)
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
        }
    }

    if !seed_wikilinks.is_empty() || !seed_tags.is_empty() {
        for cand in corpus {
            let c_title = cand.title.as_deref().unwrap_or_default();
            let c_sum = cand.summary.as_deref().unwrap_or_default();
            let c_meta = cand.metadata_json.as_deref().unwrap_or_default();
            let combined = format!("{c_title} {c_sum} {c_meta}");
            let (cand_wls, cand_tgs) = extract_wikilinks_and_tags(&combined);
            let cand_title_norm = c_title.trim().to_lowercase();

            if let Some(origins) = seed_wikilinks.get(&cand_title_norm) {
                for (seed_id, strength) in origins {
                    if seed_id != &cand.id {
                        *raw_scores.entry(cand.id.clone()).or_insert(0.0) +=
                            strength * 0.80 * cfg.hop1_decay;
                        min_hops
                            .entry(cand.id.clone())
                            .and_modify(|h| *h = (*h).min(1))
                            .or_insert(1);
                        seed_links
                            .entry(cand.id.clone())
                            .or_default()
                            .insert(seed_id.clone());
                        relation_kinds
                            .entry(cand.id.clone())
                            .or_default()
                            .insert("WIKILINK".to_string());
                    }
                }
            }
            for wl in &cand_wls {
                if let Some(origins) = seed_wikilinks.get(wl) {
                    for (seed_id, strength) in origins {
                        if seed_id != &cand.id {
                            *raw_scores.entry(cand.id.clone()).or_insert(0.0) +=
                                strength * 0.72 * cfg.hop1_decay;
                            min_hops
                                .entry(cand.id.clone())
                                .and_modify(|h| *h = (*h).min(1))
                                .or_insert(1);
                            seed_links
                                .entry(cand.id.clone())
                                .or_default()
                                .insert(seed_id.clone());
                            relation_kinds
                                .entry(cand.id.clone())
                                .or_default()
                                .insert("WIKILINK".to_string());
                        }
                    }
                }
            }
            for tg in &cand_tgs {
                if let Some(origins) = seed_tags.get(tg) {
                    for (seed_id, strength) in origins {
                        if seed_id != &cand.id {
                            *raw_scores.entry(cand.id.clone()).or_insert(0.0) +=
                                strength * 0.55 * cfg.hop2_decay;
                            min_hops
                                .entry(cand.id.clone())
                                .and_modify(|h| *h = (*h).min(2))
                                .or_insert(2);
                            seed_links
                                .entry(cand.id.clone())
                                .or_default()
                                .insert(seed_id.clone());
                            relation_kinds
                                .entry(cand.id.clone())
                                .or_default()
                                .insert(format!("TAG:#{tg}"));
                        }
                    }
                }
            }
        }
    }

    let mut hits: Vec<GraphStructuralHit> = Vec::new();
    for (node_id, base_score) in raw_scores {
        if base_score <= 0.0 {
            continue;
        }
        let Some(node) = node_map.get(&node_id).cloned() else {
            continue;
        };
        let connected: Vec<String> = {
            let mut v: Vec<String> = seed_links
                .remove(&node_id)
                .unwrap_or_default()
                .into_iter()
                .collect();
            v.sort();
            v
        };
        let relations: Vec<String> = {
            let mut v: Vec<String> = relation_kinds
                .remove(&node_id)
                .unwrap_or_default()
                .into_iter()
                .collect();
            v.sort();
            v
        };
        let degree = *degree_map.get(&node_id).unwrap_or(&1);

        // Multi-seed convergence bonus + log-degree centrality boost
        let convergence_bonus = if connected.len() >= 2 {
            1.0 + 0.25 * (connected.len() as f64).ln()
        } else {
            1.0
        };
        let centrality_boost = 1.0 + 0.08 * (1.0 + degree as f64).ln();
        let final_graph_score = base_score * convergence_bonus * centrality_boost;

        hits.push(GraphStructuralHit {
            node,
            graph_score: final_graph_score,
            min_hop: *min_hops.get(&node_id).unwrap_or(&1),
            connected_seeds: connected,
            relations,
            degree,
        });
    }

    hits.sort_by(|a, b| {
        b.graph_score
            .total_cmp(&a.graph_score)
            .then_with(|| a.min_hop.cmp(&b.min_hop))
            .then_with(|| b.node.updated_at.cmp(&a.node.updated_at))
            .then_with(|| a.node.id.cmp(&b.node.id))
    });
    hits.truncate(cfg.max_neighbors.max(1));
    Ok(hits)
}
