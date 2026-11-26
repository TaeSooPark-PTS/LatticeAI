use crate::db::CoreError;
use crate::temporal::stamp_temporal_fact;

use super::chunk_parents::{purge_document_chunks, purge_stale_chunks};
use super::ingest::IngestOutcome;
use super::types::IngestContentRequest;
use super::GraphWriter;

#[derive(Debug, Clone, Default)]
pub struct RevisionReplace {
    pub prior_node_id: Option<String>,
    pub subject: String,
    pub recorded_at: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RevisionOutcome {
    pub ingest: IngestOutcome,
    pub purged_chunks: usize,
}

impl GraphWriter {
    pub fn replace_document_revision(
        &self,
        request: &IngestContentRequest,
        replace: &RevisionReplace,
    ) -> Result<RevisionOutcome, CoreError> {
        let prior = replace
            .prior_node_id
            .as_deref()
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .map(str::to_string);
        let (ingest, purged_chunks) =
            self.ingest_content_with(request, &mut |txn, content_id, chunk_ids, parent_ids| {
                let mut purged = if request.chunks.is_empty() {
                    0
                } else {
                    purge_stale_chunks(txn, content_id, chunk_ids, parent_ids)?
                };
                if let Some(prior_id) = prior.as_deref().filter(|id| *id != content_id) {
                    stamp_temporal_fact(
                        txn,
                        content_id,
                        &replace.subject,
                        &replace.recorded_at,
                        None,
                        &replace.recorded_at,
                        Some(prior_id),
                    )?;
                    purged += purge_document_chunks(txn, prior_id)?;
                }
                Ok(purged)
            })?;
        Ok(RevisionOutcome {
            ingest,
            purged_chunks,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use serde_json::{json, Map};

    use super::*;
    use crate::db::Store;
    use crate::graph_write::chunk_parents::{load_chunk_parents, PARENT_INDEX_FIELD};
    use crate::graph_write::types::{ChunkParentPiece, ChunkPiece};

    fn piece(text: &str, parent: Option<usize>) -> ChunkPiece {
        let mut fields = Map::new();
        fields.insert("strategy".into(), json!("prose"));
        fields.insert("context_prefix".into(), json!("Document: guide"));
        if let Some(index) = parent {
            fields.insert(PARENT_INDEX_FIELD.into(), json!(index));
        }
        ChunkPiece {
            text: text.into(),
            fields,
            embedding: None,
        }
    }

    fn request(
        text: &str,
        chunks: Vec<ChunkPiece>,
        parents: Vec<ChunkParentPiece>,
    ) -> IngestContentRequest {
        IngestContentRequest {
            source_type: "note".into(),
            title: "guide".into(),
            text: text.into(),
            source_uri: Some("/vault/guide.md".into()),
            workspace_id: Some("personal".into()),
            chunks,
            parents,
            ..Default::default()
        }
    }

    fn count(writer: &GraphWriter, sql: &str) -> i64 {
        writer
            .store()
            .with_read_conn(|conn| Ok(conn.query_row(sql, [], |r| r.get(0))?))
            .expect("count")
    }

    #[test]
    fn a_revision_replace_purges_every_index_of_the_prior_chunks_in_one_transaction() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let parents = vec![ChunkParentPiece {
            text: "alpha one alpha two".into(),
            fields: Map::new(),
        }];
        let first = writer
            .replace_document_revision(
                &request(
                    "alpha one alpha two",
                    vec![piece("alpha one", Some(0)), piece("alpha two", Some(0))],
                    parents,
                ),
                &RevisionReplace::default(),
            )
            .expect("first");
        assert_eq!(first.purged_chunks, 0);
        assert_eq!(first.ingest.chunk_ids.len(), 2);
        let parent_id: String = writer
            .store()
            .with_read_conn(|conn| {
                Ok(conn.query_row(
                    "SELECT json_extract(metadata_json, '$.parent_id') FROM chunks LIMIT 1",
                    [],
                    |r| r.get(0),
                )?)
            })
            .expect("parent id");
        let loaded = writer
            .store()
            .with_read_conn(|conn| load_chunk_parents(conn, std::slice::from_ref(&parent_id)))
            .expect("parents");
        assert_eq!(loaded[&parent_id].text, "alpha one alpha two");
        let vector_text_hash: String = writer
            .store()
            .with_read_conn(|conn| {
                Ok(conn.query_row(
                    "SELECT text_hash FROM vector_embeddings WHERE item_type='chunk' ORDER BY item_id LIMIT 1",
                    [],
                    |r| r.get(0),
                )?)
            })
            .expect("hash");
        let contextual = [
            crate::sha256_text(&crate::clean_text("Document: guide\nalpha one")),
            crate::sha256_text(&crate::clean_text("Document: guide\nalpha two")),
        ];
        assert!(contextual.contains(&vector_text_hash));

        let second = writer
            .replace_document_revision(
                &request("beta only", vec![piece("beta only", None)], Vec::new()),
                &RevisionReplace {
                    prior_node_id: Some(first.ingest.node_id.clone()),
                    subject: "guide".into(),
                    recorded_at: "2026-11-26T00:00:00Z".into(),
                },
            )
            .expect("second");
        assert_eq!(second.purged_chunks, 2);
        assert_eq!(count(&writer, "SELECT COUNT(*) FROM chunks"), 1);
        assert_eq!(
            count(&writer, "SELECT COUNT(*) FROM nodes WHERE type='Chunk'"),
            1
        );
        assert_eq!(
            count(
                &writer,
                "SELECT COUNT(*) FROM vector_embeddings WHERE item_type='chunk'"
            ),
            1
        );
        assert_eq!(count(&writer, "SELECT COUNT(*) FROM chunk_parents"), 0);
        assert_eq!(
            count(
                &writer,
                "SELECT COUNT(*) FROM bm25_node_stats WHERE node_id LIKE 'chunk:%'"
            ),
            1
        );
        assert_eq!(
            count(
                &writer,
                "SELECT COUNT(*) FROM node_fts WHERE substr(node_id, 1, 6) = 'chunk:'"
            ),
            1
        );
        let superseded: Option<String> = writer
            .store()
            .with_read_conn(|conn| {
                Ok(conn.query_row(
                    "SELECT json_extract(metadata_json, '$.superseded_by') FROM nodes WHERE id = ?1",
                    [first.ingest.node_id.as_str()],
                    |r| r.get(0),
                )?)
            })
            .expect("superseded");
        assert_eq!(superseded.as_deref(), Some(second.ingest.node_id.as_str()));
    }

    #[test]
    fn rechunking_the_same_content_purges_the_stale_chunk_rows_of_that_node() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let text = "gamma one gamma two";
        let old = writer
            .replace_document_revision(
                &request(text, vec![piece(text, None)], Vec::new()),
                &RevisionReplace::default(),
            )
            .expect("old");
        let new = writer
            .replace_document_revision(
                &request(
                    text,
                    vec![piece("gamma one", None), piece("gamma two", None)],
                    Vec::new(),
                ),
                &RevisionReplace {
                    prior_node_id: Some(old.ingest.node_id.clone()),
                    ..RevisionReplace::default()
                },
            )
            .expect("new");
        assert_eq!(old.ingest.node_id, new.ingest.node_id);
        assert_eq!(new.purged_chunks, 1);
        assert_eq!(count(&writer, "SELECT COUNT(*) FROM chunks"), 2);
    }
}
