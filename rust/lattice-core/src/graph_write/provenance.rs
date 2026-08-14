//! Provenance and portability — port of `lattice_brain/graph/provenance.py`.
//!
//! `record_ingestion` is `record_provenance`: an upsert keyed on
//! **`(node, content, source_type, source_uri, pipeline)`** and deliberately
//! *not* on the wall clock. Through 11.0.x the basis included a
//! second-resolution timestamp, which made a record's identity depend on when
//! it happened: re-ingesting unchanged content twice inside one second
//! collapsed onto a single row, and one second later appended a duplicate. That
//! is not an audit trail, it is a race — and it grew this table without bound
//! on every re-scan of an unchanged folder.

use serde_json::{json, Map, Value};

use crate::db::CoreError;

use super::pyaux::{json_of, py_str, sha256_text, truthy};
use super::types::{EdgeSpec, ImportRequest, IngestionRecord, NodeSpec, RebuildRequest};
use super::vectors::RebuildOutcome;
use super::GraphWriter;

/// What `record_provenance` returns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProvenanceReceipt {
    pub id: String,
    pub node_id: String,
    pub created_at: String,
}

impl ProvenanceReceipt {
    /// `{"id": …, "node_id": …, "created_at": …}`.
    pub fn to_json(&self) -> Value {
        json!({"id": self.id, "node_id": self.node_id, "created_at": self.created_at})
    }
}

/// What `import_graph_data` returns (the plan, plus what happened to it).
#[derive(Debug, Clone)]
pub struct ImportOutcome {
    pub mode: String,
    pub nodes: usize,
    pub edges: usize,
    pub chunks: usize,
    pub knowledge_sources: usize,
    pub provenance: usize,
    pub dry_run: bool,
    pub imported: bool,
    /// The re-derived vector index. `None` on a dry run.
    pub index: Option<RebuildOutcome>,
}

impl ImportOutcome {
    /// The plan body, without the `index` block.
    ///
    /// `index` is composed in Python from the read-side `vector_freshness()`
    /// report, which belongs to the retrieval layer rather than to the write
    /// engine; the rebuild it wraps *is* observable here, because it writes a
    /// `vector_index_operations` row.
    pub fn to_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("mode".into(), json!(self.mode));
        map.insert("nodes".into(), json!(self.nodes));
        map.insert("edges".into(), json!(self.edges));
        map.insert("chunks".into(), json!(self.chunks));
        map.insert("knowledge_sources".into(), json!(self.knowledge_sources));
        map.insert("provenance".into(), json!(self.provenance));
        if self.dry_run {
            map.insert("dry_run".into(), json!(true));
        }
        if self.imported {
            map.insert("imported".into(), json!(true));
        }
        Value::Object(map)
    }
}

impl GraphWriter {
    /// `record_provenance` — where an ingested node came from.
    pub fn record_ingestion(
        &self,
        record: &IngestionRecord,
    ) -> Result<ProvenanceReceipt, CoreError> {
        let now = self.clock.now_iso();
        let basis = format!(
            "{}|{}|{}|{}|{}",
            record.node_id,
            record.content_hash.clone().unwrap_or_default(),
            record.source_type,
            record.source_uri.clone().unwrap_or_default(),
            record.pipeline,
        );
        let prov_id = format!("prov:{}", &sha256_text(&basis)[..24]);
        self.store.with_write_txn(|txn| {
            txn.execute(
                "INSERT OR REPLACE INTO ingestion_provenance( \
                   id, node_id, source_type, source_uri, content_hash, title, pipeline, \
                   owner, workspace_id, captured_at, modified_at, embedded, linked, \
                   duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    prov_id,
                    record.node_id,
                    record.source_type,
                    record.source_uri,
                    record.content_hash,
                    record.title,
                    record.pipeline,
                    record.owner,
                    record.workspace_id,
                    record.captured_at,
                    record.modified_at,
                    i64::from(record.embedded),
                    i64::from(record.linked),
                    i64::from(record.duplicate),
                    record.agent_used,
                    record.chunk_count,
                    json_of(&record.permissions),
                    json_of(&record.metadata),
                    now,
                ],
            )?;
            Ok(())
        })?;
        Ok(ProvenanceReceipt {
            id: prov_id,
            node_id: record.node_id.clone(),
            created_at: now,
        })
    }

    /// `import_graph_data` — a logical export back into the store.
    ///
    /// `mode="replace"` clears the graph **inside the same transaction** as the
    /// re-import, so a malformed artifact rolls back to the previous graph
    /// rather than leaving a cleared one. The pre-v4 path committed the clear
    /// first, which is exactly how an import could destroy a Brain.
    pub fn import_graph_data(&self, request: &ImportRequest) -> Result<ImportOutcome, CoreError> {
        let nodes = array_of(&request.data, "nodes");
        let edges = array_of(&request.data, "edges");
        let chunks = array_of(&request.data, "chunks");
        let sources = array_of(&request.data, "knowledge_sources");
        let provenance = array_of(&request.data, "provenance");

        if let Some(header) = request.data.get("header").and_then(Value::as_object) {
            if let Some(incoming) = header.get("graph_schema_version").and_then(Value::as_i64) {
                if incoming > super::schema::GRAPH_SCHEMA_VERSION {
                    return Err(CoreError::InvalidRequest(format!(
                        "Artifact graph_schema_version {incoming} is newer than this \
                         build ({}); refusing to import.",
                        super::schema::GRAPH_SCHEMA_VERSION
                    )));
                }
            }
        }

        let mut outcome = ImportOutcome {
            mode: request.mode.clone(),
            nodes: nodes.len(),
            edges: edges.len(),
            chunks: chunks.len(),
            knowledge_sources: sources.len(),
            provenance: provenance.len(),
            dry_run: request.dry_run,
            imported: false,
            index: None,
        };
        if request.dry_run {
            return Ok(outcome);
        }

        let now = self.clock.now_iso();
        self.store.with_write_txn(|txn| {
            if request.mode == "replace" {
                for table in [
                    "local_file_index",
                    "knowledge_sources",
                    "chunks",
                    "edges",
                    "nodes",
                    "vector_embeddings",
                ] {
                    txn.execute_batch(&format!("DELETE FROM {table}"))?;
                }
                txn.execute_batch("DELETE FROM edges_v2; DELETE FROM nodes_v2;")?;
            }
            for node in &nodes {
                let Some(node) = node.as_object() else { continue };
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: text_of(node, "id"),
                        node_type: text_of(node, "type"),
                        title: optional_text(node, "title").unwrap_or_default(),
                        summary: optional_text(node, "summary").unwrap_or_default(),
                        metadata: crate::pytext::safe_loads(
                            node.get("metadata_json").and_then(Value::as_str),
                        ),
                        raw: crate::pytext::safe_loads(
                            node.get("raw_json").and_then(Value::as_str),
                        ),
                        owner: None,
                        workspace_id: None,
                        visibility: None,
                    },
                )?;
            }
            for chunk in &chunks {
                let Some(chunk) = chunk.as_object() else { continue };
                self.upsert_chunk(
                    txn,
                    &text_of(chunk, "id"),
                    &text_of(chunk, "source_node"),
                    &optional_text(chunk, "text").unwrap_or_default(),
                    &crate::pytext::safe_loads(chunk.get("metadata_json").and_then(Value::as_str)),
                )?;
            }
            for edge in &edges {
                let Some(edge) = edge.as_object() else { continue };
                let meta =
                    crate::pytext::safe_loads(edge.get("metadata_json").and_then(Value::as_str));
                // Whatever label the export carried is preserved, so two legacy
                // strings that normalize to one canonical type stay two rows.
                let legacy_label = meta
                    .get("legacy_label")
                    .filter(|value| truthy(value))
                    .map(py_str)
                    .or_else(|| optional_text(edge, "type").filter(|value| !value.is_empty()));
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: text_of(edge, "from_node"),
                        to_node: text_of(edge, "to_node"),
                        edge_type: text_of(edge, "type"),
                        weight: edge
                            .get("weight")
                            .and_then(Value::as_f64)
                            .filter(|value| *value != 0.0)
                            .unwrap_or(1.0),
                        metadata: meta,
                        legacy_label,
                    },
                )?;
            }
            for source in &sources {
                let Some(source) = source.as_object() else { continue };
                txn.execute(
                    "INSERT OR REPLACE INTO knowledge_sources( \
                       id, root_path, os_type, drive_id, label, status, include_ocr, \
                       watch_enabled, consent_json, created_at, updated_at, last_scanned_at) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rusqlite::params![
                        text_of(source, "id"),
                        text_of(source, "root_path"),
                        text_of(source, "os_type"),
                        optional_text(source, "drive_id"),
                        optional_text(source, "label"),
                        optional_text(source, "status")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| "active".into()),
                        int_of(source, "include_ocr"),
                        int_of(source, "watch_enabled"),
                        optional_text(source, "consent_json")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| "{}".into()),
                        optional_text(source, "created_at")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| now.clone()),
                        optional_text(source, "updated_at")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| now.clone()),
                        optional_text(source, "last_scanned_at"),
                    ],
                )?;
            }
            for record in &provenance {
                let Some(record) = record.as_object() else { continue };
                txn.execute(
                    "INSERT OR REPLACE INTO ingestion_provenance( \
                       id, node_id, source_type, source_uri, content_hash, title, pipeline, \
                       owner, workspace_id, captured_at, modified_at, embedded, linked, \
                       duplicate, agent_used, chunk_count, permissions_json, metadata_json, created_at) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rusqlite::params![
                        text_of(record, "id"),
                        text_of(record, "node_id"),
                        text_of(record, "source_type"),
                        optional_text(record, "source_uri"),
                        optional_text(record, "content_hash"),
                        optional_text(record, "title"),
                        optional_text(record, "pipeline")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| "import".into()),
                        optional_text(record, "owner"),
                        optional_text(record, "workspace_id"),
                        optional_text(record, "captured_at"),
                        optional_text(record, "modified_at"),
                        int_of(record, "embedded"),
                        int_of(record, "linked"),
                        int_of(record, "duplicate"),
                        optional_text(record, "agent_used"),
                        int_of(record, "chunk_count"),
                        optional_text(record, "permissions_json")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| "{}".into()),
                        optional_text(record, "metadata_json")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| "{}".into()),
                        optional_text(record, "created_at")
                            .filter(|value| !value.is_empty())
                            .unwrap_or_else(|| now.clone()),
                    ],
                )?;
            }
            Ok(())
        })?;
        outcome.imported = true;
        // `_reindex_after_import`: the write door already embeds inline, so this
        // is a verification in the common case — but "the index is consistent
        // after an import" has to be a guarantee the import makes, not an
        // accident of where the embedding call happens to live. It is also the
        // only path that records the embedder fingerprint.
        outcome.index = Some(self.rebuild_vector_index(&RebuildRequest {
            full: false,
            include_nodes: true,
            include_chunks: true,
        })?);
        Ok(outcome)
    }
}

fn array_of(data: &Map<String, Value>, key: &str) -> Vec<Value> {
    data.get(key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn text_of(map: &Map<String, Value>, key: &str) -> String {
    map.get(key).map(py_str).unwrap_or_default()
}

fn optional_text(map: &Map<String, Value>, key: &str) -> Option<String> {
    map.get(key).filter(|value| !value.is_null()).map(py_str)
}

/// `int(row.get(key) or 0)` — a JSON `null`, `false` or absent key is 0.
fn int_of(map: &Map<String, Value>, key: &str) -> i64 {
    match map.get(key) {
        Some(Value::Bool(flag)) => i64::from(*flag),
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0),
        Some(Value::String(text)) => text.parse::<i64>().unwrap_or(0),
        _ => 0,
    }
}
