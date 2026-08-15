//! Deleting, flagging, and the local-source writes.
//!
//! Port of `documents.py::delete_document_tree`,
//! `write_master.py::set_node_sensitivity`, `discovery.py`'s two source writes,
//! and the orphan sweep in `discovery_index/cleanup.py` that
//! `remove_local_source` leans on.
//!
//! All four are `GRAPH_MUTATION_OPS` entries the Wave-2 route families added to
//! the Python whitelist (R2 setup demo-corpus delete, R6 local-knowledge watch
//! and workspace indexing removal, R9 network-boundary sensitivity); W3
//! replaces those seam calls with the methods below.

use rusqlite::{ToSql, Transaction};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::primitives::delete_nodes_v2;
use super::pyaux::{py_dumps_ordered, truthy};
use super::GraphWriter;

impl GraphWriter {
    /// `set_node_sensitivity` — mark (or unmark) one node as never-leaving.
    ///
    /// The cloud filter has always looked for this flag; until 10.2.0 nothing
    /// could set it, so the guard was unreachable. This is the user-driven half
    /// — ingestion stamps secret-bearing *paths* automatically, and this covers
    /// what a path cannot tell you, like a note whose content is private.
    ///
    /// Unmarking is allowed and audited by the caller, and clears the reason
    /// with the flag so a stale justification cannot linger.
    pub fn set_node_sensitivity(
        &self,
        node_id: &str,
        local_only: bool,
        reason: Option<&str>,
    ) -> Result<Value, CoreError> {
        self.store.with_write_txn(|txn| {
            let stored: Option<Option<String>> = txn
                .query_row(
                    "SELECT metadata_json FROM nodes WHERE id=?",
                    rusqlite::params![node_id],
                    |row| row.get(0),
                )
                .ok();
            let Some(stored) = stored else {
                return Ok(json!({
                    "ok": false,
                    "node_id": node_id,
                    "reason": "node not found",
                }));
            };
            let mut metadata = safe_loads(stored.as_deref());
            // This writer is `json.dumps(metadata, ensure_ascii=False)` — the
            // one *without* `sort_keys`. CPython's dict remembers the order the
            // document listed, and a key assigned for the first time is
            // appended; `serde_json::Map` is a `BTreeMap` and would file
            // `local_only` alphabetically instead. The stored order is read
            // back so the two agree byte for byte.
            let mut key_order = super::pyaux::object_key_order(stored.as_deref().unwrap_or("{}"));
            if local_only {
                metadata.insert(super::pyaux::LOCAL_ONLY_FLAG.into(), json!(true));
                metadata.insert(
                    super::pyaux::LOCAL_ONLY_REASON.into(),
                    json!(reason
                        .filter(|value| !value.is_empty())
                        .unwrap_or("marked by the user")),
                );
                for key in [
                    super::pyaux::LOCAL_ONLY_FLAG,
                    super::pyaux::LOCAL_ONLY_REASON,
                ] {
                    if !key_order.iter().any(|existing| existing == key) {
                        key_order.push(key.to_string());
                    }
                }
            } else {
                metadata.remove(super::pyaux::LOCAL_ONLY_FLAG);
                metadata.remove(super::pyaux::LOCAL_ONLY_REASON);
                key_order.retain(|key| {
                    key != super::pyaux::LOCAL_ONLY_FLAG && key != super::pyaux::LOCAL_ONLY_REASON
                });
            }
            let key_order: Vec<&str> = key_order.iter().map(String::as_str).collect();
            txn.execute(
                "UPDATE nodes SET metadata_json=?, updated_at=? WHERE id=?",
                rusqlite::params![
                    py_dumps_ordered(&Value::Object(metadata.clone()), &key_order),
                    self.clock.now_iso(),
                    node_id,
                ],
            )?;
            Ok(json!({
                "ok": true,
                "node_id": node_id,
                "local_only": local_only,
                "reason": metadata
                    .get(super::pyaux::LOCAL_ONLY_REASON)
                    .cloned()
                    .unwrap_or(Value::Null),
            }))
        })
    }

    /// `delete_document_tree` — an ingested node plus everything it owns.
    ///
    /// Removes the node, its retrieval chunks (both the `chunks` rows and the
    /// `Chunk` nodes), the auto-extracted Task/Decision nodes that point back at
    /// it, every touching edge, the vector rows, and — only when it becomes
    /// orphaned — the `Source` node it was indexed from. Shared `Concept` nodes
    /// are deliberately left alone: another document may cite them.
    pub fn delete_document_tree(&self, node_id: &str) -> Result<Value, CoreError> {
        let node_id = node_id.trim().to_string();
        if node_id.is_empty() {
            return Ok(json!({"status": "skipped", "removed_nodes": 0}));
        }
        self.store.with_write_txn(|txn| {
            let exists: Option<i64> = txn
                .query_row(
                    "SELECT 1 FROM nodes WHERE id=?",
                    rusqlite::params![node_id],
                    |row| row.get(0),
                )
                .ok();
            if exists.is_none() {
                return Ok(json!({
                    "status": "not_found",
                    "node_id": node_id,
                    "removed_nodes": 0,
                }));
            }
            let mut remove_ids: Vec<String> = vec![node_id.clone()];
            {
                let mut statement = txn.prepare(
                    "SELECT id FROM nodes \
                     WHERE json_extract(metadata_json, '$.source_node') = ?",
                )?;
                let rows = statement
                    .query_map(rusqlite::params![node_id], |row| row.get::<_, String>(0))?;
                for row in rows {
                    let owned = row?;
                    if !remove_ids.contains(&owned) {
                        remove_ids.push(owned);
                    }
                }
            }
            let source_ids: Vec<String> = {
                let mut statement = txn.prepare(
                    "SELECT to_node FROM edges \
                     WHERE from_node=? AND type IN ('indexed_from', 'INDEXED_FROM')",
                )?;
                let rows = statement
                    .query_map(rusqlite::params![node_id], |row| row.get::<_, String>(0))?;
                rows.collect::<Result<Vec<_>, _>>()?
            };

            let placeholders = vec!["?"; remove_ids.len()].join(",");
            let params: Vec<&dyn ToSql> = remove_ids.iter().map(|id| id as &dyn ToSql).collect();
            let doubled: Vec<&dyn ToSql> = params.iter().chain(params.iter()).copied().collect();
            txn.execute(
                &format!("DELETE FROM chunks WHERE source_node IN ({placeholders})"),
                params.as_slice(),
            )?;
            txn.execute(
                &format!(
                    "DELETE FROM edges WHERE from_node IN ({placeholders}) \
                     OR to_node IN ({placeholders})"
                ),
                doubled.as_slice(),
            )?;
            txn.execute(
                &format!(
                    "DELETE FROM vector_embeddings WHERE item_id IN ({placeholders}) \
                     OR source_node IN ({placeholders})"
                ),
                doubled.as_slice(),
            )?;
            txn.execute(
                &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
                params.as_slice(),
            )?;
            delete_nodes_v2(txn, &remove_ids)?;

            let mut removed_sources = 0usize;
            for source_id in &source_ids {
                let still_linked: Option<i64> = txn
                    .query_row(
                        "SELECT 1 FROM edges WHERE from_node=? OR to_node=? LIMIT 1",
                        rusqlite::params![source_id, source_id],
                        |row| row.get(0),
                    )
                    .ok();
                if still_linked.is_some() {
                    continue;
                }
                txn.execute(
                    "DELETE FROM vector_embeddings WHERE item_id=?",
                    rusqlite::params![source_id],
                )?;
                txn.execute("DELETE FROM nodes WHERE id=?", rusqlite::params![source_id])?;
                delete_nodes_v2(txn, std::slice::from_ref(source_id))?;
                removed_sources += 1;
            }
            Ok(json!({
                "status": "ok",
                "node_id": node_id,
                "removed_nodes": remove_ids.len() + removed_sources,
            }))
        })
    }

    /// Remove exactly one node — the legacy row and its `nodes_v2` projection.
    ///
    /// `Ok(false)` means there was no such row. Deliberately narrower than
    /// [`Self::delete_document_tree`]: it removes nothing the caller did not
    /// name, and in particular it leaves the node's edges in place. That is
    /// `delete_self_model_fact`'s Python behaviour verbatim
    /// (`DELETE FROM nodes WHERE id=?` plus the v2 row, nothing else) — a
    /// Self-Model fact's only edge is `PART_OF self:root`, and re-adding the
    /// same fact re-upserts it.
    pub fn delete_node(&self, node_id: &str) -> Result<bool, CoreError> {
        let node_id = node_id.trim().to_string();
        if node_id.is_empty() {
            return Ok(false);
        }
        self.store.with_write_txn(|txn| {
            let removed =
                txn.execute("DELETE FROM nodes WHERE id=?", rusqlite::params![node_id])?;
            delete_nodes_v2(txn, std::slice::from_ref(&node_id))?;
            Ok(removed > 0)
        })
    }

    /// `KGStoreV2.stamp_node_validity` — write a node's validity window.
    ///
    /// Only the fields actually supplied are written; `None` leaves the stored
    /// value alone, so a second resolution cannot silently un-supersede an
    /// earlier one. `Ok(false)` means nothing was written — either no field was
    /// supplied or there is no such row.
    pub fn stamp_node_validity(
        &self,
        node_id: &str,
        valid_from: Option<&str>,
        valid_to: Option<&str>,
        superseded_by: Option<&str>,
    ) -> Result<bool, CoreError> {
        let node_id = node_id.trim().to_string();
        let fields: Vec<(&str, &str)> = [
            ("valid_from", valid_from),
            ("valid_to", valid_to),
            ("superseded_by", superseded_by),
        ]
        .into_iter()
        .filter_map(|(column, value)| value.map(|value| (column, value)))
        .collect();
        if node_id.is_empty() || fields.is_empty() {
            return Ok(false);
        }
        // The column names are this function's own three literals, never a
        // caller's string, so the format! cannot carry a caller's SQL.
        let assignments = fields
            .iter()
            .map(|(column, _)| format!("{column}=?"))
            .collect::<Vec<_>>()
            .join(", ");
        self.store.with_write_txn(|txn| {
            let mut params: Vec<&dyn rusqlite::ToSql> = fields
                .iter()
                .map(|(_, value)| value as &dyn ToSql)
                .collect();
            params.push(&node_id);
            let updated = txn.execute(
                &format!("UPDATE nodes_v2 SET {assignments} WHERE id=?"),
                params.as_slice(),
            )?;
            Ok(updated > 0)
        })
    }

    /// `set_local_source_watch` — turn a connected folder's watcher on or off.
    pub fn set_local_source_watch(
        &self,
        source_id: &str,
        enabled: bool,
    ) -> Result<Value, CoreError> {
        let source_id = source_id.trim().to_string();
        if source_id.is_empty() {
            return Err(CoreError::InvalidRequest("source_id required".into()));
        }
        self.store.with_write_txn(|txn| {
            let exists: Option<String> = txn
                .query_row(
                    "SELECT id FROM knowledge_sources WHERE id=?",
                    rusqlite::params![source_id],
                    |row| row.get(0),
                )
                .ok();
            if exists.is_none() {
                return Err(CoreError::InvalidRequest(format!(
                    "knowledge source not found: {source_id}"
                )));
            }
            txn.execute(
                "UPDATE knowledge_sources SET watch_enabled=?, updated_at=? WHERE id=?",
                rusqlite::params![i64::from(enabled), self.clock.now_iso(), source_id],
            )?;
            Ok(json!({"source_id": source_id, "watch_enabled": enabled}))
        })
    }

    /// `remove_local_source` — drop one approved folder and its derived graph.
    ///
    /// Deliberately non-destructive for the user's files: only index rows,
    /// graph nodes, edges and chunks derived from the source are removed. The
    /// original folder is never touched.
    pub fn remove_local_source(&self, source_id: &str) -> Result<Value, CoreError> {
        let source_id = source_id.trim().to_string();
        if source_id.is_empty() {
            return Err(CoreError::InvalidRequest("source_id required".into()));
        }
        self.store.with_write_txn(|txn| {
            let source: Option<(String, String)> = txn
                .query_row(
                    "SELECT id, root_path FROM knowledge_sources WHERE id=?",
                    rusqlite::params![source_id],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .ok();
            let Some((_, root_path)) = source else {
                return Err(CoreError::InvalidRequest(format!(
                    "knowledge source not found: {source_id}"
                )));
            };
            let graph_node_ids: Vec<String> = {
                let mut statement = txn.prepare(
                    "SELECT graph_node_id FROM local_file_index \
                     WHERE source_id=? AND graph_node_id IS NOT NULL",
                )?;
                let rows = statement.query_map(rusqlite::params![source_id], |row| {
                    row.get::<_, Option<String>>(0)
                })?;
                rows.collect::<Result<Vec<_>, _>>()?
                    .into_iter()
                    .flatten()
                    .filter(|value| !value.is_empty())
                    .collect()
            };
            for graph_node_id in &graph_node_ids {
                self.delete_local_file_graph(txn, graph_node_id)?;
            }
            txn.execute(
                "DELETE FROM local_file_index WHERE source_id=?",
                rusqlite::params![source_id],
            )?;
            txn.execute(
                "DELETE FROM knowledge_sources WHERE id=?",
                rusqlite::params![source_id],
            )?;
            self.cleanup_local_graph_orphans(txn, &source_id)?;
            Ok(json!({
                "source_id": source_id,
                "root_path": root_path,
                "removed_graph_nodes": graph_node_ids.len(),
            }))
        })
    }

    /// `_delete_local_file_graph` — one indexed file's node and what it owns.
    fn delete_local_file_graph(
        &self,
        txn: &Transaction<'_>,
        file_node_id: &str,
    ) -> Result<(), CoreError> {
        if file_node_id.is_empty() {
            return Ok(());
        }
        let file_metadata: Option<Option<String>> = txn
            .query_row(
                "SELECT metadata_json FROM nodes WHERE id=?",
                rusqlite::params![file_node_id],
                |row| row.get(0),
            )
            .ok();
        let source_id = file_metadata
            .flatten()
            .map(|raw| safe_loads(Some(&raw)))
            .and_then(|meta| meta.get("source_id").filter(|value| truthy(value)).cloned())
            .map(|value| super::pyaux::py_str(&value));

        let linked: Vec<(String, String, String)> = {
            let mut statement = txn.prepare(
                "SELECT n.id, n.type, n.metadata_json FROM edges e \
                 JOIN nodes n ON n.id=e.to_node WHERE e.from_node=?",
            )?;
            let rows = statement.query_map(rusqlite::params![file_node_id], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?))
            })?;
            rows.collect::<Result<Vec<_>, _>>()?
        };
        let mut owned_ids: Vec<String> = Vec::new();
        let mut auto_candidate_ids: Vec<String> = Vec::new();
        for (id, node_type, metadata_json) in linked {
            let metadata = safe_loads(Some(&metadata_json));
            let owned = matches!(node_type.as_str(), "Chunk" | "ImageText" | "Section")
                || metadata.get("source_node").and_then(Value::as_str) == Some(file_node_id);
            if owned {
                if !owned_ids.contains(&id) {
                    owned_ids.push(id);
                }
            } else if metadata.get("auto_extracted").map(truthy).unwrap_or(false)
                && metadata.get("source").and_then(Value::as_str) == Some("local_folder")
                && !auto_candidate_ids.contains(&id)
            {
                auto_candidate_ids.push(id);
            }
        }

        txn.execute(
            "DELETE FROM chunks WHERE source_node=?",
            rusqlite::params![file_node_id],
        )?;
        txn.execute(
            "DELETE FROM edges WHERE from_node=? OR to_node=?",
            rusqlite::params![file_node_id, file_node_id],
        )?;
        txn.execute(
            "DELETE FROM nodes WHERE id=?",
            rusqlite::params![file_node_id],
        )?;
        delete_nodes_v2(txn, &[file_node_id.to_string()])?;
        delete_node_set(txn, &owned_ids)?;

        // An auto-extracted concept only goes when nothing outside the set
        // still points at it.
        let mut removable: Vec<String> = Vec::new();
        for node_id in &auto_candidate_ids {
            let mut statement =
                txn.prepare("SELECT from_node, to_node FROM edges WHERE from_node=? OR to_node=?")?;
            let rows = statement.query_map(rusqlite::params![node_id, node_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            let mut all_internal = true;
            for row in rows {
                let (from_node, to_node) = row?;
                if !(auto_candidate_ids.contains(&from_node)
                    && auto_candidate_ids.contains(&to_node))
                {
                    all_internal = false;
                    break;
                }
            }
            if all_internal {
                removable.push(node_id.clone());
            }
        }
        delete_node_set(txn, &removable)?;
        if let Some(source_id) = source_id {
            self.cleanup_local_graph_orphans(txn, &source_id)?;
        }
        Ok(())
    }

    /// `_cleanup_local_graph_orphans` — sweep childless Folder/Drive/Computer.
    fn cleanup_local_graph_orphans(
        &self,
        txn: &Transaction<'_>,
        source_id: &str,
    ) -> Result<(), CoreError> {
        loop {
            let folders: Vec<(String, String)> = {
                let mut statement =
                    txn.prepare("SELECT id, metadata_json FROM nodes WHERE type='Folder'")?;
                let rows =
                    statement.query_map([], |row| Ok((row.get(0)?, row.get::<_, String>(1)?)))?;
                rows.collect::<Result<Vec<_>, _>>()?
            };
            let mut leaf_ids: Vec<String> = Vec::new();
            for (id, metadata_json) in folders {
                let metadata = safe_loads(Some(&metadata_json));
                if metadata.get("source_id").and_then(Value::as_str) != Some(source_id) {
                    continue;
                }
                let has_children: Option<i64> = txn
                    .query_row(
                        "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                        rusqlite::params![id],
                        |row| row.get(0),
                    )
                    .ok();
                if has_children.is_none() {
                    leaf_ids.push(id);
                }
            }
            if leaf_ids.is_empty() {
                break;
            }
            let placeholders = vec!["?"; leaf_ids.len()].join(",");
            let params: Vec<&dyn ToSql> = leaf_ids.iter().map(|id| id as &dyn ToSql).collect();
            let doubled: Vec<&dyn ToSql> = params.iter().chain(params.iter()).copied().collect();
            txn.execute(
                &format!(
                    "DELETE FROM edges WHERE from_node IN ({placeholders}) \
                     OR to_node IN ({placeholders})"
                ),
                doubled.as_slice(),
            )?;
            txn.execute(
                &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
                params.as_slice(),
            )?;
            delete_nodes_v2(txn, &leaf_ids)?;
        }
        for node_type in ["Drive", "Computer"] {
            let ids: Vec<String> = {
                let mut statement = txn.prepare("SELECT id FROM nodes WHERE type=?")?;
                let rows = statement
                    .query_map(rusqlite::params![node_type], |row| row.get::<_, String>(0))?;
                rows.collect::<Result<Vec<_>, _>>()?
            };
            let mut removable: Vec<String> = Vec::new();
            for id in ids {
                let has_children: Option<i64> = txn
                    .query_row(
                        "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                        rusqlite::params![id],
                        |row| row.get(0),
                    )
                    .ok();
                if has_children.is_none() {
                    removable.push(id);
                }
            }
            if removable.is_empty() {
                continue;
            }
            let placeholders = vec!["?"; removable.len()].join(",");
            let params: Vec<&dyn ToSql> = removable.iter().map(|id| id as &dyn ToSql).collect();
            let doubled: Vec<&dyn ToSql> = params.iter().chain(params.iter()).copied().collect();
            txn.execute(
                &format!(
                    "DELETE FROM edges WHERE from_node IN ({placeholders}) \
                     OR to_node IN ({placeholders})"
                ),
                doubled.as_slice(),
            )?;
            txn.execute(
                &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
                params.as_slice(),
            )?;
            delete_nodes_v2(txn, &removable)?;
        }
        Ok(())
    }

    /// Direct `_upsert_node` access for callers that already know the rows.
    ///
    /// The seam W3 replaces for `/knowledge-graph/nodes`-shaped writes.
    pub fn upsert_nodes(&self, nodes: &[super::types::NodeSpec]) -> Result<Vec<String>, CoreError> {
        self.upsert_nodes_with_vectors(nodes, &[])
    }

    /// [`Self::upsert_nodes`], filing a supplied vector for each matching
    /// `node.id`. Unnamed nodes still hash inline.
    pub fn upsert_nodes_with_vectors(
        &self,
        nodes: &[super::types::NodeSpec],
        supplied: &[(String, super::types::SuppliedVector)],
    ) -> Result<Vec<String>, CoreError> {
        self.store.with_write_txn(|txn| {
            let mut ids = Vec::with_capacity(nodes.len());
            for node in nodes {
                let offered = supplied
                    .iter()
                    .find(|(id, _)| id == &node.id)
                    .map(|(_, vector)| vector);
                ids.push(self.upsert_node_with_vector(txn, node, offered)?);
            }
            Ok(ids)
        })
    }

    /// Direct `_upsert_edge` access, same contract as [`Self::upsert_nodes`].
    pub fn upsert_edges(&self, edges: &[super::types::EdgeSpec]) -> Result<Vec<String>, CoreError> {
        self.store.with_write_txn(|txn| {
            let mut ids = Vec::with_capacity(edges.len());
            for edge in edges {
                ids.push(self.upsert_edge(txn, edge)?);
            }
            Ok(ids)
        })
    }
}

/// The nested `delete_nodes` helper inside `_delete_local_file_graph`.
fn delete_node_set(txn: &Transaction<'_>, ids: &[String]) -> Result<(), CoreError> {
    if ids.is_empty() {
        return Ok(());
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let params: Vec<&dyn ToSql> = ids.iter().map(|id| id as &dyn ToSql).collect();
    let doubled: Vec<&dyn ToSql> = params.iter().chain(params.iter()).copied().collect();
    txn.execute(
        &format!("DELETE FROM chunks WHERE source_node IN ({placeholders})"),
        params.as_slice(),
    )?;
    txn.execute(
        &format!(
            "DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})"
        ),
        doubled.as_slice(),
    )?;
    txn.execute(
        &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
        params.as_slice(),
    )?;
    delete_nodes_v2(txn, ids)?;
    Ok(())
}
