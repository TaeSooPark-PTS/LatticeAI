use std::collections::{BTreeMap, BTreeSet, HashMap};

use lattice_core::pytext::{parse_iso, recency_score, round6};
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::expansion::{
    expansion_enabled, one_hop, rrf_enabled, rrf_scores, EXPANSION_SCORE_FACTOR, EXPANSION_SEEDS,
};
use crate::policy::{class_weights, resolve_policy};
use crate::service::{
    graph_search, keyword_search_ranked, vector_search, GraphSearchOptions, Scope,
};
use crate::shape::{truthy, DEFAULT_EXPANSION_CAP};

pub const DEFAULT_HYBRID_WEIGHTS: [(&str, f64); 3] =
    [("keyword", 0.35), ("vector", 0.40), ("graph", 0.25)];

const CHANNELS: [&str; 3] = ["keyword", "vector", "graph"];

#[derive(Debug, Clone)]
pub struct ServiceHybridOptions {
    pub limit: i64,
    pub keyword_limit: i64,
    pub vector_limit: i64,
    pub graph_limit: i64,
    pub weights: Option<Map<String, Value>>,
    pub scope: Scope,
    pub now_secs: f64,
    pub query_embedding: Option<crate::bm25::QueryVectorOverride>,
    pub semantic_degraded: Option<String>,
    pub recency: Option<crate::temporal::RecencyDecay>,
    pub include_historical: bool,
    pub keyword_ranking: crate::keyword::KeywordRanking,
}

impl Default for ServiceHybridOptions {
    fn default() -> Self {
        Self {
            limit: 30,
            keyword_limit: 30,
            vector_limit: 30,
            graph_limit: 30,
            weights: None,
            scope: Scope::default(),
            now_secs: 0.0,
            query_embedding: None,
            semantic_degraded: None,
            recency: None,
            include_historical: false,
            keyword_ranking: crate::keyword::KeywordRanking::Legacy,
        }
    }
}

struct Fused {
    item: Map<String, Value>,
    score: f64,
    sources: Vec<String>,
    source_scores: Map<String, Value>,
    graph_context: Option<Vec<Value>>,
    ranks: Vec<usize>,
}

fn weights_value(weights: &Map<String, Value>, source: &str) -> f64 {
    weights.get(source).and_then(Value::as_f64).unwrap_or(0.0)
}

fn expand_matches(conn: &Connection, matches: &mut Vec<Value>) -> Result<Option<Value>, CoreError> {
    if !expansion_enabled() || matches.is_empty() {
        return Ok(None);
    }
    let node_id = |item: &Value| -> String {
        item.get("node_id")
            .or_else(|| item.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    let seeds: Vec<String> = matches
        .iter()
        .take(EXPANSION_SEEDS)
        .map(node_id)
        .filter(|id| !id.is_empty())
        .collect();
    let seed_scores: Vec<f64> = matches
        .iter()
        .take(EXPANSION_SEEDS)
        .map(|item| item.get("score").and_then(Value::as_f64).unwrap_or(0.0))
        .collect();
    let present: BTreeSet<String> = matches.iter().map(node_id).collect();
    let (neighbours, report) = one_hop(
        conn,
        &seeds,
        &present,
        DEFAULT_EXPANSION_CAP.max(0) as usize,
    )?;
    for neighbour in neighbours {
        let seed_score = seeds
            .iter()
            .position(|id| *id == neighbour.seed_id)
            .and_then(|index| seed_scores.get(index).copied())
            .unwrap_or(0.0);
        let mut item = Map::new();
        item.insert("id".into(), Value::String(neighbour.node_id.clone()));
        item.insert("node_id".into(), Value::String(neighbour.node_id.clone()));
        item.insert("type".into(), neighbour.node_type.clone());
        item.insert("title".into(), neighbour.title.clone());
        item.insert("summary".into(), neighbour.summary.clone());
        item.insert("metadata".into(), neighbour.metadata.clone());
        item.insert("updated_at".into(), neighbour.updated_at.clone());
        item.insert(
            "sources".into(),
            Value::Array(vec![Value::String("graph_expansion".into())]),
        );
        item.insert("source_scores".into(), Value::Object(Map::new()));
        item.insert(
            "score".into(),
            Value::from(round6(seed_score * EXPANSION_SCORE_FACTOR)),
        );
        item.insert("via".into(), neighbour.provenance());
        matches.push(Value::Object(item));
    }
    Ok(Some(report.as_json(DEFAULT_EXPANSION_CAP)))
}

fn node_row_to_item(node: &lattice_core::read::NodeRow) -> Map<String, Value> {
    let mut item = Map::new();
    item.insert("id".into(), Value::String(node.id.clone()));
    item.insert("node_id".into(), Value::String(node.id.clone()));
    item.insert("item_type".into(), Value::String("node".into()));
    item.insert(
        "type".into(),
        node.node_type
            .clone()
            .map(Value::String)
            .unwrap_or(Value::Null),
    );
    item.insert(
        "title".into(),
        node.title.clone().map(Value::String).unwrap_or(Value::Null),
    );
    item.insert(
        "summary".into(),
        node.summary
            .clone()
            .map(Value::String)
            .unwrap_or(Value::Null),
    );
    item.insert(
        "metadata".into(),
        Value::Object(lattice_core::pytext::safe_loads(
            node.metadata_json.as_deref(),
        )),
    );
    item.insert(
        "updated_at".into(),
        node.updated_at
            .clone()
            .map(Value::String)
            .unwrap_or(Value::Null),
    );
    item
}

pub fn service_hybrid_search(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    options: &ServiceHybridOptions,
) -> Result<Value, CoreError> {
    let mut query_class: Option<String> = None;
    let mut search_query = query.to_string();
    let mut rewrite_rules: Vec<String> = Vec::new();
    let mut recency_half_life: Option<f64> = None;
    let weights = match options.weights.as_ref() {
        None => {
            let policy = resolve_policy(query);
            let mut resolved = Map::new();
            for (channel, weight) in class_weights(&policy.query_class) {
                resolved.insert(channel.to_string(), Value::from(weight));
            }
            if !policy.search_query.is_empty() && policy.search_query != policy.original_query {
                search_query = policy.search_query.clone();
            }
            rewrite_rules = policy.rewrite_rules.clone();
            recency_half_life = policy.recency_half_life_days;
            query_class = Some(policy.query_class);
            resolved
        }
        Some(pinned) => {
            let mut merged = Map::new();
            for (channel, weight) in DEFAULT_HYBRID_WEIGHTS {
                merged.insert(channel.to_string(), Value::from(weight));
            }
            for (key, value) in pinned {
                merged.insert(key.clone(), value.clone());
            }
            merged
        }
    };

    let scope = &options.scope;
    let vector_payload = if let Some(ovr) = options.query_embedding.as_ref() {
        crate::service::vector_search_aligned_service(
            conn,
            crate::vector::VectorQueryInput::Precomputed {
                model_id: &ovr.model_id,
                vector: &ovr.vector,
                is_hash_fallback: ovr.is_hash_fallback,
            },
            &search_query,
            options.vector_limit,
            0.0,
            scope,
            options.include_historical,
        )?
    } else if let Some(reason) = options.semantic_degraded.as_ref() {
        serde_json::json!({
            "query": search_query,
            "mode": "vector",
            "provider_status": "degraded",
            "degraded_reason": reason,
            "matches": [],
        })
    } else {
        vector_search(
            conn,
            model,
            &search_query,
            options.vector_limit,
            0.0,
            scope,
            options.include_historical,
        )?
    };
    let channels: Vec<(&str, Value)> = vec![
        (
            "keyword",
            keyword_search_ranked(
                conn,
                &search_query,
                options.keyword_limit,
                scope,
                options.keyword_ranking,
            )?,
        ),
        ("vector", vector_payload),
        (
            "graph",
            graph_search(
                conn,
                &search_query,
                &GraphSearchOptions {
                    limit: options.graph_limit,
                    expand_depth: 1,
                    scope: scope.clone(),
                    keyword_ranking: options.keyword_ranking,
                },
            )?,
        ),
    ];

    let mut order: Vec<Fused> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for source in CHANNELS {
        let payload = channels
            .iter()
            .find(|(name, _)| *name == source)
            .map(|(_, payload)| payload)
            .expect("every channel was pushed above");
        let source_weight = weights_value(&weights, source);
        let empty: Vec<Value> = Vec::new();
        for (rank, result) in payload["matches"]
            .as_array()
            .unwrap_or(&empty)
            .iter()
            .enumerate()
        {
            let key = result_key(result);
            if key.is_empty() {
                continue;
            }
            let source_score = result
                .get("source_scores")
                .and_then(|scores| scores.get(source))
                .and_then(Value::as_f64)
                .unwrap_or_else(|| result.get("score").and_then(Value::as_f64).unwrap_or(0.0));
            let rank_score = 1.0 / (rank as f64 + 1.0);
            let contribution = source_weight * source_score.max(rank_score);
            let position = match index.get(&key) {
                Some(position) => *position,
                None => {
                    order.push(Fused {
                        item: result.as_object().cloned().unwrap_or_default(),
                        score: 0.0,
                        sources: Vec::new(),
                        source_scores: Map::new(),
                        graph_context: result
                            .get("graph_context")
                            .and_then(Value::as_array)
                            .cloned(),
                        ranks: Vec::new(),
                    });
                    let position = order.len() - 1;
                    index.insert(key.clone(), position);
                    position
                }
            };
            let fused = &mut order[position];
            fused.score += contribution;
            fused.ranks.push(rank + 1);
            if !fused.sources.iter().any(|known| known == source) {
                fused.sources.push(source.to_string());
            }
            fused
                .source_scores
                .insert(source.to_string(), Value::from(round6(source_score)));
            if let Some(incoming) = result
                .get("graph_context")
                .filter(|value| truthy(value))
                .and_then(Value::as_array)
            {
                let existing = fused.graph_context.get_or_insert_with(Vec::new);
                existing.extend(incoming.iter().cloned());
            }
        }
    }

    let use_bm25_graphrag = crate::bm25::bm25f_graphrag_enabled();
    let rescue_empty = !use_bm25_graphrag && order.is_empty() && !search_query.trim().is_empty();
    if use_bm25_graphrag || rescue_empty {
        let bm25_params = crate::bm25::Bm25fParams::default();
        let graph_cfg = crate::bm25::GraphRagConfig::default();
        let clean_q = lattice_core::clean_text(&search_query);
        if !clean_q.is_empty() {
            let take = options.keyword_limit.max(1) as usize;
            if let Ok((bm25_hits, ghits)) = crate::bm25::score_and_expand_with_sidecar(
                conn,
                scope,
                &clean_q,
                take,
                &bm25_params,
                &graph_cfg,
            ) {
                let mut merge_channel =
                    |channel: &str, weight: f64, entries: Vec<(&lattice_core::NodeRow, f64)>| {
                        let max_raw = entries.first().map(|(_, s)| *s).unwrap_or(1.0).max(1e-6);
                        for (rank, (node, raw_score)) in entries.into_iter().enumerate() {
                            let key = node.id.clone();
                            let norm = (raw_score / max_raw).clamp(0.0, 1.0);
                            let pos = *index.entry(key).or_insert_with(|| {
                                order.push(Fused {
                                    item: node_row_to_item(node),
                                    score: weight * norm,
                                    sources: vec![channel.to_string()],
                                    source_scores: Map::new(),
                                    graph_context: None,
                                    ranks: Vec::new(),
                                });
                                order.len() - 1
                            });
                            let fused = &mut order[pos];
                            fused.ranks.push(rank + 1);
                            if !fused.sources.iter().any(|s| s == channel) {
                                fused.sources.push(channel.to_string());
                            }
                            fused
                                .source_scores
                                .insert(channel.to_string(), Value::from(round6(raw_score)));
                        }
                    };
                merge_channel(
                    "bm25f",
                    0.35,
                    bm25_hits.iter().map(|h| (&h.node, h.bm25_score)).collect(),
                );
                merge_channel(
                    "graph_rag",
                    0.25,
                    ghits.iter().map(|g| (&g.node, g.graph_score)).collect(),
                );
            }
        }
    }
    let use_rrf = rrf_enabled() || use_bm25_graphrag || (rescue_empty && !order.is_empty());
    if use_rrf {
        let ranks: BTreeMap<String, Vec<usize>> = order
            .iter()
            .enumerate()
            .map(|(position, fused)| (position.to_string(), fused.ranks.clone()))
            .collect();
        let scored = rrf_scores(&ranks);
        for (position, fused) in order.iter_mut().enumerate() {
            fused.score = scored
                .get(&position.to_string())
                .copied()
                .unwrap_or(fused.score);
        }
    }

    if let Some(decay) = options.recency.as_ref() {
        let recency_class = crate::policy::classify_query(query) == "recency";
        for fused in order.iter_mut() {
            let stamp = fused.item.get("updated_at").and_then(Value::as_str);
            let multiplier = decay.multiplier(stamp, options.now_secs, recency_class);
            fused
                .source_scores
                .insert("age_decay".into(), Value::from(multiplier));
            fused.score *= multiplier;
        }
    } else if let Some(half_life) = recency_half_life {
        for fused in order.iter_mut() {
            let stamp = fused.item.get("updated_at").and_then(Value::as_str);
            let multiplier = if parse_iso(stamp).is_some() {
                0.5 + 0.5 * recency_score(stamp, options.now_secs, half_life)
            } else {
                1.0
            };
            fused
                .source_scores
                .insert("age_decay".into(), Value::from(round6(multiplier)));
            fused.score *= multiplier;
        }
    }

    order.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let matches: Vec<Value> = order
        .into_iter()
        .map(|fused| {
            let mut item = fused.item;
            item.insert(
                "sources".into(),
                Value::Array(fused.sources.into_iter().map(Value::String).collect()),
            );
            item.insert("source_scores".into(), Value::Object(fused.source_scores));
            item.insert("score".into(), Value::from(fused.score));
            if let Some(context) = fused.graph_context {
                item.insert("graph_context".into(), Value::Array(context));
            }
            Value::Object(item)
        })
        .collect();
    let mut matches = crate::service::scope_matches(conn, matches, scope)?;
    matches.truncate(options.limit.clamp(1, 100) as usize);
    let expansion = expand_matches(conn, &mut matches)?;
    for (index, item) in matches.iter_mut().enumerate() {
        let Some(item) = item.as_object_mut() else {
            continue;
        };
        let score = item.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        item.insert("rank".into(), Value::from(index + 1));
        item.insert("score".into(), Value::from(round6(score)));
        let mut fusion = Map::new();
        fusion.insert("weights".into(), Value::Object(weights.clone()));
        fusion.insert(
            "sources".into(),
            item.get("sources").cloned().unwrap_or(Value::Array(vec![])),
        );
        if let Some(class) = query_class.as_ref() {
            fusion.insert("query_class".into(), Value::String(class.clone()));
        }
        item.insert("fusion".into(), Value::Object(fusion));
    }

    let mut channel_report = Map::new();
    for (name, payload) in channels {
        let mut echo = payload.as_object().cloned().unwrap_or_default();
        echo.remove("matches");
        channel_report.insert(name.to_string(), Value::Object(echo));
    }
    let mut policy = Map::new();
    policy.insert("search_query".into(), Value::String(search_query));
    policy.insert(
        "rewrite_rules".into(),
        Value::Array(rewrite_rules.into_iter().map(Value::String).collect()),
    );

    let mut report = Map::new();
    report.insert("query".into(), Value::String(query.to_string()));
    report.insert("mode".into(), Value::String("hybrid".into()));
    if use_rrf {
        report.insert("fusion_strategy".into(), Value::String("rrf".into()));
    }
    if let Some(expansion) = expansion {
        report.insert("graph_expansion".into(), expansion);
    }
    report.insert(
        "query_class".into(),
        query_class.map(Value::String).unwrap_or(Value::Null),
    );
    report.insert("weights".into(), Value::Object(weights));
    report.insert("policy".into(), Value::Object(policy));
    report.insert("channels".into(), Value::Object(channel_report));
    report.insert("matches".into(), Value::Array(matches));
    Ok(Value::Object(report))
}

fn result_key(result: &Value) -> String {
    for key in ["id", "node_id"] {
        if let Some(value) = result.get(key).filter(|value| truthy(value)) {
            return crate::shape::py_str(value);
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn graph() -> (tempfile::TempDir, Connection, LocalEmbeddingModel) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE edges(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, type TEXT,
                                weight REAL, metadata_json TEXT, created_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
               source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
               metadata_json TEXT, indexed_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('a','Decision','Alpha ranking','about ranking','{}','2026-01-02T00:00:00'),
               ('b','Concept','Beta ranking','also ranking','{}','2026-01-03T00:00:00');
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b','w1');
             INSERT INTO edges VALUES
               ('e1','a','b','MENTIONS',0.9,'{}','2026-02-01T00:00:00');",
        )
        .unwrap();
        let model = LocalEmbeddingModel::new(384);
        conn.execute(
            "INSERT INTO vector_embeddings VALUES ('a','node','a',?,384,?,'{}','2026-03-01')",
            rusqlite::params![
                model.encode(&model.embed("alpha ranking")),
                model.model_id()
            ],
        )
        .unwrap();
        (dir, conn, model)
    }

    #[test]
    fn the_default_weights_are_the_fact_row() {
        let mut fact = Map::new();
        for (channel, weight) in class_weights("fact") {
            fact.insert(channel.into(), Value::from(weight));
        }
        let mut defaults = Map::new();
        for (channel, weight) in DEFAULT_HYBRID_WEIGHTS {
            defaults.insert(channel.into(), Value::from(weight));
        }
        assert_eq!(fact, defaults);
        assert_eq!(CHANNELS, ["keyword", "vector", "graph"]);
    }

    #[test]
    fn pinning_weights_disables_the_policy() {
        let (_dir, conn, model) = graph();
        let policied =
            service_hybrid_search(&conn, &model, "ranking", &ServiceHybridOptions::default())
                .unwrap();
        assert_eq!(policied["query_class"], "fact");
        assert_eq!(policied["weights"]["vector"], json!(0.4));

        let mut pinned = Map::new();
        pinned.insert("graph".into(), Value::from(1.0));
        let out = service_hybrid_search(
            &conn,
            &model,
            "ranking",
            &ServiceHybridOptions {
                weights: Some(pinned),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(out["query_class"], Value::Null);
        assert_eq!(
            out["weights"],
            json!({"keyword": 0.35, "vector": 0.4, "graph": 1.0})
        );
        assert_eq!(out["policy"]["rewrite_rules"], json!([]));
        assert!(out["matches"][0]["fusion"].get("query_class").is_none());
    }

    #[test]
    fn contributions_are_summed_not_maxed() {
        let (_dir, conn, model) = graph();
        let out = service_hybrid_search(&conn, &model, "ranking", &ServiceHybridOptions::default())
            .unwrap();
        let first = &out["matches"][0];
        assert_eq!(first["rank"], json!(1));
        assert!(first["sources"].as_array().unwrap().len() >= 2);
        let score = first["score"].as_f64().unwrap();
        assert!(score > 0.4, "three channels must add up, got {score}");
        assert_eq!(out["mode"], "hybrid");
        assert!(out["channels"]["vector"].get("matches").is_none());
        assert_eq!(out["channels"]["vector"]["embedding_dim"], 384);
        assert_eq!(out["channels"]["graph"]["expand_depth"], 1);
    }

    #[test]
    fn only_the_recency_class_records_a_decay() {
        let (_dir, conn, model) = graph();
        let now = parse_iso(Some("2026-03-01T00:00:00")).unwrap();
        let fact = service_hybrid_search(
            &conn,
            &model,
            "ranking",
            &ServiceHybridOptions {
                now_secs: now,
                ..Default::default()
            },
        )
        .unwrap();
        assert!(fact["matches"][0]["source_scores"]
            .get("age_decay")
            .is_none());
        let recent = service_hybrid_search(
            &conn,
            &model,
            "지난주 ranking",
            &ServiceHybridOptions {
                now_secs: now,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(recent["query_class"], "recency");
        let decay = recent["matches"][0]["source_scores"]["age_decay"]
            .as_f64()
            .unwrap();
        assert!((0.5..=1.0).contains(&decay), "{decay}");
    }

    #[test]
    fn the_limit_clamps_at_both_ends() {
        let (_dir, conn, model) = graph();
        for (asked, most) in [(0i64, 1usize), (1, 1), (500, 100)] {
            let out = service_hybrid_search(
                &conn,
                &model,
                "ranking",
                &ServiceHybridOptions {
                    limit: asked,
                    ..Default::default()
                },
            )
            .unwrap();
            assert!(out["matches"].as_array().unwrap().len() <= most);
        }
        assert!(format!("{:?}", ServiceHybridOptions::default()).contains("graph_limit"));
    }

    #[test]
    fn a_result_without_an_id_is_skipped() {
        assert_eq!(result_key(&json!({"id": "a", "node_id": "b"})), "a");
        assert_eq!(result_key(&json!({"node_id": "b"})), "b");
        assert_eq!(result_key(&json!({"id": "", "node_id": "b"})), "b");
        assert_eq!(result_key(&json!({})), "");
        assert_eq!(weights_value(&Map::new(), "graph"), 0.0);
    }
}
