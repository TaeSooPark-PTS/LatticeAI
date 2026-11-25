use std::collections::BTreeSet;
use std::path::Path;

use crate::pystr::decode_utf8_ignore;

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
pub const CODE_EXTENSIONS: [&str; 21] = [
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".go", ".rs", ".java", ".c",
    ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt", ".sh", ".sql",
];
pub const DOCUMENT_EXTENSIONS: [&str; 4] = [".pdf", ".docx", ".xlsx", ".pptx"];
pub const DEFAULT_MAX_FILE_BYTES: u64 = 4_000_000;
pub const LATTICEIGNORE_FILENAME: &str = ".latticeignore";

pub fn default_folder_extensions() -> BTreeSet<String> {
    TEXT_EXTENSIONS
        .iter()
        .chain(CODE_EXTENSIONS.iter())
        .chain(DOCUMENT_EXTENSIONS.iter())
        .map(|extension| (*extension).to_string())
        .collect()
}

pub fn default_skip_dirs() -> BTreeSet<String> {
    DEFAULT_SKIP_DIRS
        .iter()
        .map(|name| (*name).to_string())
        .collect()
}

pub fn is_document_extension(extension: &str) -> bool {
    DOCUMENT_EXTENSIONS.contains(&extension)
}

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

fn match_element(pattern: &[char], at: usize, candidate: char) -> (usize, bool) {
    match pattern[at] {
        '?' => (at + 1, true),
        '[' => match class_span(pattern, at) {
            Some(close) => {
                let (negated, items) = class_items(&pattern[at + 1..close]);
                (close + 1, class_contains(items, candidate) != negated)
            }
            None => (at + 1, candidate == '['),
        },
        literal => (at + 1, literal == candidate),
    }
}

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
        assert_eq!(extensions.len(), 35);
        for wanted in [
            ".md", ".py", ".pdf", ".toml", ".sql", ".ini", ".docx", ".htm",
        ] {
            assert!(extensions.contains(wanted), "{wanted}");
        }
        for unwanted in [".png", ".mp4", ".exe", ".gz", ""] {
            assert!(!extensions.contains(unwanted), "{unwanted}");
        }
        assert!(is_document_extension(".pdf"));
        assert!(
            is_document_extension(".docx"),
            "the worker extracts it, we do not"
        );
        assert!(!is_document_extension(".md"));
        assert!(
            !is_document_extension(".htm"),
            "markup is read inline, then parsed"
        );
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
        assert!(matches_ignore(
            "deep/nested/app.log",
            "app.log",
            false,
            &patterns
        ));
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
