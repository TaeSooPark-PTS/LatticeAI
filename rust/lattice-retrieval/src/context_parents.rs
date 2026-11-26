use std::collections::BTreeMap;

use lattice_core::graph_write::chunk_parents::{
    load_chunk_parents, ChunkParentRow, PARENT_ID_FIELD,
};
use rusqlite::Connection;
use serde_json::Value;

pub const MAX_PARENT_TOKENS: usize = 1200;

pub fn parent_id_of(item: &Value) -> Option<&str> {
    item.get("metadata")
        .and_then(|metadata| metadata.get(PARENT_ID_FIELD))
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty())
}

pub fn parents_for_payload(
    conn: &Connection,
    payload: &Value,
    limit: i64,
) -> BTreeMap<String, ChunkParentRow> {
    let ids: Vec<String> = payload
        .get("matches")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(limit.max(0) as usize)
                .filter_map(parent_id_of)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    load_chunk_parents(conn, &ids).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parent_ids_are_read_from_match_metadata_and_legacy_stores_yield_none() {
        let payload = json!({"matches": [
            {"id": "chunk:a", "metadata": {"parent_id": "parent:1"}},
            {"id": "chunk:b", "metadata": {"parent_id": ""}},
            {"id": "doc:c", "metadata": {}},
            {"id": "doc:d"},
        ]});
        let matches = payload["matches"].as_array().expect("matches");
        assert_eq!(parent_id_of(&matches[0]), Some("parent:1"));
        assert_eq!(parent_id_of(&matches[1]), None);
        assert_eq!(parent_id_of(&matches[2]), None);
        assert_eq!(parent_id_of(&matches[3]), None);
        let legacy = Connection::open_in_memory().expect("memory");
        assert!(parents_for_payload(&legacy, &payload, 5).is_empty());
        assert!(parents_for_payload(&legacy, &json!({}), 5).is_empty());
    }

    use std::sync::Arc;

    use lattice_core::db::Store;
    use lattice_core::graph_write::chunk_parents::PARENT_INDEX_FIELD;
    use lattice_core::graph_write::types::{ChunkParentPiece, ChunkPiece, IngestContentRequest};
    use lattice_core::graph_write::GraphWriter;
    use lattice_core::LocalEmbeddingModel;
    use serde_json::Map;

    use crate::context::{assemble_context, ContextRequest};

    fn child(text: &str, parent: usize) -> ChunkPiece {
        let mut fields = Map::new();
        fields.insert(PARENT_INDEX_FIELD.into(), json!(parent));
        ChunkPiece {
            text: text.into(),
            fields,
            embedding: None,
        }
    }

    fn knowledge_of(out: &Value) -> Value {
        out["trace"]["sections"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|section| section["source"] == "knowledge")
            .cloned()
            .unwrap_or(Value::Null)
    }

    #[test]
    fn retrieved_children_are_replaced_by_their_deduplicated_parents() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("knowledge_graph.sqlite");
        let store = Arc::new(Store::open(&path).expect("store"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let outcome = writer
            .ingest_content(&IngestContentRequest {
                source_type: "note".into(),
                title: "Runbook".into(),
                text: "full runbook text".into(),
                chunks: vec![
                    child("restart the host", 0),
                    child("check the port", 0),
                    child("rotate the keys", 1),
                ],
                parents: vec![
                    ChunkParentPiece {
                        text: "PARENT-A restart the host then check the port".into(),
                        fields: Map::new(),
                    },
                    ChunkParentPiece {
                        text: "PARENT-B rotate the keys every quarter".into(),
                        fields: Map::new(),
                    },
                ],
                ..Default::default()
            })
            .expect("ingest");
        let conn = Connection::open(&path).expect("read conn");
        let mut matches = Vec::new();
        for (rank, id) in outcome.chunk_ids.iter().enumerate() {
            let (title, metadata): (String, String) = conn
                .query_row(
                    "SELECT title, metadata_json FROM nodes WHERE id = ?1",
                    [id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .expect("chunk node");
            let metadata: Value = serde_json::from_str(&metadata).expect("json");
            matches.push(json!({
                "id": id,
                "title": title,
                "summary": "child body",
                "metadata": metadata,
                "score": 1.0 - rank as f64 / 10.0,
            }));
        }
        let request = ContextRequest {
            query: "runbook".into(),
            budget: 2000,
            knowledge: true,
            knowledge_limit: 5,
            knowledge_payload: Some(json!({"matches": matches})),
            ..Default::default()
        };
        let out =
            assemble_context(&conn, &LocalEmbeddingModel::new(384), &request).expect("assemble");
        let text = out["text"].as_str().expect("text");
        assert_eq!(text.matches("PARENT-A").count(), 1, "{text}");
        assert_eq!(text.matches("PARENT-B").count(), 1, "{text}");
        assert!(!text.contains("child body"));
        let provenance = knowledge_of(&out)["provenance"].clone();
        assert_eq!(provenance.as_array().map(Vec::len), Some(2));
        assert_eq!(
            provenance[0]["children"],
            json!([outcome.chunk_ids[0], outcome.chunk_ids[1]])
        );
        assert!(provenance[1]["parent_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("parent:")));
    }

    #[test]
    fn a_legacy_shaped_store_without_parents_keeps_the_child_snippets() {
        let conn = Connection::open_in_memory().expect("memory");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE chunks(id TEXT PRIMARY KEY, source_node TEXT, text TEXT, metadata_json TEXT);",
        )
        .expect("legacy schema");
        let payload = json!({"matches": [
            {"id": "chunk:1", "title": "Old", "summary": "legacy child", "metadata": {"parent_id": "parent:gone"}},
        ]});
        let out = assemble_context(
            &conn,
            &LocalEmbeddingModel::new(384),
            &ContextRequest {
                query: "old".into(),
                budget: 2000,
                knowledge: true,
                knowledge_limit: 5,
                knowledge_payload: Some(payload),
                ..Default::default()
            },
        )
        .expect("assemble on a legacy store");
        assert_eq!(out["text"], "[Knowledge]\n- Old: legacy child");
        assert!(out["trace"].get("token_estimator").is_none());
    }

    #[test]
    fn the_python_era_fixture_store_assembles_knowledge_without_a_parents_table() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("knowledge_graph.sqlite");
        let fixture: std::path::PathBuf = [
            env!("CARGO_MANIFEST_DIR"),
            "..",
            "fixtures",
            "parity_store.sqlite",
        ]
        .iter()
        .collect();
        std::fs::copy(fixture, &path).expect("copy fixture");
        let conn = Connection::open(&path).expect("conn");
        assert!(!lattice_core::graph_write::chunk_parents::chunk_parents_exist(&conn));
        let out = assemble_context(
            &conn,
            &LocalEmbeddingModel::new(384),
            &ContextRequest {
                query: "decision".into(),
                budget: 2000,
                knowledge: true,
                knowledge_limit: 5,
                density_aware: true,
                ..Default::default()
            },
        )
        .expect("assemble on the legacy fixture");
        assert!(out["approx_tokens"].as_u64().is_some());
    }

    #[test]
    fn the_density_aware_budget_counts_korean_by_syllable() {
        let conn = Connection::open_in_memory().expect("memory");
        let notes = "지식그래프연결".repeat(100);
        let request = ContextRequest {
            query: "q".into(),
            budget: 50,
            notes: Some(notes.clone()),
            density_aware: true,
            ..Default::default()
        };
        let out =
            assemble_context(&conn, &LocalEmbeddingModel::new(384), &request).expect("assemble");
        assert_eq!(out["trace"]["token_estimator"], "density");
        assert!(out["approx_tokens"].as_u64().expect("tokens") <= 50);
        let kept = out["text"]
            .as_str()
            .expect("text")
            .chars()
            .filter(|c| *c >= '\u{AC00}')
            .count();
        assert!(kept <= 50, "{kept}");
        let legacy = assemble_context(
            &conn,
            &LocalEmbeddingModel::new(384),
            &ContextRequest {
                density_aware: false,
                ..request
            },
        )
        .expect("assemble");
        let legacy_kept = legacy["text"]
            .as_str()
            .expect("text")
            .chars()
            .filter(|c| *c >= '\u{AC00}')
            .count();
        assert!(legacy_kept > kept * 3, "{legacy_kept} vs {kept}");
    }
}
