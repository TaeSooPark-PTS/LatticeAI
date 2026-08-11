//! The polling watcher, over real directories.
//!
//! Every test builds its own `tempfile::TempDir`, so nothing here depends on
//! the developer's home, the repository layout or what a previous test left
//! behind. Detection is asserted through the diff rather than through timing:
//! `scan_once` is synchronous and pure with respect to (snapshot, disk), and
//! the one test that does involve the timer waits on a channel, never on a
//! sleep long enough to "probably" be enough.
//!
//! Where a stamp needs to change without a real clock moving, the *stored*
//! snapshot is edited instead of the file's mtime — a filesystem whose
//! timestamp granularity is coarser than a test is a flake, not a finding.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::time::Duration;

use lattice_ingest::filters::LATTICEIGNORE_FILENAME;
use lattice_ingest::watch::{
    poll_watch, snapshot_of, FileStamp, ScanDiff, WatchConfig, WatchScanner, MAX_FILES_PER_SCAN,
};

fn write(root: &Path, relative: &str, contents: &str) {
    let path = root.join(relative);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("mkdir");
    }
    fs::write(path, contents).expect("write");
}

fn scanner(root: &Path) -> WatchScanner {
    WatchScanner::new(root, WatchConfig::default())
}

/// A scanner whose baseline is the folder as it stands right now.
fn baselined(root: &Path) -> WatchScanner {
    let mut scanner = scanner(root);
    scanner.take_baseline().expect("baseline");
    scanner
}

fn tracked(scanner: &WatchScanner) -> BTreeSet<String> {
    scanner.snapshot().keys().cloned().collect()
}

#[test]
fn a_baseline_means_enabling_a_watch_never_re_ingests_what_is_there() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "notes.md", "hello");
    write(dir.path(), "deep/module.py", "x = 1");
    let mut scanner = baselined(dir.path());
    assert_eq!(scanner.snapshot().len(), 2);

    let diff = scanner.scan_once().expect("scan");
    assert_eq!(diff, ScanDiff::default());
    assert!(diff.is_empty());
}

#[test]
fn without_a_baseline_the_first_scan_reports_everything_as_new() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "a.md", "one");
    write(dir.path(), "b.md", "two");
    let diff = scanner(dir.path()).scan_once().expect("scan");
    assert_eq!(diff.added, vec!["a.md".to_string(), "b.md".to_string()]);
    assert!(diff.changed.is_empty());
    assert_eq!(diff.pending, diff.added);
}

#[test]
fn a_new_file_a_changed_file_and_a_removed_file_are_each_reported_once() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "keep.md", "same");
    write(dir.path(), "edit.md", "before");
    write(dir.path(), "gone.md", "bye");
    let mut scanner = baselined(dir.path());

    write(dir.path(), "fresh.md", "new file");
    write(dir.path(), "edit.md", "after — a different length");
    fs::remove_file(dir.path().join("gone.md")).expect("remove");

    let diff = scanner.scan_once().expect("scan");
    assert_eq!(diff.added, vec!["fresh.md".to_string()]);
    assert_eq!(diff.changed, vec!["edit.md".to_string()]);
    assert_eq!(diff.removed, vec!["gone.md".to_string()]);
    assert_eq!(
        diff.pending,
        vec!["edit.md".to_string(), "fresh.md".to_string()],
        "pending follows walk order, which is sorted within a directory"
    );

    // The snapshot is adopted, so the same change is never reported twice.
    assert_eq!(scanner.scan_once().expect("scan"), ScanDiff::default());
}

#[test]
fn a_removed_file_is_counted_and_nothing_else_happens_to_it() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "gone.md", "bye");
    let mut scanner = baselined(dir.path());
    fs::remove_file(dir.path().join("gone.md")).expect("remove");
    let diff = scanner.scan_once().expect("scan");
    assert_eq!(diff.removed, vec!["gone.md".to_string()]);
    // A removal is never queued for delegation — deleting a node is a
    // destructive act that stays behind an explicit user flow.
    assert!(diff.pending.is_empty());
    assert!(!scanner.snapshot().contains_key("gone.md"));
}

#[test]
fn an_mtime_only_change_is_a_change() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "note.md", "same size");
    let settled = baselined(dir.path());
    let stamp = settled.snapshot()["note.md"];
    // Rewind the stored mtime rather than touching the file: a filesystem with
    // coarse timestamps would otherwise make this test a coin flip.
    let mut rewound = settled.snapshot().clone();
    rewound.insert(
        "note.md".to_string(),
        FileStamp {
            mtime: stamp.mtime - 60.0,
            size: stamp.size,
        },
    );
    let mut scanner = WatchScanner::with_snapshot(dir.path(), WatchConfig::default(), rewound);
    let diff = scanner.scan_once().expect("scan");
    assert_eq!(diff.changed, vec!["note.md".to_string()]);
    assert_eq!(diff.pending, vec!["note.md".to_string()]);
    // Adopting the real stamp settles it again — the size never moved.
    assert_eq!(scanner.snapshot()["note.md"].size, stamp.size);
    assert_eq!(scanner.scan_once().expect("scan"), ScanDiff::default());
}

#[test]
fn the_extension_allow_list_is_the_folder_ingest_allow_list() {
    let dir = tempfile::tempdir().expect("tempdir");
    for name in ["a.md", "b.py", "c.pdf", "d.toml"] {
        write(dir.path(), name, "x");
    }
    for name in ["e.png", "f.exe", "g", "h.tar.gz", "i.mp4"] {
        write(dir.path(), name, "x");
    }
    let scanner = baselined(dir.path());
    assert_eq!(
        tracked(&scanner),
        ["a.md", "b.py", "c.pdf", "d.toml"]
            .iter()
            .map(|name| (*name).to_string())
            .collect()
    );
}

#[test]
fn hidden_entries_and_skip_dirs_never_reach_the_snapshot() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "visible.md", "x");
    write(dir.path(), ".secret.md", "x");
    write(dir.path(), ".hidden/inside.md", "x");
    for skipped in ["node_modules", ".git", "target", "__pycache__", "dist"] {
        write(dir.path(), &format!("{skipped}/inside.md"), "x");
    }
    let scanner = baselined(dir.path());
    assert_eq!(
        tracked(&scanner),
        ["visible.md".to_string()].into_iter().collect()
    );
}

#[test]
fn latticeignore_prunes_files_and_directories_and_never_ingests_itself() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(
        dir.path(),
        LATTICEIGNORE_FILENAME,
        "# comment\n*.log\ndrafts/\ndeep/skip.md\n",
    );
    write(dir.path(), "keep.md", "x");
    write(dir.path(), "app.log", "x");
    write(dir.path(), "drafts/idea.md", "x");
    write(dir.path(), "deep/skip.md", "x");
    write(dir.path(), "deep/keep.md", "x");
    let scanner = baselined(dir.path());
    assert_eq!(
        tracked(&scanner),
        ["deep/keep.md".to_string(), "keep.md".to_string()]
            .into_iter()
            .collect()
    );
    // `.log` is not in the allow-list either, so prove the pattern bites on a
    // file that *would* otherwise be admitted.
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), LATTICEIGNORE_FILENAME, "*.md\n");
    write(dir.path(), "note.md", "x");
    write(dir.path(), "code.py", "x");
    assert_eq!(
        tracked(&baselined(dir.path())),
        ["code.py".to_string()].into_iter().collect()
    );
}

#[test]
fn a_file_over_the_size_cap_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "small.md", "x");
    write(dir.path(), "big.md", &"x".repeat(2048));
    let config = WatchConfig {
        max_file_bytes: 1024,
        ..WatchConfig::default()
    };
    let mut scanner = WatchScanner::new(dir.path(), config);
    scanner.take_baseline().expect("baseline");
    assert_eq!(
        tracked(&scanner),
        ["small.md".to_string()].into_iter().collect()
    );
    // A file that grows past the cap disappears from the snapshot, which the
    // diff reports as a removal rather than pretending it is still watched.
    write(dir.path(), "small.md", &"y".repeat(2048));
    let diff = scanner.scan_once().expect("scan");
    assert_eq!(diff.removed, vec!["small.md".to_string()]);
}

#[test]
fn a_non_recursive_watch_sees_only_the_top_level() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "top.md", "x");
    write(dir.path(), "deep/inner.md", "x");
    let config = WatchConfig {
        recursive: false,
        ..WatchConfig::default()
    };
    let mut scanner = WatchScanner::new(dir.path(), config);
    scanner.take_baseline().expect("baseline");
    assert_eq!(
        tracked(&scanner),
        ["top.md".to_string()].into_iter().collect()
    );
}

#[test]
fn the_walk_yields_a_directorys_files_before_its_subdirectories() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "b.md", "x");
    write(dir.path(), "a.md", "x");
    write(dir.path(), "zeta/one.md", "x");
    write(dir.path(), "alpha/two.md", "x");
    write(dir.path(), "alpha/nested/three.md", "x");
    let files = scanner(dir.path()).walk().expect("walk");
    let order: Vec<&str> = files
        .iter()
        .map(|file| file.relative_path.as_str())
        .collect();
    assert_eq!(
        order,
        vec![
            "a.md",
            "b.md",
            "alpha/two.md",
            "alpha/nested/three.md",
            "zeta/one.md",
        ],
        "os.walk order: sorted files here, then each sorted subdirectory in turn"
    );
    assert_eq!(snapshot_of(&files).len(), files.len());
}

#[test]
fn the_per_scan_cap_truncates_the_pending_list_but_not_the_counts() {
    let dir = tempfile::tempdir().expect("tempdir");
    let total = MAX_FILES_PER_SCAN + 7;
    for index in 0..total {
        write(dir.path(), &format!("note-{index:04}.md"), "x");
    }
    let diff = scanner(dir.path()).scan_once().expect("scan");
    assert_eq!(diff.added.len(), total, "every new file is still counted");
    assert_eq!(diff.pending.len(), MAX_FILES_PER_SCAN);
    assert!(diff.truncated);
    assert_eq!(diff.pending[0], "note-0000.md");
    // Just under the cap, nothing is truncated.
    let dir = tempfile::tempdir().expect("tempdir");
    for index in 0..MAX_FILES_PER_SCAN {
        write(dir.path(), &format!("note-{index:04}.md"), "x");
    }
    let diff = scanner(dir.path()).scan_once().expect("scan");
    assert_eq!(diff.pending.len(), MAX_FILES_PER_SCAN);
    assert!(!diff.truncated);
}

#[test]
fn a_missing_or_replaced_folder_is_an_error_not_an_empty_scan() {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path().join("watched");
    fs::create_dir(&root).expect("mkdir");
    write(&root, "note.md", "x");
    let mut scanner = baselined(&root);
    fs::remove_dir_all(&root).expect("remove");
    let error = scanner
        .scan_once()
        .expect_err("a vanished folder must fail loudly");
    assert!(error.to_string().contains("folder unavailable"));
    // The snapshot survives the failure, so the watch resumes where it was.
    assert_eq!(scanner.snapshot().len(), 1);
    // A file where a directory used to be is the same refusal.
    fs::write(&root, "not a folder").expect("write");
    assert!(scanner.scan_once().is_err());
}

#[test]
fn a_stored_snapshot_resumes_a_watch_across_restarts() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "note.md", "x");
    let stored = baselined(dir.path()).snapshot().clone();
    write(dir.path(), "second.md", "y");

    let mut resumed = WatchScanner::with_snapshot(dir.path(), WatchConfig::default(), stored);
    let diff = resumed.scan_once().expect("scan");
    assert_eq!(diff.added, vec!["second.md".to_string()]);
    assert_eq!(resumed.root(), dir.path());
}

#[cfg(unix)]
#[test]
fn a_symlinked_directory_is_never_descended_into() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "real/inside.md", "x");
    write(dir.path(), "top.md", "x");
    std::os::unix::fs::symlink(dir.path().join("real"), dir.path().join("link")).expect("symlink");
    let scanner = baselined(dir.path());
    assert_eq!(
        tracked(&scanner),
        ["real/inside.md".to_string(), "top.md".to_string()]
            .into_iter()
            .collect(),
        "os.walk does not follow links, so link/inside.md must not appear"
    );
}

#[tokio::test]
async fn the_poll_loop_reports_a_change_and_then_stops() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "seed.md", "x");
    let mut scanner = scanner(dir.path());
    scanner.take_baseline().expect("baseline");

    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    let (seen_tx, mut seen_rx) = tokio::sync::mpsc::unbounded_channel::<ScanDiff>();
    let loop_handle = tokio::spawn(poll_watch(
        scanner,
        Duration::from_millis(20),
        stop_rx,
        move |diff| {
            let seen_tx = seen_tx.clone();
            async move {
                let _ = seen_tx.send(diff);
            }
        },
    ));

    write(dir.path(), "arrived.md", "hello");
    // Wait on the channel, not on a sleep: the assertion is "a scan saw it",
    // not "two hundred milliseconds probably passed".
    let diff = loop {
        let received = tokio::time::timeout(Duration::from_secs(10), seen_rx.recv())
            .await
            .expect("the poll loop must report within ten seconds")
            .expect("the loop must not drop the channel");
        if !received.is_empty() {
            break received;
        }
    };
    assert_eq!(diff.added, vec!["arrived.md".to_string()]);

    stop_tx.send(true).expect("stop");
    let report = tokio::time::timeout(Duration::from_secs(10), loop_handle)
        .await
        .expect("the loop must observe the stop signal")
        .expect("the loop task must not panic");
    assert!(report.scans >= 1);
    assert_eq!(report.failures, 0);
}

#[tokio::test]
async fn a_loop_told_to_stop_before_it_starts_never_scans() {
    let dir = tempfile::tempdir().expect("tempdir");
    write(dir.path(), "note.md", "x");
    let (stop_tx, stop_rx) = tokio::sync::watch::channel(true);
    let report = poll_watch(
        scanner(dir.path()),
        Duration::from_secs(3_600),
        stop_rx,
        |_| async {},
    )
    .await;
    assert_eq!(report.scans, 0);
    drop(stop_tx);
}

#[tokio::test]
async fn a_dropped_stop_sender_ends_the_loop_rather_than_orphaning_it() {
    let dir = tempfile::tempdir().expect("tempdir");
    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    let handle = tokio::spawn(poll_watch(
        scanner(dir.path()),
        Duration::from_secs(3_600),
        stop_rx,
        |_| async {},
    ));
    drop(stop_tx);
    let report = tokio::time::timeout(Duration::from_secs(10), handle)
        .await
        .expect("the loop must notice its sender is gone")
        .expect("no panic");
    assert_eq!(report.scans, 0);
}

#[tokio::test]
async fn a_folder_that_vanishes_is_counted_as_a_failure_not_a_crash() {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path().join("watched");
    fs::create_dir(&root).expect("mkdir");
    let scanner = scanner(&root);
    fs::remove_dir_all(&root).expect("remove");

    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    let handle = tokio::spawn(poll_watch(
        scanner,
        Duration::from_millis(10),
        stop_rx,
        |_| async { unreachable!("a failed scan produces no diff") },
    ));
    tokio::time::sleep(Duration::from_millis(60)).await;
    stop_tx.send(true).expect("stop");
    let report = tokio::time::timeout(Duration::from_secs(10), handle)
        .await
        .expect("stop")
        .expect("no panic");
    assert_eq!(report.scans, 0);
    assert!(
        report.failures >= 1,
        "one bad scan must be counted, not fatal"
    );
}
