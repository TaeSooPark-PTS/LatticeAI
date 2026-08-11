//! `chunk_strategy_for` — filename / path / URI (+ MIME hint) → strategy.
//!
//! The routing rule, in the order Python applies it: strip the query and
//! fragment, normalise separators, take the last path component, look at its
//! extension, and only then fall back to the MIME type. Unknown and
//! extension-less input stays `plain` on purpose — `plain` is the byte-
//! compatible legacy walk, and guessing `prose` for something that might be a
//! data dump would move chunk boundaries (and therefore chunk ids) for no
//! retrieval gain.
//!
//! The Python original wraps the whole body in `try/except` and can only
//! return one of four strings. Rust's type system gives that for free: the
//! signature takes `&str`, so there is no malformed input left to raise on.

use crate::pystr::py_strip;

/// `_MARKDOWN_CHUNK_EXTENSIONS`.
pub const MARKDOWN_EXTENSIONS: [&str; 2] = [".md", ".markdown"];
/// `_CODE_CHUNK_EXTENSIONS`.
pub const CODE_EXTENSIONS: [&str; 21] = [
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".c", ".h", ".cpp", ".css",
    ".sh", ".sql", ".vue", ".svelte", ".json", ".yaml", ".yml", ".toml",
];
/// `_PROSE_CHUNK_EXTENSIONS`.
pub const PROSE_EXTENSIONS: [&str; 9] = [
    ".txt", ".pdf", ".docx", ".doc", ".rtf", ".odt", ".epub", ".html", ".htm",
];

/// Route a filename / path / URI (plus an optional MIME hint) to a strategy.
///
/// Returns `"markdown"`, `"code"`, `"prose"` or `"plain"`. Case-insensitive,
/// tolerant of URLs and Windows separators, and never anything else.
pub fn chunk_strategy_for(filename: &str, content_type: &str) -> &'static str {
    let lowered = py_strip(filename).to_lowercase();
    // `name.split(sep, 1)[0]` for "?" then "#": everything before the first one.
    let mut name = lowered.as_str();
    for separator in ['?', '#'] {
        name = name.split(separator).next().unwrap_or("");
    }
    let normalised = name.replace('\\', "/");
    let trimmed = normalised.trim_end_matches('/');
    let base = trimmed.rsplit('/').next().unwrap_or("");
    // `dot > 0` is the same test on byte and character indices: a string's
    // first character always begins at byte zero.
    let extension = match base.rfind('.') {
        Some(dot) if dot > 0 => &base[dot..],
        _ => "",
    };
    if MARKDOWN_EXTENSIONS.contains(&extension) {
        return "markdown";
    }
    if CODE_EXTENSIONS.contains(&extension) {
        return "code";
    }
    if PROSE_EXTENSIONS.contains(&extension) {
        return "prose";
    }
    let mime = py_strip(content_type).to_lowercase();
    if mime.contains("markdown") {
        return "markdown";
    }
    if mime.starts_with("text/html") || mime.starts_with("text/plain") {
        return "prose";
    }
    "plain"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extensions_win_over_the_mime_hint() {
        assert_eq!(chunk_strategy_for("guide.md", ""), "markdown");
        assert_eq!(chunk_strategy_for("guide.MD", "text/plain"), "markdown");
        assert_eq!(chunk_strategy_for("report.pdf", "text/markdown"), "prose");
        assert_eq!(chunk_strategy_for("app.TSX", ""), "code");
    }

    #[test]
    fn urls_lose_their_query_and_fragment() {
        assert_eq!(
            chunk_strategy_for("https://example.com/docs/guide.md?v=2#top", ""),
            "markdown"
        );
        assert_eq!(
            chunk_strategy_for("https://example.com/docs/guide?v=2#top", ""),
            "plain"
        );
        // The fragment is stripped before the extension is read, so a `.md`
        // that only appears after `#` does not count.
        assert_eq!(chunk_strategy_for("https://x/a#b.md", ""), "plain");
    }

    #[test]
    fn windows_separators_and_trailing_slashes_normalise() {
        assert_eq!(chunk_strategy_for("C:\\projects\\module.py", ""), "code");
        assert_eq!(chunk_strategy_for("/var/data/notes/", ""), "plain");
        assert_eq!(chunk_strategy_for("/var/data/notes//", ""), "plain");
        assert_eq!(chunk_strategy_for("/var/data/a.txt", ""), "prose");
    }

    #[test]
    fn the_mime_fallback_only_fires_without_a_known_extension() {
        assert_eq!(chunk_strategy_for("x", "text/markdown"), "markdown");
        assert_eq!(
            chunk_strategy_for("x", "  application/x-markdown  "),
            "markdown"
        );
        assert_eq!(chunk_strategy_for("x", "TEXT/HTML; charset=utf-8"), "prose");
        assert_eq!(chunk_strategy_for("x", "text/plain"), "prose");
        assert_eq!(chunk_strategy_for("x", "application/octet-stream"), "plain");
        assert_eq!(chunk_strategy_for("x", "x/text/html"), "plain");
    }

    #[test]
    fn extensionless_and_hidden_names_stay_plain() {
        for name in [
            "",
            "   ",
            "noextension",
            ".hidden",
            "trailing.",
            "archive.tar.gz",
        ] {
            assert_eq!(chunk_strategy_for(name, ""), "plain", "{name:?}");
        }
        assert_eq!(chunk_strategy_for("한글 문서.md", ""), "markdown");
    }
}
