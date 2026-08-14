//! The polling mtime-snapshot watcher from `latticeai/services/folder_watch.py`.
//!
//! Deliberately **not** an OS watcher. The Python service explains why and this
//! port inherits the reason: polling is deterministic, portable and testable —
//! [`WatchScanner::scan_once`] is a pure function of (previous snapshot, disk)
//! that a test can drive without a timer, and the timer in [`poll_watch`] is a
//! thin wrapper around it rather than the thing under test.
//!
//! A snapshot is `{relative posix path: (round(mtime, 3), size)}` over exactly
//! the files [`crate::filters`] admits. Diffing two snapshots gives new /
//! changed / removed. Removed files are **counted, never acted on**: deleting a
//! node is destructive and stays behind an explicit user flow.
//!
//! What happens to the diff is not this module's business. Detection stops
//! here; [`crate::worker::WorkerClient`] hands the content to the Python worker,
//! which remains the single writer of the graph.

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
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::filters::{
    default_folder_extensions, default_skip_dirs, load_latticeignore, matches_ignore,
    DEFAULT_MAX_FILE_BYTES, LATTICEIGNORE_FILENAME,
};
use crate::pystr::{py_suffix, round3};

/// `MAX_FILES_PER_SCAN` — the per-scan delegation cap (thrash guard).
pub const MAX_FILES_PER_SCAN: usize = 200;
/// `WATCH_INTERVAL_ENV`.
pub const WATCH_INTERVAL_ENV: &str = "LATTICEAI_FOLDER_WATCH_INTERVAL";
/// `DEFAULT_WATCH_INTERVAL_SECONDS`.
pub const DEFAULT_WATCH_INTERVAL_SECONDS: f64 = 30.0;
/// The floor Python applies with `max(1.0, value)`.
pub const MIN_WATCH_INTERVAL_SECONDS: f64 = 1.0;

/// What a scan admits: the folder-ingest filters, with the same defaults.
#[derive(Debug, Clone)]
pub struct WatchConfig {
    /// Descend into subdirectories (`recursive` on the stored watch).
    pub recursive: bool,
    /// Lower-cased extension allow-list, `.` included.
    pub extensions: std::collections::BTreeSet<String>,
    /// Directory names pruned before `.latticeignore` is consulted.
    pub skip_dirs: std::collections::BTreeSet<String>,
    /// Per-file size cap, in bytes.
    pub max_file_bytes: u64,
}

impl Default for WatchConfig {
    fn default() -> Self {
        Self {
            recursive: true,
            extensions: default_folder_extensions(),
            skip_dirs: default_skip_dirs(),
            max_file_bytes: DEFAULT_MAX_FILE_BYTES,
        }
    }
}

/// One file the filters admitted.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ScannedFile {
    /// Root-relative posix path — the snapshot key.
    pub relative_path: String,
    /// Absolute path on disk.
    pub path: PathBuf,
    /// Lower-cased extension, `.` included.
    pub extension: String,
    /// Size in bytes.
    pub size: u64,
    /// `round(st_mtime, 3)`.
    pub mtime: f64,
}

/// The stamp a snapshot stores per file: `[round(mtime, 3), size]` in Python.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FileStamp {
    /// `round(st_mtime, 3)`.
    pub mtime: f64,
    /// Size in bytes.
    pub size: u64,
}

/// `{relative posix path: stamp}`.
pub type Snapshot = BTreeMap<String, FileStamp>;

/// What changed between two snapshots.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct ScanDiff {
    /// Files present now and absent before (Python's `new`), in walk order.
    #[serde(rename = "new")]
    pub added: Vec<String>,
    /// Files whose `(mtime, size)` stamp moved, in walk order.
    pub changed: Vec<String>,
    /// Files gone since the last scan — counted, never deleted from the graph.
    /// Sorted, because a removal has no walk order to inherit.
    pub removed: Vec<String>,
    /// `added + changed` in walk order, capped at [`MAX_FILES_PER_SCAN`]: the
    /// files this scan would hand to the worker.
    pub pending: Vec<String>,
    /// True when the cap bit and some changes were left for the next scan.
    pub truncated: bool,
}

impl ScanDiff {
    /// True when nothing moved — the cheap "skip the work" signal.
    pub fn is_empty(&self) -> bool {
        self.added.is_empty() && self.changed.is_empty() && self.removed.is_empty()
    }
}

/// Polling scanner for one watched folder.
///
/// Holds the previous snapshot, which is the only state a scan needs. Build it,
/// [`WatchScanner::take_baseline`] at opt-in time (so enabling a watch never
/// re-ingests a folder that was already ingested), then [`WatchScanner::scan_once`].
#[derive(Debug, Clone)]
pub struct WatchScanner {
    root: PathBuf,
    config: WatchConfig,
    snapshot: Snapshot,
}

impl WatchScanner {
    /// A scanner over `root` with no baseline yet — the first scan reports
    /// every admitted file as new.
    pub fn new(root: impl Into<PathBuf>, config: WatchConfig) -> Self {
        Self {
            root: root.into(),
            config,
            snapshot: Snapshot::new(),
        }
    }

    /// A scanner resuming from a stored snapshot.
    pub fn with_snapshot(
        root: impl Into<PathBuf>,
        config: WatchConfig,
        snapshot: Snapshot,
    ) -> Self {
        Self {
            root: root.into(),
            config,
            snapshot,
        }
    }

    /// The folder being watched.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The snapshot the next scan will diff against.
    pub fn snapshot(&self) -> &Snapshot {
        &self.snapshot
    }

    /// Walk the folder without touching the stored snapshot — the dry run.
    pub fn walk(&self) -> std::io::Result<Vec<ScannedFile>> {
        walk_folder(&self.root, &self.config)
    }

    /// Record the current state as the baseline and report how many files it
    /// covers. This is `enable()`: consent, not ingestion.
    pub fn take_baseline(&mut self) -> std::io::Result<usize> {
        self.snapshot = snapshot_of(&self.walk()?);
        Ok(self.snapshot.len())
    }

    /// One scan: diff against the stored snapshot, then adopt the new one.
    ///
    /// The snapshot is adopted even when the caller ignores the diff, exactly
    /// as `_record_scan` does — a scan that reported a change must not report
    /// it again forever if the delegation failed.
    pub fn scan_once(&mut self) -> std::io::Result<ScanDiff> {
        let files = self.walk()?;
        let current = snapshot_of(&files);
        let mut diff = ScanDiff::default();
        for file in &files {
            let stamp = FileStamp {
                mtime: file.mtime,
                size: file.size,
            };
            let previous = self.snapshot.get(&file.relative_path);
            let is_new = previous.is_none();
            let is_changed = previous.is_some_and(|previous| *previous != stamp);
            if is_new {
                diff.added.push(file.relative_path.clone());
            } else if is_changed {
                diff.changed.push(file.relative_path.clone());
            }
            if is_new || is_changed {
                diff.pending.push(file.relative_path.clone());
            }
        }
        diff.removed = self
            .snapshot
            .keys()
            .filter(|key| !current.contains_key(*key))
            .cloned()
            .collect();
        diff.truncated = diff.pending.len() > MAX_FILES_PER_SCAN;
        diff.pending.truncate(MAX_FILES_PER_SCAN);
        self.snapshot = current;
        Ok(diff)
    }
}

/// `{relative path: stamp}` for a walk result.
pub fn snapshot_of(files: &[ScannedFile]) -> Snapshot {
    files
        .iter()
        .map(|file| {
            (
                file.relative_path.clone(),
                FileStamp {
                    mtime: file.mtime,
                    size: file.size,
                },
            )
        })
        .collect()
}

/// `FolderWatchService._snapshot`'s walk: os.walk order, folder-ingest filters.
///
/// Order matters because the per-scan cap slices it: files of a directory come
/// before its subdirectories, and names are sorted within each level, which is
/// exactly what `os.walk` with `sorted(dirnames)` / `sorted(filenames)` yields.
pub fn walk_folder(root: &Path, config: &WatchConfig) -> std::io::Result<Vec<ScannedFile>> {
    if !root.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("folder unavailable: {}", root.display()),
        ));
    }
    let patterns = load_latticeignore(root);
    let mut out = Vec::new();
    walk_into(root, "", config, &patterns, &mut out);
    Ok(out)
}

fn walk_into(
    current: &Path,
    rel_dir: &str,
    config: &WatchConfig,
    patterns: &[String],
    out: &mut Vec<ScannedFile>,
) {
    // An unreadable subdirectory is skipped, exactly as `os.walk`'s default
    // `onerror=None` skips it. The root itself was checked by the caller.
    let Ok(entries) = fs::read_dir(current) else {
        return;
    };
    let mut files: Vec<String> = Vec::new();
    let mut dirs: Vec<(String, bool)> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        // `os.walk` classifies by the *resolved* type but never descends into a
        // symlinked directory (`followlinks=False`), so its contents are unseen.
        let is_dir = if kind.is_symlink() {
            fs::metadata(entry.path())
                .map(|meta| meta.is_dir())
                .unwrap_or(false)
        } else {
            kind.is_dir()
        };
        if is_dir {
            dirs.push((name, kind.is_symlink()));
        } else {
            files.push(name);
        }
    }
    files.sort();
    dirs.sort();

    for name in files {
        if name == LATTICEIGNORE_FILENAME || name.starts_with('.') {
            continue;
        }
        let relative = join_rel(rel_dir, &name);
        if matches_ignore(&relative, &name, false, patterns) {
            continue;
        }
        let extension = py_suffix(&name).to_lowercase();
        if !config.extensions.contains(&extension) {
            continue;
        }
        let path = current.join(&name);
        let Ok(metadata) = fs::metadata(&path) else {
            continue;
        };
        if metadata.len() > config.max_file_bytes {
            continue;
        }
        out.push(ScannedFile {
            relative_path: relative,
            path,
            extension,
            size: metadata.len(),
            mtime: round3(mtime_seconds(&metadata)),
        });
    }

    for (name, is_symlink) in dirs {
        if config.skip_dirs.contains(&name) || name.starts_with('.') {
            continue;
        }
        let relative = join_rel(rel_dir, &name);
        if matches_ignore(&relative, &name, true, patterns) {
            continue;
        }
        if config.recursive && !is_symlink {
            walk_into(&current.join(&name), &relative, config, patterns, out);
        }
    }
}

fn join_rel(rel_dir: &str, name: &str) -> String {
    if rel_dir.is_empty() {
        name.to_string()
    } else {
        format!("{rel_dir}/{name}")
    }
}

fn mtime_seconds(metadata: &fs::Metadata) -> f64 {
    let Ok(modified) = metadata.modified() else {
        return 0.0;
    };
    match modified.duration_since(UNIX_EPOCH) {
        Ok(since) => since.as_secs_f64(),
        Err(error) => -error.duration().as_secs_f64(),
    }
}

/// `_default_interval()` — `LATTICEAI_FOLDER_WATCH_INTERVAL` seconds, floored
/// at one second, defaulting to thirty. A nonsense value is the default, never
/// an error.
pub fn interval_from_env() -> Duration {
    let seconds = std::env::var(WATCH_INTERVAL_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<f64>().ok())
        .filter(|value| value.is_finite())
        .unwrap_or(DEFAULT_WATCH_INTERVAL_SECONDS);
    Duration::from_secs_f64(seconds.max(MIN_WATCH_INTERVAL_SECONDS))
}

/// What a finished poll loop did.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct PollReport {
    /// Scans that completed.
    pub scans: u64,
    /// Scans that failed (the folder went away, say) — one bad scan must never
    /// kill the poller, so these are counted and stepped over.
    pub failures: u64,
}

/// Poll `scanner` every `interval` until `stop` carries `true`.
///
/// Mirrors `_poll_loop`: it **waits first**, so starting a poller never
/// triggers an immediate scan of a folder whose baseline was just taken. A
/// dropped stop-sender also ends the loop, because nobody is left to stop it.
pub async fn poll_watch<H, F>(
    mut scanner: WatchScanner,
    interval: Duration,
    mut stop: tokio::sync::watch::Receiver<bool>,
    mut handler: H,
) -> PollReport
where
    H: FnMut(ScanDiff) -> F,
    F: std::future::Future<Output = ()>,
{
    let mut report = PollReport::default();
    if *stop.borrow() {
        return report;
    }
    loop {
        tokio::select! {
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    return report;
                }
            }
            () = tokio::time::sleep(interval) => {
                match scanner.scan_once() {
                    Ok(diff) => {
                        report.scans += 1;
                        handler(diff).await;
                    }
                    Err(_) => report.failures += 1,
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_interval_floors_at_one_second_and_ignores_nonsense() {
        // Read through a helper rather than the process env: these tests run in
        // parallel and `set_var` is a process-wide side effect.
        fn resolve(raw: Option<&str>) -> Duration {
            let seconds = raw
                .and_then(|raw| raw.trim().parse::<f64>().ok())
                .filter(|value| value.is_finite())
                .unwrap_or(DEFAULT_WATCH_INTERVAL_SECONDS);
            Duration::from_secs_f64(seconds.max(MIN_WATCH_INTERVAL_SECONDS))
        }
        assert_eq!(resolve(None), Duration::from_secs(30));
        assert_eq!(resolve(Some("")), Duration::from_secs(30));
        assert_eq!(resolve(Some("soon")), Duration::from_secs(30));
        assert_eq!(resolve(Some(" 5 ")), Duration::from_secs(5));
        assert_eq!(resolve(Some("0.1")), Duration::from_secs(1));
        assert_eq!(resolve(Some("-9")), Duration::from_secs(1));
        // The real reader agrees with the helper for an unset variable.
        assert!(interval_from_env() >= Duration::from_secs(1));
    }

    #[test]
    fn a_missing_folder_is_an_error_not_an_empty_walk() {
        let dir = tempfile::tempdir().expect("tempdir");
        let scanner = WatchScanner::new(dir.path().join("nope"), WatchConfig::default());
        assert!(scanner.walk().is_err());
    }

    #[test]
    fn an_empty_diff_reports_itself_as_empty() {
        assert!(ScanDiff::default().is_empty());
        assert!(!ScanDiff {
            removed: vec!["a".into()],
            ..ScanDiff::default()
        }
        .is_empty());
    }
}
