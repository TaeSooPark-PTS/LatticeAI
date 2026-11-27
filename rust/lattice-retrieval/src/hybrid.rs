use std::collections::{BTreeMap, BTreeSet, HashMap};

use lattice_core::pytext::{parse_iso, recency_score, round6};
use lattice_core::read::filter_scoped_nodes;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::expansion::{
    expansion_enabled, one_hop, rrf_enabled, rrf_scores, EXPANSION_SCORE_FACTOR, EXPANSION_SEEDS,
};
use crate::keyword::search;
use crate::policy::resolve_policy;
use crate::shape::{
    class_json, empty_result, expansion_json, multimodal_signal, parent_node_id, policy_json,
    py_str, sources_json, truthy, DEFAULT_EXPANSION_CAP,
};

#[derive(Debug, Clone)]
pub struct HybridOptions {
    pub top_k: i64,
    pub alpha: Option<f64>,
    pub allowed_workspaces: Option<BTreeSet<String>>,
    pub include_legacy_global: bool,
    pub lexical_limit: Option<i64>,
    pub vector_limit: Option<i64>,
    pub min_vector_score: f64,
    pub now_secs: f64,
    pub query_embedding: Option<crate::bm25::QueryVectorOverride>,
    pub semantic_degraded: Option<String>,
    pub recency: Option<crate::temporal::RecencyDecay>,
}

impl Default for HybridOptions {
    fn default() -> Self {
        Self {
            top_k: 20,
            alpha: None,
            allowed_workspaces: None,
            include_legacy_global: false,
            lexical_limit: None,
            vector_limit: None,
            min_vector_score: 0.0,
            now_secs: 0.0,
            query_embedding: None,
            semantic_degraded: None,
            recency: None,
        }
    }
}

struct Entry {
    node_id: String,
    id: Value,
    node_type: Value,
    title: Value,
    summary: Value,
    metadata: Value,
    updated_at: Value,
    lexical: f64,
    vector: f64,
    from_lexical: bool,
    from_vector: bool,
    age_decay: Option<f64>,
    score: f64,
    fusion: &'static str,
    ranks: Vec<usize>,
    via: Option<Value>,
}

fn stale_embedder(conn: &Connection, model: &LocalEmbeddingModel) -> bool {
    let recorded: Option<String> = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key=?",
            ["embedder_fingerprint"],
            |row| row.get(0),
        )
        .ok();
    let Some(raw) = recorded else { return false };
    let payload = lattice_core::pytext::safe_loads(Some(raw.as_str()));
    let Some(model_id) = payload.get("model_id").filter(|v| truthy(v)).map(py_str) else {
        return false;
    };
    let dim = payload.get("dim").and_then(Value::as_i64).unwrap_or(0);
    model_id != model.model_id() || dim != model.dim() as i64
}

pub fn hybrid_search(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    options: &HybridOptions,
) -> Result<Value, CoreError> {
    let query = query.trim().to_string();
    let top_k = options.top_k.clamp(1, 100);
    let mut query_class: Option<String> = None;
    let mut search_query = query.clone();
    let mut rewrite_rules: Vec<String> = Vec::new();
    let mut recency_half_life: Option<f64> = None;
    let mut fusion_strategy = "alpha";

    let mut alpha = match options.alpha {
        Some(alpha) => alpha,
        None => {
            let policy = resolve_policy(&query);
            query_class = Some(policy.query_class.clone());
            rewrite_rules = policy.rewrite_rules.clone();
            if !policy.search_query.is_empty() && policy.search_query != query {
                search_query = policy.search_query.clone();
            }
            recency_half_life = policy.recency_half_life_days;
            policy.alpha
        }
    };
    alpha = alpha.clamp(0.0, 1.0);

    if query.is_empty() {
        return Ok(empty_result(
            &query,
            alpha,
            &query_class,
            top_k,
            &search_query,
            &rewrite_rules,
        ));
    }

    let fetch = |requested: Option<i64>| -> i64 {
        requested
            .filter(|v| *v != 0)
            .unwrap_or((top_k * 2).max(20))
            .clamp(1, 100)
    };
    let lex_fetch = fetch(options.lexical_limit);
    let vec_fetch = fetch(options.vector_limit);

    let allowed = options.allowed_workspaces.as_ref();
    let lexical_payload = search(
        conn,
        &search_query,
        lex_fetch,
        allowed,
        options.include_legacy_global,
    )?;
    let mut lexical_matches: Vec<Map<String, Value>> = lexical_payload["matches"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_object().cloned())
                .collect()
        })
        .unwrap_or_default();

    let mut mode = "hybrid";
    let mut detail: Option<String> = None;
    let mut vector_matches: Vec<Map<String, Value>> = Vec::new();
    let mut vector_recall: Option<Value> = None;
    let mut vector_meta = Map::new();
    for key in [
        "backend",
        "approx",
        "exhaustive",
        "truncated",
        "embedded_rows",
        "degraded",
    ] {
        vector_meta.insert(key.into(), Value::Null);
    }
    let mut vector_degraded: Option<String> = None;
    let vector_outcome = if let Some(reason) = options.semantic_degraded.as_ref() {
        vector_degraded = Some(reason.clone());
        Err(CoreError::InvalidRequest(reason.clone()))
    } else {
        let query_input = match options.query_embedding.as_ref() {
            Some(ovr) => crate::vector::VectorQueryInput::Precomputed {
                model_id: &ovr.model_id,
                vector: &ovr.vector,
                is_hash_fallback: ovr.is_hash_fallback,
            },
            None => crate::vector::VectorQueryInput::Model(model),
        };
        let vector_scope = crate::service::Scope {
            allowed_workspaces: options.allowed_workspaces.clone(),
            include_legacy_global: options.include_legacy_global,
        };
        crate::vector::vector_search_aligned(
            conn,
            query_input,
            &search_query,
            &crate::vector::VectorSearchOptions {
                limit: vec_fetch,
                min_score: options.min_vector_score,
                scope: Some(&vector_scope),
                include_historical: false,
            },
        )
    };
    match vector_outcome {
        Ok(payload) => {
            vector_matches = payload["matches"]
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .filter_map(|v| v.as_object().cloned())
                        .collect()
                })
                .unwrap_or_default();
            if let Some(recall) = payload.get("recall").and_then(Value::as_object) {
                vector_meta.insert("backend".into(), recall["backend"].clone());
                let truncated = recall.get("truncated").map(truthy).unwrap_or(false);
                vector_meta.insert("truncated".into(), Value::Bool(truncated));
                vector_meta.insert(
                    "embedded_rows".into(),
                    recall
                        .get("candidates_total")
                        .cloned()
                        .unwrap_or(Value::Null),
                );
                if truncated {
                    vector_recall = Some(Value::Object(recall.clone()));
                }
            }
            if let Some(index) = payload.get("index").and_then(Value::as_object) {
                vector_meta.insert(
                    "approx".into(),
                    Value::Bool(index.get("approx").map(truthy).unwrap_or(false)),
                );
                vector_meta.insert(
                    "exhaustive".into(),
                    Value::Bool(index.get("exhaustive").map(truthy).unwrap_or(false)),
                );
            }
        }
        Err(err) => {
            mode = "lexical_only";
            detail = Some(format!("vector index unavailable: {err}"));
        }
    }

    if mode == "hybrid"
        && options.query_embedding.is_none()
        && vector_matches.is_empty()
        && stale_embedder(conn, model)
    {
        vector_degraded = Some("stale_embedder".to_string());
    }
    if !vector_matches.is_empty() && allowed.is_some() {
        vector_matches = filter_scoped_nodes(
            conn,
            vector_matches,
            allowed,
            options.include_legacy_global,
            |item| item.get("node_id").map(py_str).unwrap_or_default(),
        )?;
    }
    if crate::bm25::bm25f_graphrag_enabled()
        || (lexical_matches.is_empty()
            && vector_matches.is_empty()
            && !search_query.trim().is_empty())
    {
        let scope = crate::service::Scope {
            allowed_workspaces: options.allowed_workspaces.clone(),
            include_legacy_global: options.include_legacy_global,
        };
        if let Ok(bm25_hits) = crate::bm25::bm25f_search(
            conn,
            &search_query,
            lex_fetch,
            &scope,
            &crate::bm25::Bm25fParams::default(),
        ) {
            let existing_ids: BTreeSet<String> = lexical_matches
                .iter()
                .map(parent_node_id)
                .filter(|id| !id.is_empty())
                .collect();
            for hit in bm25_hits {
                if existing_ids.contains(&hit.node.id) {
                    continue;
                }
                let mut row = Map::new();
                row.insert("id".into(), Value::String(hit.node.id.clone()));
                row.insert("node_id".into(), Value::String(hit.node.id.clone()));
                row.insert(
                    "type".into(),
                    hit.node.node_type.map(Value::String).unwrap_or(Value::Null),
                );
                row.insert(
                    "title".into(),
                    hit.node.title.map(Value::String).unwrap_or(Value::Null),
                );
                row.insert(
                    "summary".into(),
                    hit.node.summary.map(Value::String).unwrap_or(Value::Null),
                );
                row.insert(
                    "metadata".into(),
                    Value::Object(lattice_core::pytext::safe_loads(
                        hit.node.metadata_json.as_deref(),
                    )),
                );
                row.insert(
                    "updated_at".into(),
                    hit.node
                        .updated_at
                        .map(Value::String)
                        .unwrap_or(Value::Null),
                );
                row.insert("score".into(), Value::from(hit.bm25_score));
                lexical_matches.push(row);
            }
        }
    }
    let vector_total = vector_matches.len();

    let mut order: HashMap<String, usize> = HashMap::new();
    let mut entries: Vec<Entry> = Vec::new();
    let entry_for = |entries: &mut Vec<Entry>,
                     order: &mut HashMap<String, usize>,
                     node_id: &str,
                     item: &Map<String, Value>|
     -> usize {
        if let Some(index) = order.get(node_id) {
            return *index;
        }
        let metadata = match item.get("metadata") {
            Some(Value::Object(map)) if !map.is_empty() => Value::Object(map.clone()),
            _ => Value::Object(Map::new()),
        };
        entries.push(Entry {
            node_id: node_id.to_string(),
            id: item
                .get("id")
                .filter(|v| truthy(v))
                .cloned()
                .unwrap_or(Value::String(node_id.into())),
            node_type: item.get("type").cloned().unwrap_or(Value::Null),
            title: item.get("title").cloned().unwrap_or(Value::Null),
            summary: item.get("summary").cloned().unwrap_or(Value::Null),
            metadata,
            updated_at: item.get("updated_at").cloned().unwrap_or(Value::Null),
            lexical: 0.0,
            vector: 0.0,
            from_lexical: false,
            from_vector: false,
            age_decay: None,
            score: 0.0,
            fusion: "lexical",
            ranks: Vec::new(),
            via: None,
        });
        order.insert(node_id.to_string(), entries.len() - 1);
        entries.len() - 1
    };

    for (rank, item) in lexical_matches.iter().enumerate() {
        let node_id = parent_node_id(item);
        if node_id.is_empty() {
            continue;
        }
        let index = entry_for(&mut entries, &mut order, &node_id, item);
        let scored = round6(1.0 / (rank as f64 + 1.0));
        entries[index].lexical = entries[index].lexical.max(scored);
        entries[index].from_lexical = true;
        entries[index].ranks.push(rank + 1);
    }

    let mut max_vec = 0.0f64;
    for item in &vector_matches {
        if let Some(raw) = item.get("score").and_then(Value::as_f64) {
            if raw > max_vec {
                max_vec = raw;
            }
        }
    }
    for (rank, item) in vector_matches.iter().enumerate() {
        let node_id = parent_node_id(item);
        if node_id.is_empty() {
            continue;
        }
        let raw = item.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let vec_norm = if max_vec > 0.0 {
            raw.max(0.0) / max_vec
        } else {
            0.0
        };
        let index = entry_for(&mut entries, &mut order, &node_id, item);
        entries[index].vector = entries[index].vector.max(round6(vec_norm));
        entries[index].from_vector = true;
        entries[index].ranks.push(rank + 1);
        if !truthy(&entries[index].summary) {
            if let Some(summary) = item.get("summary").filter(|v| truthy(v)) {
                entries[index].summary = summary.clone();
            }
        }
    }

    let use_rrf = rrf_enabled() && mode != "lexical_only";
    let rrf = if use_rrf {
        let ranks: BTreeMap<String, Vec<usize>> = entries
            .iter()
            .map(|entry| (entry.node_id.clone(), entry.ranks.clone()))
            .collect();
        rrf_scores(&ranks)
    } else {
        BTreeMap::new()
    };
    if use_rrf {
        fusion_strategy = "rrf";
    }

    for entry in entries.iter_mut() {
        let fused = if mode == "lexical_only" {
            entry.lexical
        } else if use_rrf {
            rrf.get(&entry.node_id).copied().unwrap_or(0.0)
        } else {
            alpha * entry.vector + (1.0 - alpha) * entry.lexical
        };
        entry.fusion = match (entry.from_lexical, entry.from_vector) {
            (true, true) => "both",
            (false, true) => "vector",
            _ => "lexical",
        };
        entry.score = round6(fused);
    }

    if let Some(decay) = options.recency.as_ref() {
        let recency_class = crate::policy::classify_query(&query) == "recency";
        for entry in entries.iter_mut() {
            let multiplier =
                decay.multiplier(entry.updated_at.as_str(), options.now_secs, recency_class);
            entry.age_decay = Some(multiplier);
            entry.score = round6(entry.score * multiplier);
        }
    } else if let Some(half_life) = recency_half_life {
        for entry in entries.iter_mut() {
            let stamp = entry.updated_at.as_str();
            let multiplier = if parse_iso(stamp).is_some() {
                0.5 + 0.5 * recency_score(stamp, options.now_secs, half_life)
            } else {
                1.0
            };
            entry.age_decay = Some(round6(multiplier));
            entry.score = round6(entry.score * multiplier);
        }
    }

    entries.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.node_id.cmp(&b.node_id))
    });
    entries.truncate(((top_k * 2).max(top_k)) as usize);
    entries.truncate(top_k.max(1) as usize);

    let mut expansion = expansion_json();
    if expansion_enabled() && !entries.is_empty() {
        let seeds: Vec<String> = entries
            .iter()
            .take(EXPANSION_SEEDS)
            .map(|entry| entry.node_id.clone())
            .collect();
        let seed_scores: Vec<f64> = entries
            .iter()
            .take(EXPANSION_SEEDS)
            .map(|entry| entry.score)
            .collect();
        let present: BTreeSet<String> = entries.iter().map(|entry| entry.node_id.clone()).collect();
        let cap = DEFAULT_EXPANSION_CAP.max(0) as usize;
        let (neighbours, report) = one_hop(conn, &seeds, &present, cap)?;
        for neighbour in neighbours {
            let seed_score = seeds
                .iter()
                .position(|id| *id == neighbour.seed_id)
                .and_then(|index| seed_scores.get(index).copied())
                .unwrap_or(0.0);
            let via = neighbour.provenance();
            entries.push(Entry {
                node_id: neighbour.node_id.clone(),
                id: Value::String(neighbour.node_id),
                node_type: neighbour.node_type,
                title: neighbour.title,
                summary: neighbour.summary,
                metadata: neighbour.metadata,
                updated_at: neighbour.updated_at,
                lexical: 0.0,
                vector: 0.0,
                from_lexical: false,
                from_vector: false,
                age_decay: None,
                score: round6(seed_score * EXPANSION_SCORE_FACTOR),
                fusion: "graph",
                ranks: Vec::new(),
                via: Some(via),
            });
        }
        expansion = report.as_json(DEFAULT_EXPANSION_CAP);
    }

    let matches: Vec<Value> = entries
        .iter()
        .enumerate()
        .map(|(index, entry)| {
            let mut scores = Map::new();
            scores.insert("lexical".into(), Value::from(entry.lexical));
            scores.insert("vector".into(), Value::from(entry.vector));
            if let Some(decay) = entry.age_decay {
                scores.insert("age_decay".into(), Value::from(decay));
            }
            let mut item = Map::new();
            item.insert("node_id".into(), Value::String(entry.node_id.clone()));
            item.insert("id".into(), entry.id.clone());
            item.insert("type".into(), entry.node_type.clone());
            item.insert("title".into(), entry.title.clone());
            item.insert("summary".into(), entry.summary.clone());
            item.insert("metadata".into(), entry.metadata.clone());
            item.insert("updated_at".into(), entry.updated_at.clone());
            item.insert("scores".into(), Value::Object(scores));
            item.insert("fusion".into(), Value::String(entry.fusion.to_string()));
            item.insert("score".into(), Value::from(entry.score));
            item.insert("rerank_score".into(), Value::from(entry.score));
            item.insert("rank".into(), Value::from(index + 1));
            if let Some(via) = entry.via.as_ref() {
                item.insert("via".into(), via.clone());
            }
            Value::Object(item)
        })
        .collect();

    let mut rerank = Map::new();
    rerank.insert("mode".into(), Value::String("identity".into()));
    rerank.insert("model".into(), Value::Null);
    rerank.insert("detail".into(), Value::Null);

    let mut result = Map::new();
    result.insert("query".into(), Value::String(query));
    result.insert("mode".into(), Value::String(mode.to_string()));
    result.insert("alpha".into(), Value::from(alpha));
    result.insert("query_class".into(), class_json(&query_class));
    result.insert("top_k".into(), Value::from(top_k));
    result.insert(
        "sources".into(),
        sources_json(lexical_matches.len(), vector_total),
    );
    result.insert("matches".into(), Value::Array(matches.clone()));
    result.insert("policy".into(), policy_json(&search_query, &rewrite_rules));
    result.insert(
        "fusion_strategy".into(),
        Value::String(fusion_strategy.to_string()),
    );
    result.insert("graph_expansion".into(), expansion);
    result.insert("rerank".into(), Value::Object(rerank));
    result.insert(
        "detail".into(),
        detail.map(Value::String).unwrap_or(Value::Null),
    );
    if let Some(reason) = &vector_degraded {
        result.insert("vector_degraded".into(), Value::String(reason.clone()));
    }
    if let Some(recall) = vector_recall {
        result.insert("vector_recall".into(), recall);
        if vector_degraded.is_none() {
            result.insert(
                "vector_degraded".into(),
                Value::String("partial_recall".into()),
            );
        }
    }
    vector_meta.insert(
        "degraded".into(),
        result
            .get("vector_degraded")
            .cloned()
            .unwrap_or(Value::Null),
    );
    result.insert("vector".into(), Value::Object(vector_meta));
    if let Some(multimodal) = multimodal_signal(&matches) {
        result.insert("multimodal".into(), multimodal);
    }
    Ok(Value::Object(result))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_default_to_the_python_signature() {
        let options = HybridOptions::default();
        assert_eq!(options.top_k, 20);
        assert!(options.alpha.is_none());
        assert!(options.allowed_workspaces.is_none());
        assert!(!options.include_legacy_global);
        assert_eq!(options.min_vector_score, 0.0);
        assert!(format!("{options:?}").contains("top_k"));
    }

    #[test]
    fn the_fingerprint_probe_only_fires_on_a_real_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        let model = LocalEmbeddingModel::new(384);
        assert!(!stale_embedder(&conn, &model));
        conn.execute_batch("CREATE TABLE graph_meta(key TEXT PRIMARY KEY, value TEXT)")
            .unwrap();
        assert!(!stale_embedder(&conn, &model));
        let insert =
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES ('embedder_fingerprint', ?)";
        conn.execute(
            insert,
            [r#"{"model_id":"lattice-local-hash-v1:384","dim":384}"#],
        )
        .unwrap();
        assert!(!stale_embedder(&conn, &model));
        conn.execute(insert, [r#"{"model_id":"other","dim":384}"#])
            .unwrap();
        assert!(stale_embedder(&conn, &model));
        conn.execute(
            insert,
            [r#"{"model_id":"lattice-local-hash-v1:384","dim":768}"#],
        )
        .unwrap();
        assert!(stale_embedder(&conn, &model));
        conn.execute(insert, ["not json"]).unwrap();
        assert!(!stale_embedder(&conn, &model));
        conn.execute(insert, [r#"{"dim":384}"#]).unwrap();
        assert!(!stale_embedder(&conn, &model));
    }
}
