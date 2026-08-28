//! Env-gated HNSW candidate fetch + exact rescore.
//!
//! Default env never enters this module. When `LATTICEAI_VECTOR_INDEX=hnsw`,
//! search asks the worker sidecar for `k * 4` ids (capped at 200) and then
//! scores those rows with the same cosine the brute path uses — approximate
//! recall, exact ordering. Any failure falls back to brute with the reason.

use lattice_core::db::WORKER_ORIGIN_ENV;
use lattice_core::read::{vector_row_from, VectorRow, VECTOR_ROW_SELECT};
use lattice_core::worker::WorkerSeamClient;
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

use crate::vector::{
    recall_report_custom, vector_match, BackendSelection, BRUTE_FORCE_BACKEND, VECTOR_INDEX_ENV,
};

/// How many ANN candidates to fetch per requested hit.
///
/// `k * 4` is the contract minimum; 8 is what keeps recall@10 ≥ 0.95 on the
/// ragbench synthetic store (near-duplicate hash vectors). Still capped at 200.
pub const HNSW_CANDIDATE_MULTIPLIER: i64 = 8;
/// Same cap as `VECTOR_QUERY_K_CAP` on the worker.
pub const HNSW_WORKER_K_CAP: i64 = 200;

static BOUND_ORIGIN: OnceLock<String> = OnceLock::new();

/// Remember the host's worker origin so search does not need every caller
/// to thread a seam through the golden-pinned `vector_search` signature.
pub fn bind_worker_origin(origin: &str) {
    let trimmed = origin.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return;
    }
    let _ = BOUND_ORIGIN.set(trimmed.to_string());
}

fn worker_origin() -> Option<String> {
    std::env::var(WORKER_ORIGIN_ENV)
        .ok()
        .map(|raw| raw.trim().trim_end_matches('/').to_string())
        .filter(|raw| !raw.is_empty())
        .or_else(|| BOUND_ORIGIN.get().cloned())
}

/// True when search can reach the HNSW sidecar (env origin or a bound seam).
pub(crate) fn hnsw_worker_is_bound() -> bool {
    worker_origin().is_some()
}

/// What the worker returned for one ANN query.
#[derive(Debug, Clone)]
pub struct HnswReply {
    pub ids: Vec<String>,
    pub index: String,
    pub size: i64,
    pub store_size: i64,
    pub stale: bool,
    pub detail: Option<String>,
}

/// Try the sidecar. `Err` is the honest fallback reason.
pub fn query_worker(model_id: &str, dim: i64, vector: &[f64], k: i64) -> Result<HnswReply, String> {
    let origin = worker_origin()
        .ok_or_else(|| format!("{WORKER_ORIGIN_ENV} is not set and no worker seam is bound"))?;
    let wanted = k.clamp(1, HNSW_WORKER_K_CAP);
    let body = json!({
        "embedding_model": model_id,
        "embedding_dim": dim,
        "vector": vector.iter().map(|v| *v as f32).collect::<Vec<f32>>(),
        "k": wanted,
    });
    let client =
        WorkerSeamClient::new(&origin).map_err(|error| format!("worker client: {error}"))?;
    let reply = block_on_json(&client, "/worker/vector/query", &body)?;
    let index = reply
        .get("index")
        .and_then(Value::as_str)
        .unwrap_or("none")
        .to_string();
    if index != "hnsw" {
        let detail = reply
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("worker reported no usable HNSW index")
            .to_string();
        return Err(format!("index absent ({index}): {detail}"));
    }
    let stale = reply.get("stale").and_then(Value::as_bool).unwrap_or(false);
    if stale {
        let detail = reply
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("sidecar size does not match the vector store")
            .to_string();
        return Err(format!("index stale: {detail}"));
    }
    let ids = reply
        .get("ids")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .filter(|id| !id.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if ids.is_empty() {
        return Err("worker returned no HNSW candidates".into());
    }
    Ok(HnswReply {
        ids,
        index,
        size: reply.get("size").and_then(Value::as_i64).unwrap_or(0),
        store_size: reply.get("store_size").and_then(Value::as_i64).unwrap_or(0),
        stale,
        detail: reply
            .get("detail")
            .and_then(Value::as_str)
            .map(str::to_string),
    })
}

fn block_on_json(client: &WorkerSeamClient, path: &str, body: &Value) -> Result<Value, String> {
    let fut = client.post_json(path, body);
    match tokio::runtime::Handle::try_current() {
        Ok(handle) => tokio::task::block_in_place(|| handle.block_on(fut)),
        Err(_) => tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| format!("tokio runtime: {error}"))?
            .block_on(fut),
    }
    .map_err(|error| format!("worker {path}: {error}"))
}

/// Load the named vector rows (same SELECT the brute scan uses).
pub fn load_rows(
    conn: &Connection,
    model_id: &str,
    dim: i64,
    ids: &[String],
) -> Result<Vec<VectorRow>, CoreError> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let sql = format!("{VECTOR_ROW_SELECT} AND ve.item_id IN ({placeholders})");
    let mut params: Vec<&dyn rusqlite::ToSql> = vec![&model_id, &dim];
    for id in ids {
        params.push(id);
    }
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt
        .query_map(params.as_slice(), vector_row_from)?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

fn sort_key(item: &Map<String, Value>) -> (f64, String) {
    (
        item.get("score").and_then(Value::as_f64).unwrap_or(0.0),
        item.get("updated_at")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
    )
}

/// Worker query + exact rescore, or the fallback reason.
pub fn try_hnsw(
    query: &str,
    query_vector: &[f64],
    conn: &Connection,
    model: &LocalEmbeddingModel,
    limit: i64,
    min_score: f64,
) -> Result<Value, String> {
    let candidates_total: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM vector_embeddings WHERE embedding_model=? AND embedding_dim=?",
            rusqlite::params![model.model_id(), model.dim() as i64],
            |row| row.get(0),
        )
        .map_err(|error| format!("store count: {error}"))?;
    if candidates_total <= 0 {
        return Err("vector store is empty for this embedding identity".into());
    }
    let reply = query_worker(
        model.model_id(),
        model.dim() as i64,
        query_vector,
        candidate_k(limit),
    )?;
    if reply.size > 0 && reply.store_size > 0 && reply.size != reply.store_size {
        return Err(format!(
            "identity stale: sidecar size {} vs store {}",
            reply.size, reply.store_size
        ));
    }
    rescore(
        conn,
        model,
        query,
        query_vector,
        limit,
        min_score,
        &reply,
        candidates_total,
    )
    .map_err(|error| format!("rescore: {error}"))
}

/// Exact-rescore the worker's candidate ids into a `vector_search` payload.
#[allow(clippy::too_many_arguments)]
pub fn rescore(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    query_vector: &[f64],
    limit: i64,
    min_score: f64,
    reply: &HnswReply,
    candidates_total: i64,
) -> Result<Value, CoreError> {
    let rows = load_rows(conn, model.model_id(), model.dim() as i64, &reply.ids)?;
    if rows.is_empty() {
        return Err(CoreError::InvalidRequest(
            "HNSW candidate ids were not in vector_embeddings".into(),
        ));
    }
    let mut scored: Vec<Map<String, Value>> = Vec::new();
    for row in &rows {
        let stored = model.decode(&row.embedding, row.embedding_dim.map(|d| d as usize));
        let score = model.similarity(query_vector, &stored)?;
        if score < min_score {
            continue;
        }
        scored.push(vector_match(row, score));
    }
    scored.sort_by(|a, b| {
        sort_key(b)
            .partial_cmp(&sort_key(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    scored.truncate(limit as usize);

    let candidate_count = rows.len() as i64;
    let selection = BackendSelection {
        requested: "hnsw".into(),
        name: "hnsw+rescore".into(),
        backend: "hnsw".into(),
        approx: true,
        exhaustive: false,
        detail: Some(format!(
            "rescored {candidate_count} HNSW candidates (sidecar size {})",
            reply.size
        )),
    };
    let recall = recall_report_custom(
        "hnsw+rescore",
        Some(candidate_count),
        candidates_total,
        candidate_count,
        Some(&format!(
            "approximate recall via HNSW ({candidate_count} candidates of {candidates_total}); \
             ordering is exact over the candidate set"
        )),
        false,
    );

    let mut out = Map::new();
    out.insert("query".into(), Value::String(query.to_string()));
    out.insert(
        "embedding_model".into(),
        Value::String(model.model_id().to_string()),
    );
    out.insert("embedding_dim".into(), Value::from(model.dim() as i64));
    out.insert(
        "matches".into(),
        Value::Array(scored.into_iter().map(Value::Object).collect()),
    );
    out.insert("recall".into(), recall);
    let mut index = selection.as_json().as_object().cloned().unwrap_or_default();
    index.insert("candidates".into(), Value::from(candidate_count));
    index.insert("sidecar_size".into(), Value::from(reply.size));
    if let Some(detail) = &reply.detail {
        if !detail.is_empty() {
            index.insert("sidecar_detail".into(), Value::String(detail.clone()));
        }
    }
    out.insert("index".into(), Value::Object(index));
    Ok(Value::Object(out))
}

/// Fetch up to the worker cap. `k * 8` is the floor; the cap is what
/// keeps recall@10 ≥ 0.95 on a near-duplicate hash store.
pub fn candidate_k(limit: i64) -> i64 {
    (limit * HNSW_CANDIDATE_MULTIPLIER)
        .clamp(64, HNSW_WORKER_K_CAP)
        .max(limit)
}

/// Fallback `index` block: requested HNSW, served brute, reason named.
pub fn fallback_selection(reason: &str) -> BackendSelection {
    BackendSelection {
        requested: "hnsw".into(),
        name: "brute".into(),
        backend: BRUTE_FORCE_BACKEND.into(),
        approx: false,
        exhaustive: true,
        detail: Some(format!(
            "{VECTOR_INDEX_ENV}=hnsw fell back to the exact scan: {reason}"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_k_is_four_times_limit_and_capped() {
        assert_eq!(candidate_k(10), 80);
        assert_eq!(candidate_k(1), 64);
        assert_eq!(candidate_k(80), 200);
    }

    #[test]
    fn fallback_names_the_reason() {
        let json = fallback_selection("worker down").as_json();
        assert_eq!(json["requested"], "hnsw");
        assert_eq!(json["name"], "brute");
        assert_eq!(json["honored"], false);
        assert!(json["detail"]
            .as_str()
            .unwrap()
            .contains("fell back to the exact scan: worker down"));
    }
}
