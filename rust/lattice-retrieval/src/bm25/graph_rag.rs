//! Knowledge Graph 1-hop & 2-hop structural expansion (GraphRAG).
//!
//! Uses [`super::sidecar::Bm25SidecarIndex`] for O(1) / O(deg) edge adjacency,
//! wikilink, hashtag, and title lookups without re-scanning node text.

use std::collections::{HashMap, HashSet};

use lattice_core::{CoreError, NodeRow};
use rusqlite::{params_from_iter, types::Value as SqlValue, Connection};

use crate::service::Scope;

use super::reasoning_paths::{
    is_conflict_or_supersede_relation, record_best_chain, resolve_reasoning_path, CandidateChain,
    ReasoningPath,
};
use super::scoring::{
    build_node_scope_sql, detect_nodes_v2_schema, extract_meta_from_json, row_to_node,
    KnowledgeLifecycleStatus,
};
use super::sidecar::{detect_edge_columns, Bm25SidecarIndex};

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
    /// Include `superseded`/`deprecated` neighbors (down-ranked when true; excluded when false).
    pub include_historical: bool,
}

impl Default for GraphRagConfig {
    fn default() -> Self {
        Self {
            max_hops: 2,
            max_seeds: 12,
            max_neighbors: 40,
            hop1_decay: 0.68,
            hop2_decay: 0.38,
            include_historical: false,
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
    pub bridge_nodes: Vec<String>,
    pub best_path: Option<ReasoningPath>,
    pub conflicts: Vec<(String, String, String)>,
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

fn edges_for_frontier(
    conn: &Connection,
    sidecar: &Bm25SidecarIndex,
    frontier_ids: &[String],
) -> Result<Vec<(String, String, String, f64)>, CoreError> {
    if frontier_ids.is_empty() {
        return Ok(Vec::new());
    }
    if sidecar.has_cached_edges() {
        let mut seen: HashSet<usize> = HashSet::new();
        let mut out = Vec::new();
        for fid in frontier_ids {
            if let Some(idxs) = sidecar.cached_edge_indices_for(fid) {
                for &idx in idxs {
                    if seen.insert(idx) {
                        if let Some(edge) = sidecar.cached_edge(idx) {
                            out.push((
                                edge.src.clone(),
                                edge.dst.clone(),
                                edge.rel.clone(),
                                edge.weight,
                            ));
                        }
                    }
                }
            }
        }
        return Ok(out);
    }

    let Some(layout) = sidecar.edge_layout().or_else(|| detect_edge_columns(conn)) else {
        return Ok(Vec::new());
    };
    let placeholders = (1..=frontier_ids.len())
        .map(|i| format!("?{i}"))
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "SELECT {}, {}, {}, weight FROM edges \
         WHERE {} IN ({placeholders}) OR {} IN ({placeholders})",
        layout.src_col, layout.dst_col, layout.rel_col, layout.src_col, layout.dst_col
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
    let mut out = Vec::new();
    for item in rows {
        out.push(item?);
    }
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
fn record_implicit_link(
    cand_id: &str,
    seed_id: &str,
    score_delta: f64,
    hop: usize,
    relation: String,
    raw_scores: &mut HashMap<String, f64>,
    min_hops: &mut HashMap<String, usize>,
    seed_links: &mut HashMap<String, HashSet<String>>,
    relation_kinds: &mut HashMap<String, HashSet<String>>,
    best_chains: &mut HashMap<String, CandidateChain>,
) {
    if seed_id == cand_id {
        return;
    }
    *raw_scores.entry(cand_id.to_string()).or_insert(0.0) += score_delta;
    min_hops
        .entry(cand_id.to_string())
        .and_modify(|h| *h = (*h).min(hop))
        .or_insert(hop);
    seed_links
        .entry(cand_id.to_string())
        .or_default()
        .insert(seed_id.to_string());
    relation_kinds
        .entry(cand_id.to_string())
        .or_default()
        .insert(relation.clone());
    record_best_chain(
        best_chains,
        CandidateChain {
            seed_id: seed_id.to_string(),
            bridge_id: None,
            target_id: cand_id.to_string(),
            hops: hop,
            relations: vec![relation],
            score: score_delta,
        },
    );
}

/// Expand seed nodes using the pre-tokenized [`Bm25SidecarIndex`] and cached graph adjacency.
/// Expand seed nodes across 1-hop and 2-hop Knowledge Graph edges using a pre-synced
/// [`Bm25SidecarIndex`] mutable cache.
pub fn expand_knowledge_graph_with_sidecar(
    conn: &Connection,
    seeds: &[(String, f64)],
    sidecar: &mut Bm25SidecarIndex,
    scope: &Scope,
    cfg: &GraphRagConfig,
) -> Result<Vec<GraphStructuralHit>, CoreError> {
    if seeds.is_empty() {
        return Ok(Vec::new());
    }
    sidecar.sync_edges_from_conn(conn)?;
    expand_knowledge_graph_with_snapshot(conn, seeds, sidecar, scope, cfg)
}

/// Expand seed nodes across 1-hop and 2-hop Knowledge Graph edges using an immutable
/// [`Bm25SidecarIndex`] snapshot (safe for concurrent readers without any write locks).
pub fn expand_knowledge_graph_with_snapshot(
    conn: &Connection,
    seeds: &[(String, f64)],
    sidecar: &Bm25SidecarIndex,
    scope: &Scope,
    cfg: &GraphRagConfig,
) -> Result<Vec<GraphStructuralHit>, CoreError> {
    if seeds.is_empty() {
        return Ok(Vec::new());
    }
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

    let mut raw_scores: HashMap<String, f64> = HashMap::new();
    let mut min_hops: HashMap<String, usize> = HashMap::new();
    let mut seed_links: HashMap<String, HashSet<String>> = HashMap::new();
    let mut relation_kinds: HashMap<String, HashSet<String>> = HashMap::new();
    let mut bridge_links: HashMap<String, HashSet<String>> = HashMap::new();
    let mut best_chains: HashMap<String, CandidateChain> = HashMap::new();
    let mut conflict_edges: Vec<(String, String, String)> = Vec::new();
    let mut degree_map: HashMap<String, usize> = HashMap::new();

    let mut frontier_ids: Vec<String> = active_seeds.iter().map(|(id, _)| id.clone()).collect();
    let mut visited_frontier: HashSet<String> = frontier_ids.iter().cloned().collect();

    let seed_strength: HashMap<String, f64> = active_seeds
        .iter()
        .map(|(id, s)| (id.clone(), (*s / max_seed_score).clamp(0.15, 1.0)))
        .collect();

    let mut hop1_paths: HashMap<String, Vec<(String, f64, String)>> = HashMap::new();

    for hop in 1..=cfg.max_hops.clamp(1, 2) {
        if frontier_ids.is_empty() {
            break;
        }
        let edges = edges_for_frontier(conn, sidecar, &frontier_ids)?;
        let mut next_frontier: Vec<String> = Vec::new();
        for (src, dst, rel, weight) in edges {
            *degree_map.entry(src.clone()).or_insert(0) += 1;
            *degree_map.entry(dst.clone()).or_insert(0) += 1;
            if is_conflict_or_supersede_relation(&rel) {
                conflict_edges.push((src.clone(), dst.clone(), rel.clone()));
            }

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
                        record_best_chain(
                            &mut best_chains,
                            CandidateChain {
                                seed_id: from.clone(),
                                bridge_id: None,
                                target_id: to.clone(),
                                hops: 1,
                                relations: vec![rel.clone()],
                                score: contrib,
                            },
                        );

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
                for (from, to) in [(&src, &dst), (&dst, &src)] {
                    if let Some(paths) = hop1_paths.get(from) {
                        let bridge_mult = sidecar
                            .get_doc(from)
                            .and_then(|d| d.node.node_type.as_deref())
                            .map(|nt| match nt {
                                "Concept" | "Entity" | "Topic" | "Decision" => 1.25,
                                _ => 1.0,
                            })
                            .unwrap_or(1.0);
                        for (origin_seed, p_score, rel1) in paths {
                            if origin_seed == to {
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
                                .insert(origin_seed.clone());
                            bridge_links
                                .entry(to.clone())
                                .or_default()
                                .insert(from.clone());
                            relation_kinds
                                .entry(to.clone())
                                .or_default()
                                .insert(format!("{rel1}->{rel}"));
                            record_best_chain(
                                &mut best_chains,
                                CandidateChain {
                                    seed_id: origin_seed.clone(),
                                    bridge_id: Some(from.clone()),
                                    target_id: to.clone(),
                                    hops: 2,
                                    relations: vec![rel1.clone(), rel.clone()],
                                    score: contrib,
                                },
                            );
                        }
                    }
                }
            }
        }
        next_frontier.truncate(cfg.max_neighbors * 2);
        frontier_ids = next_frontier;
    }

    // Fetch only node rows referenced by edges that are not already in the sidecar cache,
    // applying SQL workspace and lifecycle filters before loading.
    let mut fetched_nodes: HashMap<String, (NodeRow, KnowledgeLifecycleStatus)> = HashMap::new();
    let missing_ids: Vec<String> = raw_scores
        .keys()
        .filter(|id| sidecar.get_doc(id).is_none())
        .cloned()
        .collect();
    if !missing_ids.is_empty() {
        let schema = detect_nodes_v2_schema(conn)?;
        let scope_parts = build_node_scope_sql(schema, "n", scope, cfg.include_historical);
        let scope_cond = scope_parts
            .where_clause
            .strip_prefix("WHERE ")
            .unwrap_or("")
            .to_string();
        let placeholders = std::iter::repeat_n("?", missing_ids.len())
            .collect::<Vec<_>>()
            .join(", ");
        let extra_scope = if scope_cond.is_empty() {
            String::new()
        } else {
            format!(" AND ({scope_cond})")
        };
        let sql = format!(
            "SELECT n.id, n.type, n.title, n.summary, n.metadata_json, n.updated_at, {}, {}
             FROM nodes n
             {}
             WHERE n.id IN ({placeholders}){extra_scope}",
            scope_parts.lifecycle_select, scope_parts.superseded_by_select, scope_parts.join_clause
        );
        let mut params: Vec<SqlValue> = missing_ids.iter().cloned().map(SqlValue::Text).collect();
        params.extend(scope_parts.params);
        let mut stmt = conn.prepare(&sql)?;
        let iter = stmt.query_map(params_from_iter(params), |row| {
            let node = row_to_node(row)?;
            let lifecycle_raw: Option<String> = row.get("resolved_lifecycle")?;
            let superseded_raw: Option<String> = row.get("resolved_superseded_by")?;
            let lifecycle = KnowledgeLifecycleStatus::from_raw(
                lifecycle_raw.as_deref(),
                superseded_raw.as_deref(),
            );
            Ok((node, lifecycle))
        })?;
        for item in iter {
            let (node, lifecycle) = item?;
            fetched_nodes.insert(node.id.clone(), (node, lifecycle));
        }
    }

    // Link nodes via cached implicit [[wikilinks]] and #tags (no text re-scanning!)
    let mut seed_wikilinks: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    let mut seed_tags: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    for (seed_id, &strength) in &seed_strength {
        if let Some(seed_doc) = sidecar.get_doc(seed_id) {
            if !seed_doc.title_norm.is_empty() {
                seed_wikilinks
                    .entry(seed_doc.title_norm.clone())
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
            for wl in &seed_doc.wikilinks {
                seed_wikilinks
                    .entry(wl.clone())
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
            for tg in &seed_doc.tags {
                seed_tags
                    .entry(tg.clone())
                    .or_default()
                    .push((seed_id.clone(), strength));
            }
        }
    }

    for (wl, origins) in &seed_wikilinks {
        for &doc_idx in sidecar.doc_idxs_by_title(wl) {
            if let Some(cand) = sidecar.doc_at(doc_idx) {
                if !cfg.include_historical && cand.lifecycle_status.is_retired() {
                    continue;
                }
                for (seed_id, strength) in origins {
                    record_implicit_link(
                        &cand.node.id,
                        seed_id,
                        strength * 0.80 * cfg.hop1_decay,
                        1,
                        "WIKILINK".to_string(),
                        &mut raw_scores,
                        &mut min_hops,
                        &mut seed_links,
                        &mut relation_kinds,
                        &mut best_chains,
                    );
                }
            }
        }
        for &doc_idx in sidecar.doc_idxs_by_wikilink(wl) {
            if let Some(cand) = sidecar.doc_at(doc_idx) {
                if !cfg.include_historical && cand.lifecycle_status.is_retired() {
                    continue;
                }
                for (seed_id, strength) in origins {
                    record_implicit_link(
                        &cand.node.id,
                        seed_id,
                        strength * 0.72 * cfg.hop1_decay,
                        1,
                        "WIKILINK".to_string(),
                        &mut raw_scores,
                        &mut min_hops,
                        &mut seed_links,
                        &mut relation_kinds,
                        &mut best_chains,
                    );
                }
            }
        }
    }

    for (tg, origins) in &seed_tags {
        for &doc_idx in sidecar.doc_idxs_by_tag(tg) {
            if let Some(cand) = sidecar.doc_at(doc_idx) {
                if !cfg.include_historical && cand.lifecycle_status.is_retired() {
                    continue;
                }
                for (seed_id, strength) in origins {
                    record_implicit_link(
                        &cand.node.id,
                        seed_id,
                        strength * 0.55 * cfg.hop2_decay,
                        2,
                        format!("TAG:#{tg}"),
                        &mut raw_scores,
                        &mut min_hops,
                        &mut seed_links,
                        &mut relation_kinds,
                        &mut best_chains,
                    );
                }
            }
        }
    }

    let extra_node_rows: HashMap<String, NodeRow> = fetched_nodes
        .iter()
        .map(|(k, (n, _))| (k.clone(), n.clone()))
        .collect();

    let mut hits: Vec<GraphStructuralHit> = Vec::new();
    for (node_id, base_score) in raw_scores {
        if base_score <= 0.0 {
            continue;
        }
        let (node, lifecycle_status) = if let Some(doc) = sidecar.get_doc(&node_id) {
            (doc.node.clone(), doc.lifecycle_status)
        } else if let Some((node, lifecycle)) = fetched_nodes.get(&node_id) {
            (node.clone(), *lifecycle)
        } else {
            continue;
        };
        let resolved_lifecycle = if lifecycle_status == KnowledgeLifecycleStatus::Active {
            extract_meta_from_json(node.metadata_json.as_deref()).0
        } else {
            lifecycle_status
        };
        if !cfg.include_historical && resolved_lifecycle.is_retired() {
            continue;
        }
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
        let bridge_nodes: Vec<String> = {
            let mut v: Vec<String> = bridge_links
                .remove(&node_id)
                .unwrap_or_default()
                .into_iter()
                .collect();
            v.sort();
            v
        };
        let degree = *degree_map.get(&node_id).unwrap_or(&1);
        let convergence_bonus = if connected.len() >= 2 {
            1.0 + 0.25 * (connected.len() as f64).ln()
        } else {
            1.0
        };
        let centrality_boost = 1.0 + 0.08 * (1.0 + degree as f64).ln();
        let final_graph_score =
            base_score * convergence_bonus * centrality_boost * resolved_lifecycle.multiplier();
        let best_path = best_chains.get(&node_id).map(|chain| {
            resolve_reasoning_path(chain, final_graph_score, sidecar, &extra_node_rows)
        });
        let node_conflicts: Vec<(String, String, String)> = conflict_edges
            .iter()
            .filter(|(s, d, _)| s == &node_id || d == &node_id)
            .cloned()
            .collect();

        hits.push(GraphStructuralHit {
            node,
            graph_score: final_graph_score,
            min_hop: *min_hops.get(&node_id).unwrap_or(&1),
            connected_seeds: connected,
            relations,
            degree,
            bridge_nodes,
            best_path,
            conflicts: node_conflicts,
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
    let mut sidecar = Bm25SidecarIndex::from_corpus(corpus);
    expand_knowledge_graph_with_sidecar(conn, seeds, &mut sidecar, scope, cfg)
}
