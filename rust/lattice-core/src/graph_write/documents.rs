use rusqlite::{ToSql, Transaction};
use serde_json::{json, Value};

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::lifecycle::{update_lifecycle_in_txn, KnowledgeLifecycleUpdate};
use super::primitives::delete_nodes_v2;
use super::pyaux::{py_dumps_ordered, truthy};
use super::GraphWriter;

fn delete_node_subgraph(
    txn: &Transaction<'_>,
    node_id: &str,
    clean_provenance: bool,
) -> Result<(bool, usize), CoreError> {
    let existed = txn
        .query_row(
            "SELECT 1 FROM nodes WHERE id=?",
            rusqlite::params![node_id],
            |_| Ok(()),
        )
        .is_ok();
    let existed_v2 = txn
        .query_row(
            "SELECT 1 FROM nodes_v2 WHERE id=?",
            rusqlite::params![node_id],
            |_| Ok(()),
        )
        .is_ok();

    let mut remove_ids: Vec<String> = vec![node_id.to_string()];
    {
        let mut statement = txn.prepare(
            "SELECT id FROM nodes \
             WHERE json_extract(metadata_json, '$.source_node') = ?",
        )?;
        let rows =
            statement.query_map(rusqlite::params![node_id], |row| row.get::<_, String>(0))?;
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
        let rows =
            statement.query_map(rusqlite::params![node_id], |row| row.get::<_, String>(0))?;
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
            "DELETE FROM edges_v2 WHERE source IN ({placeholders}) \
             OR target IN ({placeholders})"
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
    let removed_nodes = txn.execute(
        &format!("DELETE FROM nodes WHERE id IN ({placeholders})"),
        params.as_slice(),
    )?;
    delete_nodes_v2(txn, &remove_ids)?;
    crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, &remove_ids)?;

    if clean_provenance {
        txn.execute(
            &format!("DELETE FROM ingestion_provenance WHERE node_id IN ({placeholders})"),
            params.as_slice(),
        )?;
    }

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
        crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, std::slice::from_ref(source_id))?;
        if clean_provenance {
            txn.execute(
                "DELETE FROM ingestion_provenance WHERE node_id=?",
                rusqlite::params![source_id],
            )?;
        }
        removed_sources += 1;
    }

    if clean_provenance {
        txn.execute(
            "DELETE FROM edges WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = edges.from_node) \
             OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = edges.to_node)",
            [],
        )?;
        txn.execute(
            "DELETE FROM edges_v2 WHERE NOT EXISTS (SELECT 1 FROM nodes_v2 n WHERE n.id = edges_v2.source) \
             OR NOT EXISTS (SELECT 1 FROM nodes_v2 n WHERE n.id = edges_v2.target)",
            [],
        )?;
    }

    let any_removed = existed || existed_v2 || removed_nodes > 0;
    Ok((any_removed, remove_ids.len() + removed_sources))
}

impl GraphWriter {
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
            if self.should_persist_bm25_sidecar(txn) {
                crate::bm25_stats::sync_bm25_node_stats_by_id(txn, node_id)?;
            }
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
            let (_, removed_nodes) = delete_node_subgraph(txn, &node_id, false)?;
            Ok(json!({
                "status": "ok",
                "node_id": node_id,
                "removed_nodes": removed_nodes,
            }))
        })
    }

    pub fn delete_node(&self, node_id: &str) -> Result<bool, CoreError> {
        let node_id = node_id.trim().to_string();
        if node_id.is_empty() {
            return Ok(false);
        }
        self.store.with_write_txn(|txn| {
            let (removed, _) = delete_node_subgraph(txn, &node_id, true)?;
            Ok(removed)
        })
    }

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
            if updated > 0 {
                let _ = update_lifecycle_in_txn(
                    self,
                    txn,
                    &node_id,
                    &KnowledgeLifecycleUpdate {
                        effective_from: valid_from.map(str::to_string),
                        valid_until: valid_to.map(str::to_string),
                        superseded_by: superseded_by.map(str::to_string),
                        ..Default::default()
                    },
                )?;
            }
            Ok(updated > 0)
        })
    }

    pub fn terminate_edge(
        &self,
        edge_id: &str,
        valid_to: &str,
        superseded_by: Option<&str>,
        reason: Option<&str>,
    ) -> Result<bool, CoreError> {
        let edge_id = edge_id.trim().to_string();
        if edge_id.is_empty() || valid_to.trim().is_empty() {
            return Ok(false);
        }
        let reason_str = reason.unwrap_or("terminated").trim().to_string();
        self.store.with_write_txn(|txn| {
            let updated = txn.execute(
                "UPDATE edges_v2
                 SET valid_to = ?1,
                     superseded_by = COALESCE(?2, superseded_by),
                     metadata = json_set(
                         COALESCE(NULLIF(metadata, ''), '{}'),
                         '$.relation_status', 'terminated',
                         '$.termination_reason', ?3
                     )
                 WHERE id = ?4",
                rusqlite::params![valid_to.trim(), superseded_by, reason_str, edge_id],
            )?;
            Ok(updated > 0)
        })
    }

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
        crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, &[file_node_id.to_string()])?;
        delete_node_set(txn, &owned_ids)?;

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
            crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, &leaf_ids)?;
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
            crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, &removable)?;
        }
        Ok(())
    }

    pub fn upsert_nodes(&self, nodes: &[super::types::NodeSpec]) -> Result<Vec<String>, CoreError> {
        self.upsert_nodes_with_vectors(nodes, &[])
    }

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
    crate::bm25_stats::delete_bm25_node_stats_by_ids(txn, ids)?;
    Ok(())
}
