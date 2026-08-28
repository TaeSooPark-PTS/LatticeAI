//! Folder ingest: walk, fingerprint-skip, write, report.
//!
//! Split out of ``ingest.rs`` so the HTTP handlers stay under the line
//! budget and the skip machinery has one home. Unchanged files never reach
//! parse / extract / embed.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Value};

use super::ingest::{next_job_id, now_stamp, write_job};
use super::{enrich, LocalFilesState};
use crate::fingerprint::{self, SkipDecision};
use crate::watch::{walk_folder, ScannedFile, WatchConfig};
use crate::worker::{NoteIngestor, NoteSubmission};

/// What one folder pass produced.
pub(super) struct FolderOutcome {
    pub ingested: usize,
    pub duplicate: usize,
    pub failed: usize,
    pub skipped_unchanged: usize,
    pub deleted: Vec<String>,
    pub errors: Vec<Value>,
    pub status: String,
}

pub(super) async fn ingest_folder_native(
    state: &Arc<LocalFilesState>,
    graph: GraphWriter,
    path: &str,
    recursive: bool,
    background: bool,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<Value, String> {
    let root = Path::new(path);
    let config = WatchConfig {
        recursive,
        ..WatchConfig::default()
    };
    let files = walk_folder(root, &config).map_err(|error| error.to_string())?;
    let store = state
        .store
        .clone()
        .ok_or_else(|| "the Brain is not wired".to_string())?;
    let job_id = next_job_id(&store).await?;
    let now = now_stamp(state);
    write_job(
        &store,
        &job_id,
        "running",
        files.len() as i64,
        0,
        0,
        &json!([]),
        &folder_report(files.len(), 0, 0, &[]),
        &now,
        &now,
    )
    .await?;

    if background {
        let matched = files.len();
        let state = Arc::clone(state);
        let graph = graph.clone();
        let owner = owner.map(str::to_string);
        let workspace_id = workspace_id.map(str::to_string);
        let job_id_task = job_id.clone();
        let walk_root = root.to_path_buf();
        tokio::spawn(async move {
            let _ = run_folder_items(
                &state,
                graph,
                &files,
                &job_id_task,
                &walk_root,
                owner.as_deref(),
                workspace_id.as_deref(),
            )
            .await;
        });
        return Ok(folder_payload(
            path,
            recursive,
            true,
            matched,
            0,
            0,
            0,
            0,
            vec![],
            vec![],
            "scheduled",
            &job_id,
            matched,
        ));
    }

    let outcome =
        run_folder_items(state, graph, &files, &job_id, root, owner, workspace_id).await?;
    if outcome.ingested > 0 {
        super::prune::refresh_sidecar(state.seam()).await;
    }
    Ok(folder_payload(
        path,
        recursive,
        false,
        files.len(),
        outcome.ingested,
        outcome.duplicate,
        outcome.failed,
        outcome.skipped_unchanged,
        outcome.deleted.clone(),
        outcome.errors,
        &outcome.status,
        &job_id,
        0,
    ))
}

async fn run_folder_items(
    state: &Arc<LocalFilesState>,
    graph: GraphWriter,
    files: &[ScannedFile],
    job_id: &str,
    root: &Path,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<FolderOutcome, String> {
    let store = state
        .store
        .clone()
        .ok_or_else(|| "the Brain is not wired".to_string())?;
    let mut ingestor = NoteIngestor::new(graph);
    if let Some(seam) = state.seam() {
        ingestor = ingestor.with_seam(seam.clone());
    }
    let mut ingested = 0usize;
    let mut duplicate = 0usize;
    let mut failed = 0usize;
    let mut skipped_unchanged = 0usize;
    let mut errors = Vec::new();
    let mut present = HashSet::new();
    let mut in_flight = tokio::task::JoinSet::new();
    let owner = owner.map(str::to_string);
    let workspace_id = workspace_id.map(str::to_string);
    let seam = state.seam().cloned();
    for file in files {
        present.insert(file.path.display().to_string());
        while in_flight.len() >= crate::worker::INGEST_INFLIGHT {
            collect_folder_join(
                &mut in_flight,
                &mut ingested,
                &mut duplicate,
                &mut failed,
                &mut skipped_unchanged,
                &mut errors,
            )
            .await;
        }
        let ingestor = ingestor.clone();
        let store = Arc::clone(&store);
        let seam = seam.clone();
        let file = file.clone();
        let owner = owner.clone();
        let workspace_id = workspace_id.clone();
        in_flight.spawn(async move {
            let relative = file.relative_path.clone();
            let result = ingest_one_folder_file(
                &ingestor,
                store.as_ref(),
                seam.as_ref(),
                &file,
                owner.as_deref(),
                workspace_id.as_deref(),
            )
            .await;
            (relative, result)
        });
    }
    while !in_flight.is_empty() {
        collect_folder_join(
            &mut in_flight,
            &mut ingested,
            &mut duplicate,
            &mut failed,
            &mut skipped_unchanged,
            &mut errors,
        )
        .await;
    }
    let deleted = fingerprint::missing_under_root(store.as_ref(), root, &present);
    let status = if failed == 0 {
        "completed"
    } else if ingested > 0 || skipped_unchanged > 0 {
        "partial"
    } else {
        "failed"
    };
    let now = now_stamp(state);
    let _ = write_job(
        &store,
        job_id,
        status,
        files.len() as i64,
        ingested as i64,
        failed as i64,
        &Value::Array(errors.clone()),
        &folder_report(files.len(), skipped_unchanged, ingested, &deleted),
        &now,
        &now,
    )
    .await;
    Ok(FolderOutcome {
        ingested,
        duplicate,
        failed,
        skipped_unchanged,
        deleted,
        errors,
        status: status.to_string(),
    })
}

enum FileResult {
    Skipped,
    Ingested { duplicate: bool },
}

async fn collect_folder_join(
    in_flight: &mut tokio::task::JoinSet<(String, Result<FileResult, String>)>,
    ingested: &mut usize,
    duplicate: &mut usize,
    failed: &mut usize,
    skipped_unchanged: &mut usize,
    errors: &mut Vec<Value>,
) {
    let Some(joined) = in_flight.join_next().await else {
        return;
    };
    match joined {
        Ok((_, Ok(FileResult::Skipped))) => *skipped_unchanged += 1,
        Ok((_, Ok(FileResult::Ingested { duplicate: is_dup }))) => {
            *ingested += 1;
            if is_dup {
                *duplicate += 1;
            }
        }
        Ok((relative, Err(detail))) => {
            *failed += 1;
            errors.push(json!({"path": relative, "detail": detail}));
        }
        Err(error) => {
            *failed += 1;
            errors.push(json!({"path": "", "detail": error.to_string()}));
        }
    }
}

async fn ingest_one_folder_file(
    ingestor: &NoteIngestor,
    store: &lattice_core::db::Store,
    seam: Option<&lattice_core::worker::WorkerSeamClient>,
    file: &ScannedFile,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<FileResult, String> {
    let uri = file.path.display().to_string();
    let stored = fingerprint::lookup(store, &uri);
    if fingerprint::decide(stored.as_ref(), file, None) == SkipDecision::SkipByStamp {
        return Ok(FileResult::Skipped);
    }
    let bytes = std::fs::read(&file.path).map_err(|error| error.to_string())?;
    if fingerprint::decide(stored.as_ref(), file, Some(&bytes)) == SkipDecision::SkipByHash {
        return Ok(FileResult::Skipped);
    }
    let sha = fingerprint::hash_bytes(&bytes);
    let filename = Path::new(&file.relative_path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| file.relative_path.clone());
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
        return Err("no readable text".into());
    }
    let mut metadata = serde_json::Map::new();
    metadata.insert(
        "relative_path".into(),
        Value::from(file.relative_path.as_str()),
    );
    metadata.insert("path".into(), Value::from(uri.as_str()));
    metadata.insert("folder_ingest".into(), Value::Bool(true));
    metadata.insert("detected_by".into(), Value::from("lattice-ingest"));
    fingerprint::attach(&mut metadata, file.size, file.mtime, &sha);
    let note = NoteSubmission {
        title: filename,
        content: text,
        source: Some(uri),
        metadata,
    };
    let receipt = ingestor
        .ingest_note(&note, owner, workspace_id)
        .await
        .map_err(|error| error.to_string())?;
    Ok(FileResult::Ingested {
        duplicate: receipt.duplicate,
    })
}

pub(super) fn folder_report(
    scanned: usize,
    skipped: usize,
    reingested: usize,
    deleted: &[String],
) -> Value {
    json!({
        "scanned": scanned,
        "skipped": skipped,
        "skipped_unchanged": skipped,
        "reingested": reingested,
        "ingested": reingested,
        "processed": reingested,
        "deleted": deleted,
    })
}

#[allow(clippy::too_many_arguments)]
fn folder_payload(
    path: &str,
    recursive: bool,
    background: bool,
    matched: usize,
    ingested: usize,
    duplicate: usize,
    failed: usize,
    skipped_unchanged: usize,
    deleted: Vec<String>,
    errors: Vec<Value>,
    status: &str,
    job_id: &str,
    scheduled: usize,
) -> Value {
    json!({
        "root": path,
        "recursive": recursive,
        "background": background,
        "scanned": matched,
        "matched": matched,
        "ingested": ingested,
        "reingested": ingested,
        "processed": ingested,
        "duplicate": duplicate,
        "failed": failed,
        "skipped": {
            "ignored": 0,
            "extension": 0,
            "too_large": 0,
            "hidden": 0,
            "unchanged": skipped_unchanged
        },
        "skipped_unchanged": skipped_unchanged,
        "deleted": deleted,
        "truncated": false,
        "errors": errors,
        "status": status,
        "job_id": job_id,
        "scheduled": scheduled
    })
}
