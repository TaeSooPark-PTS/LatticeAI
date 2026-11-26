use std::collections::BTreeMap;

pub const MAX_TOPICS: usize = 3;
const MAX_TITLE_CHARS: usize = 120;
const MAX_PATH_CHARS: usize = 160;

const STOPWORDS: &[&str] = &[
    "the",
    "and",
    "for",
    "are",
    "but",
    "not",
    "you",
    "all",
    "any",
    "can",
    "had",
    "her",
    "was",
    "one",
    "our",
    "out",
    "has",
    "have",
    "this",
    "that",
    "with",
    "from",
    "they",
    "will",
    "would",
    "there",
    "their",
    "what",
    "about",
    "which",
    "when",
    "your",
    "into",
    "than",
    "then",
    "them",
    "these",
    "those",
    "been",
    "were",
    "also",
    "only",
    "some",
    "such",
    "each",
    "more",
    "most",
    "other",
    "self",
    "none",
    "true",
    "false",
    "null",
    "return",
    "const",
    "function",
    "def",
    "class",
    "import",
    "export",
    "pub",
    "let",
    "use",
    "impl",
    "struct",
    "string",
    "그리고",
    "하지만",
    "있는",
    "있다",
    "합니다",
];

fn keep_token(token: &str) -> bool {
    let count = token.chars().count();
    let ascii = token.is_ascii();
    if ascii && count < 3 || !ascii && count < 2 {
        return false;
    }
    if token.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }
    !STOPWORDS.contains(&token)
}

pub fn document_topics(text: &str, limit: usize) -> Vec<String> {
    let mut counts: BTreeMap<String, (usize, usize)> = BTreeMap::new();
    let mut order = 0usize;
    for raw in text.split(|c: char| !c.is_alphanumeric()) {
        if raw.is_empty() {
            continue;
        }
        let token = raw.to_lowercase();
        if !keep_token(&token) {
            continue;
        }
        let entry = counts.entry(token).or_insert((0, order));
        entry.0 += 1;
        order += 1;
    }
    let mut ranked: Vec<(String, usize, usize)> = counts
        .into_iter()
        .filter(|(_, (count, _))| *count >= 2)
        .map(|(token, (count, first))| (token, count, first))
        .collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.2.cmp(&b.2)));
    ranked
        .into_iter()
        .take(limit)
        .map(|(token, _, _)| token)
        .collect()
}

fn clipped(text: &str, limit: usize) -> String {
    let compact = text.split_whitespace().collect::<Vec<_>>().join(" ");
    compact.chars().take(limit).collect()
}

pub fn context_prefix(
    title: &str,
    heading_path: Option<&str>,
    symbol_path: Option<&str>,
    topics: &[String],
) -> String {
    let mut first = Vec::new();
    let title = clipped(title, MAX_TITLE_CHARS);
    if !title.is_empty() {
        first.push(format!("Document: {title}"));
    }
    if let Some(path) = heading_path
        .map(|p| clipped(p, MAX_PATH_CHARS))
        .filter(|p| !p.is_empty())
    {
        first.push(format!("Section: {path}"));
    }
    if let Some(path) = symbol_path
        .map(|p| clipped(p, MAX_PATH_CHARS))
        .filter(|p| !p.is_empty())
    {
        first.push(format!("Symbol: {path}"));
    }
    let mut lines = Vec::new();
    if !first.is_empty() {
        lines.push(first.join(" | "));
    }
    if !topics.is_empty() {
        lines.push(format!("Topic: {}", topics.join(", ")));
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topics_are_frequent_content_words_in_first_seen_order_on_ties() {
        let text = "Vector index rebuild. The vector index stores vectors; rebuild the index nightly. 벡터 색인 벡터 색인";
        let topics = document_topics(text, MAX_TOPICS);
        assert_eq!(topics, vec!["index", "vector", "rebuild"]);
        assert_eq!(
            document_topics(text, 5)[3..],
            ["벡터".to_string(), "색인".to_string()]
        );
        assert!(document_topics("the the the and and 12 12 ab ab", 3).is_empty());
        assert!(document_topics("", 3).is_empty());
        assert_eq!(document_topics(text, MAX_TOPICS), topics, "deterministic");
    }

    #[test]
    fn the_prefix_is_at_most_two_lines_and_skips_missing_parts() {
        let topics = vec!["index".to_string(), "vector".to_string()];
        let full = context_prefix(
            "  Guide\n v2 ",
            Some("Setup > Install"),
            Some("fn main"),
            &topics,
        );
        assert_eq!(
            full,
            "Document: Guide v2 | Section: Setup > Install | Symbol: fn main\nTopic: index, vector"
        );
        assert_eq!(context_prefix("Guide", None, None, &[]), "Document: Guide");
        assert_eq!(
            context_prefix("", Some(" "), None, &topics),
            "Topic: index, vector"
        );
        assert_eq!(context_prefix("", None, None, &[]), "");
        let long = context_prefix(&"t".repeat(500), None, None, &[]);
        assert_eq!(long.chars().count(), "Document: ".len() + 120);
        assert!(full.lines().count() <= 2);
    }
}
