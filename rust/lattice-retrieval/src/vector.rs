//! Port of `vector_search` (`graph/retrieval_vector/search.py`).
//!
//! Default env is the exact scan. The candidate cap is the part worth reading
//! twice: when it bites, the rows kept are the most recently *indexed* ones —
//! recency, not similarity — so the answer is partial recall, and the `recall`
//! block says so instead of hiding it.
//!
//! `LATTICEAI_VECTOR_INDEX=hnsw` asks the worker sidecar for `k*8` candidates
//! and re-scores them exactly (`hnsw+rescore`). Any failure falls back to this
//! scan and names the reason. Golden suites pin the default env.
//!
//! When the env is the default `brute` *and* the store has at least
//! [`HNSW_AUTO_MIN_ROWS`] vectors *and* a worker origin is bound, search tries
//! the sidecar first. A miss falls through to this scan with the original
//! `brute` index block, so goldens (small fixtures, no worker) stay byte-
//! identical.

use lattice_core::pytext::{citation_locator, clean_text, round6, safe_loads, truncate_chars};
use lattice_core::read::{vector_row_from, VectorRow, VECTOR_ROW_SELECT};
use lattice_core::{CoreError, LocalEmbeddingModel};
use rusqlite::Connection;
use serde_json::{Map, Value};

/// `LATTICEAI_VECTOR_MAX_CANDIDATES`.
pub const VECTOR_MAX_CANDIDATES_ENV: &str = "LATTICEAI_VECTOR_MAX_CANDIDATES";
/// `LATTICEAI_VECTOR_INDEX`.
pub const VECTOR_INDEX_ENV: &str = "LATTICEAI_VECTOR_INDEX";
/// `search.DEFAULT_VECTOR_MAX_CANDIDATES`.
pub const DEFAULT_VECTOR_MAX_CANDIDATES: i64 = 10_000;
/// `search.VECTOR_MAX_CANDIDATES_CEILING`.
pub const VECTOR_MAX_CANDIDATES_CEILING: i64 = 500_000;
/// `vector_index.brute_force.BRUTE_FORCE_BACKEND`.
pub const BRUTE_FORCE_BACKEND: &str = "bruteforce-cosine";
/// Row count at which default brute search tries the HNSW sidecar first.
///
/// Below this the exact scan is cheap and golden-pinned. Above it, a bound
/// worker sidecar is asked for candidates and the rows are re-scored exactly.
pub const HNSW_AUTO_MIN_ROWS: i64 = 512;

/// `vector_index.BackendSelection` for the backends this crate can honour.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendSelection {
    pub requested: String,
    pub name: String,
    pub backend: String,
    pub approx: bool,
    pub exhaustive: bool,
    pub detail: Option<String>,
}

impl BackendSelection {
    /// `BackendSelection.as_dict()` — including the derived `honored` flag.
    pub fn as_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("requested".into(), Value::String(self.requested.clone()));
        map.insert("backend".into(), Value::String(self.backend.clone()));
        map.insert("name".into(), Value::String(self.name.clone()));
        map.insert("approx".into(), Value::Bool(self.approx));
        map.insert("exhaustive".into(), Value::Bool(self.exhaustive));
        map.insert("honored".into(), Value::Bool(self.requested == self.name));
        map.insert(
            "detail".into(),
            self.detail
                .clone()
                .map(Value::String)
                .unwrap_or(Value::Null),
        );
        Value::Object(map)
    }
}

fn brute(requested: &str, detail: Option<String>) -> BackendSelection {
    BackendSelection {
        requested: requested.to_string(),
        name: "brute".to_string(),
        backend: BRUTE_FORCE_BACKEND.to_string(),
        approx: false,
        exhaustive: true,
        detail,
    }
}

/// `vector_index.resolve_vector_index`.
///
/// `hnsw` is honoured only when the worker sidecar answers; the scan below
/// still falls back to brute and says why. `quantized` stays unimplemented.
pub fn resolve_vector_index() -> BackendSelection {
    let raw = std::env::var(VECTOR_INDEX_ENV).unwrap_or_default();
    let name = raw.trim().to_lowercase();
    let name = if name.is_empty() {
        "brute".to_string()
    } else {
        name
    };
    match name.as_str() {
        "brute" => brute("brute", None),
        "hnsw" => BackendSelection {
            requested: "hnsw".into(),
            name: "hnsw".into(),
            backend: "hnsw".into(),
            approx: true,
            exhaustive: false,
            detail: None,
        },
        "quantized" => brute(
            "quantized",
            Some(
                "quantized is not implemented in lattice-retrieval — using the exact \
                 brute-force scan"
                    .into(),
            ),
        ),
        other => brute(
            other,
            Some(format!(
                "unknown vector index backend '{other}'; expected one of brute, quantized, \
                 hnsw — using the exact brute-force scan"
            )),
        ),
    }
}

/// `_configured_vector_max_candidates` — `None` means "scan everything".
pub fn configured_max_candidates() -> Option<i64> {
    let raw = std::env::var(VECTOR_MAX_CANDIDATES_ENV).unwrap_or_default();
    if raw.trim().is_empty() {
        return Some(DEFAULT_VECTOR_MAX_CANDIDATES);
    }
    let Ok(value) = raw.trim().parse::<i64>() else {
        return Some(DEFAULT_VECTOR_MAX_CANDIDATES);
    };
    if value <= 0 {
        return None;
    }
    Some(value.min(VECTOR_MAX_CANDIDATES_CEILING))
}

/// `_vector_candidate_cap` — never scan fewer rows than the caller will receive.
pub fn candidate_cap(limit: i64) -> Option<i64> {
    configured_max_candidates().map(|cap| cap.max(limit))
}

fn recall_report(
    backend: &str,
    cap: Option<i64>,
    total: i64,
    scanned: i64,
    approx_detail: Option<&str>,
) -> Value {
    recall_report_custom(backend, cap, total, scanned, approx_detail, true)
}

/// Same shape as [`recall_report`]; `recency_cut` is the brute-scan wording.
pub(crate) fn recall_report_custom(
    backend: &str,
    cap: Option<i64>,
    total: i64,
    scanned: i64,
    approx_detail: Option<&str>,
    recency_cut: bool,
) -> Value {
    let truncated = scanned < total;
    let detail = if truncated && recency_cut {
        Some(format!(
            "partial recall: scored the {scanned} most recently indexed vectors of {total}. \
             The cut is by index recency, not similarity, so older matches were never \
             compared. Raise {VECTOR_MAX_CANDIDATES_ENV} (0 = scan everything), or switch to \
             an index that covers the whole set: {VECTOR_INDEX_ENV}=hnsw (needs the optional \
             hnsw extra) or install sqlite-vec."
        ))
    } else {
        approx_detail.map(str::to_string)
    };
    let mut map = Map::new();
    map.insert("backend".into(), Value::String(backend.to_string()));
    map.insert(
        "max_candidates".into(),
        cap.map(Value::from).unwrap_or(Value::Null),
    );
    map.insert("candidates_total".into(), Value::from(total));
    map.insert("candidates_scanned".into(), Value::from(scanned));
    map.insert("truncated".into(), Value::Bool(truncated));
    map.insert(
        "detail".into(),
        detail.map(Value::String).unwrap_or(Value::Null),
    );
    Value::Object(map)
}

fn truthy(value: &Option<String>) -> bool {
    value.as_deref().map(|v| !v.is_empty()).unwrap_or(false)
}

fn opt_json(value: &Option<String>) -> Value {
    value
        .as_ref()
        .map(|v| Value::String(v.clone()))
        .unwrap_or(Value::Null)
}

/// `_VectorSearchMixin._vector_match` — one scored row → one match.
pub fn vector_match(row: &VectorRow, score: f64) -> Map<String, Value> {
    let is_chunk = row.item_type.as_deref() == Some("chunk");
    let summary = if is_chunk && truthy(&row.chunk_text) {
        row.chunk_text.clone()
    } else {
        row.node_summary.clone()
    };
    let parent_metadata = safe_loads(row.parent_metadata.as_deref());
    let node_metadata = safe_loads(row.node_metadata.as_deref());
    let chunk_metadata = if is_chunk {
        safe_loads(row.chunk_metadata.as_deref())
    } else {
        Map::new()
    };
    let locator = citation_locator(&chunk_metadata);

    let mut metadata = if is_chunk {
        parent_metadata
    } else {
        node_metadata
    };
    metadata.insert(
        "vector".into(),
        Value::Object(safe_loads(row.vector_metadata.as_deref())),
    );
    metadata.insert("parent_node_id".into(), opt_json(&row.parent_node_id));
    metadata.insert("parent_type".into(), opt_json(&row.parent_type));
    if !chunk_metadata.is_empty() {
        metadata.insert("chunk".into(), Value::Object(chunk_metadata));
    }
    if !locator.is_empty() {
        metadata.insert("locator".into(), Value::String(locator));
    }

    let mut item = Map::new();
    item.insert("id".into(), Value::String(row.item_id.clone()));
    item.insert(
        "node_id".into(),
        if is_chunk && truthy(&row.parent_node_id) {
            opt_json(&row.parent_node_id)
        } else {
            opt_json(&row.source_node)
        },
    );
    item.insert("item_type".into(), opt_json(&row.item_type));
    item.insert(
        "type".into(),
        if is_chunk {
            Value::String("Chunk".into())
        } else {
            opt_json(&row.node_type)
        },
    );
    item.insert(
        "title".into(),
        if is_chunk && truthy(&row.parent_title) {
            opt_json(&row.parent_title)
        } else {
            opt_json(&row.node_title)
        },
    );
    item.insert(
        "summary".into(),
        Value::String(truncate_chars(
            &clean_text(summary.as_deref().unwrap_or("")),
            1000,
        )),
    );
    item.insert("score".into(), Value::from(round6(score)));
    item.insert("metadata".into(), Value::Object(metadata));
    item.insert(
        "updated_at".into(),
        if is_chunk && truthy(&row.parent_updated_at) {
            opt_json(&row.parent_updated_at)
        } else {
            opt_json(&row.node_updated_at)
        },
    );
    item
}

/// Whether this search should ask the HNSW sidecar before the exact scan.
pub fn should_try_hnsw(selection: &BackendSelection, candidates_total: i64) -> bool {
    if selection.requested == "hnsw" && selection.name == "hnsw" {
        return true;
    }
    selection.requested == "brute"
        && selection.detail.is_none()
        && candidates_total >= HNSW_AUTO_MIN_ROWS
        && crate::vector_hnsw::hnsw_worker_is_bound()
}

/// `KnowledgeGraphStore.vector_search(query, limit=…, min_score=…)`.
pub fn vector_search(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    query: &str,
    limit: i64,
    min_score: f64,
) -> Result<Value, CoreError> {
    let query = query.trim().to_string();
    let limit = if limit == 0 { 30 } else { limit }.clamp(1, 100);
    let cap = candidate_cap(limit);
    let selection = resolve_vector_index();
    let backend = selection.backend.clone();

    if query.is_empty() {
        // No query means no index was consulted, so there is nothing to report:
        // the empty return deliberately carries no `index` block.
        let mut out = Map::new();
        out.insert("query".into(), Value::String(query));
        out.insert("matches".into(), Value::Array(Vec::new()));
        out.insert("recall".into(), recall_report(&backend, cap, 0, 0, None));
        return Ok(Value::Object(out));
    }

    let query_vector = model.embed(&query);
    let model_id = model.model_id().to_string();
    let dim = model.dim() as i64;

    let candidates_total: i64 = conn.query_row(
        "SELECT COUNT(*) AS c FROM vector_embeddings WHERE embedding_model=? AND embedding_dim=?",
        rusqlite::params![model_id, dim],
        |row| row.get(0),
    )?;

    let mut index_block = selection.as_json();
    if should_try_hnsw(&selection, candidates_total) {
        match crate::vector_hnsw::try_hnsw(&query, &query_vector, conn, model, limit, min_score) {
            Ok(payload) => return Ok(payload),
            Err(reason) => {
                if selection.requested == "hnsw" {
                    index_block = crate::vector_hnsw::fallback_selection(&reason).as_json();
                }
            }
        }
    }

    let mut sql = format!("{VECTOR_ROW_SELECT} ORDER BY ve.indexed_at DESC");
    if cap.is_some() {
        sql.push_str(" LIMIT ?");
    }
    let mut stmt = conn.prepare(&sql)?;
    let rows: Vec<VectorRow> = match cap {
        Some(cap) => stmt
            .query_map(rusqlite::params![model_id, dim, cap], |row| {
                vector_row_from(row)
            })?
            .filter_map(Result::ok)
            .collect(),
        None => stmt
            .query_map(rusqlite::params![model_id, dim], vector_row_from)?
            .filter_map(Result::ok)
            .collect(),
    };

    let recall = recall_report(&backend, cap, candidates_total, rows.len() as i64, None);

    // Rows are walked in index order (not score order) so the stable sort below
    // sees the same input ordering the Python scan produces.
    let mut scored: Vec<Map<String, Value>> = Vec::new();
    for row in &rows {
        let stored = model.decode(&row.embedding, row.embedding_dim.map(|d| d as usize));
        let score = model.similarity(&query_vector, &stored)?;
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

    let mut out = Map::new();
    out.insert("query".into(), Value::String(query));
    out.insert("embedding_model".into(), Value::String(model_id));
    out.insert("embedding_dim".into(), Value::from(dim));
    out.insert(
        "matches".into(),
        Value::Array(scored.into_iter().map(Value::Object).collect()),
    );
    out.insert("recall".into(), recall);
    out.insert("index".into(), index_block);
    Ok(Value::Object(out))
}

/// `(score, updated_at or "")` — the sort key of `_vector_search_scan`.
fn sort_key(item: &Map<String, Value>) -> (f64, String) {
    (
        item.get("score").and_then(Value::as_f64).unwrap_or(0.0),
        item.get("updated_at")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_hnsw_stays_off_for_small_stores_and_unbound_workers() {
        assert!(!should_try_hnsw(&brute("brute", None), 10));
        assert!(
            !should_try_hnsw(&brute("brute", None), HNSW_AUTO_MIN_ROWS),
            "a store at the threshold still needs a bound worker"
        );
        assert!(should_try_hnsw(
            &BackendSelection {
                requested: "hnsw".into(),
                name: "hnsw".into(),
                backend: "hnsw".into(),
                approx: true,
                exhaustive: false,
                detail: None,
            },
            1
        ));
        assert!(!should_try_hnsw(
            &brute("quantized", Some("not implemented".into())),
            HNSW_AUTO_MIN_ROWS
        ));
    }

    #[test]
    fn backend_selection_reports_what_it_did() {
        let selection = brute("brute", None);
        let json = selection.as_json();
        assert_eq!(json["backend"], BRUTE_FORCE_BACKEND);
        assert_eq!(json["honored"], true);
        assert_eq!(json["approx"], false);
        assert_eq!(json["exhaustive"], true);
        assert_eq!(json["detail"], Value::Null);
        let fallback = brute("hnsw", Some("nope".into()));
        assert_eq!(fallback.as_json()["honored"], false);
        assert_eq!(fallback.as_json()["detail"], "nope");
        assert_ne!(fallback, selection);
        assert!(format!("{selection:?}").contains("brute"));
    }

    #[test]
    fn the_cap_never_scans_fewer_rows_than_the_caller_receives() {
        assert_eq!(
            configured_max_candidates(),
            Some(DEFAULT_VECTOR_MAX_CANDIDATES)
        );
        assert_eq!(candidate_cap(5), Some(DEFAULT_VECTOR_MAX_CANDIDATES));
        assert_eq!(candidate_cap(50_000), Some(50_000));
    }

    #[test]
    fn recall_is_honest_about_a_truncated_scan() {
        let complete = recall_report("bruteforce-cosine", Some(10), 10, 10, None);
        assert_eq!(complete["truncated"], false);
        assert_eq!(complete["detail"], Value::Null);
        let approx = recall_report("bruteforce-cosine", Some(10), 10, 10, Some("estimates"));
        assert_eq!(approx["detail"], "estimates");
        let partial = recall_report("bruteforce-cosine", Some(3), 10, 3, None);
        assert_eq!(partial["truncated"], true);
        assert!(partial["detail"]
            .as_str()
            .unwrap()
            .starts_with("partial recall: scored the 3"));
        let uncapped = recall_report("bruteforce-cosine", None, 0, 0, None);
        assert_eq!(uncapped["max_candidates"], Value::Null);
    }

    fn vector_db() -> (tempfile::TempDir, Connection, LocalEmbeddingModel) {
        let model = LocalEmbeddingModel::new(384);
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT,
                                 metadata_json TEXT);
             CREATE TABLE vector_embeddings(item_id TEXT PRIMARY KEY, item_type TEXT,
               source_node TEXT, embedding BLOB, embedding_dim INT, embedding_model TEXT,
               metadata_json TEXT, indexed_at TEXT);",
        )
        .unwrap();
        let node_vec = model.encode(&model.embed("retrieval ranking"));
        let chunk_vec = model.encode(&model.embed("chunk about ranking"));
        conn.execute(
            "INSERT INTO nodes VALUES ('n1','Document','Doc','Node summary','{\"a\":1}','2026-01-01T00:00:00')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO chunks VALUES ('c1','n1','chunk about ranking','{\"page\":2}')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO vector_embeddings VALUES ('n1','node','n1',?,384,?,'{}','2026-02-01T00:00:02')",
            rusqlite::params![node_vec, model.model_id()],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO vector_embeddings VALUES ('c1','chunk','c1',?,384,?,'{\"parent_source_node\":\"n1\"}','2026-02-01T00:00:01')",
            rusqlite::params![chunk_vec, model.model_id()],
        )
        .unwrap();
        (dir, conn, model)
    }

    #[test]
    fn an_empty_query_reports_no_index_consultation() {
        let (_dir, conn, model) = vector_db();
        let out = vector_search(&conn, &model, "   ", 30, 0.0).unwrap();
        assert_eq!(out["matches"].as_array().unwrap().len(), 0);
        assert_eq!(out["recall"]["candidates_total"], 0);
        assert!(out.get("index").is_none());
        assert!(out.get("embedding_model").is_none());
    }

    #[test]
    fn chunk_rows_roll_up_to_their_parent_and_carry_a_locator() {
        let (_dir, conn, model) = vector_db();
        let out = vector_search(&conn, &model, "ranking", 30, 0.0).unwrap();
        let matches = out["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 2);
        let chunk = matches.iter().find(|m| m["id"] == "c1").unwrap();
        assert_eq!(chunk["type"], "Chunk");
        assert_eq!(chunk["node_id"], "n1");
        assert_eq!(chunk["title"], "Doc");
        assert_eq!(chunk["summary"], "chunk about ranking");
        assert_eq!(chunk["updated_at"], "2026-01-01T00:00:00");
        assert_eq!(chunk["metadata"]["locator"], "p.2");
        assert_eq!(
            chunk["metadata"]["a"], 1,
            "chunk inherits the parent's metadata"
        );
        assert_eq!(chunk["metadata"]["chunk"]["page"], 2);
        assert_eq!(chunk["metadata"]["vector"]["parent_source_node"], "n1");
        let node = matches.iter().find(|m| m["id"] == "n1").unwrap();
        assert_eq!(node["type"], "Document");
        assert_eq!(node["summary"], "Node summary");
        assert_eq!(node["metadata"]["parent_node_id"], Value::Null);
        assert_eq!(out["recall"]["candidates_scanned"], 2);
        assert_eq!(out["index"]["name"], "brute");
    }

    #[test]
    fn the_min_score_floor_drops_rows() {
        let (_dir, conn, model) = vector_db();
        let out = vector_search(&conn, &model, "ranking", 30, 0.95).unwrap();
        assert!(out["matches"].as_array().unwrap().is_empty());
        // A limit of 1 still scans both rows but returns one.
        let out = vector_search(&conn, &model, "ranking", 1, 0.0).unwrap();
        assert_eq!(out["matches"].as_array().unwrap().len(), 1);
        assert_eq!(out["recall"]["candidates_scanned"], 2);
    }

    #[test]
    fn a_dimension_mismatch_is_an_error_the_caller_can_read() {
        let (_dir, conn, model) = vector_db();
        conn.execute(
            "UPDATE vector_embeddings SET embedding=x'0000000000000000' WHERE item_id='n1'",
            [],
        )
        .unwrap();
        let err = vector_search(&conn, &model, "ranking", 30, 0.0).unwrap_err();
        assert!(format!("{err}").contains("embedding dimension mismatch: 384 vs 2"));
    }
}
