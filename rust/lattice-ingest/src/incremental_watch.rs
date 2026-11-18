//! Event-driven filesystem watcher with debounce, batch coalescing, move detection,
//! and periodic full-snapshot reconciliation.
//!
//! Addresses Review Sections 4.2 #1 and 15 (P1 — 증분 수집 및 멀티모달):
//! - Uses OS filesystem notifications (`notify::RecommendedWatcher`) for low-latency
//!   detection of local folder / vault changes without constant O(N) directory walks.
//! - Coalesces bursty editor save sequences (temp-file write → rename → chmod) inside
//!   a configurable debounce window (`debounce_window_ms`) and caps batch delivery
//!   (`max_batch_size`) so bulk git checkouts never overwhelm the worker.
//! - Runs periodic full reconciliation via [`WatchScanner`] (`reconcile_interval_ms`)
//!   so missed OS events or offline changes made while Lattice was closed are 100% caught.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver};
use std::time::Duration;

use notify::{
    Config as NotifyConfig, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher,
};

use crate::hashes::sha256_bytes;
use crate::watch::{ScanDiff, WatchConfig, WatchScanner};

/// Configuration for [`DebouncedWatchEngine`].
#[derive(Debug, Clone)]
pub struct DebouncedWatchConfig {
    /// Quiet period (ms) required after the latest touch on a path before emitting it.
    pub debounce_window_ms: u64,
    /// Maximum number of paths emitted in a single drained batch.
    pub max_batch_size: usize,
    /// Interval (ms) between full mtime+size snapshot reconciliations.
    pub reconcile_interval_ms: u64,
    /// Underlying filter rules (supported extensions, ignored dirs, recursion, size cap).
    pub watch: WatchConfig,
}

impl Default for DebouncedWatchConfig {
    fn default() -> Self {
        Self {
            debounce_window_ms: 350,
            max_batch_size: 64,
            reconcile_interval_ms: 60_000,
            watch: WatchConfig::default(),
        }
    }
}

/// High-level filesystem event normalized before debouncing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RawFsChange {
    /// File created or modified at relative path.
    Touched(String),
    /// File removed at relative path.
    Removed(String),
    /// Atomic rename/move within the watched root (`from_rel` -> `to_rel`).
    Moved { from: String, to: String },
}

/// A detected move between two relative paths with identical content digest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetectedMove {
    pub from: String,
    pub to: String,
    pub content_sha256: String,
}

/// Coalesced, ready-to-process incremental batch emitted by [`DebouncedWatchEngine`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WatchBatch {
    /// Relative paths created or modified (excluding pure moves when matched).
    pub upserts: Vec<String>,
    /// Relative paths deleted from disk.
    pub removals: Vec<String>,
    /// Files renamed/moved whose content hash matches a vanished path.
    pub moves: Vec<DetectedMove>,
    /// Whether this batch included a full-tree periodic reconciliation pass.
    pub reconciled: bool,
    /// Number of pending paths still queued behind `max_batch_size` or debounce window.
    pub remaining_queued: usize,
}

impl WatchBatch {
    pub fn is_empty(&self) -> bool {
        self.upserts.is_empty() && self.removals.is_empty() && self.moves.is_empty()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PendingOp {
    Upsert,
    Remove,
}

#[derive(Debug, Clone)]
struct PendingEntry {
    op: PendingOp,
    last_event_ms: u64,
    moved_from: Option<String>,
}

/// Stateful event-driven + periodic-reconciliation watcher for a single root directory.
pub struct DebouncedWatchEngine {
    root: PathBuf,
    canonical_root: PathBuf,
    config: DebouncedWatchConfig,
    scanner: WatchScanner,
    pending: BTreeMap<String, PendingEntry>,
    known_hashes: BTreeMap<String, String>,
    last_reconcile_ms: u64,
    watcher: Option<RecommendedWatcher>,
    rx: Option<Receiver<notify::Result<Event>>>,
}

impl DebouncedWatchEngine {
    /// Construct a watch engine rooted at `root` and seed its baseline snapshot + hashes.
    pub fn new(root: impl AsRef<Path>, config: DebouncedWatchConfig, now_ms: u64) -> Self {
        let root_path = root.as_ref().to_path_buf();
        let canonical_root =
            std::fs::canonicalize(&root_path).unwrap_or_else(|_| root_path.clone());
        let mut scanner = WatchScanner::new(&root_path, config.watch.clone());
        let initial = scanner.scan_once().unwrap_or_default();
        let mut known_hashes = BTreeMap::new();
        for rel in initial.added.into_iter().chain(initial.changed) {
            if let Ok(bytes) = std::fs::read(canonical_root.join(&rel)) {
                known_hashes.insert(rel, sha256_bytes(&bytes));
            }
        }
        Self {
            root: root_path,
            canonical_root,
            config,
            scanner,
            pending: BTreeMap::new(),
            known_hashes,
            last_reconcile_ms: now_ms,
            watcher: None,
            rx: None,
        }
    }

    /// Start the OS-level `notify::RecommendedWatcher` on `root`.
    ///
    /// If OS watch registration fails (e.g. restrictive container ulimit), the
    /// engine degrades gracefully to periodic reconciliation without failing closed.
    pub fn start_os_watcher(&mut self) -> Result<(), String> {
        let (tx, rx) = mpsc::channel();
        let notify_cfg = NotifyConfig::default().with_poll_interval(Duration::from_millis(500));
        let mut watcher = RecommendedWatcher::new(tx, notify_cfg).map_err(|err| err.to_string())?;
        let mode = if self.config.watch.recursive {
            RecursiveMode::Recursive
        } else {
            RecursiveMode::NonRecursive
        };
        watcher
            .watch(&self.canonical_root, mode)
            .map_err(|err| err.to_string())?;
        self.watcher = Some(watcher);
        self.rx = Some(rx);
        Ok(())
    }

    /// Whether an active OS-level filesystem watcher is attached.
    pub fn has_os_watcher(&self) -> bool {
        self.watcher.is_some()
    }

    /// Root directory being watched.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Record a normalized filesystem change at timestamp `now_ms`.
    pub fn record_change(&mut self, change: RawFsChange, now_ms: u64) {
        match change {
            RawFsChange::Touched(rel) => {
                if self.accepts_relative_path(&rel, true) {
                    let prev_move = self
                        .pending
                        .get(&rel)
                        .and_then(|entry| entry.moved_from.clone());
                    self.pending.insert(
                        rel,
                        PendingEntry {
                            op: PendingOp::Upsert,
                            last_event_ms: now_ms,
                            moved_from: prev_move,
                        },
                    );
                }
            }
            RawFsChange::Removed(rel) => {
                if !self.accepts_relative_path(&rel, false) {
                    return;
                }
                // Ephemeral temp file created and deleted before ever being indexed -> drop both!
                if !self.known_hashes.contains_key(&rel)
                    && self
                        .pending
                        .get(&rel)
                        .is_some_and(|e| e.op == PendingOp::Upsert && e.moved_from.is_none())
                {
                    self.pending.remove(&rel);
                    return;
                }
                self.pending.insert(
                    rel,
                    PendingEntry {
                        op: PendingOp::Remove,
                        last_event_ms: now_ms,
                        moved_from: None,
                    },
                );
            }
            RawFsChange::Moved { from, to } => {
                let from_ok = self.accepts_relative_path(&from, false);
                let to_ok = self.accepts_relative_path(&to, true);
                if from_ok && !to_ok {
                    self.record_change(RawFsChange::Removed(from), now_ms);
                } else if !from_ok && to_ok {
                    self.record_change(RawFsChange::Touched(to), now_ms);
                } else if from_ok && to_ok {
                    self.pending.remove(&from);
                    self.pending.insert(
                        to,
                        PendingEntry {
                            op: PendingOp::Upsert,
                            last_event_ms: now_ms,
                            moved_from: Some(from),
                        },
                    );
                }
            }
        }
    }

    /// Drain any queued OS `notify::Event`s into the debounce buffer.
    pub fn pump_os_events(&mut self, now_ms: u64) -> usize {
        let mut events = Vec::new();
        if let Some(rx) = &self.rx {
            while let Ok(Ok(event)) = rx.try_recv() {
                events.push(event);
            }
        }
        let count = events.len();
        for event in events {
            self.ingest_notify_event(event, now_ms);
        }
        count
    }

    fn ingest_notify_event(&mut self, event: Event, now_ms: u64) {
        let rels: Vec<String> = event
            .paths
            .iter()
            .filter_map(|p| self.to_relative(p))
            .collect();
        if rels.is_empty() {
            return;
        }
        match event.kind {
            EventKind::Remove(_) => {
                for rel in rels {
                    self.record_change(RawFsChange::Removed(rel), now_ms);
                }
            }
            EventKind::Modify(notify::event::ModifyKind::Name(_)) if rels.len() == 2 => {
                self.record_change(
                    RawFsChange::Moved {
                        from: rels[0].clone(),
                        to: rels[1].clone(),
                    },
                    now_ms,
                );
            }
            EventKind::Create(_) | EventKind::Modify(_) => {
                for rel in rels {
                    if self.canonical_root.join(&rel).exists() {
                        self.record_change(RawFsChange::Touched(rel), now_ms);
                    } else {
                        self.record_change(RawFsChange::Removed(rel), now_ms);
                    }
                }
            }
            _ => {}
        }
    }

    /// Force or trigger periodic full-snapshot reconciliation if `reconcile_interval_ms` elapsed.
    pub fn reconcile_if_due(&mut self, now_ms: u64, force: bool) -> bool {
        if !force
            && now_ms.saturating_sub(self.last_reconcile_ms) < self.config.reconcile_interval_ms
        {
            return false;
        }
        self.last_reconcile_ms = now_ms;
        let ScanDiff {
            added,
            changed,
            removed,
            ..
        } = self.scanner.scan_once().unwrap_or_default();
        // Immediate promotion timestamp so reconciled misses do not wait another debounce window.
        let ready_ts = now_ms.saturating_sub(self.config.debounce_window_ms);
        for rel in added.into_iter().chain(changed) {
            self.pending.entry(rel).or_insert(PendingEntry {
                op: PendingOp::Upsert,
                last_event_ms: ready_ts,
                moved_from: None,
            });
        }
        for rel in removed {
            self.pending.entry(rel).or_insert(PendingEntry {
                op: PendingOp::Remove,
                last_event_ms: ready_ts,
                moved_from: None,
            });
        }
        true
    }

    /// Pump OS events, run periodic reconciliation if due, and emit up to `max_batch_size`
    /// debounced changes.
    pub fn poll_batch(&mut self, now_ms: u64, force_reconcile: bool) -> WatchBatch {
        self.pump_os_events(now_ms);
        let reconciled = self.reconcile_if_due(now_ms, force_reconcile);

        let ready_keys: Vec<String> = self
            .pending
            .iter()
            .filter(|(_, entry)| {
                now_ms.saturating_sub(entry.last_event_ms) >= self.config.debounce_window_ms
            })
            .take(self.config.max_batch_size.max(1))
            .map(|(k, _)| k.clone())
            .collect();

        let mut raw_upserts: Vec<(String, Option<String>)> = Vec::new();
        let mut raw_removals: BTreeSet<String> = BTreeSet::new();

        for key in ready_keys {
            let Some(entry) = self.pending.remove(&key) else {
                continue;
            };
            let disk_path = self.canonical_root.join(&key);
            match entry.op {
                PendingOp::Upsert => {
                    if disk_path.is_file() {
                        raw_upserts.push((key, entry.moved_from));
                    } else if self.known_hashes.contains_key(&key) {
                        raw_removals.insert(key);
                    }
                }
                PendingOp::Remove => {
                    if disk_path.is_file() {
                        raw_upserts.push((key, None));
                    } else {
                        raw_removals.insert(key);
                    }
                }
            }
        }

        // Match moves either via explicit OS rename (`moved_from`) or by identical SHA-256
        // against a vanished file in the same batch!
        let mut upserts = Vec::new();
        let mut moves = Vec::new();

        for (rel, explicit_from) in raw_upserts {
            let Ok(bytes) = std::fs::read(self.canonical_root.join(&rel)) else {
                continue;
            };
            let digest = sha256_bytes(&bytes);
            if let Some(from_rel) = explicit_from {
                raw_removals.remove(&from_rel);
                self.known_hashes.remove(&from_rel);
                self.known_hashes.insert(rel.clone(), digest.clone());
                moves.push(DetectedMove {
                    from: from_rel,
                    to: rel,
                    content_sha256: digest,
                });
                continue;
            }

            // Content-hash move detection across simultaneous remove + add!
            let matched_removed = raw_removals
                .iter()
                .find(|rem| self.known_hashes.get(*rem) == Some(&digest))
                .cloned();
            if let Some(from_rel) = matched_removed {
                raw_removals.remove(&from_rel);
                self.known_hashes.remove(&from_rel);
                self.known_hashes.insert(rel.clone(), digest.clone());
                moves.push(DetectedMove {
                    from: from_rel,
                    to: rel,
                    content_sha256: digest,
                });
                continue;
            }

            // Skip pure metadata touch if bytes never actually changed!
            if self.known_hashes.get(&rel) == Some(&digest) {
                continue;
            }
            self.known_hashes.insert(rel.clone(), digest);
            upserts.push(rel);
        }

        let mut removals = Vec::new();
        for rem in raw_removals {
            self.known_hashes.remove(&rem);
            removals.push(rem);
        }

        // Advance underlying snapshot so subsequent reconciliations don't re-report processed items.
        if !upserts.is_empty() || !removals.is_empty() || !moves.is_empty() {
            let _ = self.scanner.scan_once();
        }

        WatchBatch {
            upserts,
            removals,
            moves,
            reconciled,
            remaining_queued: self.pending.len(),
        }
    }

    fn to_relative(&self, path: &Path) -> Option<String> {
        let candidate = if path.is_absolute() {
            std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
        } else {
            self.canonical_root.join(path)
        };
        let rel = candidate
            .strip_prefix(&self.canonical_root)
            .or_else(|_| path.strip_prefix(&self.root))
            .ok()?;
        let normalized = rel
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        if normalized.is_empty() {
            None
        } else {
            Some(normalized)
        }
    }

    fn accepts_relative_path(&self, rel: &str, check_size: bool) -> bool {
        if rel.is_empty() {
            return false;
        }
        let parts: Vec<&str> = rel.split('/').collect();
        if !self.config.watch.recursive && parts.len() > 1 {
            return false;
        }
        for dir in &parts[..parts.len().saturating_sub(1)] {
            if dir.starts_with('.') || self.config.watch.skip_dirs.contains(*dir) {
                return false;
            }
        }
        let file_name = parts.last().copied().unwrap_or("");
        if file_name.starts_with('.') || file_name.ends_with('~') {
            return false;
        }
        let ext = Path::new(file_name)
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if !self.config.watch.extensions.contains(&ext) {
            return false;
        }
        if check_size {
            if let Ok(meta) = std::fs::metadata(self.canonical_root.join(rel)) {
                if meta.len() > self.config.watch.max_file_bytes {
                    return false;
                }
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coalesces_burst_edits_caps_batches_detects_moves_and_reconciles_missed_events() {
        let dir = tempfile::tempdir().expect("tempdir");
        std::fs::write(dir.path().join("alpha.md"), "alpha v1").expect("seed alpha");
        std::fs::write(dir.path().join("move_me.md"), "stable body for move").expect("seed move");

        let cfg = DebouncedWatchConfig {
            debounce_window_ms: 200,
            max_batch_size: 2,
            reconcile_interval_ms: 5_000,
            watch: WatchConfig::default(),
        };
        let mut engine = DebouncedWatchEngine::new(dir.path(), cfg, 1_000);

        // 1. Burst writes within 200ms debounce window coalesce into a single upsert!
        std::fs::write(dir.path().join("alpha.md"), "alpha v2").expect("v2");
        engine.record_change(RawFsChange::Touched("alpha.md".into()), 1_050);
        std::fs::write(dir.path().join("alpha.md"), "alpha v3").expect("v3");
        engine.record_change(RawFsChange::Touched("alpha.md".into()), 1_120);

        // Ephemeral file created then deleted before debounce settles -> completely ignored!
        engine.record_change(RawFsChange::Touched("scratch.md".into()), 1_100);
        engine.record_change(RawFsChange::Removed("scratch.md".into()), 1_130);

        // Too early (only 80ms after last alpha touch) -> empty batch!
        let early = engine.poll_batch(1_200, false);
        assert!(early.is_empty());
        assert_eq!(early.remaining_queued, 1);

        let settled = engine.poll_batch(1_350, false);
        assert_eq!(settled.upserts, vec!["alpha.md".to_string()]);
        assert!(settled.removals.is_empty());
        assert_eq!(settled.remaining_queued, 0);

        // 2. Rename/move detection via content SHA-256 (even if OS reported remove + create separately!).
        std::fs::rename(
            dir.path().join("move_me.md"),
            dir.path().join("renamed_target.md"),
        )
        .expect("rename");
        engine.record_change(RawFsChange::Removed("move_me.md".into()), 2_000);
        engine.record_change(RawFsChange::Touched("renamed_target.md".into()), 2_010);

        let moved = engine.poll_batch(2_250, false);
        assert!(moved.upserts.is_empty());
        assert!(moved.removals.is_empty());
        assert_eq!(moved.moves.len(), 1);
        assert_eq!(moved.moves[0].from, "move_me.md");
        assert_eq!(moved.moves[0].to, "renamed_target.md");

        // 3. Batch cap (`max_batch_size = 2`) + periodic reconciliation catching missed OS events!
        std::fs::write(dir.path().join("missed_one.md"), "one").expect("one");
        std::fs::write(dir.path().join("missed_two.md"), "two").expect("two");
        std::fs::write(dir.path().join("missed_three.md"), "three").expect("three");
        // Notice: ZERO `record_change` calls — simulating dropped OS notifications!
        let recon_1 = engine.poll_batch(7_500, false);
        assert!(recon_1.reconciled);
        assert_eq!(recon_1.upserts.len(), 2);
        assert_eq!(recon_1.remaining_queued, 1);

        let recon_2 = engine.poll_batch(7_600, false);
        assert_eq!(recon_2.upserts.len(), 1);
        assert_eq!(recon_2.remaining_queued, 0);
    }
}
