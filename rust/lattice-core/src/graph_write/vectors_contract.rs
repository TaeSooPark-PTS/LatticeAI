use rusqlite::{Connection, Transaction};
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::embeddings::EmbeddingIndexContract;
use crate::pytext::safe_loads;

use super::pyaux::json_of;
use super::GraphWriter;

pub const EMBEDDER_FINGERPRINT_KEY: &str = "embedder_fingerprint";
pub const EMBEDDING_INDEX_CONTRACT_KEY: &str = "embedding_index_contract";

impl GraphWriter {
    pub fn read_index_contract(conn: &Connection) -> Option<EmbeddingIndexContract> {
        let contract_raw: Option<String> = conn
            .query_row(
                "SELECT value FROM graph_meta WHERE key=?",
                rusqlite::params![EMBEDDING_INDEX_CONTRACT_KEY],
                |row| row.get(0),
            )
            .ok();
        if let Some(raw) = contract_raw {
            if let Ok(parsed) = serde_json::from_str::<EmbeddingIndexContract>(&raw) {
                if !parsed.model_id.is_empty() && parsed.dimension > 0 {
                    return Some(parsed);
                }
            }
        }
        let fp_raw: Option<String> = conn
            .query_row(
                "SELECT value FROM graph_meta WHERE key=?",
                rusqlite::params![EMBEDDER_FINGERPRINT_KEY],
                |row| row.get(0),
            )
            .ok();
        if let Some(raw) = fp_raw {
            let payload = safe_loads(Some(&raw));
            if let Some(model_id) = payload
                .get("model_id")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
            {
                let dim = payload.get("dim").and_then(Value::as_u64).unwrap_or(0) as usize;
                if dim > 0 {
                    return Some(EmbeddingIndexContract::from_parts(None, model_id, dim));
                }
            }
        }
        None
    }

    pub fn current_index_contract(&self) -> Result<Option<EmbeddingIndexContract>, CoreError> {
        self.store
            .with_read_conn(|conn| Ok(Self::read_index_contract(conn)))
    }

    pub fn record_supplied_contract(
        &self,
        contract: &EmbeddingIndexContract,
    ) -> Result<bool, CoreError> {
        self.store
            .with_write_txn(|txn| Self::record_index_contract_in_txn(txn, contract))
    }

    pub(crate) fn has_conflicting_vectors(
        conn: &Connection,
        contract: &EmbeddingIndexContract,
    ) -> Result<bool, CoreError> {
        let conflict: Option<i64> = conn
            .query_row(
                "SELECT 1 FROM vector_embeddings 
                 WHERE (embedding_model <> ? OR embedding_dim <> ?) 
                   AND ( 
                     (embedding_model NOT LIKE 'lattice-local-hash%' 
                      AND embedding_model NOT LIKE 'lattice-fallback-%') 
                      OR item_type = 'chunk' 
                      OR COALESCE(json_extract(metadata_json, '$.node_type'), '') IN ('Document', 'Note') 
                    ) 
                 LIMIT 1",
                rusqlite::params![contract.model_id, contract.dimension as i64],
                |row| row.get(0),
            )
            .ok();
        Ok(conflict.is_some())
    }

    pub(crate) fn record_index_contract_in_txn(
        txn: &Transaction<'_>,
        contract: &EmbeddingIndexContract,
    ) -> Result<bool, CoreError> {
        let existing = Self::read_index_contract(txn);
        let conflicting_rows = Self::has_conflicting_vectors(txn, contract)?;
        let reindex_required = conflicting_rows
            || existing
                .as_ref()
                .is_some_and(|stored| contract.requires_reindex(stored));

        if existing.is_none() || !conflicting_rows {
            let mut fingerprint = Map::new();
            fingerprint.insert("model_id".into(), json!(contract.model_id));
            fingerprint.insert("dim".into(), json!(contract.dimension));
            txn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                rusqlite::params![EMBEDDER_FINGERPRINT_KEY, json_of(&fingerprint)],
            )?;
            let contract_json =
                serde_json::to_string(contract).unwrap_or_else(|_| "{}".to_string());
            txn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                rusqlite::params![EMBEDDING_INDEX_CONTRACT_KEY, contract_json],
            )?;
        }
        Ok(reindex_required)
    }

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
}
