use rusqlite::Transaction;
use serde_json::{Map, Value};

use crate::db::CoreError;
use crate::pytext::{clean_text, truncate_chars};

use super::pyaux::{json_of, py_str, sha256_text, truthy};
use super::taxonomy::edge_type_from_legacy;
use super::types::{EdgeSpec, NodeSpec, VectorItem};
use super::GraphWriter;

pub(crate) use super::projection_v2::{
    delete_nodes_v2, project_edge_v2, project_node_v2, EdgeProjection, NodeProjection,
};

#[derive(Debug, Clone, Copy)]
pub(crate) struct ChunkRowWrite<'a> {
    pub chunk_id: &'a str,
    pub source_node: &'a str,
    pub text: &'a str,
    pub index_text: &'a str,
    pub metadata: &'a Map<String, Value>,
}

impl GraphWriter {
    pub(crate) fn upsert_node(
        &self,
        txn: &Transaction<'_>,
        spec: &NodeSpec,
    ) -> Result<String, CoreError> {
        self.upsert_node_with_vector(txn, spec, None)
    }

    pub(crate) fn upsert_node_with_vector(
        &self,
        txn: &Transaction<'_>,
        spec: &NodeSpec,
        supplied: Option<&super::types::SuppliedVector>,
    ) -> Result<String, CoreError> {
        let now = self.clock.now_iso();
        let title_s = truncate_chars(&spec.title, 240);
        let summary_s = truncate_chars(&spec.summary, 1000);
        let meta_json = json_of(&spec.metadata);
        let workspace_id = self.resolve_workspace(spec.workspace_id.as_deref());
        project_node_v2(
            txn,
            &NodeProjection {
                node_id: &spec.id,
                node_type: &spec.node_type,
                title: &title_s,
                summary: Some(&summary_s),
                metadata_json: Some(&meta_json),
                created_at: Some(&now),
                updated_at: &now,
                owner: spec.owner.as_deref(),
                workspace_id: workspace_id.as_deref(),
                visibility: spec.visibility.as_deref(),
            },
        )?;
        txn.execute(
            "INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?) \
             ON CONFLICT(id) DO UPDATE SET \
               title=excluded.title, \
               summary=excluded.summary, \
               metadata_json=excluded.metadata_json, \
               raw_json=excluded.raw_json, \
               updated_at=excluded.updated_at",
            rusqlite::params![
                spec.id,
                spec.node_type,
                title_s,
                summary_s,
                meta_json,
                json_of(&spec.raw),
                now,
                now,
            ],
        )?;
        if self.should_persist_bm25_sidecar(txn) {
            let (stats, _) = crate::bm25_stats::compute_node_bm25_stats(
                &spec.id,
                Some(&spec.node_type),
                Some(&title_s),
                Some(&summary_s),
                Some(&meta_json),
                Some(&now),
            );
            crate::bm25_stats::upsert_bm25_node_stats(txn, &stats)?;
        }
        if spec.node_type != "Chunk" {
            let mut metadata = Map::new();
            metadata.insert("node_type".into(), Value::String(spec.node_type.clone()));
            for (key, value) in &spec.metadata {
                metadata.insert(key.clone(), value.clone());
            }
            self.upsert_vector_item_with(
                txn,
                &VectorItem {
                    item_id: spec.id.clone(),
                    item_type: "node".into(),
                    source_node: spec.id.clone(),
                    text: vector_text_for_node(&title_s, &summary_s, &spec.metadata),
                    metadata,
                },
                supplied,
            )?;
        }
        Ok(spec.id.clone())
    }

    pub(crate) fn upsert_edge(
        &self,
        txn: &Transaction<'_>,
        spec: &EdgeSpec,
    ) -> Result<String, CoreError> {
        let passed_for_legacy = spec
            .legacy_label
            .clone()
            .filter(|label| !label.is_empty())
            .unwrap_or_else(|| spec.edge_type.clone());
        let canonical = edge_type_from_legacy(&spec.edge_type);
        let mut metadata = spec.metadata.clone();
        let explicit_label = spec.legacy_label.as_deref().filter(|l| !l.is_empty());
        if canonical != spec.edge_type
            || explicit_label
                .map(|label| label != canonical)
                .unwrap_or(false)
        {
            let ll = explicit_label
                .map(str::to_string)
                .unwrap_or_else(|| spec.edge_type.clone());
            if ll != canonical {
                metadata
                    .entry("legacy_label".to_string())
                    .or_insert_with(|| Value::String(ll));
            }
        }
        let edge_type = canonical;
        let edge_id = format!(
            "edge:{}",
            &sha256_text(&format!("{}|{edge_type}|{}", spec.from_node, spec.to_node))[..24]
        );
        let now = self.clock.now_iso();
        let meta_json = json_of(&metadata);
        let v2_legacy = if !passed_for_legacy.is_empty() && passed_for_legacy != edge_type {
            match spec.legacy_label {
                Some(_) => passed_for_legacy.clone(),
                None => String::new(),
            }
        } else {
            String::new()
        };
        let v2_eid = if v2_legacy.is_empty() {
            edge_id.clone()
        } else {
            format!(
                "edge:{}",
                &sha256_text(&format!(
                    "{}|{edge_type}|{}|{v2_legacy}",
                    spec.from_node, spec.to_node
                ))[..24]
            )
        };
        project_edge_v2(
            txn,
            &EdgeProjection {
                from_node: &spec.from_node,
                to_node: &spec.to_node,
                edge_type: &edge_type,
                weight: spec.weight,
                metadata_json: Some(&meta_json),
                edge_id: Some(&v2_eid),
                created_at: Some(&now),
                legacy_type: Some(&v2_legacy),
            },
        )?;
        txn.execute(
            "INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?) \
             ON CONFLICT(from_node, to_node, type) DO UPDATE SET \
               weight=max(edges.weight, excluded.weight), \
               metadata_json=excluded.metadata_json",
            rusqlite::params![
                edge_id,
                spec.from_node,
                spec.to_node,
                edge_type,
                spec.weight,
                meta_json,
                now,
            ],
        )?;
        Ok(edge_id)
    }

    pub(crate) fn upsert_chunk(
        &self,
        txn: &Transaction<'_>,
        chunk_id: &str,
        source_node: &str,
        text: &str,
        metadata: &Map<String, Value>,
    ) -> Result<(), CoreError> {
        self.upsert_chunk_with_vector(
            txn,
            &ChunkRowWrite {
                chunk_id,
                source_node,
                text,
                index_text: text,
                metadata,
            },
            None,
        )
    }

    pub(crate) fn upsert_chunk_with_vector(
        &self,
        txn: &Transaction<'_>,
        row: &ChunkRowWrite<'_>,
        supplied: Option<&super::types::SuppliedVector>,
    ) -> Result<(), CoreError> {
        let ChunkRowWrite {
            chunk_id,
            source_node,
            text,
            index_text,
            metadata,
        } = *row;
        txn.execute(
            "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) \
             VALUES (?, ?, ?, ?, ?)",
            rusqlite::params![
                chunk_id,
                source_node,
                text,
                json_of(metadata),
                self.clock.now_iso(),
            ],
        )?;
        let mut vector_metadata = metadata.clone();
        vector_metadata.insert(
            "parent_source_node".into(),
            Value::String(source_node.to_string()),
        );
        self.upsert_vector_item_with(
            txn,
            &VectorItem {
                item_id: chunk_id.to_string(),
                item_type: "chunk".into(),
                source_node: chunk_id.to_string(),
                text: index_text.to_string(),
                metadata: vector_metadata,
            },
            supplied,
        )?;
        Ok(())
    }

    pub(crate) fn upsert_vector_item_with(
        &self,
        txn: &Transaction<'_>,
        item: &VectorItem,
        supplied: Option<&super::types::SuppliedVector>,
    ) -> Result<bool, CoreError> {
        self.upsert_vector_item_with_precomputed(txn, item, supplied, None)
    }

    pub(crate) fn upsert_vector_item_with_precomputed(
        &self,
        txn: &Transaction<'_>,
        item: &VectorItem,
        supplied: Option<&super::types::SuppliedVector>,
        precomputed_native: Option<&[f64]>,
    ) -> Result<bool, CoreError> {
        let text = clean_text(&item.text);
        if text.chars().count() < 2 {
            txn.execute(
                "DELETE FROM vector_embeddings WHERE item_id=?",
                rusqlite::params![item.item_id],
            )?;
            return Ok(false);
        }
        let text_hash = sha256_text(&text);
        let mut metadata_buf;
        let (embedding, dim, model, metadata_ref) = match supplied
            .filter(|vector| !vector.values.is_empty())
        {
            Some(vector) => {
                if vector.dim > 0 && vector.values.len() != vector.dim {
                    return Err(CoreError::InvalidRequest(format!(
                        "supplied vector dimension mismatch for {}: expected {}, got {}",
                        vector.model_id,
                        vector.dim,
                        vector.values.len()
                    )));
                }
                if vector.values.iter().any(|value| !value.is_finite()) {
                    return Err(CoreError::InvalidRequest(format!(
                        "supplied vector for {} contains non-finite values",
                        vector.model_id
                    )));
                }
                let dim = if vector.dim > 0 {
                    vector.dim
                } else {
                    vector.values.len()
                };
                let model = if vector.model_id.is_empty() {
                    self.embedder.model_id().to_string()
                } else {
                    vector.model_id.clone()
                };
                let contract =
                    crate::embeddings::EmbeddingIndexContract::from_parts(None, model.clone(), dim);
                metadata_buf = item.metadata.clone();
                metadata_buf.insert(
                    "embedding_grade".into(),
                    Value::String(contract.grade.clone()),
                );
                metadata_buf.insert("semantic".into(), Value::Bool(contract.is_semantic()));
                metadata_buf.insert(
                    "embedding_provider".into(),
                    Value::String(contract.provider.clone()),
                );
                metadata_buf.insert(
                    "embedding_normalization".into(),
                    Value::String(contract.normalization.clone()),
                );
                (
                    self.embedder.encode(&vector.values),
                    dim as i64,
                    model,
                    &metadata_buf,
                )
            }
            None => {
                let encoded = match precomputed_native {
                    Some(values) if !values.is_empty() => self.embedder.encode(values),
                    _ => self
                        .embedder
                        .encode(&self.embedder.embed(&truncate_chars(&text, 50_000))),
                };
                (
                    encoded,
                    self.embedder.dim() as i64,
                    self.embedder.model_id().to_string(),
                    &item.metadata,
                )
            }
        };
        let existing: Option<(String, i64, String)> = txn
            .query_row(
                "SELECT text_hash, embedding_dim, embedding_model \
                 FROM vector_embeddings WHERE item_id=?",
                rusqlite::params![item.item_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .ok();
        if let Some((hash, stored_dim, stored_model)) = existing {
            if hash == text_hash && stored_dim == dim && stored_model == model {
                return Ok(false);
            }
        }
        txn.execute(
            "INSERT INTO vector_embeddings( \
               item_id, item_type, source_node, text_hash, embedding, \
               embedding_dim, embedding_model, metadata_json, indexed_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) \
             ON CONFLICT(item_id) DO UPDATE SET \
               item_type=excluded.item_type, \
               source_node=excluded.source_node, \
               text_hash=excluded.text_hash, \
               embedding=excluded.embedding, \
               embedding_dim=excluded.embedding_dim, \
               embedding_model=excluded.embedding_model, \
               metadata_json=excluded.metadata_json, \
               indexed_at=excluded.indexed_at",
            rusqlite::params![
                item.item_id,
                item.item_type,
                item.source_node,
                text_hash,
                embedding,
                dim,
                model,
                json_of(metadata_ref),
                self.clock.now_iso(),
            ],
        )?;
        Ok(true)
    }

    pub(crate) fn node_exists(
        &self,
        txn: &Transaction<'_>,
        node_id: &str,
    ) -> Result<bool, CoreError> {
        let found: Option<i64> = txn
            .query_row(
                "SELECT 1 FROM nodes WHERE id = ?",
                rusqlite::params![node_id],
                |row| row.get(0),
            )
            .ok();
        Ok(found.is_some())
    }
}

pub(crate) fn vector_text_for_node(
    title: &str,
    summary: &str,
    metadata: &Map<String, Value>,
) -> String {
    const KEYS: [&str; 8] = [
        "filename",
        "relative_path",
        "file_path",
        "conversation_id",
        "source",
        "category",
        "ext",
        "role",
    ];
    let mut parts: Vec<String> = Vec::new();
    for key in KEYS {
        if let Some(value) = metadata.get(key) {
            if truthy(value) {
                parts.push(py_str(value));
            }
        }
    }
    clean_text(&format!("{title}\n{summary}\n{}", parts.join(" ")))
}

#[cfg(test)]
mod supplied_vector_door {
    use super::*;
    use crate::db::Store;
    use crate::embeddings::LocalEmbeddingModel;
    use crate::graph_write::types::SuppliedVector;
    use crate::graph_write::{GraphWriter, SystemClock};
    use std::sync::Arc;

    fn writer() -> (tempfile::TempDir, GraphWriter) {
        let dir = tempfile::tempdir().expect("tmp");
        let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).expect("store"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        (dir, writer)
    }

    #[test]
    fn the_default_door_still_hashes_and_the_supplied_door_files_the_caller_bytes() {
        let (_dir, writer) = writer();
        let spec = NodeSpec {
            id: "n:hash".into(),
            node_type: "Concept".into(),
            title: "hash me".into(),
            ..NodeSpec::default()
        };
        writer.upsert_nodes(&[spec]).expect("hash write");
        let native = writer.embedder().model_id().to_string();
        let hashed: String = writer
            .store()
            .with_read_conn(|conn| {
                conn.query_row(
                    "SELECT embedding_model FROM vector_embeddings WHERE item_id='n:hash'",
                    [],
                    |row| row.get(0),
                )
                .map_err(crate::db::CoreError::Sqlite)
            })
            .expect("hashed row");
        assert_eq!(hashed, native, "the default door is the hash model");

        let offered = SuppliedVector {
            values: vec![0.5; 8],
            model_id: "openai:text-embedding-3-small".into(),
            dim: 8,
        };
        writer
            .upsert_nodes_with_vectors(
                &[NodeSpec {
                    id: "n:prov".into(),
                    node_type: "Concept".into(),
                    title: "provider me".into(),
                    ..NodeSpec::default()
                }],
                &[("n:prov".into(), offered.clone())],
            )
            .expect("supplied write");
        let (model, dim, bytes): (String, i64, Vec<u8>) = writer
            .store()
            .with_read_conn(|conn| {
                conn.query_row(
                    "SELECT embedding_model, embedding_dim, embedding \
                     FROM vector_embeddings WHERE item_id='n:prov'",
                    [],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .map_err(crate::db::CoreError::Sqlite)
            })
            .expect("supplied row");
        assert_eq!(model, "openai:text-embedding-3-small");
        assert_eq!(dim, 8);
        assert_eq!(
            bytes,
            LocalEmbeddingModel::from_env().encode(&offered.values)
        );
        assert_ne!(model, native);
    }

    #[test]
    fn production_open_stamps_personal_when_no_workspace_is_supplied() {
        let dir = tempfile::tempdir().expect("tmp");
        let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).expect("store"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        writer
            .upsert_nodes(&[NodeSpec {
                id: "n:unstamped".into(),
                node_type: "Concept".into(),
                title: "no workspace".into(),
                ..NodeSpec::default()
            }])
            .expect("write");
        let workspace: Option<String> = writer
            .store()
            .with_read_conn(|conn| {
                conn.query_row(
                    "SELECT workspace_id FROM nodes_v2 WHERE id='n:unstamped'",
                    [],
                    |row| row.get(0),
                )
                .map_err(crate::db::CoreError::Sqlite)
            })
            .expect("row");
        assert_eq!(workspace.as_deref(), Some("personal"));
    }

    #[test]
    fn the_parity_constructor_leaves_legacy_null() {
        let dir = tempfile::tempdir().expect("tmp");
        let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).expect("store"));
        let writer = GraphWriter::with_parts(
            store,
            dir.path().join("blobs"),
            LocalEmbeddingModel::from_env(),
            Arc::new(SystemClock),
        )
        .expect("writer");
        writer
            .upsert_nodes(&[NodeSpec {
                id: "n:legacy".into(),
                node_type: "Concept".into(),
                title: "legacy".into(),
                ..NodeSpec::default()
            }])
            .expect("write");
        let workspace: Option<String> = writer
            .store()
            .with_read_conn(|conn| {
                conn.query_row(
                    "SELECT workspace_id FROM nodes_v2 WHERE id='n:legacy'",
                    [],
                    |row| row.get(0),
                )
                .map_err(crate::db::CoreError::Sqlite)
            })
            .expect("row");
        assert_eq!(workspace, None);
    }
}
