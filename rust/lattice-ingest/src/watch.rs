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

pub const MAX_FILES_PER_SCAN: usize = 200;
pub const WATCH_INTERVAL_ENV: &str = "LATTICEAI_FOLDER_WATCH_INTERVAL";
pub const DEFAULT_WATCH_INTERVAL_SECONDS: f64 = 30.0;
pub const MIN_WATCH_INTERVAL_SECONDS: f64 = 1.0;

#[derive(Debug, Clone)]
pub struct WatchConfig {
    pub recursive: bool,
    pub extensions: std::collections::BTreeSet<String>,
    pub skip_dirs: std::collections::BTreeSet<String>,
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

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ScannedFile {
    pub relative_path: String,
    pub path: PathBuf,
    pub extension: String,
    pub size: u64,
    pub mtime: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FileStamp {
    pub mtime: f64,
    pub size: u64,
}

pub type Snapshot = BTreeMap<String, FileStamp>;

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct ScanDiff {
    #[serde(rename = "new")]
    pub added: Vec<String>,
    pub changed: Vec<String>,
    pub removed: Vec<String>,
    pub pending: Vec<String>,
    pub truncated: bool,
}

impl ScanDiff {
    pub fn is_empty(&self) -> bool {
        self.added.is_empty() && self.changed.is_empty() && self.removed.is_empty()
    }
}

#[derive(Debug, Clone)]
pub struct WatchScanner {
    root: PathBuf,
    config: WatchConfig,
    snapshot: Snapshot,
}

impl WatchScanner {
    pub fn new(root: impl Into<PathBuf>, config: WatchConfig) -> Self {
        Self {
            root: root.into(),
            config,
            snapshot: Snapshot::new(),
        }
    }

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

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn snapshot(&self) -> &Snapshot {
        &self.snapshot
    }

    pub fn walk(&self) -> std::io::Result<Vec<ScannedFile>> {
        walk_folder(&self.root, &self.config)
    }

    pub fn take_baseline(&mut self) -> std::io::Result<usize> {
        self.snapshot = snapshot_of(&self.walk()?);
        Ok(self.snapshot.len())
    }

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

pub fn walk_folder(root: &Path, config: &WatchConfig) -> std::io::Result<Vec<ScannedFile>> {
    if !root.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("folder unavailable: {}", root.display()),
        ));
    }
    let patterns = load_latticeignore(root);
    let mut out = Vec::new();
    walk_into(root, root, "", config, &patterns, &mut out);
    Ok(out)
}

fn walk_into(
    root: &Path,
    current: &Path,
    rel_dir: &str,
    config: &WatchConfig,
    patterns: &[String],
    out: &mut Vec<ScannedFile>,
) {
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
        let is_dir = if kind.is_symlink() {
            fs::metadata(entry.path())
                .map(|meta| meta.is_dir())
                .unwrap_or(false)
        } else {
            kind.is_dir()
        };
        if is_dir {
            dirs.push((name, kind.is_symlink()));
        } else if kind.is_symlink() {
            let candidate = entry.path();
            let root_canon = fs::canonicalize(root).ok();
            let target_canon = fs::canonicalize(&candidate).ok();
            if let (Some(rc), Some(tc)) = (root_canon, target_canon) {
                if tc.starts_with(&rc) {
                    files.push(name);
                }
            }
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
            walk_into(root, &current.join(&name), &relative, config, patterns, out);
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

pub fn interval_from_env() -> Duration {
    let seconds = std::env::var(WATCH_INTERVAL_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<f64>().ok())
        .filter(|value| value.is_finite())
        .unwrap_or(DEFAULT_WATCH_INTERVAL_SECONDS);
    Duration::from_secs_f64(seconds.max(MIN_WATCH_INTERVAL_SECONDS))
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct PollReport {
    pub scans: u64,
    pub failures: u64,
}

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
