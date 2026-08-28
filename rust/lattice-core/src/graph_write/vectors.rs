//! Vector-index writes — port of `lattice_brain/graph/retrieval_vector/`.
//!
//! Two entry points and one fingerprint:
//!
//! * [`GraphWriter::write_vectors`] is `index_node_incremental` — embed one
//!   node and its chunks after an ingest, and **never raise**: the graph write
//!   already landed, so a failure here downgrades the result to backlog that a
//!   later rebuild picks up;
//! * [`GraphWriter::rebuild_vector_index`] is the full/incremental rebuild,
//!   bracketed by a `vector_index_operations` row so the work is visible while
//!   it runs and afterwards;
//! * the embedder fingerprint in `graph_meta` records *which* embedder built
//!   the index. `vector_search` filters on the current model and dimension, so
//!   swapping the embedder silently yields zero vector rows; the fingerprint is
//!   what turns that into the honest `stale_embedder` signal instead.

use rusqlite::Transaction;
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::{clean_text, round_to, safe_loads};

use super::primitives::vector_text_for_node;
use super::pyaux::{json_of, py_float_repr, sha256_text};
use super::types::{RebuildRequest, VectorItem};
use super::GraphWriter;

/// `graph_meta` key holding the embedder fingerprint.
pub const EMBEDDER_FINGERPRINT_KEY: &str = "embedder_fingerprint";

/// What `rebuild_vector_index` returns.
#[derive(Debug, Clone, PartialEq)]
pub struct RebuildOutcome {
    pub status: String,
    pub operation_id: String,
    pub full: bool,
    pub items_total: usize,
    pub items_indexed: usize,
    pub items_skipped: usize,
    pub duration_ms: f64,
    pub embedding_model: String,
    pub embedding_dim: usize,
}

impl RebuildOutcome {
    /// The body Python returns, key for key.
    pub fn to_json(&self) -> Value {
        json!({
            "status": self.status,
            "operation_id": self.operation_id,
            "full": self.full,
            "items_total": self.items_total,
            "items_indexed": self.items_indexed,
            "items_skipped": self.items_skipped,
            "duration_ms": self.duration_ms,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
        })
    }
}

/// What `index_node_incremental` returns.
#[derive(Debug, Clone, PartialEq)]
pub struct IncrementalOutcome {
    pub node_id: String,
    pub items_total: usize,
    pub items_indexed: usize,
    pub items_skipped: usize,
    pub status: String,
    pub detail: Option<String>,
    pub duration_ms: Option<f64>,
    pub embedding_model: Option<String>,
}

impl IncrementalOutcome {
    /// The body Python returns; `detail` / `duration_ms` / `embedding_model`
    /// appear only on the branches that set them.
    pub fn to_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("node_id".into(), json!(self.node_id));
        map.insert("items_total".into(), json!(self.items_total));
        map.insert("items_indexed".into(), json!(self.items_indexed));
        map.insert("items_skipped".into(), json!(self.items_skipped));
        map.insert("status".into(), json!(self.status));
        if let Some(detail) = &self.detail {
            map.insert("detail".into(), json!(detail));
        }
        if let Some(duration) = self.duration_ms {
            map.insert("duration_ms".into(), json!(duration));
        }
        if let Some(model) = &self.embedding_model {
            map.insert("embedding_model".into(), json!(model));
        }
        Value::Object(map)
    }
}

impl GraphWriter {
    /// `index_node_incremental` — embed one node and its chunks.
    ///
    /// Never returns `Err`: an embedding or storage failure is reported as
    /// `status: "failed"` so an ingest caller degrades instead of losing a
    /// write that already landed.
    pub fn write_vectors(&self, node_id: &str) -> IncrementalOutcome {
        self.write_vectors_with(node_id, &[])
    }

    /// [`Self::write_vectors`], filing caller-supplied vectors for matching
    /// `item_id`s instead of re-deriving them with the native hash model.
    ///
    /// Items the caller did not name still hash. An empty slice is the default
    /// door, so the parity goldens stay byte-identical.
    pub fn write_vectors_with(
        &self,
        node_id: &str,
        supplied: &[(String, super::types::SuppliedVector)],
    ) -> IncrementalOutcome {
        let node_id = node_id.trim().to_string();
        let started = self.clock.perf_counter();
        if node_id.is_empty() {
            return IncrementalOutcome {
                node_id,
                items_total: 0,
                items_indexed: 0,
                items_skipped: 0,
                status: "skipped".into(),
                detail: Some("node_id required".into()),
                duration_ms: None,
                embedding_model: None,
            };
        }
        match self.write_vectors_inner(&node_id, supplied) {
            Ok(Some((total, indexed, skipped))) => IncrementalOutcome {
                node_id,
                items_total: total,
                items_indexed: indexed,
                items_skipped: skipped,
                status: if indexed > 0 {
                    "indexed".into()
                } else {
                    "noop".into()
                },
                detail: None,
                duration_ms: Some(self.elapsed_ms(started)),
                embedding_model: Some(self.embedder.model_id().to_string()),
            },
            Ok(None) => IncrementalOutcome {
                node_id,
                items_total: 0,
                items_indexed: 0,
                items_skipped: 0,
                status: "skipped".into(),
                detail: Some("node not found".into()),
                duration_ms: None,
                embedding_model: None,
            },
            Err(error) => IncrementalOutcome {
                node_id,
                items_total: 0,
                items_indexed: 0,
                items_skipped: 0,
                status: "failed".into(),
                detail: Some(error.to_string()),
                duration_ms: Some(self.elapsed_ms(started)),
                embedding_model: None,
            },
        }
    }

    fn write_vectors_inner(
        &self,
        node_id: &str,
        supplied: &[(String, super::types::SuppliedVector)],
    ) -> Result<Option<(usize, usize, usize)>, CoreError> {
        // Collect + embed *outside* the write txn. Hashing a 50 KB passage
        // inside BEGIN IMMEDIATE held the only SQLite writer for the whole
        // embed; `write_vectors_with` already accepted precomputed vectors,
        // so the default door now prepares them the same way. Goldens stay
        // identical: FrozenClock pins duration_ms at 0 and the filed bytes
        // are encode(embed(text)) either way.
        let items = self
            .store
            .with_read_conn(|conn| self.collect_vector_items(conn, node_id))?;
        let Some(items) = items else {
            return Ok(None);
        };
        let prepared = self.embed_items_outside_txn(&items, supplied);
        self.store.with_write_txn(|txn| {
            let mut indexed = 0usize;
            let mut skipped = 0usize;
            for (item, offered) in &prepared {
                let wrote = match offered.as_ref() {
                    Some(vector) => self.upsert_vector_item_with(txn, item, Some(vector))?,
                    None => self.upsert_vector_item(txn, item)?,
                };
                if wrote {
                    indexed += 1;
                } else {
                    skipped += 1;
                }
            }
            // The first successful vector write establishes the fingerprint;
            // later incremental writes never overwrite it — only a full rebuild
            // may flip it after an embedder swap.
            if indexed > 0 && self.embedder_fingerprint(txn)?.is_none() {
                self.write_embedder_fingerprint(txn)?;
            }
            Ok(Some((items.len(), indexed, skipped)))
        })
    }

    fn collect_vector_items(
        &self,
        conn: &rusqlite::Connection,
        node_id: &str,
    ) -> Result<Option<Vec<VectorItem>>, CoreError> {
        let row: Option<(String, String, String, Option<String>, String)> = conn
            .query_row(
                "SELECT id, type, title, summary, metadata_json FROM nodes WHERE id=?",
                rusqlite::params![node_id],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .ok();
        let Some((id, node_type, title, summary, metadata_json)) = row else {
            return Ok(None);
        };
        let mut items: Vec<VectorItem> = Vec::new();
        if node_type != "Chunk" {
            let metadata = safe_loads(Some(&metadata_json));
            let text = vector_text_for_node(&title, summary.as_deref().unwrap_or(""), &metadata);
            if !text.is_empty() {
                let mut item_metadata = Map::new();
                item_metadata.insert("node_type".into(), json!(node_type));
                for (key, value) in &metadata {
                    item_metadata.insert(key.clone(), value.clone());
                }
                items.push(VectorItem {
                    item_id: id.clone(),
                    item_type: "node".into(),
                    source_node: id.clone(),
                    text,
                    metadata: item_metadata,
                });
            }
        }
        {
            let mut statement = conn.prepare(
                "SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json \
                 FROM chunks c JOIN nodes n ON n.id=c.id \
                 WHERE c.source_node=? ORDER BY c.created_at ASC, c.id ASC",
            )?;
            let rows = statement.query_map(rusqlite::params![node_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })?;
            for row in rows {
                let (chunk_id, parent, chunk_text, metadata_json) = row?;
                let text = clean_text(chunk_text.as_deref().unwrap_or(""));
                if text.is_empty() {
                    continue;
                }
                let mut metadata = safe_loads(Some(&metadata_json));
                metadata.insert("parent_source_node".into(), json!(parent));
                items.push(VectorItem {
                    item_id: chunk_id.clone(),
                    item_type: "chunk".into(),
                    source_node: chunk_id,
                    text,
                    metadata,
                });
            }
        }
        Ok(Some(items))
    }

    fn embed_items_outside_txn(
        &self,
        items: &[VectorItem],
        supplied: &[(String, super::types::SuppliedVector)],
    ) -> Vec<(VectorItem, Option<super::types::SuppliedVector>)> {
        use super::types::SuppliedVector;
        use crate::pytext::truncate_chars;
        items
            .iter()
            .map(|item| {
                if let Some((_, vector)) = supplied.iter().find(|(id, _)| id == &item.item_id) {
                    return (item.clone(), Some(vector.clone()));
                }
                let text = clean_text(&item.text);
                if text.chars().count() < 2 {
                    return (item.clone(), None);
                }
                let values = self.embedder.embed(&truncate_chars(&text, 50_000));
                (
                    item.clone(),
                    Some(SuppliedVector {
                        values,
                        model_id: self.embedder.model_id().to_string(),
                        dim: self.embedder.dim(),
                    }),
                )
            })
            .collect()
    }

    /// `rebuild_vector_index` — rebuild the derived index without touching
    /// graph content.
    ///
    /// Embed happens *outside* the write txn, the same way
    /// [`Self::write_vectors_with`] already does. Holding BEGIN IMMEDIATE
    /// while hashing every changed passage was the remaining writer stall
    /// on a full rebuild.
    pub fn rebuild_vector_index(
        &self,
        request: &RebuildRequest,
    ) -> Result<RebuildOutcome, CoreError> {
        let op_id = format!(
            "vector-op:{}",
            &sha256_text(&format!(
                "{}:{}",
                py_float_repr(self.clock.unix_time()),
                self.clock.pid()
            ))[..24]
        );
        let requested_at = self.clock.now_iso();
        let started = self.clock.perf_counter();
        let operation = if request.full {
            "rebuild_full"
        } else {
            "rebuild_incremental"
        };
        let items = self.store.with_read_conn(|conn| {
            self.iter_vector_source_items(conn, request.include_nodes, request.include_chunks)
        })?;
        let known = if request.full {
            Vec::new()
        } else {
            self.store
                .with_read_conn(|conn| self.vector_text_hashes(conn))?
        };
        let mut to_embed = Vec::new();
        let mut skipped = 0usize;
        for item in &items {
            let hash = sha256_text(&clean_text(&item.text));
            if known
                .iter()
                .any(|(id, stored)| *id == item.item_id && *stored == hash)
            {
                skipped += 1;
                continue;
            }
            to_embed.push(item.clone());
        }
        let prepared = self.embed_items_outside_txn(&to_embed, &[]);
        let outcome = self.store.with_write_txn(|txn| {
            let mut opening = Map::new();
            opening.insert("include_nodes".into(), json!(request.include_nodes));
            opening.insert("include_chunks".into(), json!(request.include_chunks));
            txn.execute(
                "INSERT INTO vector_index_operations( \
                   id, operation, status, requested_at, started_at, metadata_json) \
                 VALUES (?, ?, 'running', ?, ?, ?)",
                rusqlite::params![
                    op_id,
                    operation,
                    requested_at,
                    requested_at,
                    json_of(&opening)
                ],
            )?;
            if request.full {
                let mut filters: Vec<&str> = Vec::new();
                if request.include_nodes {
                    filters.push("'node'");
                }
                if request.include_chunks {
                    filters.push("'chunk'");
                }
                if !filters.is_empty() {
                    txn.execute_batch(&format!(
                        "DELETE FROM vector_embeddings WHERE item_type IN ({})",
                        filters.join(",")
                    ))?;
                }
            }
            let mut indexed = 0usize;
            for (item, offered) in &prepared {
                if self.upsert_vector_item_with(txn, item, offered.as_ref())? {
                    indexed += 1;
                } else {
                    skipped += 1;
                }
            }
            let total = items.len();
            let duration_ms = self.elapsed_ms(started);
            let mut closing = Map::new();
            closing.insert("include_nodes".into(), json!(request.include_nodes));
            closing.insert("include_chunks".into(), json!(request.include_chunks));
            closing.insert("duration_ms".into(), json!(duration_ms));
            closing.insert("embedding_model".into(), json!(self.embedder.model_id()));
            closing.insert("embedding_dim".into(), json!(self.embedder.dim()));
            txn.execute(
                "UPDATE vector_index_operations \
                 SET status='completed', completed_at=?, items_total=?, \
                     items_indexed=?, items_skipped=?, metadata_json=? \
                 WHERE id=?",
                rusqlite::params![
                    self.clock.now_iso(),
                    total,
                    indexed,
                    skipped,
                    json_of(&closing),
                    op_id,
                ],
            )?;
            // A successful rebuild (re)establishes which embedder built the
            // index — the only path that may flip a recorded fingerprint.
            self.write_embedder_fingerprint(txn)?;
            Ok(RebuildOutcome {
                status: "completed".into(),
                operation_id: op_id.clone(),
                full: request.full,
                items_total: total,
                items_indexed: indexed,
                items_skipped: skipped,
                duration_ms,
                embedding_model: self.embedder.model_id().to_string(),
                embedding_dim: self.embedder.dim(),
            })
        });
        match outcome {
            Ok(outcome) => Ok(outcome),
            Err(error) => {
                // Python records the failure on its own connection and re-raises;
                // the row is the point — a rebuild that died must not look like
                // one that never ran.
                let duration_ms = self.elapsed_ms(started);
                let mut metadata = Map::new();
                metadata.insert("duration_ms".into(), json!(duration_ms));
                let _ = self.store.with_write_txn(|txn| {
                    txn.execute(
                        "INSERT INTO vector_index_operations( \
                           id, operation, status, requested_at, started_at, completed_at, \
                           error_message, metadata_json) \
                         VALUES (?, ?, 'failed', ?, ?, ?, ?, ?) \
                         ON CONFLICT(id) DO UPDATE SET \
                           status='failed', \
                           completed_at=excluded.completed_at, \
                           error_message=excluded.error_message, \
                           metadata_json=excluded.metadata_json",
                        rusqlite::params![
                            op_id,
                            operation,
                            requested_at,
                            requested_at,
                            self.clock.now_iso(),
                            error.to_string(),
                            json_of(&metadata),
                        ],
                    )?;
                    Ok(())
                });
                Err(error)
            }
        }
    }

    /// `_vector_text_hashes` — `item_id → text_hash` for rows *this* embedder
    /// wrote. Rows from another embedder are left out, so they compare as
    /// missing and get re-embedded, which is what an embedder swap requires.
    fn vector_text_hashes(
        &self,
        conn: &rusqlite::Connection,
    ) -> Result<Vec<(String, String)>, CoreError> {
        let mut statement = conn.prepare(
            "SELECT item_id, text_hash FROM vector_embeddings \
             WHERE embedding_model=? AND embedding_dim=?",
        )?;
        let rows = statement.query_map(
            rusqlite::params![self.embedder.model_id(), self.embedder.dim() as i64],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    /// `_iter_vector_source_items` — the graph's embeddable text.
    fn iter_vector_source_items(
        &self,
        conn: &rusqlite::Connection,
        include_nodes: bool,
        include_chunks: bool,
    ) -> Result<Vec<VectorItem>, CoreError> {
        let mut items: Vec<VectorItem> = Vec::new();
        if include_nodes {
            let mut statement = conn.prepare(
                "SELECT id, type, title, summary, metadata_json FROM nodes \
                 WHERE type <> 'Chunk' ORDER BY updated_at DESC, id ASC",
            )?;
            let rows = statement.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                ))
            })?;
            for row in rows {
                let (id, node_type, title, summary, metadata_json) = row?;
                let metadata = safe_loads(Some(&metadata_json));
                let text =
                    vector_text_for_node(&title, summary.as_deref().unwrap_or(""), &metadata);
                if text.is_empty() {
                    continue;
                }
                let mut item_metadata = Map::new();
                item_metadata.insert("node_type".into(), json!(node_type));
                for (key, value) in &metadata {
                    item_metadata.insert(key.clone(), value.clone());
                }
                items.push(VectorItem {
                    item_id: id.clone(),
                    item_type: "node".into(),
                    source_node: id,
                    text,
                    metadata: item_metadata,
                });
            }
        }
        if include_chunks {
            let mut statement = conn.prepare(
                "SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json \
                 FROM chunks c JOIN nodes n ON n.id=c.id \
                 ORDER BY c.created_at DESC, c.id ASC",
            )?;
            let rows = statement.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })?;
            for row in rows {
                let (chunk_id, parent, chunk_text, metadata_json) = row?;
                let text = clean_text(chunk_text.as_deref().unwrap_or(""));
                if text.is_empty() {
                    continue;
                }
                let mut metadata = safe_loads(Some(&metadata_json));
                metadata.insert("parent_source_node".into(), json!(parent));
                items.push(VectorItem {
                    item_id: chunk_id.clone(),
                    item_type: "chunk".into(),
                    source_node: chunk_id,
                    text,
                    metadata,
                });
            }
        }
        Ok(items)
    }

    /// `_embedder_fingerprint_record`.
    pub(crate) fn embedder_fingerprint(
        &self,
        txn: &Transaction<'_>,
    ) -> Result<Option<(String, i64)>, CoreError> {
        let stored: Option<String> = txn
            .query_row(
                "SELECT value FROM graph_meta WHERE key=?",
                rusqlite::params![EMBEDDER_FINGERPRINT_KEY],
                |row| row.get(0),
            )
            .ok();
        let Some(stored) = stored else {
            return Ok(None);
        };
        let payload = safe_loads(Some(&stored));
        let Some(model_id) = payload
            .get("model_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        else {
            return Ok(None);
        };
        let dim = payload.get("dim").and_then(Value::as_i64).unwrap_or(0);
        Ok(Some((model_id.to_string(), dim)))
    }

    /// `_write_embedder_fingerprint` — in the caller's transaction.
    pub(crate) fn write_embedder_fingerprint(
        &self,
        txn: &Transaction<'_>,
    ) -> Result<(), CoreError> {
        let mut fingerprint = Map::new();
        fingerprint.insert("model_id".into(), json!(self.embedder.model_id()));
        fingerprint.insert("dim".into(), json!(self.embedder.dim()));
        txn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            rusqlite::params![EMBEDDER_FINGERPRINT_KEY, json_of(&fingerprint)],
        )?;
        Ok(())
    }

    /// `round((perf_counter() - started) * 1000, 2)`.
    fn elapsed_ms(&self, started: f64) -> f64 {
        round_to((self.clock.perf_counter() - started) * 1000.0, 2)
    }
}
