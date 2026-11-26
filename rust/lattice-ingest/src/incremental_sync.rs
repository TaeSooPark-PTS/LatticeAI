use std::collections::{BTreeMap, BTreeSet};

use lattice_core::db::CoreError;
use lattice_core::embeddings::{EmbeddingIndexContract, LocalEmbeddingModel};
use lattice_core::graph_write::chunk_parents::chunk_index_text_json;
use lattice_core::graph_write::pyaux::sha256_text;
use lattice_core::graph_write::types::{
    ChunkParentPiece, ChunkPiece, IngestContentRequest, IngestionRecord, SuppliedVector,
};
use lattice_core::graph_write::{GraphWriter, RevisionReplace};
use lattice_core::pytext::clean_text;
use rusqlite::params;
use serde_json::{json, Map, Value};

use crate::chunk_pieces::{chunk_set_for, embedding_inputs};
use crate::sections;
use crate::worker::{NOTE_SOURCE_TYPE, PIPELINE_NAME};

#[derive(Debug, Clone)]
pub struct IncrementalSyncRequest {
    pub title: String,
    pub text: String,
    pub source_uri: String,
    pub moved_from_uri: Option<String>,
    pub workspace_id: Option<String>,
    pub owner: Option<String>,
    pub modified_at: Option<String>,
    pub metadata: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IncrementalSyncReceipt {
    pub node_id: String,
    pub previous_node_id: Option<String>,
    pub moved_from_uri: Option<String>,
    pub total_chunks: usize,
    pub reused_chunks: usize,
    pub reembedded_chunks: usize,
    pub removed_chunks: usize,
    pub embedder_calls: usize,
    pub embedded_text_count: usize,
    pub duplicate: bool,
    pub document_version: i64,
    pub purged_chunks: usize,
}

pub const DOCUMENT_VERSION_FIELD: &str = "document_version";

fn index_hash(text: &str, metadata_json: Option<&str>) -> Option<String> {
    let cleaned = clean_text(&chunk_index_text_json(text, metadata_json));
    (!cleaned.is_empty()).then(|| sha256_text(&cleaned))
}

#[derive(Debug)]
struct PriorRevision {
    node_id: String,
    source_uri: String,
    chunk_hashes: BTreeSet<String>,
    cached_vectors: BTreeMap<String, Vec<f64>>,
    doc_vector: Option<(String, Vec<f64>)>,
    document_version: i64,
}

fn find_active_prior_revision(
    writer: &GraphWriter,
    source_uri: &str,
    moved_from_uri: Option<&str>,
    workspace_id: Option<&str>,
    owner: Option<&str>,
    contract: &EmbeddingIndexContract,
) -> Result<Option<PriorRevision>, CoreError> {
    let target_ws = workspace_id
        .map(str::trim)
        .filter(|w| !w.is_empty())
        .unwrap_or("legacy-global")
        .to_string();
    let candidate_uris: Vec<String> = {
        let mut uris = vec![source_uri.to_string()];
        if let Some(prev_uri) = moved_from_uri.map(str::trim).filter(|u| !u.is_empty()) {
            if prev_uri != source_uri {
                uris.push(prev_uri.to_string());
            }
        }
        uris
    };

    writer.store().with_read_conn(|conn| {
        let mut chosen: Option<(String, String, i64)> = None;
        for uri in &candidate_uris {
            let mut stmt = conn.prepare(
                "SELECT id, metadata_json FROM nodes
                 WHERE type = 'Document'
                   AND COALESCE(json_extract(metadata_json, '$.source_uri'), '') = ?1
                   AND json_extract(metadata_json, '$.superseded_by') IS NULL
                 ORDER BY updated_at DESC, id DESC",
            )?;
            let rows = stmt.query_map(params![uri], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?;
            for row in rows {
                let (id, meta_raw) = row?;
                let meta: Value = serde_json::from_str(&meta_raw).unwrap_or_else(|_| json!({}));
                let row_ws = meta
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .filter(|w| !w.is_empty())
                    .unwrap_or("legacy-global");
                let foreign = meta
                    .get("owner")
                    .and_then(Value::as_str)
                    .is_some_and(|held| !held.is_empty() && Some(held) != owner);
                if row_ws == target_ws && !foreign {
                    let version = meta
                        .get(DOCUMENT_VERSION_FIELD)
                        .and_then(Value::as_i64)
                        .filter(|v| *v > 0)
                        .unwrap_or(1);
                    chosen = Some((id, uri.clone(), version));
                    break;
                }
            }
            if chosen.is_some() {
                break;
            }
        }

        let Some((prev_id, prev_uri, document_version)) = chosen else {
            return Ok(None);
        };

        let decoder = LocalEmbeddingModel::new(contract.dimension);
        let mut chunk_hashes = BTreeSet::new();
        {
            let mut c_stmt =
                conn.prepare("SELECT text, metadata_json FROM chunks WHERE source_node = ?1")?;
            let rows = c_stmt.query_map(params![prev_id], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
            })?;
            for row in rows {
                let (text, metadata_json) = row?;
                if let Some(hash) = index_hash(&text, metadata_json.as_deref()) {
                    chunk_hashes.insert(hash);
                }
            }
        }

        let mut cached_vectors = BTreeMap::new();
        {
            let mut v_stmt = conn.prepare(
                "SELECT v.text_hash, v.embedding
                 FROM vector_embeddings v
                 JOIN chunks c ON c.id = v.item_id
                 WHERE c.source_node = ?1
                   AND v.embedding_model = ?2
                   AND v.embedding_dim = ?3",
            )?;
            let rows = v_stmt.query_map(
                params![prev_id, contract.model_id, contract.dimension as i64],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?)),
            )?;
            for row in rows {
                let (hash, blob) = row?;
                let decoded = decoder.decode(&blob, Some(contract.dimension));
                if decoded.len() == contract.dimension {
                    cached_vectors.insert(hash, decoded);
                }
            }
        }

        let doc_vector = conn
            .query_row(
                "SELECT text_hash, embedding FROM vector_embeddings
                 WHERE item_id = ?1 AND embedding_model = ?2 AND embedding_dim = ?3",
                params![prev_id, contract.model_id, contract.dimension as i64],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?)),
            )
            .ok()
            .and_then(|(hash, blob)| {
                let decoded = decoder.decode(&blob, Some(contract.dimension));
                (decoded.len() == contract.dimension).then_some((hash, decoded))
            });

        Ok(Some(PriorRevision {
            node_id: prev_id,
            source_uri: prev_uri,
            chunk_hashes,
            cached_vectors,
            doc_vector,
            document_version,
        }))
    })
}

#[derive(Debug)]
pub struct IncrementalSyncPlan {
    req: IncrementalSyncRequest,
    prior: Option<PriorRevision>,
    chunks: Vec<ChunkPiece>,
    parents: Vec<ChunkParentPiece>,
    new_hash_set: BTreeSet<String>,
    reused_chunks: usize,
    missing_chunk_indices: Vec<usize>,
    need_doc_embed: bool,
    doc_embedding: Option<SuppliedVector>,
    batch_inputs: Vec<String>,
    duplicate_receipt: Option<IncrementalSyncReceipt>,
}

impl IncrementalSyncPlan {
    pub fn texts_to_embed(&self) -> &[String] {
        &self.batch_inputs
    }

    pub fn is_duplicate(&self) -> bool {
        self.duplicate_receipt.is_some()
    }
}

pub fn plan_incremental_sync(
    writer: &GraphWriter,
    req: &IncrementalSyncRequest,
    contract: &EmbeddingIndexContract,
) -> Result<IncrementalSyncPlan, CoreError> {
    let cleaned_body = clean_text(&req.text);
    if cleaned_body.is_empty() {
        return Err(CoreError::InvalidRequest(
            "incremental sync requires non-empty document text".into(),
        ));
    }

    let prior = find_active_prior_revision(
        writer,
        &req.source_uri,
        req.moved_from_uri.as_deref(),
        req.workspace_id.as_deref(),
        req.owner.as_deref(),
        contract,
    )?;

    let chunk_set = chunk_set_for(&req.text, &req.title, "", &req.title);
    let parents = chunk_set.parents;
    let mut chunks: Vec<ChunkPiece> = chunk_set.children;
    let index_texts = embedding_inputs(&chunks);
    let new_hashes: Vec<String> = index_texts
        .iter()
        .map(|text| sha256_text(&clean_text(text)))
        .collect();
    let new_hash_set: BTreeSet<String> = new_hashes.iter().cloned().collect();

    let content_hash = sha256_text(&format!(
        "{NOTE_SOURCE_TYPE}|{}|{}",
        req.source_uri, req.text
    ));
    let ws_scope = req
        .workspace_id
        .as_deref()
        .filter(|w| !w.is_empty())
        .unwrap_or("legacy-global");
    let expected_node_id = format!(
        "webdoc:{}",
        &sha256_text(&format!("{ws_scope}|{content_hash}"))[..24]
    );

    if let Some(prev) = &prior {
        if prev.node_id == expected_node_id
            && prev.source_uri == req.source_uri
            && prev.doc_vector.is_some()
            && new_hashes
                .iter()
                .all(|h| prev.cached_vectors.contains_key(h))
        {
            let receipt = IncrementalSyncReceipt {
                node_id: prev.node_id.clone(),
                previous_node_id: Some(prev.node_id.clone()),
                moved_from_uri: None,
                total_chunks: chunks.len(),
                reused_chunks: chunks.len(),
                reembedded_chunks: 0,
                removed_chunks: 0,
                embedder_calls: 0,
                embedded_text_count: 0,
                duplicate: true,
                document_version: prev.document_version,
                purged_chunks: 0,
            };
            return Ok(IncrementalSyncPlan {
                req: req.clone(),
                prior,
                chunks,
                parents,
                new_hash_set,
                reused_chunks: receipt.reused_chunks,
                missing_chunk_indices: Vec::new(),
                need_doc_embed: false,
                doc_embedding: None,
                batch_inputs: Vec::new(),
                duplicate_receipt: Some(receipt),
            });
        }
    }

    let mut reused_chunks = 0usize;
    let mut missing_chunk_indices: Vec<usize> = Vec::new();
    let mut batch_inputs: Vec<String> = Vec::new();

    let summary_probe = format!("{}\n{}", clean_text(&req.title), cleaned_body);
    let summary_probe_hash = sha256_text(&summary_probe);
    let reused_doc_vec = prior.as_ref().and_then(|prev| {
        prev.doc_vector.as_ref().and_then(|(h, vec)| {
            let same_title_move = prev.source_uri != req.source_uri
                && new_hash_set == prev.chunk_hashes
                && std::path::Path::new(&prev.source_uri).file_name()
                    == std::path::Path::new(&req.source_uri).file_name();
            if h == &summary_probe_hash || same_title_move {
                Some(SuppliedVector {
                    values: vec.clone(),
                    model_id: contract.model_id.clone(),
                    dim: contract.dimension,
                })
            } else {
                None
            }
        })
    });

    let need_doc_embed = reused_doc_vec.is_none();
    if need_doc_embed {
        batch_inputs.push(format!("{}\n{}", req.title, req.text));
    }

    for (idx, piece) in chunks.iter_mut().enumerate() {
        let h = &new_hashes[idx];
        if let Some(cached) = prior.as_ref().and_then(|p| p.cached_vectors.get(h)) {
            piece.embedding = Some(SuppliedVector {
                values: cached.clone(),
                model_id: contract.model_id.clone(),
                dim: contract.dimension,
            });
            reused_chunks += 1;
        } else {
            missing_chunk_indices.push(idx);
            batch_inputs.push(index_texts[idx].clone());
        }
    }

    Ok(IncrementalSyncPlan {
        req: req.clone(),
        prior,
        chunks,
        parents,
        new_hash_set,
        reused_chunks,
        missing_chunk_indices,
        need_doc_embed,
        doc_embedding: reused_doc_vec,
        batch_inputs,
        duplicate_receipt: None,
    })
}

pub fn commit_incremental_sync(
    writer: &GraphWriter,
    mut plan: IncrementalSyncPlan,
    contract: &EmbeddingIndexContract,
    computed_vectors: Option<Vec<Vec<f64>>>,
) -> Result<IncrementalSyncReceipt, CoreError> {
    if let Some(receipt) = plan.duplicate_receipt {
        return Ok(receipt);
    }

    let mut embedder_calls = 0usize;
    let embedded_text_count = plan.batch_inputs.len();
    if !plan.batch_inputs.is_empty() {
        let computed = computed_vectors.ok_or_else(|| {
            CoreError::InvalidRequest(format!(
                "incremental commit requires {} computed vectors",
                plan.batch_inputs.len()
            ))
        })?;
        embedder_calls = 1;
        if computed.len() != plan.batch_inputs.len() {
            return Err(CoreError::InvalidRequest(format!(
                "incremental embedder returned {} vectors for {} inputs",
                computed.len(),
                plan.batch_inputs.len()
            )));
        }
        let mut iter = computed.into_iter();
        if plan.need_doc_embed {
            if let Some(vec) = iter.next() {
                plan.doc_embedding = Some(SuppliedVector {
                    values: vec,
                    model_id: contract.model_id.clone(),
                    dim: contract.dimension,
                });
            }
        }
        for idx in &plan.missing_chunk_indices {
            if let Some(vec) = iter.next() {
                plan.chunks[*idx].embedding = Some(SuppliedVector {
                    values: vec,
                    model_id: contract.model_id.clone(),
                    dim: contract.dimension,
                });
            }
        }
    }

    let req = &plan.req;
    let chunks = &plan.chunks;
    let doc_embedding = &plan.doc_embedding;
    let reused_chunks = plan.reused_chunks;
    let missing_chunk_indices = &plan.missing_chunk_indices;
    let new_hash_set = &plan.new_hash_set;
    let prior = plan.prior;

    let content_hash = sha256_text(&format!(
        "{NOTE_SOURCE_TYPE}|{}|{}",
        req.source_uri, req.text
    ));
    let document_version = match &prior {
        None => 1,
        Some(prev) if prev_matches_content(prev, req, &content_hash) => prev.document_version,
        Some(prev) => prev.document_version + 1,
    };

    let mut meta = req.metadata.clone();
    if let Some(moved) = &req.moved_from_uri {
        meta.insert("moved_from_uri".into(), json!(moved));
    }
    meta.insert("incremental_reused_chunks".into(), json!(reused_chunks));
    meta.insert(
        "incremental_reembedded_chunks".into(),
        json!(missing_chunk_indices.len()),
    );
    meta.insert(DOCUMENT_VERSION_FIELD.into(), json!(document_version));
    meta.insert("parent_chunks".into(), json!(plan.parents.len()));

    let ingest_req = IngestContentRequest {
        source_type: NOTE_SOURCE_TYPE.into(),
        title: req.title.clone(),
        text: req.text.clone(),
        source_uri: Some(req.source_uri.clone()),
        owner: req.owner.clone(),
        workspace_id: req.workspace_id.clone(),
        modified_at: req.modified_at.clone(),
        metadata: meta.clone(),
        node_type: Some("Document".into()),
        chunks: chunks.clone(),
        parents: plan.parents.clone(),
        embedding: doc_embedding.clone(),
        ..Default::default()
    };

    let recorded_at = req
        .modified_at
        .clone()
        .filter(|at| !at.trim().is_empty())
        .unwrap_or_else(|| writer.clock().now_iso());
    let revision = writer.replace_document_revision(
        &ingest_req,
        &RevisionReplace {
            prior_node_id: prior.as_ref().map(|prev| prev.node_id.clone()),
            subject: req.title.clone(),
            recorded_at,
        },
    )?;
    let purged_chunks = revision.purged_chunks;
    let outcome = revision.ingest;
    let overlay = sections::build_overlay(
        chunks,
        &outcome.chunk_ids,
        &outcome.node_id,
        req.owner.as_deref(),
        req.workspace_id.as_deref(),
    );
    if !overlay.is_empty() {
        writer.upsert_nodes(&overlay.nodes)?;
        writer.upsert_edges(&overlay.edges)?;
    }

    let mut supplied_pairs: Vec<(String, SuppliedVector)> = Vec::new();
    if let Some(dv) = &doc_embedding {
        supplied_pairs.push((outcome.node_id.clone(), dv.clone()));
    }
    for (chunk_id, piece) in outcome.chunk_ids.iter().zip(chunks.iter()) {
        if let Some(cv) = &piece.embedding {
            supplied_pairs.push((chunk_id.clone(), cv.clone()));
        }
    }
    let _ = writer.write_vectors_with(&outcome.node_id, &supplied_pairs);

    let mut removed_chunks = 0usize;
    let mut previous_node_id = None;
    let mut resolved_move = None;

    if let Some(prev) = prior {
        previous_node_id = Some(prev.node_id.clone());
        if prev.source_uri != req.source_uri {
            resolved_move = Some(prev.source_uri.clone());
        }
        removed_chunks = prev.chunk_hashes.difference(new_hash_set).count();
    }

    let _ = writer.record_ingestion(&IngestionRecord {
        node_id: outcome.node_id.clone(),
        source_type: NOTE_SOURCE_TYPE.into(),
        pipeline: PIPELINE_NAME.into(),
        source_uri: Some(req.source_uri.clone()),
        content_hash: outcome.content_hash.clone(),
        title: Some(req.title.clone()),
        owner: req.owner.clone(),
        workspace_id: req.workspace_id.clone(),
        captured_at: outcome.captured_at.clone(),
        modified_at: req.modified_at.clone(),
        embedded: true,
        linked: !overlay.is_empty(),
        duplicate: outcome.duplicate,
        agent_used: None,
        chunk_count: outcome.chunk_count as i64,
        permissions: Map::new(),
        metadata: meta,
    });

    Ok(IncrementalSyncReceipt {
        node_id: outcome.node_id,
        previous_node_id,
        moved_from_uri: resolved_move,
        total_chunks: chunks.len(),
        reused_chunks,
        reembedded_chunks: missing_chunk_indices.len(),
        removed_chunks,
        embedder_calls,
        embedded_text_count,
        duplicate: outcome.duplicate,
        document_version,
        purged_chunks,
    })
}

fn prev_matches_content(
    prev: &PriorRevision,
    req: &IncrementalSyncRequest,
    content_hash: &str,
) -> bool {
    let ws_scope = req
        .workspace_id
        .as_deref()
        .filter(|w| !w.is_empty())
        .unwrap_or("legacy-global");
    let expected = format!(
        "webdoc:{}",
        &sha256_text(&format!("{ws_scope}|{content_hash}"))[..24]
    );
    prev.node_id == expected
}

pub fn sync_document_incremental<F>(
    writer: &GraphWriter,
    req: &IncrementalSyncRequest,
    contract: &EmbeddingIndexContract,
    mut embed_texts: F,
) -> Result<IncrementalSyncReceipt, CoreError>
where
    F: FnMut(&[String]) -> Result<Vec<Vec<f64>>, String>,
{
    let plan = plan_incremental_sync(writer, req, contract)?;
    if plan.is_duplicate() {
        return commit_incremental_sync(writer, plan, contract, None);
    }
    let computed = if plan.texts_to_embed().is_empty() {
        None
    } else {
        Some(embed_texts(plan.texts_to_embed()).map_err(CoreError::InvalidRequest)?)
    };
    commit_incremental_sync(writer, plan, contract, computed)
}

pub fn reconcile_deleted_uris(
    writer: &GraphWriter,
    deleted_uris: &[String],
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<usize, CoreError> {
    let target_ws = workspace_id
        .map(str::trim)
        .filter(|w| !w.is_empty())
        .unwrap_or("legacy-global");
    let mut removed_docs = 0usize;
    for uri in deleted_uris {
        let ids: Vec<String> = writer.store().with_read_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT id, metadata_json FROM nodes
                 WHERE type = 'Document'
                   AND COALESCE(json_extract(metadata_json, '$.source_uri'), '') = ?1",
            )?;
            let rows = stmt.query_map(params![uri], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?;
            let mut matched = Vec::new();
            for row in rows {
                let (id, meta_raw) = row?;
                let meta: Value = serde_json::from_str(&meta_raw).unwrap_or_else(|_| json!({}));
                let ws = meta
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .filter(|w| !w.is_empty())
                    .unwrap_or("legacy-global");
                let held = meta
                    .get("owner")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if ws == target_ws && held == owner.unwrap_or_default() {
                    matched.push(id);
                }
            }
            Ok(matched)
        })?;
        for id in ids {
            let res = writer.delete_document_tree(&id)?;
            if res.get("status").and_then(Value::as_str) == Some("ok") {
                removed_docs += 1;
            }
        }
    }
    Ok(removed_docs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::db::Store;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn incremental_sync_reuses_unchanged_chunks_handles_moves_and_retires_deleted_paragraphs() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("open graph writer");
        let embedder = LocalEmbeddingModel::new(384);
        let contract = embedder.contract();

        let embedded_items = AtomicUsize::new(0);
        let mut counting_embed = |texts: &[String]| -> Result<Vec<Vec<f64>>, String> {
            embedded_items.fetch_add(texts.len(), Ordering::SeqCst);
            Ok(texts.iter().map(|t| embedder.embed(t)).collect())
        };

        let p1 = format!("# Sec 1\n{}", "Alpha architecture paragraph. ".repeat(25));
        let p2 = format!("# Sec 2\n{}", "Beta storage engine details. ".repeat(25));
        let p3 = format!(
            "# Sec 3\n{}",
            "Gamma secret obsolete legacy note. ".repeat(25)
        );
        let doc_v1 = format!("{p1}\n\n{p2}\n\n{p3}");

        let r1 = sync_document_incremental(
            &writer,
            &IncrementalSyncRequest {
                title: "handbook.md".into(),
                text: doc_v1.clone(),
                source_uri: "/vault/handbook.md".into(),
                moved_from_uri: None,
                workspace_id: Some("personal".into()),
                owner: Some("owner@local".into()),
                modified_at: Some("2026-11-19T01:00:00Z".into()),
                metadata: Map::new(),
            },
            &contract,
            &mut counting_embed,
        )
        .expect("v1 sync");
        assert!(r1.total_chunks >= 3);
        assert_eq!(r1.reused_chunks, 0);
        assert_eq!(r1.reembedded_chunks, r1.total_chunks);
        let initial_embeds = embedded_items.load(Ordering::SeqCst);
        assert_eq!(initial_embeds, 1 + r1.total_chunks);

        let r_dup = sync_document_incremental(
            &writer,
            &IncrementalSyncRequest {
                title: "handbook.md".into(),
                text: doc_v1,
                source_uri: "/vault/handbook.md".into(),
                moved_from_uri: None,
                workspace_id: Some("personal".into()),
                owner: Some("owner@local".into()),
                modified_at: Some("2026-11-19T01:05:00Z".into()),
                metadata: Map::new(),
            },
            &contract,
            &mut counting_embed,
        )
        .expect("dup sync");
        assert!(r_dup.duplicate);
        assert_eq!(r_dup.embedder_calls, 0);
        assert_eq!(embedded_items.load(Ordering::SeqCst), initial_embeds);

        let p3_new = format!(
            "# Sec 3\n{}",
            "Delta modern incremental watcher. ".repeat(25)
        );
        let doc_v2 = format!("{p1}\n\n{p2}\n\n{p3_new}");
        let r2 = sync_document_incremental(
            &writer,
            &IncrementalSyncRequest {
                title: "handbook.md".into(),
                text: doc_v2.clone(),
                source_uri: "/vault/handbook.md".into(),
                moved_from_uri: None,
                workspace_id: Some("personal".into()),
                owner: Some("owner@local".into()),
                modified_at: Some("2026-11-19T02:00:00Z".into()),
                metadata: Map::new(),
            },
            &contract,
            &mut counting_embed,
        )
        .expect("v2 sync");
        assert!(
            r2.reused_chunks >= 2,
            "unchanged chunks must reuse cached vectors: {r2:?}"
        );
        assert_eq!(r2.reembedded_chunks, 1);
        assert_eq!(r2.removed_chunks, 1);
        assert_eq!(r2.previous_node_id.as_deref(), Some(r1.node_id.as_str()));

        let leaked_obsolete: i64 = writer
            .store()
            .with_read_conn(|conn| {
                Ok(conn.query_row(
                    "SELECT COUNT(*) FROM chunks WHERE text LIKE '%obsolete legacy note%'",
                    [],
                    |r| r.get(0),
                )?)
            })
            .expect("query obsolete");
        assert_eq!(leaked_obsolete, 0);

        let r_move = sync_document_incremental(
            &writer,
            &IncrementalSyncRequest {
                title: "handbook.md".into(),
                text: doc_v2,
                source_uri: "/vault/archive/handbook.md".into(),
                moved_from_uri: Some("/vault/handbook.md".into()),
                workspace_id: Some("personal".into()),
                owner: Some("owner@local".into()),
                modified_at: Some("2026-11-19T03:00:00Z".into()),
                metadata: Map::new(),
            },
            &contract,
            &mut counting_embed,
        )
        .expect("move sync");
        assert_eq!(r_move.reused_chunks, r_move.total_chunks);
        assert_eq!(r_move.reembedded_chunks, 0);
        assert_eq!(r_move.moved_from_uri.as_deref(), Some("/vault/handbook.md"));

        let deleted = reconcile_deleted_uris(
            &writer,
            &["/vault/archive/handbook.md".to_string()],
            Some("owner@local"),
            Some("personal"),
        )
        .expect("reconcile delete");
        assert_eq!(deleted, 1);
    }
}

#[cfg(test)]
mod ownership_tests {
    use std::sync::Arc;

    use lattice_core::db::Store;

    use super::*;

    #[test]
    fn another_owners_sync_or_delete_of_the_same_path_leaves_the_document_alone() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let contract = LocalEmbeddingModel::new(384).contract();
        let sync = |owner: &str, text: &str| {
            sync_document_incremental(
                &writer,
                &IncrementalSyncRequest {
                    title: "plan.md".into(),
                    text: text.into(),
                    source_uri: "/vault/plan.md".into(),
                    moved_from_uri: None,
                    workspace_id: None,
                    owner: Some(owner.into()),
                    modified_at: None,
                    metadata: Map::new(),
                },
                &contract,
                |texts| Ok(texts.iter().map(|_| vec![0.5; 384]).collect()),
            )
            .expect("sync")
        };
        let chunks = |id: &str| -> i64 {
            writer
                .store()
                .with_read_conn(|conn| {
                    Ok(conn.query_row(
                        "SELECT COUNT(*) FROM chunks WHERE source_node = ?1",
                        [id],
                        |row| row.get(0),
                    )?)
                })
                .expect("count")
        };
        let first = sync("a@x", "port is one");
        let second = sync("b@x", "port is two");
        assert_eq!(second.previous_node_id, None);
        assert_eq!(second.purged_chunks, 0);
        assert_eq!(chunks(&first.node_id), 1);

        let removed =
            reconcile_deleted_uris(&writer, &["/vault/plan.md".to_string()], Some("b@x"), None)
                .expect("delete");
        assert_eq!(removed, 1);
        assert_eq!(chunks(&first.node_id), 1);
        assert_eq!(chunks(&second.node_id), 0);
    }
}
