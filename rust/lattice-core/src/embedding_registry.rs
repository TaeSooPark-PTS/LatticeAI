//! Embedding Model Registry, Shadow Index Re-Indexing, Atomic Cutover & Instant Rollback (P1.4).
//!
//! Guarantees that vectors produced by incompatible embedding models (different
//! `model_id`, `version`, `dimensions`, `normalization`, or `distance_metric`)
//! are never compared in the same vector space, and enables resumable
//! background shadow re-indexing with quality-gated atomic cutover and instant
//! rollback preserving the previous generation.

use std::collections::BTreeMap;

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

use crate::db::CoreError;
use crate::embedding_contract::EmbeddingIndexContract;
use crate::graph_write::clock::{Clock, SystemClock};

fn utc_now_iso() -> String {
    SystemClock.now_iso()
}

fn encode_vector_f32(values: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(values.len() * 4);
    for v in values {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

/// Lifecycle state of a registered embedding model or index generation.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingModelStatus {
    Active,
    Shadow,
    Deprecated,
    Retired,
}

impl EmbeddingModelStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Shadow => "shadow",
            Self::Deprecated => "deprecated",
            Self::Retired => "retired",
        }
    }

    pub fn parse(raw: &str) -> Self {
        match raw.trim().to_ascii_lowercase().as_str() {
            "active" => Self::Active,
            "shadow" => Self::Shadow,
            "deprecated" => Self::Deprecated,
            _ => Self::Retired,
        }
    }
}

/// Complete specification of a registered embedding model.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EmbeddingModelSpec {
    pub model_id: String,
    pub version: String,
    pub provider: String,
    pub dimensions: usize,
    pub normalization: String,
    pub distance_metric: String,
    pub languages: Vec<String>,
    pub modalities: Vec<String>,
    pub semantic: bool,
    pub status: EmbeddingModelStatus,
}

impl EmbeddingModelSpec {
    /// Two specs can share a vector space only when model, version, dim,
    /// normalization, and distance metric all match strictly.
    pub fn is_space_compatible_with(&self, other: &Self) -> bool {
        self.model_id == other.model_id
            && self.version == other.version
            && self.dimensions == other.dimensions
            && self
                .normalization
                .eq_ignore_ascii_case(&other.normalization)
            && self
                .distance_metric
                .eq_ignore_ascii_case(&other.distance_metric)
    }

    pub fn to_index_contract(&self, index_version: u32) -> EmbeddingIndexContract {
        EmbeddingIndexContract {
            provider: self.provider.clone(),
            model_id: self.model_id.clone(),
            model_revision: Some(self.version.clone()),
            dimension: self.dimensions,
            normalization: self.normalization.clone(),
            passage_mode: "passage".to_string(),
            query_mode: "query".to_string(),
            index_version,
            grade: if self.semantic {
                "production".to_string()
            } else {
                "fallback".to_string()
            },
            semantic: self.semantic,
        }
    }
}

/// Built-in production embedding model catalog entries.
pub fn builtin_embedding_registry() -> Vec<EmbeddingModelSpec> {
    vec![
        EmbeddingModelSpec {
            model_id: "BAAI/bge-m3".to_string(),
            version: "1.0.0".to_string(),
            provider: "sentence_transformers".to_string(),
            dimensions: 1024,
            normalization: "l2".to_string(),
            distance_metric: "cosine".to_string(),
            languages: vec![
                "ko".to_string(),
                "en".to_string(),
                "multilingual".to_string(),
            ],
            modalities: vec!["text".to_string()],
            semantic: true,
            status: EmbeddingModelStatus::Active,
        },
        EmbeddingModelSpec {
            model_id: "intfloat/multilingual-e5-small".to_string(),
            version: "1.0.0".to_string(),
            provider: "sentence_transformers".to_string(),
            dimensions: 384,
            normalization: "l2".to_string(),
            distance_metric: "cosine".to_string(),
            languages: vec![
                "ko".to_string(),
                "en".to_string(),
                "multilingual".to_string(),
            ],
            modalities: vec!["text".to_string()],
            semantic: true,
            status: EmbeddingModelStatus::Shadow,
        },
        EmbeddingModelSpec {
            model_id: "nomic-embed-text".to_string(),
            version: "1.5".to_string(),
            provider: "ollama".to_string(),
            dimensions: 768,
            normalization: "l2".to_string(),
            distance_metric: "cosine".to_string(),
            languages: vec!["en".to_string()],
            modalities: vec!["text".to_string()],
            semantic: true,
            status: EmbeddingModelStatus::Shadow,
        },
        EmbeddingModelSpec {
            model_id: "local-hash-384".to_string(),
            version: "1.0".to_string(),
            provider: "local_hash".to_string(),
            dimensions: 384,
            normalization: "l2".to_string(),
            distance_metric: "cosine".to_string(),
            languages: vec!["multilingual".to_string()],
            modalities: vec!["text".to_string()],
            semantic: false,
            status: EmbeddingModelStatus::Deprecated,
        },
    ]
}

/// Resumable shadow re-indexing job state.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ShadowReindexJob {
    pub generation_id: String,
    pub model_id: String,
    pub model_version: String,
    pub dimensions: usize,
    pub normalization: String,
    pub status: String,
    pub cursor_item_id: Option<String>,
    pub processed_items: usize,
    pub total_items: usize,
    pub shadow_recall_at_5: Option<f64>,
    pub active_recall_at_5: Option<f64>,
    pub created_at: String,
    pub updated_at: String,
}

/// Evaluation gate comparing shadow index readiness against active index.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ShadowEvaluationGate {
    pub generation_id: String,
    pub coverage_ratio: f64,
    pub active_recall_at_5: f64,
    pub shadow_recall_at_5: f64,
    pub passed: bool,
    pub rationale: String,
}

/// Result of an atomic shadow cutover or rollback.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CutoverReceipt {
    pub active_generation_id: String,
    pub previous_generation_id: Option<String>,
    pub vectors_swapped: usize,
    pub active_model_id: String,
    pub active_model_version: String,
}

/// Ensure additive tables for the Embedding Model Registry & Shadow Index exist.
pub fn ensure_embedding_registry_schema(conn: &Connection) -> Result<(), CoreError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS embedding_model_registry (
            model_id TEXT NOT NULL,
            version TEXT NOT NULL,
            provider TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            normalization TEXT NOT NULL DEFAULT 'l2',
            distance_metric TEXT NOT NULL DEFAULT 'cosine',
            languages_json TEXT NOT NULL DEFAULT '[]',
            modalities_json TEXT NOT NULL DEFAULT '[\"text\"]',
            semantic INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'shadow',
            registered_at TEXT NOT NULL,
            PRIMARY KEY (model_id, version)
        );
        CREATE TABLE IF NOT EXISTS vector_index_generations (
            generation_id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            normalization TEXT NOT NULL DEFAULT 'l2',
            status TEXT NOT NULL DEFAULT 'building',
            cursor_item_id TEXT,
            processed_items INTEGER NOT NULL DEFAULT 0,
            total_items INTEGER NOT NULL DEFAULT 0,
            shadow_recall_at_5 REAL,
            active_recall_at_5 REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vector_embeddings_shadow (
            generation_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            source_node TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            model_version TEXT NOT NULL,
            normalization TEXT NOT NULL DEFAULT 'l2',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (generation_id, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_vec_shadow_gen ON vector_embeddings_shadow(generation_id);",
    )?;
    Ok(())
}

/// Register or update an embedding model in the persistent SQLite registry.
pub fn register_embedding_model(
    conn: &Connection,
    spec: &EmbeddingModelSpec,
) -> Result<(), CoreError> {
    ensure_embedding_registry_schema(conn)?;
    let now = utc_now_iso();
    let langs = serde_json::to_string(&spec.languages).unwrap_or_else(|_| "[]".to_string());
    let mods = serde_json::to_string(&spec.modalities).unwrap_or_else(|_| "[\"text\"]".to_string());
    conn.execute(
        "INSERT INTO embedding_model_registry(
            model_id, version, provider, dimensions, normalization, distance_metric,
            languages_json, modalities_json, semantic, status, registered_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
         ON CONFLICT(model_id, version) DO UPDATE SET
            provider = excluded.provider,
            dimensions = excluded.dimensions,
            normalization = excluded.normalization,
            distance_metric = excluded.distance_metric,
            languages_json = excluded.languages_json,
            modalities_json = excluded.modalities_json,
            semantic = excluded.semantic,
            status = excluded.status",
        params![
            spec.model_id,
            spec.version,
            spec.provider,
            spec.dimensions as i64,
            spec.normalization,
            spec.distance_metric,
            langs,
            mods,
            if spec.semantic { 1 } else { 0 },
            spec.status.as_str(),
            now,
        ],
    )?;
    Ok(())
}

/// List registered embedding models (seeding built-ins if the registry table is empty).
pub fn list_embedding_models(conn: &Connection) -> Result<Vec<EmbeddingModelSpec>, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    let count: i64 = conn.query_row("SELECT COUNT(*) FROM embedding_model_registry", [], |r| {
        r.get(0)
    })?;
    if count == 0 {
        for spec in builtin_embedding_registry() {
            register_embedding_model(conn, &spec)?;
        }
    }
    let mut stmt = conn.prepare(
        "SELECT model_id, version, provider, dimensions, normalization, distance_metric,
                languages_json, modalities_json, semantic, status
         FROM embedding_model_registry
         ORDER BY model_id ASC, version ASC",
    )?;
    let rows = stmt
        .query_map([], |r| {
            let langs_raw: String = r.get(6)?;
            let mods_raw: String = r.get(7)?;
            let status_raw: String = r.get(9)?;
            Ok(EmbeddingModelSpec {
                model_id: r.get(0)?,
                version: r.get(1)?,
                provider: r.get(2)?,
                dimensions: r.get::<_, i64>(3)?.max(0) as usize,
                normalization: r.get(4)?,
                distance_metric: r.get(5)?,
                languages: serde_json::from_str(&langs_raw).unwrap_or_default(),
                modalities: serde_json::from_str(&mods_raw).unwrap_or_default(),
                semantic: r.get::<_, i64>(8)? != 0,
                status: EmbeddingModelStatus::parse(&status_raw),
            })
        })?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

/// Guard ensuring two models belong to the same compatible vector space before similarity search.
pub fn assert_embedding_space_compatible(
    query_spec: &EmbeddingModelSpec,
    index_spec: &EmbeddingModelSpec,
) -> Result<(), CoreError> {
    if !query_spec.is_space_compatible_with(index_spec) {
        return Err(CoreError::InvalidRequest(format!(
            "incompatible embedding spaces: query={}@{} ({}d/{}/{}) vs index={}@{} ({}d/{}/{})",
            query_spec.model_id,
            query_spec.version,
            query_spec.dimensions,
            query_spec.normalization,
            query_spec.distance_metric,
            index_spec.model_id,
            index_spec.version,
            index_spec.dimensions,
            index_spec.normalization,
            index_spec.distance_metric,
        )));
    }
    Ok(())
}

/// Start or resume a shadow re-indexing generation.
pub fn start_shadow_reindex(
    conn: &Connection,
    generation_id: &str,
    spec: &EmbeddingModelSpec,
    total_items: usize,
) -> Result<ShadowReindexJob, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    register_embedding_model(conn, spec)?;
    let now = utc_now_iso();
    conn.execute(
        "INSERT INTO vector_index_generations(
            generation_id, model_id, model_version, dimensions, normalization,
            status, cursor_item_id, processed_items, total_items, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, 'building', NULL, 0, ?6, ?7, ?7)
         ON CONFLICT(generation_id) DO UPDATE SET
            status = CASE WHEN vector_index_generations.status = 'active' THEN 'active' ELSE 'building' END,
            total_items = excluded.total_items,
            updated_at = excluded.updated_at",
        params![
            generation_id,
            spec.model_id,
            spec.version,
            spec.dimensions as i64,
            spec.normalization,
            total_items as i64,
            now,
        ],
    )?;
    load_shadow_job(conn, generation_id)?
        .ok_or_else(|| CoreError::InvalidRequest("shadow job missing after upsert".to_string()))
}

/// Write a resumable batch of vectors into `vector_embeddings_shadow` and advance the cursor atomically.
pub fn write_shadow_batch(
    conn: &mut Connection,
    generation_id: &str,
    spec: &EmbeddingModelSpec,
    batch: &[(String, String, String, String, Vec<f32>)],
) -> Result<ShadowReindexJob, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    let now = utc_now_iso();
    let tx = conn.transaction()?;
    let mut last_item: Option<String> = None;
    for (item_id, item_type, source_node, text_hash, vec_f32) in batch {
        if vec_f32.len() != spec.dimensions {
            return Err(CoreError::InvalidRequest(format!(
                "shadow vector dim {} != spec dim {}",
                vec_f32.len(),
                spec.dimensions
            )));
        }
        let blob = encode_vector_f32(vec_f32);
        tx.execute(
            "INSERT INTO vector_embeddings_shadow(
                generation_id, item_id, item_type, source_node, text_hash, embedding,
                embedding_dim, embedding_model, model_version, normalization, metadata_json, indexed_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, '{}', ?11)
             ON CONFLICT(generation_id, item_id) DO UPDATE SET
                text_hash = excluded.text_hash,
                embedding = excluded.embedding,
                indexed_at = excluded.indexed_at",
            params![
                generation_id,
                item_id,
                item_type,
                source_node,
                text_hash,
                blob,
                spec.dimensions as i64,
                spec.model_id,
                spec.version,
                spec.normalization,
                now,
            ],
        )?;
        last_item = Some(item_id.clone());
    }
    let processed: i64 = tx.query_row(
        "SELECT COUNT(*) FROM vector_embeddings_shadow WHERE generation_id = ?1",
        params![generation_id],
        |r| r.get(0),
    )?;
    tx.execute(
        "UPDATE vector_index_generations
         SET cursor_item_id = COALESCE(?2, cursor_item_id),
             processed_items = ?3,
             updated_at = ?4
         WHERE generation_id = ?1",
        params![generation_id, last_item, processed, now],
    )?;
    tx.commit()?;
    load_shadow_job(conn, generation_id)?
        .ok_or_else(|| CoreError::InvalidRequest("shadow job missing".to_string()))
}

/// Evaluate shadow index readiness against coverage and recall thresholds before cutover.
pub fn evaluate_shadow_generation(
    conn: &Connection,
    generation_id: &str,
    active_recall_at_5: f64,
    shadow_recall_at_5: f64,
    min_coverage_ratio: f64,
    max_recall_regression: f64,
) -> Result<ShadowEvaluationGate, CoreError> {
    let job = load_shadow_job(conn, generation_id)?
        .ok_or_else(|| CoreError::InvalidRequest(format!("unknown generation: {generation_id}")))?;
    let coverage_ratio = if job.total_items == 0 {
        1.0
    } else {
        (job.processed_items as f64 / job.total_items as f64).min(1.0)
    };
    let recall_ok = shadow_recall_at_5 + max_recall_regression >= active_recall_at_5;
    let coverage_ok = coverage_ratio >= min_coverage_ratio;
    let passed = recall_ok && coverage_ok;
    let rationale = if passed {
        format!(
            "Shadow generation passed gate (coverage={:.1}%, recall@5={:.3} vs active {:.3}).",
            coverage_ratio * 100.0,
            shadow_recall_at_5,
            active_recall_at_5
        )
    } else if !coverage_ok {
        format!(
            "Shadow coverage {:.1}% below required {:.1}%.",
            coverage_ratio * 100.0,
            min_coverage_ratio * 100.0
        )
    } else {
        format!(
            "Shadow recall@5 ({:.3}) regressed beyond tolerance vs active ({:.3}).",
            shadow_recall_at_5, active_recall_at_5
        )
    };
    let now = utc_now_iso();
    conn.execute(
        "UPDATE vector_index_generations
         SET shadow_recall_at_5 = ?2,
             active_recall_at_5 = ?3,
             status = ?4,
             updated_at = ?5
         WHERE generation_id = ?1",
        params![
            generation_id,
            shadow_recall_at_5,
            active_recall_at_5,
            if passed {
                "evaluated_pass"
            } else {
                "evaluated_fail"
            },
            now
        ],
    )?;
    Ok(ShadowEvaluationGate {
        generation_id: generation_id.to_string(),
        coverage_ratio,
        active_recall_at_5,
        shadow_recall_at_5,
        passed,
        rationale,
    })
}

/// Atomically swap a ready shadow generation into `vector_embeddings`, archiving
/// the prior active vectors into `vector_embeddings_shadow` for instant rollback.
pub fn cutover_shadow_generation(
    conn: &mut Connection,
    generation_id: &str,
) -> Result<CutoverReceipt, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    let job = load_shadow_job(conn, generation_id)?
        .ok_or_else(|| CoreError::InvalidRequest(format!("unknown generation: {generation_id}")))?;
    if job.status == "evaluated_fail" {
        return Err(CoreError::InvalidRequest(
            "refusing cutover for shadow generation that failed evaluation gate".to_string(),
        ));
    }
    let now = utc_now_iso();
    let tx = conn.transaction()?;

    let prev_active_gen: Option<String> = tx
        .query_row(
            "SELECT value FROM graph_meta WHERE key = 'active_index_generation'",
            [],
            |r| r.get(0),
        )
        .optional()?;

    let backup_gen = prev_active_gen
        .clone()
        .unwrap_or_else(|| format!("gen-pre-{}", generation_id));

    // Archive current active vectors into shadow storage under `backup_gen` for instant rollback.
    let active_count: i64 =
        tx.query_row("SELECT COUNT(*) FROM vector_embeddings", [], |r| r.get(0))?;
    if active_count > 0 {
        tx.execute(
            "INSERT OR REPLACE INTO vector_embeddings_shadow(
                generation_id, item_id, item_type, source_node, text_hash, embedding,
                embedding_dim, embedding_model, model_version, normalization, metadata_json, indexed_at
             )
             SELECT ?1, item_id, item_type, source_node, text_hash, embedding,
                    embedding_dim, embedding_model, 'archived', 'l2', metadata_json, indexed_at
             FROM vector_embeddings",
            params![backup_gen],
        )?;
        tx.execute(
            "INSERT OR IGNORE INTO vector_index_generations(
                generation_id, model_id, model_version, dimensions, normalization,
                status, processed_items, total_items, created_at, updated_at
             )
             SELECT ?1, COALESCE(MAX(embedding_model), 'unknown'), 'archived',
                    COALESCE(MAX(embedding_dim), 384), 'l2', 'archived',
                    COUNT(*), COUNT(*), ?2, ?2
             FROM vector_embeddings",
            params![backup_gen, now],
        )?;
    }

    // Swap shadow vectors into live `vector_embeddings`.
    tx.execute("DELETE FROM vector_embeddings", [])?;
    let swapped = tx.execute(
        "INSERT INTO vector_embeddings(
            item_id, item_type, source_node, text_hash, embedding,
            embedding_dim, embedding_model, metadata_json, indexed_at
         )
         SELECT item_id, item_type, source_node, text_hash, embedding,
                embedding_dim, embedding_model, metadata_json, indexed_at
         FROM vector_embeddings_shadow
         WHERE generation_id = ?1",
        params![generation_id],
    )?;

    tx.execute(
        "UPDATE vector_index_generations SET status = 'archived', updated_at = ?1 WHERE status = 'active'",
        params![now],
    )?;
    tx.execute(
        "UPDATE vector_index_generations SET status = 'active', updated_at = ?2 WHERE generation_id = ?1",
        params![generation_id, now],
    )?;

    let mut meta_updates: BTreeMap<&str, String> = BTreeMap::new();
    meta_updates.insert("active_index_generation", generation_id.to_string());
    if active_count > 0 {
        meta_updates.insert("previous_index_generation", backup_gen.clone());
    }
    for (k, v) in meta_updates {
        tx.execute(
            "INSERT INTO graph_meta(key, value) VALUES (?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![k, v],
        )?;
    }

    tx.commit()?;
    Ok(CutoverReceipt {
        active_generation_id: generation_id.to_string(),
        previous_generation_id: (active_count > 0).then_some(backup_gen),
        vectors_swapped: swapped,
        active_model_id: job.model_id,
        active_model_version: job.model_version,
    })
}

/// Instantly roll back `vector_embeddings` to the previously archived generation.
pub fn rollback_index_generation(conn: &mut Connection) -> Result<CutoverReceipt, CoreError> {
    let prev: String = conn
        .query_row(
            "SELECT value FROM graph_meta WHERE key = 'previous_index_generation'",
            [],
            |r| r.get(0),
        )
        .optional()?
        .ok_or_else(|| {
            CoreError::InvalidRequest("no previous generation available for rollback".to_string())
        })?;
    cutover_shadow_generation(conn, &prev)
}

/// Load a shadow re-index job record by generation ID.
pub fn load_shadow_job(
    conn: &Connection,
    generation_id: &str,
) -> Result<Option<ShadowReindexJob>, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    conn.query_row(
        "SELECT generation_id, model_id, model_version, dimensions, normalization,
                status, cursor_item_id, processed_items, total_items,
                shadow_recall_at_5, active_recall_at_5, created_at, updated_at
         FROM vector_index_generations
         WHERE generation_id = ?1",
        params![generation_id],
        |r| {
            Ok(ShadowReindexJob {
                generation_id: r.get(0)?,
                model_id: r.get(1)?,
                model_version: r.get(2)?,
                dimensions: r.get::<_, i64>(3)?.max(0) as usize,
                normalization: r.get(4)?,
                status: r.get(5)?,
                cursor_item_id: r.get(6)?,
                processed_items: r.get::<_, i64>(7)?.max(0) as usize,
                total_items: r.get::<_, i64>(8)?.max(0) as usize,
                shadow_recall_at_5: r.get(9)?,
                active_recall_at_5: r.get(10)?,
                created_at: r.get(11)?,
                updated_at: r.get(12)?,
            })
        },
    )
    .optional()
    .map_err(Into::into)
}

/// List all vector index shadow/active/retired generations ordered newest-first.
pub fn list_shadow_jobs(conn: &Connection) -> Result<Vec<ShadowReindexJob>, CoreError> {
    ensure_embedding_registry_schema(conn)?;
    let mut stmt = conn.prepare(
        "SELECT generation_id, model_id, model_version, dimensions, normalization,
                status, cursor_item_id, processed_items, total_items,
                shadow_recall_at_5, active_recall_at_5, created_at, updated_at
         FROM vector_index_generations
         ORDER BY updated_at DESC, created_at DESC
         LIMIT 50",
    )?;
    let rows = stmt
        .query_map([], |r| {
            Ok(ShadowReindexJob {
                generation_id: r.get(0)?,
                model_id: r.get(1)?,
                model_version: r.get(2)?,
                dimensions: r.get::<_, i64>(3)?.max(0) as usize,
                normalization: r.get(4)?,
                status: r.get(5)?,
                cursor_item_id: r.get(6)?,
                processed_items: r.get::<_, i64>(7)?.max(0) as usize,
                total_items: r.get::<_, i64>(8)?.max(0) as usize,
                shadow_recall_at_5: r.get(9)?,
                active_recall_at_5: r.get(10)?,
                created_at: r.get(11)?,
                updated_at: r.get(12)?,
            })
        })?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph_write::schema::bootstrap;

    #[test]
    fn registry_guards_incompatible_spaces_and_supports_shadow_cutover_rollback() {
        let mut conn = Connection::open_in_memory().unwrap();
        {
            let tx = conn.transaction().unwrap();
            bootstrap(&tx, "2026-01-01T00:00:00").unwrap();
            tx.commit().unwrap();
        }

        let models = list_embedding_models(&conn).unwrap();
        assert!(models.iter().any(|m| m.model_id == "BAAI/bge-m3"));

        let bge = models
            .iter()
            .find(|m| m.model_id == "BAAI/bge-m3")
            .unwrap()
            .clone();
        let e5 = models
            .iter()
            .find(|m| m.model_id == "intfloat/multilingual-e5-small")
            .unwrap()
            .clone();
        assert!(assert_embedding_space_compatible(&bge, &e5).is_err());
        assert!(assert_embedding_space_compatible(&e5, &e5).is_ok());

        // Seed active vector with legacy 4-dim test model.
        conn.execute(
            "INSERT INTO nodes(id, type, title, summary, raw_json, metadata_json, created_at, updated_at)
             VALUES ('n1', 'Document', 'T', 'S', '{}', '{}', '2026-01-01', '2026-01-01')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO vector_embeddings(item_id, item_type, source_node, text_hash, embedding, embedding_dim, embedding_model, metadata_json, indexed_at)
             VALUES ('n1', 'node', 'n1', 'h1', ?1, 4, 'old-model', '{}', '2026-01-01')",
            params![encode_vector_f32(&[1.0, 0.0, 0.0, 0.0])],
        )
        .unwrap();

        let new_spec = EmbeddingModelSpec {
            model_id: "new-semantic-v2".to_string(),
            version: "2.0.0".to_string(),
            provider: "sentence_transformers".to_string(),
            dimensions: 4,
            normalization: "l2".to_string(),
            distance_metric: "cosine".to_string(),
            languages: vec!["ko".to_string(), "en".to_string()],
            modalities: vec!["text".to_string()],
            semantic: true,
            status: EmbeddingModelStatus::Shadow,
        };
        let job = start_shadow_reindex(&conn, "gen-2", &new_spec, 1).unwrap();
        assert_eq!(job.processed_items, 0);

        // Resumable batch write.
        let job = write_shadow_batch(
            &mut conn,
            "gen-2",
            &new_spec,
            &[(
                "n1".to_string(),
                "node".to_string(),
                "n1".to_string(),
                "h1".to_string(),
                vec![0.0, 1.0, 0.0, 0.0],
            )],
        )
        .unwrap();
        assert_eq!(job.processed_items, 1);
        assert_eq!(job.cursor_item_id.as_deref(), Some("n1"));

        let gate = evaluate_shadow_generation(&conn, "gen-2", 0.80, 0.90, 0.95, 0.02).unwrap();
        assert!(gate.passed);

        let cutover = cutover_shadow_generation(&mut conn, "gen-2").unwrap();
        assert_eq!(cutover.active_generation_id, "gen-2");
        assert_eq!(cutover.vectors_swapped, 1);
        let live_model: String = conn
            .query_row(
                "SELECT embedding_model FROM vector_embeddings WHERE item_id = 'n1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(live_model, "new-semantic-v2");

        // Instant rollback restores `old-model`.
        let rolled = rollback_index_generation(&mut conn).unwrap();
        assert_eq!(rolled.vectors_swapped, 1);
        let restored_model: String = conn
            .query_row(
                "SELECT embedding_model FROM vector_embeddings WHERE item_id = 'n1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(restored_model, "old-model");
    }
}
