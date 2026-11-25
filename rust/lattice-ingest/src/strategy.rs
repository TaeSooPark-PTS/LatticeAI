use crate::pystr::py_strip;

pub const MARKDOWN_EXTENSIONS: [&str; 2] = [".md", ".markdown"];
pub const CODE_EXTENSIONS: [&str; 21] = [
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".c", ".h", ".cpp", ".css",
    ".sh", ".sql", ".vue", ".svelte", ".json", ".yaml", ".yml", ".toml",
];
pub const PROSE_EXTENSIONS: [&str; 9] = [
    ".txt", ".pdf", ".docx", ".doc", ".rtf", ".odt", ".epub", ".html", ".htm",
];

pub fn chunk_strategy_for(filename: &str, content_type: &str) -> &'static str {
    let lowered = py_strip(filename).to_lowercase();
    let mut name = lowered.as_str();
    for separator in ['?', '#'] {
        name = name.split(separator).next().unwrap_or("");
    }
    let normalised = name.replace('\\', "/");
    let trimmed = normalised.trim_end_matches('/');
    let base = trimmed.rsplit('/').next().unwrap_or("");
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
