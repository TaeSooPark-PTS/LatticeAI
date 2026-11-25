use lattice_core::read::{vector_row_from, VectorRow};
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::{params_from_iter, types::Value as SqlValue, Connection};
use serde_json::{Map, Value};

use crate::bm25::scoring::{build_workspace_sql_predicate, detect_nodes_v2_schema};
use crate::service::Scope;
use crate::vector::{candidate_cap, recall_report, resolve_vector_index, sort_key, vector_match};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VectorProviderStatus {
    SemanticProviderMatch,
    HashFallback,
    ReindexRequired,
}

impl VectorProviderStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SemanticProviderMatch => "semantic_provider_match",
            Self::HashFallback => "hash_fallback",
            Self::ReindexRequired => "reindex_required",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredEmbeddingSummary {
    pub model_id: String,
    pub dim: i64,
    pub row_count: i64,
    pub is_hash: bool,
}

#[derive(Debug, Clone, Copy)]
pub enum VectorQueryInput<'a> {
    Model(&'a LocalEmbeddingModel),
    Precomputed {
        model_id: &'a str,
        vector: &'a [f64],
        is_hash_fallback: bool,
    },
}

#[derive(Debug, Clone)]
pub struct VectorSearchOptions<'a> {
    pub limit: i64,
    pub min_score: f64,
    pub scope: Option<&'a Scope>,
    pub include_historical: bool,
}

impl<'a> Default for VectorSearchOptions<'a> {
    fn default() -> Self {
        Self {
            limit: 30,
            min_score: 0.0,
            scope: None,
            include_historical: false,
        }
    }
}

pub fn is_hash_model_id(model_id: &str) -> bool {
    let lower = model_id.trim().to_ascii_lowercase();
    lower.contains("hash")
        || lower.starts_with("lattice-local-hash")
        || lower.starts_with("lattice-fallback")
}

pub fn canonical_model_family(model_id: &str) -> String {
    let trimmed = model_id.trim().to_ascii_lowercase();
    let no_org = trimmed.rsplit('/').next().unwrap_or(&trimmed);
    let no_rev = no_org.split('@').next().unwrap_or(no_org);
    let stem = match no_rev.rsplit_once(':') {
        Some((prefix, suffix)) if suffix.chars().all(|c| c.is_ascii_digit()) => prefix,
        _ => no_rev,
    };
    stem.to_string()
}

pub fn inspect_stored_embedding_models(
    conn: &Connection,
) -> Result<Vec<StoredEmbeddingSummary>, CoreError> {
    let mut stmt = conn.prepare(
        "SELECT embedding_model, embedding_dim, COUNT(*) AS cnt
         FROM vector_embeddings
         GROUP BY embedding_model, embedding_dim
         ORDER BY cnt DESC, embedding_model ASC",
    )?;
    let iter = stmt.query_map([], |row| {
        let model_id: String = row.get(0)?;
        let dim: i64 = row.get(1)?;
        let row_count: i64 = row.get(2)?;
        Ok(StoredEmbeddingSummary {
            is_hash: is_hash_model_id(&model_id),
            model_id,
            dim,
            row_count,
        })
    })?;
    let mut out = Vec::new();
    for item in iter {
        out.push(item?);
    }
    Ok(out)
}

pub(crate) fn stored_models_json(stored: &[StoredEmbeddingSummary]) -> Value {
    Value::Array(
        stored
            .iter()
            .map(|s| {
                let mut m = Map::new();
                m.insert("model_id".into(), Value::String(s.model_id.clone()));
                m.insert("dim".into(), Value::from(s.dim));
                m.insert("row_count".into(), Value::from(s.row_count));
                m.insert("is_hash".into(), Value::Bool(s.is_hash));
                Value::Object(m)
            })
            .collect(),
    )
}

#[derive(Debug, Clone)]
pub(crate) struct AlignmentPlan {
    pub(crate) status: VectorProviderStatus,
    pub(crate) reindex_required: bool,
    pub(crate) matched_models: Vec<String>,
    pub(crate) note: Option<String>,
}

pub(crate) fn resolve_alignment_plan(
    stored: &[StoredEmbeddingSummary],
    active_model_id: &str,
    active_dim: i64,
    active_is_hash: bool,
) -> AlignmentPlan {
    let has_exact = stored
        .iter()
        .any(|s| s.row_count > 0 && s.model_id == active_model_id && s.dim == active_dim);
    if has_exact {
        if active_is_hash {
            let semantic_stored: Vec<&str> = stored
                .iter()
                .filter(|s| !s.is_hash && s.row_count > 0)
                .map(|s| s.model_id.as_str())
                .collect();
            let note = if semantic_stored.is_empty() {
                None
            } else {
                Some(format!(
                    "query uses hash fallback while non-hash embeddings ({}) are stored",
                    semantic_stored.join(", ")
                ))
            };
            return AlignmentPlan {
                status: VectorProviderStatus::HashFallback,
                reindex_required: false,
                matched_models: vec![active_model_id.to_string()],
                note,
            };
        }
        return AlignmentPlan {
            status: VectorProviderStatus::SemanticProviderMatch,
            reindex_required: false,
            matched_models: vec![active_model_id.to_string()],
            note: None,
        };
    }

    if !active_is_hash {
        let active_family = canonical_model_family(active_model_id);
        let family_matches: Vec<String> = stored
            .iter()
            .filter(|s| {
                s.row_count > 0
                    && !s.is_hash
                    && s.dim == active_dim
                    && canonical_model_family(&s.model_id) == active_family
            })
            .map(|s| s.model_id.clone())
            .collect();
        if !family_matches.is_empty() {
            return AlignmentPlan {
                status: VectorProviderStatus::SemanticProviderMatch,
                reindex_required: false,
                matched_models: family_matches,
                note: Some(format!(
                    "aligned stored embeddings by canonical model family '{active_family}' (dim={active_dim})"
                )),
            };
        }
    }

    if stored.iter().any(|s| s.row_count > 0) {
        return AlignmentPlan {
            status: VectorProviderStatus::ReindexRequired,
            reindex_required: true,
            matched_models: Vec::new(),
            note: Some(format!(
                "stored embeddings do not match active model '{active_model_id}' (dim={active_dim}); re-indexing required"
            )),
        };
    }

    AlignmentPlan {
        status: if active_is_hash {
            VectorProviderStatus::HashFallback
        } else {
            VectorProviderStatus::SemanticProviderMatch
        },
        reindex_required: false,
        matched_models: vec![active_model_id.to_string()],
        note: None,
    }
}

pub(crate) fn build_reindex_or_mismatch_response(
    query: String,
    model_id: String,
    dim: i64,
    plan: &AlignmentPlan,
    stored_models: &[StoredEmbeddingSummary],
    recall: Value,
    index_block: Value,
) -> Value {
    let mut out = Map::new();
    out.insert("query".into(), Value::String(query));
    out.insert("embedding_model".into(), Value::String(model_id));
    out.insert("embedding_dim".into(), Value::from(dim));
    out.insert(
        "provider_status".into(),
        Value::String(plan.status.as_str().to_string()),
    );
    out.insert(
        "reindex_required".into(),
        Value::Bool(plan.reindex_required),
    );
    out.insert("stored_models".into(), stored_models_json(stored_models));
    if let Some(note) = &plan.note {
        out.insert("alignment_note".into(), Value::String(note.clone()));
    }
    out.insert("matches".into(), Value::Array(Vec::new()));
    out.insert("recall".into(), recall);
    out.insert("index".into(), index_block);
    Value::Object(out)
}

fn l2_normalize(vec: &[f64]) -> Vec<f64> {
    let norm = vec.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm <= 1e-12 {
        vec.to_vec()
    } else {
        vec.iter().map(|v| v / norm).collect()
    }
}

fn build_aligned_vector_sql(
    conn: &Connection,
    matched_models: &[String],
    dim: i64,
    scope: Option<&Scope>,
    include_historical: bool,
    cap: Option<i64>,
) -> Result<(String, String, Vec<SqlValue>, Vec<SqlValue>), CoreError> {
    let model_placeholders = std::iter::repeat_n("?", matched_models.len())
        .collect::<Vec<_>>()
        .join(", ");
    let mut base_params: Vec<SqlValue> =
        matched_models.iter().cloned().map(SqlValue::Text).collect();
    base_params.push(SqlValue::Integer(dim));

    let schema = detect_nodes_v2_schema(conn)?;
    let v2_join = if schema.exists {
        "LEFT JOIN nodes_v2 v2 ON v2.id = COALESCE(NULLIF(ve.source_node, ''), c.source_node)"
    } else {
        ""
    };

    let mut extra_clauses: Vec<String> = Vec::new();
    let mut scope_params: Vec<SqlValue> = Vec::new();

    if let Some(active_scope) = scope {
        let meta_expr = "COALESCE(NULLIF(n.metadata_json, ''), NULLIF(pn.metadata_json, ''), NULLIF(c.metadata_json, ''), ve.metadata_json)";
        let json_ws = format!(
            "CASE WHEN json_valid({meta_expr}) THEN COALESCE(NULLIF(json_extract({meta_expr}, '$.workspace_id'), ''), NULLIF(json_extract({meta_expr}, '$.workspace'), '')) END"
        );
        let ws_expr = if schema.exists && schema.has_workspace_id {
            format!("COALESCE(NULLIF(v2.workspace_id, ''), {json_ws})")
        } else {
            json_ws.clone()
        };
        let presence_guard = if schema.exists && schema.has_workspace_id {
            Some(format!("(v2.id IS NOT NULL OR {json_ws} IS NOT NULL)"))
        } else {
            None
        };
        if let Some((ws_clause, ws_params)) =
            build_workspace_sql_predicate(&ws_expr, presence_guard.as_deref(), active_scope)
        {
            extra_clauses.push(ws_clause);
            scope_params.extend(ws_params);
        }
    }

    if !include_historical {
        let meta_expr =
            "COALESCE(NULLIF(n.metadata_json, ''), NULLIF(pn.metadata_json, ''), ve.metadata_json)";
        let json_sup = format!(
            "CASE WHEN json_valid({meta_expr}) THEN NULLIF(json_extract({meta_expr}, '$.superseded_by'), '') END"
        );
        let sup_expr = if schema.exists && schema.has_superseded_by {
            format!("COALESCE(NULLIF(v2.superseded_by, ''), {json_sup})")
        } else {
            json_sup
        };
        let json_st = format!(
            "CASE WHEN json_valid({meta_expr}) THEN COALESCE(NULLIF(json_extract({meta_expr}, '$.lifecycle_status'), ''), NULLIF(json_extract({meta_expr}, '$.status'), '')) END"
        );
        let st_expr = if schema.exists && schema.has_lifecycle_status {
            format!("LOWER(COALESCE(NULLIF(v2.lifecycle_status, ''), {json_st}, 'active'))")
        } else {
            format!("LOWER(COALESCE({json_st}, 'active'))")
        };
        extra_clauses.push(format!(
            "NOT ({sup_expr} IS NOT NULL OR {st_expr} IN ('superseded', 'replaced', 'deprecated', 'archived', 'retired'))"
        ));
    }

    let extra_where = if extra_clauses.is_empty() {
        String::new()
    } else {
        format!(" AND {}", extra_clauses.join(" AND "))
    };
    let join_block = format!(
        "FROM vector_embeddings ve
         LEFT JOIN nodes n ON n.id=ve.source_node
         LEFT JOIN chunks c ON c.id=ve.item_id
         LEFT JOIN nodes pn ON pn.id=c.source_node
         {v2_join}"
    );
    let where_block = format!(
        "WHERE ve.embedding_model IN ({model_placeholders}) AND ve.embedding_dim=?{extra_where}"
    );
    let count_sql = format!("SELECT COUNT(*) AS c {join_block} {where_block}");
    let limit_clause = if cap.is_some() { " LIMIT ?" } else { "" };
    let select_sql = format!(
        "SELECT
           ve.item_id, ve.item_type, ve.source_node, ve.embedding,
           ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
           n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
           n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
           c.text AS chunk_text, c.source_node AS parent_node_id,
           c.metadata_json AS chunk_metadata,
           pn.type AS parent_type, pn.title AS parent_title,
           pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
           pn.updated_at AS parent_updated_at
         {join_block}
         {where_block}
         ORDER BY ve.indexed_at DESC{limit_clause}"
    );
    base_params.extend(scope_params);
    let count_params = base_params.clone();
    let mut select_params = base_params;
    if let Some(c) = cap {
        select_params.push(SqlValue::Integer(c));
    }
    Ok((count_sql, select_sql, count_params, select_params))
}

pub fn vector_search_aligned(
    conn: &Connection,
    query_input: VectorQueryInput<'_>,
    query: &str,
    options: &VectorSearchOptions<'_>,
) -> Result<Value, CoreError> {
    let query = query.trim().to_string();
    let limit = if options.limit == 0 {
        30
    } else {
        options.limit
    }
    .clamp(1, 100);
    let cap = candidate_cap(limit);
    let selection = resolve_vector_index();
    let backend = selection.backend.clone();

    let (active_model_id, active_dim, active_is_hash, query_vector) = match query_input {
        VectorQueryInput::Model(model) => {
            let id = model.model_id().to_string();
            let dim = model.dim() as i64;
            let hash = is_hash_model_id(&id);
            let vec = if query.is_empty() {
                Vec::new()
            } else {
                model.embed(&query)
            };
            (id, dim, hash, vec)
        }
        VectorQueryInput::Precomputed {
            model_id,
            vector,
            is_hash_fallback,
        } => {
            let id = model_id.to_string();
            let dim = vector.len() as i64;
            let hash = is_hash_fallback || is_hash_model_id(&id);
            (id, dim, hash, l2_normalize(vector))
        }
    };

    let stored_models = inspect_stored_embedding_models(conn)?;
    let plan = resolve_alignment_plan(&stored_models, &active_model_id, active_dim, active_is_hash);

    if query.is_empty() {
        let mut out = Map::new();
        out.insert("query".into(), Value::String(query));
        out.insert("embedding_model".into(), Value::String(active_model_id));
        out.insert("embedding_dim".into(), Value::from(active_dim));
        out.insert(
            "provider_status".into(),
            Value::String(plan.status.as_str().to_string()),
        );
        out.insert(
            "reindex_required".into(),
            Value::Bool(plan.reindex_required),
        );
        out.insert("stored_models".into(), stored_models_json(&stored_models));
        out.insert("matches".into(), Value::Array(Vec::new()));
        out.insert("recall".into(), recall_report(&backend, cap, 0, 0, None));
        return Ok(Value::Object(out));
    }

    if plan.reindex_required || plan.matched_models.is_empty() {
        let mut reindex_plan = plan;
        reindex_plan.status = VectorProviderStatus::ReindexRequired;
        reindex_plan.reindex_required = true;
        return Ok(build_reindex_or_mismatch_response(
            query,
            active_model_id,
            active_dim,
            &reindex_plan,
            &stored_models,
            recall_report(&backend, cap, 0, 0, None),
            selection.as_json(),
        ));
    }

    let (count_sql, select_sql, count_params, select_params) = build_aligned_vector_sql(
        conn,
        &plan.matched_models,
        active_dim,
        options.scope,
        options.include_historical,
        cap,
    )?;

    let candidates_total: i64 =
        conn.query_row(&count_sql, params_from_iter(count_params), |row| row.get(0))?;

    let want_native_hnsw = !crate::vector::explicit_bruteforce_pinned()
        && selection.detail.is_none()
        && plan.matched_models.len() == 1
        && (selection.requested == "hnsw" || candidates_total >= crate::vector::HNSW_AUTO_MIN_ROWS);

    let mut index_json = selection.as_json();
    let mut hnsw_candidate_ids: Option<std::collections::HashSet<String>> = None;
    let mut hnsw_store_active: Option<usize> = None;

    if want_native_hnsw {
        let fetch_k = ((limit as usize).saturating_mul(8)).clamp(64, 256);
        match crate::native_hnsw::query_native_hnsw(
            conn,
            &plan.matched_models[0],
            active_dim.max(1) as usize,
            &query_vector,
            fetch_k,
        ) {
            Ok((ids, active_cnt)) if !ids.is_empty() => {
                hnsw_candidate_ids = Some(ids.into_iter().collect());
                hnsw_store_active = Some(active_cnt);
                let mut idx = Map::new();
                idx.insert(
                    "requested".into(),
                    Value::String(selection.requested.clone()),
                );
                idx.insert(
                    "backend".into(),
                    Value::String("native-hnsw+rescore".into()),
                );
                idx.insert("name".into(), Value::String("hnsw+rescore".into()));
                idx.insert("approx".into(), Value::Bool(true));
                idx.insert("exhaustive".into(), Value::Bool(false));
                idx.insert("honored".into(), Value::Bool(true));
                idx.insert("detail".into(), Value::Null);
                index_json = Value::Object(idx);
            }
            Ok(_) => {}
            Err(reason) => {
                if selection.requested == "hnsw" {
                    index_json = crate::vector_hnsw::fallback_selection(&reason).as_json();
                }
            }
        }
    }

    let mut stmt = conn.prepare(&select_sql)?;
    let all_rows: Vec<VectorRow> = stmt
        .query_map(params_from_iter(select_params), vector_row_from)?
        .filter_map(Result::ok)
        .collect();

    let rows: Vec<VectorRow> = if let Some(ref allow_ids) = hnsw_candidate_ids {
        let filtered: Vec<VectorRow> = all_rows
            .iter()
            .filter(|r| allow_ids.contains(&r.item_id))
            .cloned()
            .collect();
        if filtered.is_empty() {
            index_json = selection.as_json();
            all_rows
        } else {
            filtered
        }
    } else {
        all_rows
    };

    let effective_backend = if hnsw_store_active.is_some()
        && index_json.get("name").and_then(Value::as_str) == Some("hnsw+rescore")
    {
        "native-hnsw+rescore"
    } else {
        backend.as_str()
    };
    let recall = crate::vector::recall_report_custom(
        effective_backend,
        cap,
        candidates_total,
        rows.len() as i64,
        None,
        effective_backend != "native-hnsw+rescore",
    );
    let decoder = LocalEmbeddingModel::new(active_dim.max(1) as usize);

    let mut scored: Vec<Map<String, Value>> = Vec::new();
    for row in &rows {
        let stored = decoder.decode(&row.embedding, row.embedding_dim.map(|d| d as usize));
        let Ok(score) = decoder.similarity(&query_vector, &stored) else {
            continue;
        };
        if score < options.min_score {
            continue;
        };
        scored.push(vector_match(row, score));
    }
    scored.sort_by(|a, b| {
        sort_key(b)
            .partial_cmp(&sort_key(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    scored.truncate(limit as usize);

    let effective_model = if plan.matched_models.len() == 1 {
        plan.matched_models[0].clone()
    } else {
        active_model_id
    };

    let mut out = Map::new();
    out.insert("query".into(), Value::String(query));
    out.insert("embedding_model".into(), Value::String(effective_model));
    out.insert("embedding_dim".into(), Value::from(active_dim));
    out.insert(
        "provider_status".into(),
        Value::String(plan.status.as_str().to_string()),
    );
    out.insert(
        "reindex_required".into(),
        Value::Bool(plan.reindex_required),
    );
    out.insert("stored_models".into(), stored_models_json(&stored_models));
    if let Some(note) = plan.note {
        out.insert("alignment_note".into(), Value::String(note));
    }
    out.insert(
        "matches".into(),
        Value::Array(scored.into_iter().map(Value::Object).collect()),
    );
    out.insert("recall".into(), recall);
    out.insert("index".into(), index_json);
    Ok(Value::Object(out))
}
