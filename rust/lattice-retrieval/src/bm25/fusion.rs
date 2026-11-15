//! 3-Way Hybrid Retrieval combining BM25F + Dense/Subword Vector + Knowledge Graph RRF.

use std::collections::{HashMap, HashSet};

use lattice_auth::OrderedMap;
use lattice_core::{
    clean_text, round6, safe_loads, CoreError, FallbackTextEmbedder, LocalEmbeddingModel, NodeRow,
};
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::policy::{classify_query, rewrite_query};
use crate::service::{vector_search as scoped_vector_search, Scope};
use crate::shape::json_opt;

use super::graph_rag::{expand_knowledge_graph_with_sidecar, GraphRagConfig, GraphStructuralHit};
use super::scoring::{Bm25fParams, Bm25fScoredNode};
use super::sidecar_cache::with_bm25_sidecar;

/// Options for 3-Way Hybrid BM25F + Vector + Knowledge Graph RRF search.
#[derive(Debug, Clone)]
pub struct GraphRagOptions {
    pub limit: i64,
    pub bm25_limit: i64,
    pub vector_limit: i64,
    pub graph_limit: i64,
    pub rrf_k: usize,
    pub bm25_weight: f64,
    pub vector_weight: f64,
    pub graph_weight: f64,
    pub bm25_params: Bm25fParams,
    pub graph_config: GraphRagConfig,
    pub scope: Scope,
    pub now_secs: f64,
}

impl Default for GraphRagOptions {
    fn default() -> Self {
        Self {
            limit: 20,
            bm25_limit: 40,
            vector_limit: 40,
            graph_limit: 40,
            rrf_k: 60,
            bm25_weight: 0.42,
            vector_weight: 0.33,
            graph_weight: 0.25,
            bm25_params: Bm25fParams::default(),
            graph_config: GraphRagConfig::default(),
            scope: Scope::default(),
            now_secs: 0.0,
        }
    }
}

/// Execute genuine 3-way **BM25F + Vector + Knowledge Graph Hybrid Retrieval (GraphRAG)**
/// with Reciprocal Rank Fusion (RRF), CJK bigram/stem BM25F scoring, subword fallback
/// vector similarity, and 1-hop/2-hop Knowledge Graph structural expansion.
pub fn hybrid_bm25_vector_graph_search(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    options: &GraphRagOptions,
) -> Result<Value, CoreError> {
    let cleaned = clean_text(query);
    let (rewritten, _expansions) = rewrite_query(&cleaned);
    let effective_query = if rewritten.is_empty() {
        cleaned.clone()
    } else {
        rewritten
    };
    let query_class = classify_query(&cleaned);

    if effective_query.is_empty() {
        let mut empty = OrderedMap::new();
        empty.insert("query", json!(query));
        empty.insert("rewritten_query", json!(effective_query));
        empty.insert("query_class", json!(query_class));
        empty.insert("mode", json!("bm25_vector_graph_rrf"));
        empty.insert("matches", json!([]));
        empty.insert("results", json!([]));
        empty.insert("expanded_results", json!([]));
        return Ok(serde_json::to_value(empty).unwrap_or(Value::Null));
    }

    let (corpus, mut bm25_hits) = with_bm25_sidecar(conn, &options.scope, 5_000, |sidecar| {
        let hits = sidecar.score(&effective_query, &options.bm25_params);
        Ok((sidecar.corpus_nodes(), hits))
    })?;
    let corpus_map: HashMap<String, NodeRow> =
        corpus.iter().map(|n| (n.id.clone(), n.clone())).collect();

    // 1. Channel 1: Calibrated Field-Weighted Okapi BM25F (via pre-tokenized sidecar)
    bm25_hits.truncate(options.bm25_limit.max(1) as usize);
    let max_bm25 = bm25_hits
        .first()
        .map(|h| h.bm25_score)
        .unwrap_or(1.0)
        .max(1e-6);

    // 2. Channel 2: Dense Vector Search + Upgraded Offline Subword FallbackTextEmbedder
    let mut vector_ranked: Vec<(String, f64, NodeRow)> = Vec::new();
    if let Ok(vec_report) = scoped_vector_search(
        conn,
        model,
        &effective_query,
        options.vector_limit.max(1),
        0.05,
        &options.scope,
    ) {
        if let Some(arr) = vec_report
            .get("matches")
            .or_else(|| vec_report.get("results"))
            .and_then(Value::as_array)
        {
            for item in arr {
                if let Some(id) = item
                    .get("node_id")
                    .or_else(|| item.get("id"))
                    .and_then(Value::as_str)
                {
                    let sim = item
                        .get("similarity")
                        .or_else(|| item.get("score"))
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0);
                    if sim > 0.0 {
                        if let Some(node) = corpus_map.get(id).cloned() {
                            vector_ranked.push((id.to_string(), sim, node));
                        }
                    }
                }
            }
        }
    }

    // Augment or fall back to upgraded `FallbackTextEmbedder` (multi-scale 3..5-grams + CJK bigrams)
    let subword_embedder = FallbackTextEmbedder::default();
    let q_sub_vec = subword_embedder.embed(&effective_query);
    if q_sub_vec.iter().any(|v| *v != 0.0) {
        let mut sub_scores: HashMap<String, f64> = HashMap::new();
        for node in &corpus {
            let doc_text = format!(
                "{} {}",
                node.title.as_deref().unwrap_or_default(),
                node.summary.as_deref().unwrap_or_default()
            );
            let d_vec = subword_embedder.embed(&doc_text);
            let sim = subword_embedder
                .similarity(&q_sub_vec, &d_vec)
                .unwrap_or(0.0);
            if sim > 0.12 {
                sub_scores.insert(node.id.clone(), sim);
            }
        }
        for (id, sim, _) in &mut vector_ranked {
            if let Some(&sub_sim) = sub_scores.get(id) {
                *sim = (*sim * 0.70 + sub_sim * 0.30).max(*sim);
            }
        }
        let existing_vec_ids: HashSet<String> =
            vector_ranked.iter().map(|(id, _, _)| id.clone()).collect();
        let mut extra_sub: Vec<(String, f64, NodeRow)> = sub_scores
            .into_iter()
            .filter(|(id, _)| !existing_vec_ids.contains(id))
            .filter_map(|(id, sim)| corpus_map.get(&id).cloned().map(|n| (id, sim, n)))
            .collect();
        extra_sub.sort_by(|a, b| b.1.total_cmp(&a.1));
        for item in extra_sub
            .into_iter()
            .take(options.vector_limit.max(1) as usize)
        {
            if vector_ranked.len() < options.vector_limit.max(1) as usize {
                vector_ranked.push(item);
            }
        }
        vector_ranked.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    }
    let max_vec = vector_ranked
        .first()
        .map(|(_, s, _)| *s)
        .unwrap_or(1.0)
        .max(1e-6);

    // 3. Channel 3: Knowledge Graph 1-Hop & 2-Hop Structural Expansion
    let mut seed_map: HashMap<String, f64> = HashMap::new();
    for (rank, hit) in bm25_hits
        .iter()
        .take(options.graph_config.max_seeds)
        .enumerate()
    {
        let norm = (hit.bm25_score / max_bm25) * (1.0 / (1.0 + 0.12 * rank as f64));
        seed_map.insert(hit.node.id.clone(), norm);
    }
    for (rank, (id, sim, _)) in vector_ranked
        .iter()
        .take(options.graph_config.max_seeds)
        .enumerate()
    {
        let norm = (*sim / max_vec) * (1.0 / (1.0 + 0.15 * rank as f64));
        seed_map
            .entry(id.clone())
            .and_modify(|v| *v = (*v + norm * 0.85).min(1.5))
            .or_insert(norm * 0.85);
    }
    let mut seed_list: Vec<(String, f64)> = seed_map.into_iter().collect();
    seed_list.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));

    let mut graph_hits = with_bm25_sidecar(conn, &options.scope, 5_000, |sidecar| {
        expand_knowledge_graph_with_sidecar(
            conn,
            &seed_list,
            sidecar,
            &options.scope,
            &options.graph_config,
        )
    })?;
    graph_hits.truncate(options.graph_limit.max(1) as usize);
    let max_graph = graph_hits
        .first()
        .map(|h| h.graph_score)
        .unwrap_or(1.0)
        .max(1e-6);

    // 4. 3-Way Reciprocal Rank Fusion (RRF) + Calibrated Normalized Score Blend
    let k = options.rrf_k.max(1) as f64;
    let w_sum = (options.bm25_weight + options.vector_weight + options.graph_weight).max(1e-6);
    let w_bm25 = options.bm25_weight / w_sum;
    let w_vec = options.vector_weight / w_sum;
    let w_graph = options.graph_weight / w_sum;

    struct CandidateFusion {
        node: NodeRow,
        bm25_rank: Option<usize>,
        bm25_raw: f64,
        bm25_norm: f64,
        matched_terms: Vec<String>,
        matched_fields: Vec<String>,
        vec_rank: Option<usize>,
        vec_raw: f64,
        vec_norm: f64,
        graph_rank: Option<usize>,
        graph_raw: f64,
        graph_norm: f64,
        graph_hop: Option<usize>,
        graph_relations: Vec<String>,
        connected_seeds: Vec<String>,
    }

    let mut fused_map: HashMap<String, CandidateFusion> = HashMap::new();

    for (idx, hit) in bm25_hits.into_iter().enumerate() {
        let id = hit.node.id.clone();
        let norm = (hit.bm25_score / max_bm25).clamp(0.0, 1.0);
        fused_map.insert(
            id,
            CandidateFusion {
                node: hit.node,
                bm25_rank: Some(idx + 1),
                bm25_raw: hit.bm25_score,
                bm25_norm: norm,
                matched_terms: hit.matched_terms,
                matched_fields: hit.matched_fields,
                vec_rank: None,
                vec_raw: 0.0,
                vec_norm: 0.0,
                graph_rank: None,
                graph_raw: 0.0,
                graph_norm: 0.0,
                graph_hop: None,
                graph_relations: Vec::new(),
                connected_seeds: Vec::new(),
            },
        );
    }

    for (idx, (id, sim, node)) in vector_ranked.into_iter().enumerate() {
        let norm = (sim / max_vec).clamp(0.0, 1.0);
        let entry = fused_map.entry(id).or_insert_with(|| CandidateFusion {
            node,
            bm25_rank: None,
            bm25_raw: 0.0,
            bm25_norm: 0.0,
            matched_terms: Vec::new(),
            matched_fields: Vec::new(),
            vec_rank: None,
            vec_raw: 0.0,
            vec_norm: 0.0,
            graph_rank: None,
            graph_raw: 0.0,
            graph_norm: 0.0,
            graph_hop: None,
            graph_relations: Vec::new(),
            connected_seeds: Vec::new(),
        });
        entry.vec_rank = Some(idx + 1);
        entry.vec_raw = sim;
        entry.vec_norm = norm;
    }

    for (idx, ghit) in graph_hits.into_iter().enumerate() {
        let id = ghit.node.id.clone();
        let norm = (ghit.graph_score / max_graph).clamp(0.0, 1.0);
        let entry = fused_map.entry(id).or_insert_with(|| CandidateFusion {
            node: ghit.node,
            bm25_rank: None,
            bm25_raw: 0.0,
            bm25_norm: 0.0,
            matched_terms: Vec::new(),
            matched_fields: Vec::new(),
            vec_rank: None,
            vec_raw: 0.0,
            vec_norm: 0.0,
            graph_rank: None,
            graph_raw: 0.0,
            graph_norm: 0.0,
            graph_hop: None,
            graph_relations: Vec::new(),
            connected_seeds: Vec::new(),
        });
        entry.graph_rank = Some(idx + 1);
        entry.graph_raw = ghit.graph_score;
        entry.graph_norm = norm;
        entry.graph_hop = Some(ghit.min_hop);
        entry.graph_relations = ghit.relations;
        entry.connected_seeds = ghit.connected_seeds;
    }

    let mut ranked_items: Vec<(f64, f64, String, Value, bool)> = Vec::new();
    for (id, c) in fused_map {
        let rrf_bm25 = c
            .bm25_rank
            .map(|r| w_bm25 * (k + 1.0) / (k + r as f64))
            .unwrap_or(0.0);
        let rrf_vec = c
            .vec_rank
            .map(|r| w_vec * (k + 1.0) / (k + r as f64))
            .unwrap_or(0.0);
        let rrf_graph = c
            .graph_rank
            .map(|r| w_graph * (k + 1.0) / (k + r as f64))
            .unwrap_or(0.0);
        let rrf_score = rrf_bm25 + rrf_vec + rrf_graph;

        let linear_norm = w_bm25 * c.bm25_norm + w_vec * c.vec_norm + w_graph * c.graph_norm;
        let mut sources = Vec::new();
        if c.bm25_rank.is_some() {
            sources.push("bm25f");
        }
        if c.vec_rank.is_some() {
            sources.push("vector");
        }
        if c.graph_rank.is_some() {
            sources.push("graph");
        }
        let agreement_bonus = match sources.len() {
            3 => 1.18,
            2 => 1.10,
            _ => 1.0,
        };
        let hybrid_score = round6((0.55 * rrf_score + 0.45 * linear_norm) * agreement_bonus);

        let is_graph_only = c.bm25_rank.is_none() && c.vec_rank.is_none() && c.graph_rank.is_some();
        let meta = safe_loads(c.node.metadata_json.as_deref());
        let workspace_id = meta
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or("personal");

        let mut item = OrderedMap::new();
        item.insert("id", json!(c.node.id));
        item.insert("node_id", json!(c.node.id));
        item.insert("type", json_opt(&c.node.node_type));
        item.insert("title", json_opt(&c.node.title));
        item.insert("summary", json_opt(&c.node.summary));
        item.insert("metadata", Value::Object(meta.clone()));
        item.insert("updated_at", json_opt(&c.node.updated_at));
        item.insert("workspace_id", json!(workspace_id));
        item.insert("score", json!(hybrid_score));
        item.insert("hybrid_score", json!(hybrid_score));
        item.insert("rrf_score", json!(round6(rrf_score)));
        item.insert("bm25_score", json!(round6(c.bm25_raw)));
        item.insert("bm25_norm", json!(round6(c.bm25_norm)));
        item.insert("vector_score", json!(round6(c.vec_raw)));
        item.insert("graph_score", json!(round6(c.graph_raw)));
        item.insert("matched_terms", json!(c.matched_terms));
        item.insert("matched_fields", json!(c.matched_fields));
        item.insert("graph_relations", json!(c.graph_relations));
        item.insert("connected_seeds", json!(c.connected_seeds));
        item.insert("graph_hop", json!(c.graph_hop));
        item.insert("sources", json!(sources));

        ranked_items.push((
            hybrid_score,
            rrf_score,
            id,
            serde_json::to_value(item).unwrap_or(Value::Null),
            is_graph_only,
        ));
    }

    ranked_items.sort_by(|a, b| {
        b.0.total_cmp(&a.0)
            .then_with(|| b.1.total_cmp(&a.1))
            .then_with(|| a.2.cmp(&b.2))
    });

    let take = options.limit.max(1) as usize;
    let mut results = Vec::new();
    let mut expanded_results = Vec::new();
    for (_, _, _, val, is_graph_only) in ranked_items {
        if is_graph_only {
            expanded_results.push(val.clone());
        }
        if results.len() < take {
            results.push(val);
        }
    }

    let mut out = OrderedMap::new();
    out.insert("query", json!(query));
    out.insert("rewritten_query", json!(effective_query));
    out.insert("query_class", json!(query_class));
    out.insert("mode", json!("bm25_vector_graph_rrf"));
    out.insert(
        "weights",
        json!({
            "bm25": round6(w_bm25),
            "vector": round6(w_vec),
            "graph": round6(w_graph),
        }),
    );
    out.insert(
        "fusion",
        json!({
            "strategy": "bm25_vector_graph_rrf",
            "rrf_k": options.rrf_k,
            "bm25_k1": options.bm25_params.k1,
            "bm25_b": options.bm25_params.b,
            "graph_max_hops": options.graph_config.max_hops,
        }),
    );
    out.insert("matches", Value::Array(results.clone()));
    out.insert("results", Value::Array(results));
    out.insert("expanded_results", Value::Array(expanded_results));
    Ok(serde_json::to_value(out).unwrap_or(Value::Null))
}

/// Run inverted-index BM25F scoring and sidecar-cached GraphRAG expansion in one sidecar borrow.
pub fn score_and_expand_with_sidecar(
    conn: &Connection,
    scope: &Scope,
    query: &str,
    take: usize,
    bm25_params: &Bm25fParams,
    graph_cfg: &GraphRagConfig,
) -> Result<(Vec<Bm25fScoredNode>, Vec<GraphStructuralHit>), CoreError> {
    with_bm25_sidecar(conn, scope, 5_000, |sidecar| {
        let mut hits = sidecar.score(query, bm25_params);
        hits.truncate(take.max(1));
        let max_bm25 = hits.first().map(|h| h.bm25_score).unwrap_or(1.0).max(1e-6);
        let seeds: Vec<(String, f64)> = hits
            .iter()
            .map(|hit| {
                (
                    hit.node.id.clone(),
                    (hit.bm25_score / max_bm25).clamp(0.0, 1.0),
                )
            })
            .collect();
        let ghits = expand_knowledge_graph_with_sidecar(conn, &seeds, sidecar, scope, graph_cfg)?;
        Ok((hits, ghits))
    })
}
