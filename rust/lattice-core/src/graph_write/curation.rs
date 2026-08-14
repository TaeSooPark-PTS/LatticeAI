//! Curation — port of `lattice_brain/graph/projection/curation.py`.
//!
//! Two jobs and the queue between them. [`GraphWriter::curate`] applies the
//! curator's gated topic promotion — or, in review mode, parks it in
//! `graph_meta` for a human decision; [`GraphWriter::curate_noise`] removes
//! heuristic concept nodes whose document-frequency stats mark them as noise and
//! normalizes free-string relation verbs.
//!
//! Both are explicit and observable: everything skipped is reported with a
//! reason, and `dry_run=true` is the default for the destructive one.
//!
//! The **decision** for promotion (topic extraction, clustering, scoring) is
//! NLP and reaches this module as [`super::types::CuratorOverlay`]; the
//! decision for noise is arithmetic over rows and lives in [`super::curator`].

use rusqlite::{ToSql, Transaction};
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::curator::{
    build_relation_verb_index, plan_concept_noise_reduction, plan_relation_normalization,
    ConceptStat,
};
use super::primitives::delete_nodes_v2;
use super::pyaux::{slug, truthy};
use super::types::{CurateNoiseRequest, CurateRequest, EdgeSpec, NodeSpec, PromotionCandidate};
use super::GraphWriter;

/// `curation._PROMOTION_REVIEW_ENV`.
pub const PROMOTION_REVIEW_ENV: &str = "LATTICEAI_GRAPH_PROMOTION_REVIEW";
/// `curation._PENDING_PROMOTIONS_KEY`.
pub const PENDING_PROMOTIONS_KEY: &str = "pending_promotions";
/// `curation._PENDING_PROMOTIONS_CAP`.
pub const PENDING_PROMOTIONS_CAP: usize = 100;
/// `curation._LAST_NOISE_CURATE_KEY`.
pub const LAST_NOISE_CURATE_KEY: &str = "last_noise_curate_at";

/// The content node types `curate` scans and `curate_noise` counts.
const CONTENT_TYPES: [&str; 9] = [
    "Document",
    "File",
    "CodeFile",
    "Message",
    "AIResponse",
    "Chat",
    "Page",
    "Slide",
    "Spreadsheet",
];
/// The node types `curate_noise` considers removable.
const CONCEPT_TYPES: [&str; 5] = ["Concept", "Feature", "Topic", "Code", "Error"];

/// The key order `curate` builds a queued promotion in.
///
/// `_store_pending_promotions` writes `json.dumps(..., ensure_ascii=False)`
/// with no `sort_keys`, so the stored bytes carry the dict's insertion order.
const PENDING_PROMOTION_KEY_ORDER: [&str; 6] = [
    "id",
    "label",
    "importance",
    "aliases",
    "sources",
    "proposed_at",
];

fn promotion_review_default() -> bool {
    matches!(
        std::env::var(PROMOTION_REVIEW_ENV)
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes"
    )
}

/// The rows `curate` scans, which the worker's overlay was computed from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CurateScan {
    /// `{id, text, kind}` per scanned content node, in Python's order.
    pub documents: Vec<Value>,
    /// Lower-cased `Topic`/`Concept` titles already in the graph.
    pub existing_labels: Vec<String>,
}

impl GraphWriter {
    /// The read half of `curate`, exposed so the worker can compute the overlay
    /// against exactly the rows the write half will validate against.
    pub fn curate_scan(&self, max_documents: i64) -> Result<CurateScan, CoreError> {
        let limit = max_documents.clamp(1, 2000);
        self.store.with_read_conn(|conn| {
            // `_read_tables()`: the scan reads the v2 reconstruction views when
            // they are available, and that is not cosmetic. `nodes` never
            // updates `type` on conflict while `nodes_v2` does, so a node
            // upserted first as `Chat` and later as `Conversation` reads as
            // content in one table and not in the other — a one-row difference
            // in `documents_scanned` that would silently change what the
            // curator was asked to promote.
            let (nodes_table, _) = crate::read::read_tables(conn);
            let placeholders = vec!["?"; CONTENT_TYPES.len()].join(",");
            let mut params: Vec<&dyn ToSql> = CONTENT_TYPES
                .iter()
                .map(|value| value as &dyn ToSql)
                .collect();
            params.push(&limit);
            let mut statement = conn.prepare(&format!(
                "SELECT id, type, title, summary FROM {nodes_table} \
                 WHERE type IN ({placeholders}) \
                 ORDER BY updated_at DESC, id ASC LIMIT ?"
            ))?;
            let rows = statement.query_map(params.as_slice(), |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            })?;
            let mut documents = Vec::new();
            for row in rows {
                let (id, node_type, title, summary) = row?;
                let kind = if matches!(
                    node_type.as_str(),
                    "Document" | "File" | "CodeFile" | "Spreadsheet"
                ) {
                    "file"
                } else {
                    "chat"
                };
                documents.push(json!({
                    "id": id,
                    "text": format!("{title} {}", summary.unwrap_or_default()),
                    "kind": kind,
                }));
            }
            let mut labels = Vec::new();
            let mut statement = conn.prepare(&format!(
                "SELECT title FROM {nodes_table} WHERE type IN ('Topic', 'Concept')"
            ))?;
            let rows = statement.query_map([], |row| row.get::<_, Option<String>>(0))?;
            for row in rows {
                let label = row?.unwrap_or_default().trim().to_lowercase();
                if !labels.contains(&label) {
                    labels.push(label);
                }
            }
            Ok(CurateScan {
                documents,
                existing_labels: labels,
            })
        })
    }

    /// `curate` — write the curator's promotions, or park them for review.
    pub fn curate(&self, request: &CurateRequest) -> Result<Value, CoreError> {
        let scan = self.curate_scan(request.max_documents)?;
        let valid_ids: Vec<String> = scan
            .documents
            .iter()
            .filter_map(|document| document.get("id").and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        let review = request.review_mode.unwrap_or_else(promotion_review_default);
        let overlay = &request.overlay;
        let skipped_head: Vec<Value> = overlay.skipped.iter().take(50).cloned().collect();
        if review {
            let proposed_at = self.clock.now_iso();
            let proposed: Vec<Value> = overlay
                .promotions
                .iter()
                .map(|promotion| {
                    let sources: Vec<&String> = promotion
                        .sources
                        .iter()
                        .take(10)
                        .filter(|source| valid_ids.contains(source))
                        .collect();
                    json!({
                        "id": format!("topic:{}", slug(&promotion.label, 96)),
                        "label": promotion.label,
                        "importance": promotion.importance,
                        "aliases": promotion.aliases,
                        "sources": sources,
                        "proposed_at": proposed_at,
                    })
                })
                .collect();
            let merged = self
                .store
                .with_write_txn(|txn| self.merge_pending_promotions(txn, &proposed))?;
            return Ok(json!({
                "status": "pending_review",
                "documents_scanned": scan.documents.len(),
                "candidates_total": overlay.candidates_total,
                "pending": proposed,
                "pending_total": merged.len(),
                "skipped": skipped_head,
                "skipped_total": overlay.skipped.len(),
            }));
        }
        let promoted = self.store.with_write_txn(|txn| {
            let mut promoted = Vec::new();
            for promotion in &overlay.promotions {
                promoted.push(self.write_promotion(txn, promotion, Some(&valid_ids))?);
            }
            Ok(promoted)
        })?;
        Ok(json!({
            "status": "ok",
            "documents_scanned": scan.documents.len(),
            "candidates_total": overlay.candidates_total,
            "promoted": promoted,
            "skipped": skipped_head,
            "skipped_total": overlay.skipped.len(),
        }))
    }

    /// `_write_promotion` — one Topic node, its importance, its MENTIONS edges.
    ///
    /// The single write path shared by direct `curate()` and
    /// [`GraphWriter::apply_promotions`], so a human-approved promotion lands
    /// exactly like an auto-promoted one. `valid_source_ids` restricts the
    /// linkable sources to this run's scanned rows; `None` (apply-after-review)
    /// checks each stored source for existence instead, so a node deleted
    /// between propose and apply is skipped rather than an error.
    fn write_promotion(
        &self,
        txn: &Transaction<'_>,
        promotion: &PromotionCandidate,
        valid_source_ids: Option<&[String]>,
    ) -> Result<Value, CoreError> {
        let topic_id = promotion
            .id
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| format!("topic:{}", slug(&promotion.label, 96)));
        let mut metadata = Map::new();
        metadata.insert("curated".into(), json!(true));
        metadata.insert("importance".into(), json!(promotion.importance));
        metadata.insert("aliases".into(), json!(promotion.aliases));
        metadata.insert("source".into(), json!("graph_curator"));
        self.upsert_node(
            txn,
            &NodeSpec {
                id: topic_id.clone(),
                node_type: "Topic".into(),
                title: promotion.label.clone(),
                metadata,
                ..NodeSpec::default()
            },
        )?;
        txn.execute(
            "UPDATE nodes_v2 SET importance_score=? WHERE id=?",
            rusqlite::params![promotion.importance, topic_id],
        )?;
        let mut linked = 0i64;
        for source_id in promotion.sources.iter().take(10) {
            match valid_source_ids {
                Some(valid) => {
                    if !valid.contains(source_id) {
                        continue;
                    }
                }
                None => {
                    let exists: Option<i64> = txn
                        .query_row(
                            "SELECT 1 FROM nodes WHERE id=?",
                            rusqlite::params![source_id],
                            |row| row.get(0),
                        )
                        .ok();
                    if exists.is_none() {
                        continue;
                    }
                }
            }
            let mut edge_metadata = Map::new();
            edge_metadata.insert("source".into(), json!("graph_curator"));
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: source_id.clone(),
                    to_node: topic_id.clone(),
                    edge_type: "MENTIONS".into(),
                    weight: 0.6,
                    metadata: edge_metadata,
                    legacy_label: None,
                },
            )?;
            linked += 1;
        }
        Ok(json!({
            "node_id": topic_id,
            "label": promotion.label,
            "importance": promotion.importance,
            "linked_sources": linked,
        }))
    }

    /// `pending_promotions` — what is waiting for a human decision.
    pub fn pending_promotions(&self) -> Result<Vec<Value>, CoreError> {
        self.store
            .with_write_txn(|txn| self.read_pending_promotions(txn))
    }

    /// `apply_pending_promotions` — write the stored proposals (all when
    /// `ids` is `None`); applied entries leave the queue.
    pub fn apply_promotions(&self, ids: Option<&[String]>) -> Result<Value, CoreError> {
        self.store.with_write_txn(|txn| {
            let mut applied = Vec::new();
            let mut remaining = Vec::new();
            for promotion in self.read_pending_promotions(txn)? {
                let id = promotion
                    .get("id")
                    .map(super::pyaux::py_str)
                    .unwrap_or_default();
                if let Some(wanted) = ids {
                    if !wanted.contains(&id) {
                        remaining.push(promotion);
                        continue;
                    }
                }
                let candidate: PromotionCandidate = serde_json::from_value(promotion.clone())
                    .map_err(|err| {
                        CoreError::InvalidRequest(format!(
                            "a queued promotion is not readable ({err}); refusing to apply it"
                        ))
                    })?;
                applied.push(self.write_promotion(txn, &candidate, None)?);
            }
            self.store_pending_promotions(txn, &remaining)?;
            Ok(json!({
                "status": "ok",
                "applied": applied,
                "remaining": remaining.len(),
            }))
        })
    }

    /// `reject_pending_promotions` — drop them without writing.
    pub fn reject_promotions(&self, ids: Option<&[String]>) -> Result<Value, CoreError> {
        self.store.with_write_txn(|txn| {
            let mut rejected: Vec<String> = Vec::new();
            let mut remaining = Vec::new();
            for promotion in self.read_pending_promotions(txn)? {
                let id = promotion
                    .get("id")
                    .map(super::pyaux::py_str)
                    .unwrap_or_default();
                if let Some(wanted) = ids {
                    if !wanted.contains(&id) {
                        remaining.push(promotion);
                        continue;
                    }
                }
                rejected.push(id);
            }
            self.store_pending_promotions(txn, &remaining)?;
            Ok(json!({
                "status": "ok",
                "rejected": rejected,
                "remaining": remaining.len(),
            }))
        })
    }

    fn read_pending_promotions(&self, txn: &Transaction<'_>) -> Result<Vec<Value>, CoreError> {
        let stored: Option<String> = txn
            .query_row(
                "SELECT value FROM graph_meta WHERE key=?",
                rusqlite::params![PENDING_PROMOTIONS_KEY],
                |row| row.get(0),
            )
            .ok();
        let Some(stored) = stored.filter(|value| !value.is_empty()) else {
            return Ok(Vec::new());
        };
        let Ok(Value::Array(items)) = serde_json::from_str::<Value>(&stored) else {
            return Ok(Vec::new());
        };
        Ok(items
            .into_iter()
            .filter(|item| {
                item.as_object()
                    .and_then(|map| map.get("id"))
                    .map(truthy)
                    .unwrap_or(false)
            })
            .collect())
    }

    fn store_pending_promotions(
        &self,
        txn: &Transaction<'_>,
        entries: &[Value],
    ) -> Result<(), CoreError> {
        txn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            rusqlite::params![
                PENDING_PROMOTIONS_KEY,
                super::pyaux::py_dumps_ordered(
                    &Value::Array(entries.to_vec()),
                    &PENDING_PROMOTION_KEY_ORDER,
                )
            ],
        )?;
        Ok(())
    }

    /// `_merge_pending_promotions` — dedupe by id, newest wins, cap at 100.
    fn merge_pending_promotions(
        &self,
        txn: &Transaction<'_>,
        proposed: &[Value],
    ) -> Result<Vec<Value>, CoreError> {
        let mut merged: Vec<(String, Value)> = Vec::new();
        for item in self
            .read_pending_promotions(txn)?
            .into_iter()
            .chain(proposed.iter().cloned())
        {
            let id = item.get("id").map(super::pyaux::py_str).unwrap_or_default();
            match merged.iter_mut().find(|(existing, _)| *existing == id) {
                Some(slot) => slot.1 = item,
                None => merged.push((id, item)),
            }
        }
        let entries: Vec<Value> = merged
            .into_iter()
            .map(|(_, item)| item)
            .rev()
            .take(PENDING_PROMOTIONS_CAP)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        self.store_pending_promotions(txn, &entries)?;
        Ok(entries)
    }

    /// `curate_noise` — remove noise concepts and normalize free-string verbs.
    pub fn curate_noise(&self, request: &CurateNoiseRequest) -> Result<Value, CoreError> {
        let max_removals = request.max_removals.max(0) as usize;
        self.store.with_write_txn(|txn| {
            let content_placeholders = vec!["?"; CONTENT_TYPES.len()].join(",");
            let content_params: Vec<&dyn ToSql> = CONTENT_TYPES
                .iter()
                .map(|value| value as &dyn ToSql)
                .collect();
            let total_docs: i64 = txn.query_row(
                &format!("SELECT COUNT(*) AS c FROM nodes WHERE type IN ({content_placeholders})"),
                content_params.as_slice(),
                |row| row.get(0),
            )?;

            let concept_placeholders = vec!["?"; CONCEPT_TYPES.len()].join(",");
            let concept_rows: Vec<(String, String, Option<String>, String)> = {
                let concept_params: Vec<&dyn ToSql> = CONCEPT_TYPES
                    .iter()
                    .map(|value| value as &dyn ToSql)
                    .collect();
                let mut statement = txn.prepare(&format!(
                    "SELECT id, type, title, metadata_json FROM nodes \
                     WHERE type IN ({concept_placeholders})"
                ))?;
                let rows = statement.query_map(concept_params.as_slice(), |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, Option<String>>(2)?,
                        row.get::<_, String>(3)?,
                    ))
                })?;
                rows.collect::<Result<Vec<_>, _>>()?
            };
            let mut concepts: Vec<ConceptStat> = Vec::with_capacity(concept_rows.len());
            for (id, node_type, title, metadata_json) in concept_rows {
                let meta = safe_loads(Some(&metadata_json));
                let heuristic = meta.get("auto_extracted").map(truthy).unwrap_or(false)
                    || meta.get("source").and_then(Value::as_str) == Some("graph_curator")
                    || meta.get("curated") == Some(&Value::Bool(true));
                // Document frequency: distinct *content* nodes linked to this
                // concept in either direction.
                let mut df_params: Vec<&dyn ToSql> =
                    vec![&id as &dyn ToSql, &id as &dyn ToSql, &id as &dyn ToSql];
                df_params.extend(CONTENT_TYPES.iter().map(|value| value as &dyn ToSql));
                let df: i64 = txn.query_row(
                    &format!(
                        "SELECT COUNT(DISTINCT n.id) AS c \
                         FROM edges e \
                         JOIN nodes n \
                           ON n.id = CASE WHEN e.to_node = ? THEN e.from_node ELSE e.to_node END \
                         WHERE (e.to_node = ? OR e.from_node = ?) \
                           AND n.type IN ({content_placeholders})"
                    ),
                    df_params.as_slice(),
                    |row| row.get(0),
                )?;
                concepts.push(ConceptStat {
                    id,
                    label: title,
                    node_type,
                    df,
                    heuristic,
                });
            }

            let plan = plan_concept_noise_reduction(
                &concepts,
                total_docs,
                request.max_df_ratio,
                request.min_doc_frequency,
                request.min_corpus_docs,
            );
            let removals: Vec<Value> = plan.remove.iter().take(max_removals).cloned().collect();

            let verb_index = build_relation_verb_index();
            let edge_types: Vec<String> = {
                let mut statement = txn.prepare("SELECT DISTINCT type FROM edges")?;
                let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
                rows.collect::<Result<Vec<_>, _>>()?
            };
            let verb_plan = if request.normalize_verbs {
                plan_relation_normalization(&edge_types, &verb_index)
            } else {
                Vec::new()
            };

            let mut removed_count = 0i64;
            let mut renamed_edges = 0i64;
            if !request.dry_run {
                for decision in &removals {
                    let node_id = decision
                        .get("id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string();
                    txn.execute(
                        "DELETE FROM edges WHERE from_node=? OR to_node=?",
                        rusqlite::params![node_id, node_id],
                    )?;
                    txn.execute(
                        "DELETE FROM vector_embeddings WHERE item_id=?",
                        rusqlite::params![node_id],
                    )?;
                    txn.execute("DELETE FROM nodes WHERE id=?", rusqlite::params![node_id])?;
                    delete_nodes_v2(txn, std::slice::from_ref(&node_id))?;
                    removed_count += 1;
                }
                for (original, canonical) in &verb_plan {
                    let count: i64 = txn.query_row(
                        "SELECT COUNT(*) AS c FROM edges WHERE type=?",
                        rusqlite::params![original],
                        |row| row.get(0),
                    )?;
                    renamed_edges += count;
                    // UNIQUE(from_node, to_node, type): merge rows that collide
                    // after the rename instead of failing the UPDATE.
                    txn.execute(
                        "UPDATE OR IGNORE edges SET type=? WHERE type=?",
                        rusqlite::params![canonical, original],
                    )?;
                    txn.execute(
                        "DELETE FROM edges WHERE type=?",
                        rusqlite::params![original],
                    )?;
                }
                // Every applied run is stamped — even a no-op one means the
                // graph was inspected, so the Command Center hygiene advisory
                // stops re-suggesting for a while.
                txn.execute(
                    "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                    rusqlite::params![LAST_NOISE_CURATE_KEY, self.clock.now_iso()],
                )?;
            }

            let protected = plan
                .keep
                .iter()
                .filter(|entry| {
                    entry.get("reason").and_then(Value::as_str) == Some("user_created_protected")
                })
                .count();
            let mut verb_map = Map::new();
            for (original, canonical) in &verb_plan {
                verb_map.insert(original.clone(), json!(canonical));
            }
            Ok(json!({
                "status": "ok",
                "dry_run": request.dry_run,
                "total_content_docs": total_docs,
                "concepts_examined": concepts.len(),
                "remove": removals,
                "remove_total": plan.remove.len(),
                "kept": plan.keep.len(),
                "protected_user_nodes": protected,
                "verb_normalizations": Value::Object(verb_map),
                "applied": {
                    "removed_nodes": removed_count,
                    "renamed_edges": renamed_edges,
                },
                "thresholds": {
                    "max_df_ratio": request.max_df_ratio,
                    "min_doc_frequency": request.min_doc_frequency,
                    "min_corpus_docs": request.min_corpus_docs,
                },
            }))
        })
    }

    /// `last_noise_curate_at` — when the last applied run inspected the graph.
    pub fn last_noise_curate_at(&self) -> Result<Option<String>, CoreError> {
        self.store.with_read_conn(|conn| {
            Ok(conn
                .query_row(
                    "SELECT value FROM graph_meta WHERE key=?",
                    rusqlite::params![LAST_NOISE_CURATE_KEY],
                    |row| row.get::<_, String>(0),
                )
                .ok()
                .filter(|value| !value.is_empty()))
        })
    }

    /// `mark_superseded` — record that one node was replaced by another.
    ///
    /// The old node stays queryable (knowledge is durable); readers follow the
    /// revision chain through `nodes_v2.superseded_by`.
    pub fn mark_superseded(
        &self,
        old_node_id: &str,
        new_node_id: &str,
    ) -> Result<Value, CoreError> {
        self.store.with_write_txn(|txn| {
            for node_id in [old_node_id, new_node_id] {
                let exists: Option<i64> = txn
                    .query_row(
                        "SELECT 1 FROM nodes_v2 WHERE id=?",
                        rusqlite::params![node_id],
                        |row| row.get(0),
                    )
                    .ok();
                if exists.is_none() {
                    return Err(CoreError::InvalidRequest(node_id.to_string()));
                }
            }
            txn.execute(
                "UPDATE nodes_v2 SET superseded_by=?, updated_at=? WHERE id=?",
                rusqlite::params![new_node_id, self.clock.now_iso(), old_node_id],
            )?;
            Ok(json!({
                "status": "ok",
                "node_id": old_node_id,
                "superseded_by": new_node_id,
            }))
        })
    }
}
