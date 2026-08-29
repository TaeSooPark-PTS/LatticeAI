//! Folder / vault watch: the declaration, the poller, and the delivery.
//!
//! Three things that were three separate half-features until v11.7.0.
//! `crate::watch` could detect a change and `crate::worker::NoteIngestor` could
//! write a note, but nothing joined them — `bridge_wired` reported `false` and
//! was telling the truth. This module is the join, and it is split out of
//! `ingest.rs` because it is a background subsystem rather than another
//! request handler: the HTTP routes in that file call four functions from here
//! (`read_watch_file`, `enable_watch`, `disable_watch`, `vault_watch_on`) and
//! know nothing else about it.
//!
//! Two files hold the state, on purpose:
//!
//! * `folder_watch.json` — the user's *declaration* (which folders, which kind,
//!   on or off), small enough to read by eye;
//! * `folder_watch_state.json` — the machine's, one entry per watch: the
//!   snapshot the next scan diffs against plus the last scan's report. A
//!   per-file `(mtime, size)` map of a vault does not belong in the
//!   declaration, and would be echoed into every `GET /api/ingestion/watch`
//!   response if it lived there. Persisting it is also what makes a restart
//!   resume instead of re-ingesting the folder.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use lattice_auth::OrderedMap;
use lattice_core::graph_write::{Clock, SystemClock};
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use super::{enrich, LocalFilesState};
use crate::watch::{interval_from_env, Snapshot, WatchConfig, WatchScanner};
use crate::worker::{NoteIngestor, NoteSubmission};

fn watch_path(data_dir: &Path) -> PathBuf {
    data_dir.join("folder_watch.json")
}

/// Machine state per watch: the snapshot the next scan diffs against and the
/// last scan's report.
///
/// A second file on purpose. `folder_watch.json` is the user's *declaration*
/// (which folders, which kind, on or off) and stays small enough to read by
/// eye; a per-file `(mtime, size)` snapshot of a vault does not belong in it,
/// and would be echoed into every `GET /api/ingestion/watch` response if it did.
/// Persisting the snapshot is also what makes a restart resume where the last
/// scan stopped instead of re-ingesting the whole folder.
fn watch_state_path(data_dir: &Path) -> PathBuf {
    data_dir.join("folder_watch_state.json")
}

fn read_watch_state(data_dir: &Path) -> Map<String, Value> {
    std::fs::read_to_string(watch_state_path(data_dir))
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

fn write_watch_state(data_dir: &Path, state: &Map<String, Value>) {
    let _ = std::fs::create_dir_all(data_dir);
    let _ = std::fs::write(
        watch_state_path(data_dir),
        serde_json::to_string_pretty(&Value::Object(state.clone())).unwrap_or_else(|_| "{}".into()),
    );
}

/// The declaration file, watches array only.
fn stored_watches(data_dir: &Path) -> Vec<Value> {
    std::fs::read_to_string(watch_path(data_dir))
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .and_then(|value| value.get("watches").and_then(Value::as_array).cloned())
        .unwrap_or_default()
}

/// One watch as the API reports it: the declaration plus its live scan report.
fn merged_watch(entry: &Value, state: &Map<String, Value>) -> Value {
    let id = entry.get("id").and_then(Value::as_str).unwrap_or_default();
    let runtime = state.get(id).and_then(Value::as_object);
    let field = |key: &str, fallback: Value| -> Value {
        runtime
            .and_then(|map| map.get(key))
            .cloned()
            .unwrap_or(fallback)
    };
    let mut merged = OrderedMap::new();
    for key in [
        "id",
        "path",
        "owner",
        "workspace_id",
        "recursive",
        "kind",
        "enabled",
        "created_at",
    ] {
        merged.insert(key, entry.get(key).cloned().unwrap_or(Value::Null));
    }
    merged.insert("last_scan_at", field("last_scan_at", Value::Null));
    merged.insert("last_result", field("last_result", Value::Null));
    merged.insert("last_errors", field("last_errors", json!([])));
    merged.insert("tracked_files", field("tracked_files", json!(0)));
    serde_json::to_value(merged).unwrap_or(Value::Null)
}

pub(super) fn read_watch_file(data_dir: &Path) -> Value {
    let state = read_watch_state(data_dir);
    let watches: Vec<Value> = stored_watches(data_dir)
        .iter()
        .map(|entry| merged_watch(entry, &state))
        .collect();
    let enabled = watches
        .iter()
        .filter(|watch| {
            watch
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })
        .count();
    json!({
        "enabled_count": enabled,
        "polling": polling_now(data_dir),
        "interval_seconds": interval_from_env().as_secs_f64(),
        "watches": watches,
        "kinds": ["folder", "vault"],
        "vault_watch": {
            "name": "vault_watch",
            "flag": "LATTICEAI_VAULT_WATCH",
            "enabled": vault_watch_on(),
            "default": false,
            "source": if std::env::var("LATTICEAI_VAULT_WATCH").is_ok() { "env" } else { "default" },
            "detail": "Watching an external vault re-syncs it in the background; it is off until you turn it on.",
            // v11.7.0: the poller delivers through `NoteIngestor`, natively.
            // This said `false` for as long as there was nothing behind it.
            "bridge_wired": true
        },
        "note": "Watch mode is opt-in and off by default. Deleted files are counted but never auto-removed from the Brain."
    })
}

fn now_iso(state: &LocalFilesState) -> String {
    match state.graph() {
        Some(graph) => graph.clock().now_iso(),
        None => SystemClock.now_iso(),
    }
}

/// `enable()` — consent, not ingestion.
///
/// The baseline is taken **here**, so turning a watch on never re-ingests a
/// folder that was already ingested; only what changes afterwards flows.
pub(super) fn enable_watch(
    state: &LocalFilesState,
    path: &str,
    kind: &str,
    recursive: bool,
    owner: &str,
    workspace_id: Option<&str>,
) -> Value {
    let data_dir = state.config.data_dir().to_path_buf();
    let existing = stored_watches(&data_dir)
        .into_iter()
        .find(|entry| entry.get("path").and_then(Value::as_str) == Some(path));
    let mut runtime = read_watch_state(&data_dir);
    if let Some(entry) = existing {
        let id = entry.get("id").and_then(Value::as_str).unwrap_or_default();
        record_baseline(&mut runtime, id, path, recursive);
        write_watch_state(&data_dir, &runtime);
        return json!({
            "status": "ok",
            "watch": merged_watch(&entry, &runtime),
            "already_watching": true,
        });
    }
    let id = format!("watch_{}", super::token_urlsafe(6));
    let entry = json!({
        "id": id,
        "path": path,
        "owner": owner,
        "workspace_id": workspace_id,
        "recursive": recursive,
        "kind": kind,
        "enabled": true,
        "created_at": now_iso(state),
    });
    let mut stored = std::fs::read_to_string(watch_path(&data_dir))
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .unwrap_or_else(|| json!({"watches": []}));
    if let Some(Value::Array(watches)) = stored.get_mut("watches") {
        watches.push(entry.clone());
    } else {
        stored = json!({"watches": [entry.clone()]});
    }
    let _ = std::fs::create_dir_all(&data_dir);
    let _ = std::fs::write(
        watch_path(&data_dir),
        serde_json::to_string_pretty(&stored).unwrap_or_else(|_| "{}".into()),
    );
    record_baseline(&mut runtime, &id, path, recursive);
    write_watch_state(&data_dir, &runtime);
    json!({
        "status": "ok",
        "watch": merged_watch(&entry, &runtime),
        "already_watching": false,
    })
}

fn record_baseline(runtime: &mut Map<String, Value>, id: &str, path: &str, recursive: bool) {
    let mut scanner = WatchScanner::new(path, watch_config(recursive));
    let tracked = scanner.take_baseline().unwrap_or(0);
    let entry = runtime
        .entry(id.to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut();
    let Some(entry) = entry else {
        return;
    };
    entry.insert(
        "snapshot".into(),
        serde_json::to_value(scanner.snapshot()).unwrap_or_else(|_| json!({})),
    );
    entry.insert("tracked_files".into(), json!(tracked));
    entry.entry("last_scan_at").or_insert(Value::Null);
    entry.entry("last_result").or_insert(Value::Null);
    entry.entry("last_errors").or_insert(json!([]));
}

fn watch_config(recursive: bool) -> WatchConfig {
    WatchConfig {
        recursive,
        ..WatchConfig::default()
    }
}

pub(super) fn disable_watch(data_dir: &Path, watch_id: &str, path: &str) -> Option<Value> {
    let raw = std::fs::read_to_string(watch_path(data_dir)).ok()?;
    let mut stored: Value = serde_json::from_str(&raw).ok()?;
    let runtime = read_watch_state(data_dir);
    let watches = stored.get_mut("watches")?.as_array_mut()?;
    let hit = |watch: &Value| {
        let id_hit =
            !watch_id.is_empty() && watch.get("id").and_then(Value::as_str) == Some(watch_id);
        let path_hit = !path.is_empty() && watch.get("path").and_then(Value::as_str) == Some(path);
        id_hit || path_hit
    };
    let removed = watches.iter().find(|watch| hit(watch)).cloned()?;
    watches.retain(|watch| !hit(watch));
    let _ = std::fs::write(
        watch_path(data_dir),
        serde_json::to_string_pretty(&stored).unwrap_or_else(|_| "{}".into()),
    );
    // The snapshot goes with the declaration: re-enabling the same folder later
    // must take a fresh baseline rather than replay everything it missed.
    let mut state = runtime.clone();
    if let Some(id) = removed.get("id").and_then(Value::as_str) {
        state.remove(id);
    }
    write_watch_state(data_dir, &state);
    if stored_watches(data_dir).is_empty() {
        stop_poller(data_dir);
    }
    Some(json!({"status": "ok", "watch": merged_watch(&removed, &runtime)}))
}

pub(super) fn vault_watch_on() -> bool {
    matches!(
        std::env::var("LATTICEAI_VAULT_WATCH")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

// ── the vault-watch bridge ─────────────────────────────────────────────────
//
// Detection has been testable since v11.5.0 (`crate::watch`) and the native
// note write since v11.7.0 (`crate::worker::NoteIngestor`). Nothing joined
// them: `bridge_wired` reported `false`, and it was telling the truth. This is
// the join.
//
// Deliberately **not** `watch::poll_watch`. That helper drives one in-memory
// `WatchScanner`, which is right for a test and wrong for an install: a scanner
// that only exists in memory forgets its snapshot when the process restarts and
// re-ingests the folder. Here every tick rebuilds the scanners from the
// snapshots on disk, so `scan_once` — the tested core — still does the diffing,
// and a restart resumes.

/// Live pollers, one per data directory, keyed so `ensure` is idempotent.
fn pollers() -> &'static Mutex<HashMap<PathBuf, tokio::sync::watch::Sender<bool>>> {
    static POLLERS: OnceLock<Mutex<HashMap<PathBuf, tokio::sync::watch::Sender<bool>>>> =
        OnceLock::new();
    POLLERS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Whether a poller is running for this install *right now*.
fn polling_now(data_dir: &Path) -> bool {
    pollers()
        .lock()
        .map(|live| {
            live.get(data_dir)
                .is_some_and(|stop| stop.receiver_count() > 0)
        })
        .unwrap_or(false)
}

fn stop_poller(data_dir: &Path) {
    if let Ok(mut live) = pollers().lock() {
        if let Some(stop) = live.remove(data_dir) {
            let _ = stop.send(true);
        }
    }
}

/// Start the poller for this install, unless one is already running.
///
/// A no-op outside a Tokio runtime (`Handle::try_current`), so a synchronous
/// caller — a router built in a plain `#[test]`, say — gets `false` rather than
/// the panic `tokio::spawn` would raise.
pub(super) fn ensure_poller(state: &Arc<LocalFilesState>) -> bool {
    let data_dir = state.config.data_dir().to_path_buf();
    if tokio::runtime::Handle::try_current().is_err() {
        return false;
    }
    let Ok(mut live) = pollers().lock() else {
        return false;
    };
    if live
        .get(&data_dir)
        .is_some_and(|stop| stop.receiver_count() > 0)
    {
        return true;
    }
    let (stop, mut stop_rx) = tokio::sync::watch::channel(false);
    live.insert(data_dir, stop);
    drop(live);
    let state = Arc::clone(state);
    let interval = interval_from_env();
    tokio::spawn(async move {
        loop {
            tokio::select! {
                changed = stop_rx.changed() => {
                    if changed.is_err() || *stop_rx.borrow() {
                        return;
                    }
                }
                // Waits first, exactly as `poll_watch` does: the baseline was
                // taken at enable, so the first tick must see changes only.
                () = tokio::time::sleep(interval) => {
                    scan_watches(&state).await;
                }
            }
        }
    });
    true
}

/// Resume the pollers a previous run left declared in `folder_watch.json`.
///
/// The snapshots are on disk, so resuming ingests what changed while the
/// process was down and nothing else.
pub fn resume_watches(state: &Arc<LocalFilesState>) -> bool {
    let has_watches = stored_watches(state.config.data_dir()).iter().any(|watch| {
        watch
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    });
    has_watches && ensure_poller(state)
}

/// One pass over every enabled watch. Returns the per-watch reports.
///
/// Never raises: one unreadable folder is a counted failure, not the end of the
/// poller. Files that vanished from a watched folder are pruned from the
/// Brain through [`super::prune::prune_deleted`] (GraphWriter, confirm=true).
/// Disk files are never deleted.
pub async fn scan_watches(state: &Arc<LocalFilesState>) -> Value {
    let data_dir = state.config.data_dir().to_path_buf();
    let Some(graph) = state.graph().cloned() else {
        return json!({"status": "unavailable", "detail": "the Brain is not wired", "watches": []});
    };
    let mut ingestor = NoteIngestor::new(graph);
    if let Some(seam) = state.seam() {
        ingestor = ingestor.with_seam(seam.clone());
    }
    let mut runtime = read_watch_state(&data_dir);
    let mut reports = Vec::new();
    for entry in stored_watches(&data_dir) {
        if !entry
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            continue;
        }
        let report = scan_one_watch(state, &ingestor, &entry, &mut runtime, &now_iso(state)).await;
        reports.push(report);
    }
    write_watch_state(&data_dir, &runtime);
    json!({"status": "ok", "watches": reports})
}

async fn scan_one_watch(
    state: &Arc<LocalFilesState>,
    ingestor: &NoteIngestor,
    entry: &Value,
    runtime: &mut Map<String, Value>,
    now: &str,
) -> Value {
    let id = entry
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let root = entry
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let recursive = entry
        .get("recursive")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let owner = entry.get("owner").and_then(Value::as_str);
    let workspace_id = entry.get("workspace_id").and_then(Value::as_str);
    let snapshot: Snapshot = runtime
        .get(&id)
        .and_then(|value| value.get("snapshot"))
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .unwrap_or_default();

    let mut scanner = WatchScanner::with_snapshot(root, watch_config(recursive), snapshot);
    let diff = match scanner.scan_once() {
        Ok(diff) => diff,
        Err(error) => {
            let failure = json!({
                "id": id,
                "status": "failed",
                "scanned": 0,
                "skipped": 0,
                "skipped_unchanged": 0,
                "ingested": 0,
                "reingested": 0,
                "failed": 0,
                "removed": 0,
                "deleted": [],
                "detail": error.to_string(),
            });
            record_report(runtime, &id, scanner.snapshot(), now, &failure, &[]);
            return failure;
        }
    };

    let scanned = scanner.snapshot().len();
    let stamp_skipped = scanned.saturating_sub(diff.pending.len());
    let mut ingested = 0usize;
    let mut hash_skipped = 0usize;
    let mut errors: Vec<Value> = Vec::new();
    let mut in_flight = tokio::task::JoinSet::new();
    let root_path = PathBuf::from(root);
    let watch_id = id.clone();
    let owner_s = owner.map(str::to_string);
    let workspace_s = workspace_id.map(str::to_string);
    let store = state.store.clone();
    let seam = state.seam().cloned();
    for relative in &diff.pending {
        while in_flight.len() >= crate::worker::INGEST_INFLIGHT {
            collect_watch_join(
                &mut in_flight,
                &mut ingested,
                &mut hash_skipped,
                &mut errors,
            )
            .await;
        }
        let ingestor = ingestor.clone();
        let store = store.clone();
        let seam = seam.clone();
        let root_path = root_path.clone();
        let relative = relative.clone();
        let watch_id = watch_id.clone();
        let owner_s = owner_s.clone();
        let workspace_s = workspace_s.clone();
        in_flight.spawn(async move {
            let result = deliver(
                &ingestor,
                store.as_deref(),
                seam.as_ref(),
                &root_path,
                &relative,
                &watch_id,
                owner_s.as_deref(),
                workspace_s.as_deref(),
            )
            .await;
            (relative, result)
        });
    }
    while !in_flight.is_empty() {
        collect_watch_join(
            &mut in_flight,
            &mut ingested,
            &mut hash_skipped,
            &mut errors,
        )
        .await;
    }
    let skipped = stamp_skipped + hash_skipped;
    let pruned = if diff.removed.is_empty() {
        json!({"status": "ok", "files": [], "removed": {"nodes": 0}})
    } else {
        super::prune::prune_deleted(ingestor.graph(), Path::new(root), true)
            .unwrap_or_else(|detail| json!({"status": "error", "detail": detail}))
    };
    let status = if errors.is_empty() {
        "ok"
    } else if ingested > 0 || skipped > 0 {
        "partial"
    } else {
        "failed"
    };
    let report = json!({
        "id": id,
        "status": status,
        "scanned": scanned,
        "skipped": skipped,
        "skipped_unchanged": skipped,
        "ingested": ingested,
        "reingested": ingested,
        "failed": errors.len(),
        "removed": diff.removed.len(),
        "deleted": diff.removed,
        "pruned": pruned,
        "truncated": diff.truncated,
    });
    record_report(runtime, &id, scanner.snapshot(), now, &report, &errors);
    report
}

fn record_report(
    runtime: &mut Map<String, Value>,
    id: &str,
    snapshot: &Snapshot,
    now: &str,
    report: &Value,
    errors: &[Value],
) {
    let entry = runtime
        .entry(id.to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut();
    let Some(entry) = entry else {
        return;
    };
    entry.insert(
        "snapshot".into(),
        serde_json::to_value(snapshot).unwrap_or_else(|_| json!({})),
    );
    entry.insert("tracked_files".into(), json!(snapshot.len()));
    entry.insert("last_scan_at".into(), json!(now));
    entry.insert("last_result".into(), report.clone());
    // Three is what the home card shows; keeping more would only grow the file.
    entry.insert(
        "last_errors".into(),
        json!(errors.iter().take(3).collect::<Vec<_>>()),
    );
}

async fn collect_watch_join(
    in_flight: &mut tokio::task::JoinSet<(String, Result<Delivered, String>)>,
    ingested: &mut usize,
    hash_skipped: &mut usize,
    errors: &mut Vec<Value>,
) {
    let Some(joined) = in_flight.join_next().await else {
        return;
    };
    match joined {
        Ok((_, Ok(Delivered::Ingested))) => *ingested += 1,
        Ok((_, Ok(Delivered::Skipped))) => *hash_skipped += 1,
        Ok((relative, Err(detail))) => errors.push(json!({"path": relative, "detail": detail})),
        Err(error) => errors.push(json!({"path": "", "detail": error.to_string()})),
    }
}

/// What delivering one watched file did.
enum Delivered {
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
async fn deliver(
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
    ingestor
        .ingest_note(&note, owner, workspace_id)
        .await
        .map(|_| Delivered::Ingested)
        .map_err(|error| error.to_string())
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
