//! The two garden HTTP handlers and the `process()` write.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::path::Path;

use axum::extract::{Request, State};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::OrderedMap;
use lattice_core::graph_write::types::{
    ChunkPiece, ExtractReply, IngestContentRequest, IngestionRecord, SuppliedVector,
};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use crate::memory_api::shared::{body_opt_str, body_required_str, json_body, ok_json, BrainState};

use super::vault;

/// The (method, path) table this family mounts, in axum spelling.
pub const MOUNTED: &[(&str, &str)] = &[("POST", "/garden"), ("GET", "/garden/tree")];

/// The mountable router for `latticeai/api/garden.py`.
pub fn router(state: BrainState) -> Router {
    Router::new()
        .route("/garden", post(garden_put))
        .route("/garden/tree", get(garden_tree))
        .with_state(state)
}

async fn garden_put(State(state): State<BrainState>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let payload = match json_body(body).await {
        Ok(payload) => payload,
        Err(refusal) => return refusal,
    };
    let raw = match body_required_str(&payload, "raw_data") {
        Ok(raw) => raw,
        Err(refusal) => return refusal,
    };
    let category = body_opt_str(&payload, "category");
    let request = Request::from_parts(parts, axum::body::Body::empty());
    if let Err(refusal) = state.require_user(request.headers()) {
        return refusal;
    }
    let dir = state.brain_dir().to_path_buf();
    let now = state.now();
    let category = category.clone();
    let saved =
        match tokio::task::spawn_blocking(move || save_note(&dir, &raw, category.as_deref(), &now))
            .await
        {
            Ok(Ok(saved)) => saved,
            Ok(Err(error)) => {
                return crate::memory_api::shared::detail_response(500, &error);
            }
            Err(error) => {
                return crate::memory_api::shared::detail_response(
                    500,
                    &format!("the task did not finish: {error}"),
                );
            }
        };
    let ingest = ingest_note(&state, &saved).await;
    let mut out = OrderedMap::new();
    out.insert("status", Value::String("saved".to_string()));
    out.insert("folder", Value::String(saved.folder.to_string()));
    out.insert("filename", Value::String(saved.filename.clone()));
    out.insert("path", Value::String(saved.path.clone()));
    out.insert("classified_as", Value::String(saved.folder.to_string()));
    out.insert(
        "description",
        Value::String(vault::description(saved.folder).to_string()),
    );
    for (key, value) in ingest {
        out.insert(key, value);
    }
    ok_json(&out)
}

async fn garden_tree(State(state): State<BrainState>, request: Request) -> Response {
    if let Err(refusal) = state.require_user(request.headers()) {
        return refusal;
    }
    let dir = state.brain_dir().to_path_buf();
    match tokio::task::spawn_blocking(move || {
        vault::ensure_structure(
            &dir,
            &vault::stamps(&crate::memory_api::shared::now_iso()).minute,
        );
        vault::get_tree(&dir)
    })
    .await
    {
        Ok(tree) => ok_json(&tree),
        Err(error) => crate::memory_api::shared::detail_response(
            500,
            &format!("the task did not finish: {error}"),
        ),
    }
}

struct Saved {
    folder: &'static str,
    filename: String,
    path: String,
    title: String,
    text: String,
}

fn save_note(dir: &Path, raw: &str, category: Option<&str>, now: &str) -> Result<Saved, String> {
    let stamps = vault::stamps(now);
    vault::ensure_structure(dir, &stamps.minute);
    let folder = vault::folder_for(category, raw);
    let filename = vault::make_filename(raw, &stamps.file);
    let path = dir.join(folder).join(&filename);
    let content = vault::wrap_markdown(raw, folder, &stamps.minute);
    std::fs::write(&path, content.as_bytes()).map_err(|error| error.to_string())?;
    let preview: String = raw.chars().take(200).collect();
    vault::append_log(dir, &preview, folder, &filename, &stamps.date, &stamps.hhmm)
        .map_err(|error| error.to_string())?;
    Ok(Saved {
        folder,
        filename,
        path: path.display().to_string(),
        title: vault::first_line_of(raw).chars().take(80).collect(),
        text: raw.to_string(),
    })
}

const EXTRACT_PATH: &str = "/worker/extract";
const EMBED_PATH: &str = "/worker/embed";

async fn extract_via_seam(seam: Option<&WorkerSeamClient>, text: &str, kind: &str) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(seam) = seam else {
        return ExtractReply::default();
    };
    match seam
        .post_json(EXTRACT_PATH, &json!({"text": text, "kind": kind}))
        .await
    {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

async fn embed_via_seam(seam: Option<&WorkerSeamClient>, text: &str) -> Option<SuppliedVector> {
    if text.trim().is_empty() {
        return None;
    }
    let (model_id, dim, rows) = embed_texts_via_seam(seam, &[text.to_string()]).await?;
    let values = rows.into_iter().next().filter(|row| !row.is_empty())?;
    Some(SuppliedVector {
        model_id,
        dim: if dim == 0 { values.len() } else { dim },
        values,
    })
}

async fn embed_texts_via_seam(
    seam: Option<&WorkerSeamClient>,
    texts: &[String],
) -> Option<(String, usize, Vec<Vec<f64>>)> {
    if texts.is_empty() {
        return None;
    }
    let seam = seam?;
    let payload = seam
        .post_json(EMBED_PATH, &json!({"texts": texts, "kind": "passage"}))
        .await
        .ok()?;
    let rows = payload.get("vectors").and_then(Value::as_array)?;
    let values: Vec<Vec<f64>> = rows
        .iter()
        .map(|row| {
            row.as_array()
                .map(|cells| cells.iter().filter_map(Value::as_f64).collect())
                .unwrap_or_default()
        })
        .collect();
    if values.iter().all(|row| row.is_empty()) {
        return None;
    }
    let model_id = payload
        .get("model_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let dim = payload.get("dim").and_then(Value::as_u64).unwrap_or(0) as usize;
    Some((model_id, dim, values))
}

/// Plain-window chunks (1200 / 160). lattice-retrieval cannot depend on
/// lattice-ingest; this is the legacy `_chunks` walk the engine already hashes.
fn plain_chunk_pieces(text: &str) -> Vec<ChunkPiece> {
    const SIZE: usize = 1200;
    const OVERLAP: usize = 160;
    let cleaned: Vec<char> = text
        .trim_matches(lattice_core::pytext::is_py_space)
        .chars()
        .collect();
    if cleaned.is_empty() {
        return Vec::new();
    }
    let mut pieces = Vec::new();
    let mut start = 0usize;
    while start < cleaned.len() {
        let end = cleaned.len().min(start + SIZE);
        let piece_text: String = cleaned[start..end].iter().collect();
        let mut fields = serde_json::Map::new();
        fields.insert("strategy".into(), json!("plain"));
        fields.insert("start_char".into(), json!(start));
        pieces.push(ChunkPiece {
            text: piece_text,
            fields,
            embedding: None,
        });
        if end >= cleaned.len() {
            break;
        }
        start = end.saturating_sub(OVERLAP);
    }
    pieces
}

fn attach_chunk_embeddings(
    mut chunks: Vec<ChunkPiece>,
    batch: Option<(String, usize, Vec<Vec<f64>>)>,
    agrees: bool,
) -> Vec<ChunkPiece> {
    if !agrees {
        return chunks;
    }
    let Some((model_id, dim, rows)) = batch else {
        return chunks;
    };
    for (piece, values) in chunks.iter_mut().zip(rows) {
        if values.is_empty() {
            continue;
        }
        piece.embedding = Some(SuppliedVector {
            model_id: model_id.clone(),
            dim: if dim == 0 { values.len() } else { dim },
            values,
        });
    }
    chunks
}

/// `_ingest_note` — native `GraphWriter` ingest with W5 extract/embed enrichment.
async fn ingest_note(state: &BrainState, saved: &Saved) -> Vec<(&'static str, Value)> {
    let Some(graph) = state.graph().cloned() else {
        return vec![
            ("graph", Value::String("unavailable".to_string())),
            (
                "graph_detail",
                Value::String("ingestion pipeline not wired".to_string()),
            ),
        ];
    };
    let extract_text = if saved.title.is_empty() {
        saved.text.clone()
    } else {
        format!("{}\n{}", saved.title, saved.text)
    };
    let extracted = extract_via_seam(state.seam(), &extract_text, "document").await;
    let embedding = embed_via_seam(state.seam(), &extract_text).await;
    let native_agrees = match embedding.as_ref() {
        Some(vector) => {
            vector.model_id == graph.embedder().model_id() && vector.dim == graph.embedder().dim()
        }
        None => true,
    };
    let mut chunks = plain_chunk_pieces(&saved.text);
    let chunk_texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
    let chunk_batch = embed_texts_via_seam(state.seam(), &chunk_texts).await;
    let chunk_agrees = match chunk_batch.as_ref() {
        Some((model_id, dim, _)) => {
            model_id == graph.embedder().model_id() && *dim == graph.embedder().dim()
        }
        None => true,
    };
    chunks = attach_chunk_embeddings(chunks, chunk_batch, chunk_agrees);
    let title = saved.title.clone();
    let text = saved.text.clone();
    let source = saved.path.clone();
    let folder = saved.folder.to_string();
    let (outcome, provenance_id) = match tokio::task::spawn_blocking(move || {
        let mut metadata = serde_json::Map::new();
        metadata.insert("garden_folder".into(), json!(folder));
        metadata.insert("pipeline".into(), json!("p-reinforce"));
        let request = IngestContentRequest {
            source_type: "note".into(),
            title: title.clone(),
            text,
            source_uri: Some(source.clone()),
            metadata: metadata.clone(),
            chunks,
            concepts: extracted.concepts,
            triples: extracted.triples,
            semantic: extracted.semantic,
            embedding,
            node_type: Some("Document".into()),
            ..Default::default()
        };
        let outcome = graph.ingest_content(&request)?;
        if native_agrees && !outcome.node_id.is_empty() {
            let _ = graph.write_vectors(&outcome.node_id);
        }
        let receipt = graph.record_ingestion(&IngestionRecord {
            node_id: outcome.node_id.clone(),
            source_type: "note".into(),
            pipeline: "p-reinforce".into(),
            source_uri: Some(source),
            content_hash: outcome.content_hash.clone(),
            title: Some(title),
            duplicate: outcome.duplicate,
            chunk_count: outcome.chunk_count as i64,
            metadata,
            ..Default::default()
        });
        Ok::<_, lattice_core::CoreError>((outcome, receipt.ok().map(|row| row.id)))
    })
    .await
    {
        Ok(Ok((outcome, provenance_id))) => (outcome, provenance_id),
        Ok(Err(error)) => {
            return vec![
                ("graph", Value::String("failed".to_string())),
                ("graph_detail", Value::String(error.to_string())),
            ];
        }
        Err(error) => {
            return vec![
                ("graph", Value::String("failed".to_string())),
                ("graph_detail", Value::String(error.to_string())),
            ];
        }
    };
    vec![
        ("graph", Value::String("ok".to_string())),
        ("graph_node_id", Value::String(outcome.node_id)),
        (
            "provenance_id",
            Value::String(provenance_id.unwrap_or_default()),
        ),
        ("duplicate", Value::Bool(outcome.duplicate)),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_short_note_is_one_plain_chunk() {
        let pieces = plain_chunk_pieces("  garden note about Lattice  ");
        assert_eq!(pieces.len(), 1);
        assert_eq!(pieces[0].text, "garden note about Lattice");
        assert_eq!(pieces[0].fields["strategy"], json!("plain"));
        assert!(plain_chunk_pieces("\n\t").is_empty());
    }

    #[test]
    fn a_long_note_windows_at_the_legacy_size() {
        let text = "α".repeat(1300);
        let pieces = plain_chunk_pieces(&text);
        assert_eq!(pieces.len(), 2);
        assert_eq!(pieces[0].text.chars().count(), 1200);
        assert_eq!(pieces[1].fields["start_char"], json!(1040));
    }
}
