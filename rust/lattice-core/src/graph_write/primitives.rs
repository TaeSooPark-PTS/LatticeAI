//! The write door: `_upsert_node`, `_upsert_edge`, `_upsert_chunk`,
//! `_upsert_vector_item`, and the v2 projection they master through.
//!
//! Port of `lattice_brain/graph/write_master.py` plus
//! `lattice_brain/graph/projection/v2_schema.py`'s per-row half. Every other
//! op in this module tree goes through these four functions, which is the same
//! shape the Python store has and for the same reason: there is exactly one
//! place that decides what a node, an edge, a chunk and a vector row look like.
//!
//! Order matters and is Python's: `nodes_v2` is written **first** (strictly —
//! a projection failure aborts the write rather than leaving the legacy table
//! ahead of it), then the legacy `nodes`/`edges` row, then the vector row.

use rusqlite::{ToSql, Transaction};
use serde_json::{Map, Value};

use crate::db::CoreError;
use crate::pytext::{clean_text, safe_loads, truncate_chars};

use super::pyaux::{json_of, py_str, sha256_text, truthy};
use super::taxonomy::{edge_type_from_legacy, node_type_from_legacy};
use super::types::{EdgeSpec, NodeSpec, VectorItem};
use super::GraphWriter;

/// The arguments `_v2_project_node` takes.
pub(crate) struct NodeProjection<'a> {
    pub node_id: &'a str,
    pub node_type: &'a str,
    pub title: &'a str,
    pub summary: Option<&'a str>,
    pub metadata_json: Option<&'a str>,
    pub created_at: Option<&'a str>,
    /// Python defaults this to `_now()`; both call sites always pass a stamp
    /// (the write door passes the clock, the backfill passes the legacy row's),
    /// so the fallback is unreachable and is not reproduced.
    pub updated_at: &'a str,
    pub owner: Option<&'a str>,
    pub workspace_id: Option<&'a str>,
    pub visibility: Option<&'a str>,
}

/// The arguments `_v2_project_edge` takes.
pub(crate) struct EdgeProjection<'a> {
    pub from_node: &'a str,
    pub to_node: &'a str,
    pub edge_type: &'a str,
    pub weight: f64,
    pub metadata_json: Option<&'a str>,
    pub edge_id: Option<&'a str>,
    pub created_at: Option<&'a str>,
    /// `None` is Python's "argument not passed"; `Some("")` is an explicit
    /// empty legacy label, and the two behave differently.
    pub legacy_type: Option<&'a str>,
}

/// `_v2_project_node` with `strict=True`.
///
/// Called by [`super::schema`]'s backfill with `updated_at` from the legacy
/// row; in that case there is no clock reading at all, which is why this is a
/// free function rather than a `GraphWriter` method.
pub(crate) fn project_node_v2(
    txn: &Transaction<'_>,
    spec: &NodeProjection<'_>,
) -> Result<(), CoreError> {
    let ts = spec.updated_at;
    let norm_type = node_type_from_legacy(spec.node_type);
    let meta = spec
        .metadata_json
        .map(|raw| safe_loads(Some(raw)))
        .unwrap_or_default();
    // `owner or meta["user_email"] or meta["owner"] or None` — Python `or`, so
    // an absent key, a JSON `null` and an empty string all fall through to the
    // next candidate.
    let owner = first_truthy(&[
        spec.owner
            .filter(|value| !value.is_empty())
            .map(str::to_string),
        truthy_str(meta.get("user_email")),
        truthy_str(meta.get("owner")),
    ]);
    let workspace_id = first_truthy(&[
        spec.workspace_id
            .filter(|value| !value.is_empty())
            .map(str::to_string),
        truthy_str(meta.get("workspace_id")),
    ]);
    let visibility = match spec.visibility.filter(|value| !value.is_empty()) {
        Some(value) => value.to_string(),
        None => if workspace_id.is_none() {
            "legacy"
        } else {
            "workspace"
        }
        .to_string(),
    };
    txn.execute(
        "INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs, \
                              owner_id, workspace_id, visibility, \
                              created_at, updated_at, importance_score) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0) \
         ON CONFLICT(id) DO UPDATE SET \
           type=excluded.type, legacy_type=excluded.legacy_type, \
           label=excluded.label, summary=excluded.summary, \
           attrs=excluded.attrs, updated_at=excluded.updated_at, \
           owner_id=COALESCE(excluded.owner_id, nodes_v2.owner_id), \
           workspace_id=COALESCE(excluded.workspace_id, nodes_v2.workspace_id), \
           visibility=CASE WHEN excluded.visibility != 'legacy' \
                           THEN excluded.visibility \
                           ELSE nodes_v2.visibility END",
        rusqlite::params![
            spec.node_id,
            norm_type,
            spec.node_type,
            spec.title,
            spec.summary,
            spec.metadata_json.unwrap_or("{}"),
            owner,
            workspace_id,
            visibility,
            spec.created_at.unwrap_or(ts),
            ts,
        ],
    )?;
    Ok(())
}

/// `_v2_project_edge` with `strict=True`, including the `edge_occurrences` row.
pub(crate) fn project_edge_v2(
    txn: &Transaction<'_>,
    spec: &EdgeProjection<'_>,
) -> Result<(), CoreError> {
    let explicit_legacy_type = spec.legacy_type.is_some();
    let mut leg_type = spec
        .legacy_type
        .map(str::to_string)
        .unwrap_or_else(|| spec.edge_type.to_string());
    // Native canonical writes carry `legacy_type=''` so (source, target, type)
    // is the effective key.
    if !leg_type.is_empty() && edge_type_from_legacy(&leg_type) == leg_type {
        leg_type = String::new();
    }
    let norm_type = edge_type_from_legacy(spec.edge_type);
    let eid = if explicit_legacy_type && !leg_type.is_empty() {
        format!(
            "edge:{}",
            &sha256_text(&format!(
                "{}|{}|{}|{}",
                spec.from_node, norm_type, spec.to_node, leg_type
            ))[..24]
        )
    } else {
        match spec.edge_id {
            Some(id) => id.to_string(),
            None => format!(
                "edge:{}",
                &sha256_text(&format!("{}|{norm_type}|{}", spec.from_node, spec.to_node))[..24]
            ),
        }
    };
    let meta_str = spec.metadata_json.unwrap_or("{}");
    let meta = safe_loads(Some(meta_str));
    let confidence = meta
        .get("confidence")
        .and_then(Value::as_f64)
        .unwrap_or(1.0);
    let created_at = spec.created_at.unwrap_or("");
    txn.execute(
        "INSERT INTO edges_v2(id, source, target, type, legacy_type, weight, \
                              confidence, evidence, metadata, created_by, created_at) \
         VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, 'legacy', ?) \
         ON CONFLICT(source, target, type, legacy_type) DO UPDATE SET \
           weight=max(edges_v2.weight, excluded.weight), \
           confidence=excluded.confidence, \
           metadata=excluded.metadata",
        rusqlite::params![
            eid,
            spec.from_node,
            spec.to_node,
            norm_type,
            leg_type,
            spec.weight,
            confidence,
            meta_str,
            created_at,
        ],
    )?;
    // Every observation is kept: the UNIQUE upsert with `weight=max` alone
    // would erase when a relationship was learned and how often.
    let row_id: Option<String> = txn
        .query_row(
            "SELECT id FROM edges_v2 WHERE source=? AND target=? AND type=? AND legacy_type=?",
            rusqlite::params![spec.from_node, spec.to_node, norm_type, leg_type],
            |row| row.get(0),
        )
        .ok();
    if let Some(row_id) = row_id {
        let source = meta.get("source").cloned().unwrap_or(Value::Null);
        txn.execute(
            "INSERT INTO edge_occurrences(edge_id, observed_at, weight, source) VALUES (?, ?, ?, ?)",
            rusqlite::params![row_id, created_at, spec.weight, JsonScalar(&source)],
        )?;
    }
    Ok(())
}

/// `_v2_delete_nodes` — mirror a legacy deletion (edges cascade on the FK).
pub(crate) fn delete_nodes_v2(txn: &Transaction<'_>, ids: &[String]) -> Result<(), CoreError> {
    if ids.is_empty() {
        return Ok(());
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let params: Vec<&dyn ToSql> = ids.iter().map(|id| id as &dyn ToSql).collect();
    txn.execute(
        &format!("DELETE FROM nodes_v2 WHERE id IN ({placeholders})"),
        params.as_slice(),
    )?;
    Ok(())
}

/// A `serde_json` scalar bound the way Python's sqlite3 binds the same value.
pub(crate) struct JsonScalar<'a>(pub &'a Value);

impl ToSql for JsonScalar<'_> {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        use rusqlite::types::{ToSqlOutput, ValueRef};
        Ok(match self.0 {
            Value::Null => ToSqlOutput::Borrowed(ValueRef::Null),
            Value::Bool(flag) => ToSqlOutput::from(i64::from(*flag)),
            Value::Number(number) => match number.as_i64() {
                Some(value) => ToSqlOutput::from(value),
                None => ToSqlOutput::from(number.as_f64().unwrap_or(0.0)),
            },
            Value::String(text) => ToSqlOutput::Borrowed(ValueRef::Text(text.as_bytes())),
            // A dict or list here would be a `sqlite3.InterfaceError` in
            // Python; nothing in the product puts one in `metadata.source`, and
            // storing its JSON is the closest honest answer to a crash.
            other => ToSqlOutput::from(super::pyaux::py_dumps(other)),
        })
    }
}

/// `value if value else None`, rendered with `str()` — Python's `or` chain.
fn truthy_str(value: Option<&Value>) -> Option<String> {
    value.filter(|value| truthy(value)).map(py_str)
}

fn first_truthy(candidates: &[Option<String>]) -> Option<String> {
    candidates.iter().flatten().next().cloned()
}

impl GraphWriter {
    /// `_upsert_node` — v2 first (strict), then legacy, then the vector row.
    ///
    /// The default door embeds with the native hash model, which is what the
    /// parity goldens cover. Call [`Self::upsert_node_with_vector`] to file a
    /// vector `/worker/embed` already produced.
    pub(crate) fn upsert_node(
        &self,
        txn: &Transaction<'_>,
        spec: &NodeSpec,
    ) -> Result<String, CoreError> {
        self.upsert_node_with_vector(txn, spec, None)
    }

    /// [`Self::upsert_node`], filing `supplied` instead of hashing inline.
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
                workspace_id: spec.workspace_id.as_deref(),
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

    /// `_upsert_edge` — the v4 write door: every new edge stores the canonical
    /// `EdgeType`, and the label it came from survives in `metadata.legacy_label`.
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
        // The normal write door dedupes even for synonym labels (legacy_type='');
        // only an explicit `legacy_label` forces a distinct v2 row.
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

    /// `_upsert_chunk` — the retrieval chunk row plus its vector.
    pub(crate) fn upsert_chunk(
        &self,
        txn: &Transaction<'_>,
        chunk_id: &str,
        source_node: &str,
        text: &str,
        metadata: &Map<String, Value>,
    ) -> Result<(), CoreError> {
        self.upsert_chunk_with_vector(txn, chunk_id, source_node, text, metadata, None)
    }

    /// [`Self::upsert_chunk`], filing `supplied` instead of hashing inline.
    pub(crate) fn upsert_chunk_with_vector(
        &self,
        txn: &Transaction<'_>,
        chunk_id: &str,
        source_node: &str,
        text: &str,
        metadata: &Map<String, Value>,
        supplied: Option<&super::types::SuppliedVector>,
    ) -> Result<(), CoreError> {
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
        // `source_node=chunk_id` is Python's, not a typo: a chunk's vector row
        // points at the Chunk node, which is what the retrieval side joins on.
        self.upsert_vector_item_with(
            txn,
            &VectorItem {
                item_id: chunk_id.to_string(),
                item_type: "chunk".into(),
                source_node: chunk_id.to_string(),
                text: text.to_string(),
                metadata: vector_metadata,
            },
            supplied,
        )?;
        Ok(())
    }

    /// `_upsert_vector_item` — `true` when a vector was actually (re)written.
    ///
    /// Text shorter than two characters deletes the row instead of embedding
    /// noise, and an unchanged `(text_hash, dim, model)` is skipped: that skip
    /// is what makes an incremental rebuild cheap, and it is also why swapping
    /// the embedder re-embeds everything.
    pub(crate) fn upsert_vector_item(
        &self,
        txn: &Transaction<'_>,
        item: &VectorItem,
    ) -> Result<bool, CoreError> {
        self.upsert_vector_item_with(txn, item, None)
    }

    /// [`Self::upsert_vector_item`], filing `supplied` instead of hashing.
    ///
    /// Empty values fall through to the native hash model so a caller that
    /// asked for the door with nothing in hand still writes a row. The skip
    /// key is `(text_hash, dim, model)` of whichever identity we are about to
    /// write, so a provider vector is not "already there" just because a hash
    /// row of the same text exists.
    pub(crate) fn upsert_vector_item_with(
        &self,
        txn: &Transaction<'_>,
        item: &VectorItem,
        supplied: Option<&super::types::SuppliedVector>,
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
        let (embedding, dim, model) = match supplied.filter(|vector| !vector.values.is_empty()) {
            Some(vector) => {
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
                (self.embedder.encode(&vector.values), dim as i64, model)
            }
            None => (
                self.embedder
                    .encode(&self.embedder.embed(&truncate_chars(&text, 50_000))),
                self.embedder.dim() as i64,
                self.embedder.model_id().to_string(),
            ),
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
                json_of(&item.metadata),
                self.clock.now_iso(),
            ],
        )?;
        Ok(true)
    }

    /// `_node_exists`.
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

/// `_vector_text_for_node` — title, summary, and the metadata a search would
/// legitimately match on, in this fixed key order.
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
    use crate::graph_write::GraphWriter;
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
}
