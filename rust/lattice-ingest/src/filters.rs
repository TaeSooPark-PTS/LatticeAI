//! The folder-ingest filter chain, in the order Python applies it.
//!
//! `lattice_brain/ingestion/folders.py:155-212` fixes that order, and
//! `latticeai/services/folder_watch.py:425-458` reuses it so watch mode can
//! never admit something a manual folder ingest would have refused:
//!
//! 1. hard skip-list directories (`.git`, `node_modules`, `target`, …);
//! 2. hidden entries (any name starting with `.`) — the watch snapshot has no
//!    `include_hidden` escape hatch, so this one is unconditional there;
//! 3. root `.latticeignore` globs (a trailing `/` restricts to directories);
//! 4. the extension allow-list;
//! 5. the per-file size cap.
//!
//! `.latticeignore` matching is `fnmatch`, not gitignore: `*` matches `/` too,
//! and each pattern is tried against both the root-relative posix path and the
//! bare basename. That is a real difference from a `.gitignore` a user might
//! expect, and it is the Python behaviour, so it is the behaviour here.

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
use std::collections::BTreeSet;
use std::path::Path;

use crate::pystr::decode_utf8_ignore;

/// `FOLDER_DEFAULT_SKIP_DIRS` — pruned regardless of `.latticeignore`.
pub const DEFAULT_SKIP_DIRS: [&str; 16] = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "target",
    ".cache",
    ".idea",
    ".vscode",
];

/// `FOLDER_TEXT_EXTENSIONS`.
pub const TEXT_EXTENSIONS: [&str; 10] = [
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
];
/// `FOLDER_CODE_EXTENSIONS`.
pub const CODE_EXTENSIONS: [&str; 20] = [
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt", ".sh", ".sql",
];
/// `FOLDER_DOCUMENT_EXTENSIONS` — routed as `pdf`, never read inline.
pub const DOCUMENT_EXTENSIONS: [&str; 1] = [".pdf"];
/// `DEFAULT_MAX_FILE_BYTES`.
pub const DEFAULT_MAX_FILE_BYTES: u64 = 4_000_000;
/// `LATTICEIGNORE_FILENAME`.
pub const LATTICEIGNORE_FILENAME: &str = ".latticeignore";

/// `DEFAULT_FOLDER_EXTENSIONS` — text ∪ code ∪ document.
pub fn default_folder_extensions() -> BTreeSet<String> {
    TEXT_EXTENSIONS
        .iter()
        .chain(CODE_EXTENSIONS.iter())
        .chain(DOCUMENT_EXTENSIONS.iter())
        .map(|extension| (*extension).to_string())
        .collect()
}

/// `FOLDER_DEFAULT_SKIP_DIRS` as an owned set.
pub fn default_skip_dirs() -> BTreeSet<String> {
    DEFAULT_SKIP_DIRS
        .iter()
        .map(|name| (*name).to_string())
        .collect()
}

/// True when a document extension routes through the worker's PDF door.
pub fn is_document_extension(extension: &str) -> bool {
    DOCUMENT_EXTENSIONS.contains(&extension)
}

/// `_load_latticeignore(root)` — glob patterns from `root/.latticeignore`.
///
/// Blank lines and `#` comments are dropped; a missing or unreadable file is
/// simply "no patterns", never an error.
pub fn load_latticeignore(root: &Path) -> Vec<String> {
    let Ok(bytes) = std::fs::read(root.join(LATTICEIGNORE_FILENAME)) else {
        return Vec::new();
    };
    decode_utf8_ignore(&bytes)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(str::to_string)
        .collect()
}

/// `_matches_ignore(rel_posix, name, is_dir=…, patterns=…)`.
pub fn matches_ignore(rel_posix: &str, name: &str, is_dir: bool, patterns: &[String]) -> bool {
    for raw in patterns {
        let mut pattern = raw.as_str();
        if pattern.ends_with('/') {
            if !is_dir {
                continue;
            }
            pattern = pattern.trim_end_matches('/');
        }
        let pattern = pattern.trim_start_matches('/');
        if pattern.is_empty() {
            continue;
        }
        if fnmatch(rel_posix, pattern) || fnmatch(name, pattern) {
            return true;
        }
    }
    false
}

/// `fnmatch.fnmatch(name, pattern)` on a case-sensitive filesystem.
///
/// The supported syntax is what `fnmatch.translate` produces: `*` (any run,
/// **including** `/`), `?` (any one character), `[seq]` / `[!seq]` character
/// classes with `a-z` ranges, and an unmatched `[` as a literal. Everything
/// else is a literal. `normcase` is the identity on posix, which is what the
/// product runs on; a Windows host would additionally fold case.
pub fn fnmatch(name: &str, pattern: &str) -> bool {
    let name: Vec<char> = name.chars().collect();
    let pattern: Vec<char> = pattern.chars().collect();
    let (mut n, mut p) = (0usize, 0usize);
    let mut star: Option<(usize, usize)> = None;
    while n < name.len() {
        if p < pattern.len() && pattern[p] == '*' {
            star = Some((p, n));
            p += 1;
            continue;
        }
        let hit = if p < pattern.len() {
            let (next, matched) = match_element(&pattern, p, name[n]);
            if matched {
                p = next;
                n += 1;
            }
            matched
        } else {
            false
        };
        if hit {
            continue;
        }
        match star {
            Some((star_p, star_n)) => {
                star = Some((star_p, star_n + 1));
                p = star_p + 1;
                n = star_n + 1;
            }
            None => return false,
        }
    }
    pattern[p..].iter().all(|c| *c == '*')
}

/// Match one non-`*` pattern element → (next pattern index, did it match).
fn match_element(pattern: &[char], at: usize, candidate: char) -> (usize, bool) {
    match pattern[at] {
        '?' => (at + 1, true),
        '[' => match class_span(pattern, at) {
            Some(close) => {
                let (negated, items) = class_items(&pattern[at + 1..close]);
                (close + 1, class_contains(items, candidate) != negated)
            }
            // `fnmatch.translate` emits an unmatched `[` as a literal.
            None => (at + 1, candidate == '['),
        },
        literal => (at + 1, literal == candidate),
    }
}

/// Index of the `]` closing the class opened at `at`, following `translate`.
fn class_span(pattern: &[char], at: usize) -> Option<usize> {
    let mut index = at + 1;
    if pattern.get(index) == Some(&'!') {
        index += 1;
    }
    if pattern.get(index) == Some(&']') {
        index += 1;
    }
    while index < pattern.len() && pattern[index] != ']' {
        index += 1;
    }
    (index < pattern.len()).then_some(index)
}

fn class_items(body: &[char]) -> (bool, &[char]) {
    match body.first() {
        Some('!') => (true, &body[1..]),
        _ => (false, body),
    }
}

fn class_contains(items: &[char], candidate: char) -> bool {
    let mut index = 0usize;
    while index < items.len() {
        // `a-z` is a range; a `-` first or last is a literal.
        if index + 2 < items.len() && items[index + 1] == '-' {
            if items[index] <= candidate && candidate <= items[index + 2] {
                return true;
            }
            index += 3;
            continue;
        }
        if items[index] == candidate {
            return true;
        }
        index += 1;
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_extension_allow_list_is_the_union_python_builds() {
        let extensions = default_folder_extensions();
        assert_eq!(extensions.len(), 31);
        for wanted in [".md", ".py", ".pdf", ".toml", ".sql", ".ini"] {
            assert!(extensions.contains(wanted), "{wanted}");
        }
        for unwanted in [".png", ".mp4", ".exe", ".gz", ""] {
            assert!(!extensions.contains(unwanted), "{unwanted}");
        }
        assert!(is_document_extension(".pdf"));
        assert!(!is_document_extension(".md"));
        assert!(default_skip_dirs().contains("node_modules"));
    }

    #[test]
    fn stars_cross_slashes_because_fnmatch_is_not_gitignore() {
        assert!(fnmatch("docs/draft.md", "*.md"));
        assert!(fnmatch("a/b/c.log", "a/*.log"));
        assert!(fnmatch("draft.md", "*"));
        assert!(!fnmatch("draft.md", "*.txt"));
    }

    #[test]
    fn question_marks_classes_and_ranges() {
        assert!(fnmatch("a1.log", "a?.log"));
        assert!(!fnmatch("a12.log", "a?.log"));
        assert!(fnmatch("a1.log", "a[0-9].log"));
        assert!(!fnmatch("ax.log", "a[0-9].log"));
        assert!(fnmatch("ax.log", "a[!0-9].log"));
        assert!(fnmatch("a-.log", "a[-x].log"));
        assert!(fnmatch("a].log", "a[]].log"));
        // An unmatched `[` is a literal.
        assert!(fnmatch("a[b", "a[b"));
        assert!(fnmatch("한글.md", "한*.md"));
    }

    #[test]
    fn trailing_slash_patterns_only_match_directories() {
        let patterns = vec!["build/".to_string(), "*.log".to_string()];
        assert!(matches_ignore("build", "build", true, &patterns));
        assert!(!matches_ignore("build", "build", false, &patterns));
        assert!(matches_ignore("logs/app.log", "app.log", false, &patterns));
        assert!(!matches_ignore("logs/app.txt", "app.txt", false, &patterns));
        // Patterns match the basename too, not only the relative path.
        assert!(matches_ignore(
            "deep/nested/app.log",
            "app.log",
            false,
            &patterns
        ));
        // A pattern that is only slashes is dropped rather than matching all.
        assert!(!matches_ignore("x", "x", true, &["/".to_string()]));
        assert!(!matches_ignore("x", "x", true, &[]));
    }

    #[test]
    fn latticeignore_parsing_drops_blanks_and_comments() {
        let dir = tempfile::tempdir().expect("tempdir");
        assert!(load_latticeignore(dir.path()).is_empty());
        std::fs::write(
            dir.path().join(LATTICEIGNORE_FILENAME),
            "# comment\n\n  *.log  \nbuild/\n\t\n",
        )
        .expect("write");
        assert_eq!(
            load_latticeignore(dir.path()),
            vec!["*.log".to_string(), "build/".to_string()]
        );
    }
}
