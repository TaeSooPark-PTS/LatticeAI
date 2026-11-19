//! Per-file delivery for watched folders/vaults, including two-phase async
//! incremental chunk sync for both local-hash and semantic embedding contracts.

use std::path::Path;

use lattice_core::worker::WorkerSeamClient;

use crate::incremental_sync::{
    commit_incremental_sync, plan_incremental_sync, IncrementalSyncRequest,
};
use crate::local_files_api::enrich;
use crate::worker::{NoteIngestor, NoteSubmission};

/// What delivering one watched file did.
pub(super) enum Delivered {
    /// Parse / extract / embed ran.
    Ingested,
    /// Fingerprint (stamp or content hash) said the file is unchanged.
    Skipped,
}

/// One detected file → one note in the Brain, through the shared enrich chain.
///
/// The binary half is F-ING's: a `.pdf` / `.docx` in a watched vault goes
/// through `POST /worker/parse` exactly as the upload door sends it, so the
/// note carries the document's text rather than its bytes. A file the seam
/// cannot read is a named per-file failure, never a failed scan.
///
/// Unchanged bytes (mtime/size moved, sha256 did not) skip parse/extract/embed
/// using the same provenance fingerprint folder ingest records.
#[allow(clippy::too_many_arguments)]
pub(super) async fn deliver(
    ingestor: &NoteIngestor,
    store: Option<&lattice_core::db::Store>,
    seam: Option<&WorkerSeamClient>,
    root: &Path,
    relative_path: &str,
    watch_id: &str,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<Delivered, String> {
    let absolute = root.join(relative_path);
    let uri = absolute.display().to_string();
    let metadata = std::fs::metadata(&absolute).map_err(|error| error.to_string())?;
    let size = metadata.len();
    let mtime = crate::pystr::round3(mtime_seconds(&metadata));
    let stored = store.and_then(|store| crate::fingerprint::lookup(store, &uri));
    if let Some(fp) = stored.as_ref() {
        if fp.stamp_matches(size, mtime) {
            return Ok(Delivered::Skipped);
        }
    }
    let bytes = std::fs::read(&absolute).map_err(|error| error.to_string())?;
    let sha = crate::fingerprint::hash_bytes(&bytes);
    if let Some(fp) = stored.as_ref() {
        if fp.hash_matches(&sha) {
            let _ = crate::fingerprint::restamp(ingestor.graph(), &uri, size, mtime, &sha);
            return Ok(Delivered::Skipped);
        }
    }
    let filename = Path::new(relative_path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| relative_path.to_string());
    let text = if enrich::needs_parse(&filename, &bytes) {
        enrich::parse_via_seam(seam, &filename, &bytes)
            .await
            .as_ref()
            .map(enrich::parsed_text)
            .unwrap_or_default()
    } else {
        String::from_utf8_lossy(&bytes).into_owned()
    };
    if text.trim().is_empty() {
        return Err(
            "no readable text: the file is binary and the parse seam did not answer".into(),
        );
    }
    let mut note = NoteSubmission::from_watched_file(root, relative_path, &text, Some(watch_id));
    crate::fingerprint::attach(&mut note.metadata, size, mtime, &sha);
    if stored.is_some()
        && try_incremental_sync_async(ingestor, seam, &note, &uri, owner, workspace_id).await
    {
        return Ok(Delivered::Ingested);
    }
    ingestor
        .ingest_note(&note, owner, workspace_id)
        .await
        .map(|_| Delivered::Ingested)
        .map_err(|error| error.to_string())
}

/// Two-phase async incremental chunk sync:
/// 1. `plan_incremental_sync` (blocking SQLite read) collects only uncached texts.
/// 2. Async `/worker/embed` (for semantic contracts) or local hash embedder computes vectors.
/// 3. `commit_incremental_sync` (blocking SQLite write) commits atomically & retires stale chunks.
async fn try_incremental_sync_async(
    ingestor: &NoteIngestor,
    seam: Option<&WorkerSeamClient>,
    note: &NoteSubmission,
    uri: &str,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> bool {
    let Ok(Some(contract)) = ingestor.graph().current_index_contract() else {
        return false;
    };
    let sync_req = IncrementalSyncRequest {
        title: note.title.clone(),
        text: note.content.clone(),
        source_uri: uri.to_string(),
        moved_from_uri: None,
        workspace_id: workspace_id.map(str::to_string),
        owner: owner.map(str::to_string),
        modified_at: None,
        metadata: note.metadata.clone(),
    };
    let graph = ingestor.graph().clone();
    let plan_contract = contract.clone();
    let Ok(Ok(plan)) = tokio::task::spawn_blocking(move || {
        plan_incremental_sync(&graph, &sync_req, &plan_contract)
    })
    .await
    else {
        return false;
    };

    let texts = plan.texts_to_embed().to_vec();
    let computed = if texts.is_empty() {
        None
    } else if contract.is_semantic() {
        let Some(client) = seam else {
            return false;
        };
        let outcome = enrich::embed_texts_with_contract(Some(client), &texts).await;
        let Some(batch) = outcome.batch() else {
            return false;
        };
        if batch.rows.len() != texts.len()
            || batch.contract.dimension != contract.dimension
            || batch.contract.model_id != contract.model_id
        {
            return false;
        }
        Some(batch.rows.clone())
    } else {
        let local_model = lattice_core::embeddings::LocalEmbeddingModel::new(contract.dimension);
        Some(texts.iter().map(|t| local_model.embed(t)).collect())
    };

    let graph = ingestor.graph().clone();
    let commit_contract = contract.clone();
    matches!(
        tokio::task::spawn_blocking(move || {
            commit_incremental_sync(&graph, plan, &commit_contract, computed)
        })
        .await,
        Ok(Ok(_))
    )
}

fn mtime_seconds(metadata: &std::fs::Metadata) -> f64 {
    use std::time::UNIX_EPOCH;
    let Ok(modified) = metadata.modified() else {
        return 0.0;
    };
    match modified.duration_since(UNIX_EPOCH) {
        Ok(since) => since.as_secs_f64(),
        Err(error) => -error.duration().as_secs_f64(),
    }
}
