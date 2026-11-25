pub mod code;
pub mod markdown;
pub mod prose;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::pystr::py_strip;

pub const DEFAULT_CHUNK_SIZE: i64 = 1200;
pub const DEFAULT_CHUNK_OVERLAP: i64 = 160;
pub const CHUNK_STRATEGIES: [&str; 4] = ["plain", "markdown", "code", "prose"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkMeta {
    pub strategy: String,
    pub start_char: usize,
    pub heading_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Chunk {
    pub text: String,
    pub meta: ChunkMeta,
}

pub fn typed_chunks(text: &str, strategy: &str, size: i64, overlap: i64) -> Vec<Chunk> {
    let cleaned: Vec<char> = py_strip(text).chars().collect();
    if cleaned.is_empty() {
        return Vec::new();
    }
    let size = size.max(1) as usize;
    let overlap = overlap.clamp(0, size as i64 - 1) as usize;
    match strategy {
        "markdown" => markdown::chunks(&cleaned, size, overlap),
        "code" => code::chunks(&cleaned, size, overlap),
        "prose" => prose::chunks(&cleaned, size, overlap),
        _ => plain_windows(&cleaned, size, overlap, 0, "plain", None),
    }
}

pub fn typed_chunks_default(text: &str, strategy: &str) -> Vec<Chunk> {
    typed_chunks(text, strategy, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP)
}

pub fn chunk_meta_fields(chunk: &Chunk) -> Map<String, Value> {
    let mut fields = Map::new();
    let strategy = if chunk.meta.strategy.is_empty() {
        "plain"
    } else {
        chunk.meta.strategy.as_str()
    };
    fields.insert("strategy".to_string(), Value::from(strategy));
    fields.insert("start_char".to_string(), Value::from(chunk.meta.start_char));
    if let Some(path) = chunk.meta.heading_path.as_ref().filter(|p| !p.is_empty()) {
        fields.insert("heading_path".to_string(), Value::from(path.clone()));
    }
    fields
}

pub(super) fn make_chunk(
    cleaned: &[char],
    start: usize,
    end: usize,
    base_offset: usize,
    strategy: &str,
    heading_path: Option<&str>,
) -> Chunk {
    Chunk {
        text: cleaned[start..end].iter().collect(),
        meta: ChunkMeta {
            strategy: strategy.to_string(),
            start_char: base_offset + start,
            heading_path: heading_path.map(str::to_string),
        },
    }
}

pub(super) fn plain_windows(
    cleaned: &[char],
    size: usize,
    overlap: usize,
    base_offset: usize,
    strategy: &str,
    heading_path: Option<&str>,
) -> Vec<Chunk> {
    let mut out = Vec::new();
    let total = cleaned.len();
    let mut start = 0usize;
    while start < total {
        let end = total.min(start + size);
        out.push(make_chunk(
            cleaned,
            start,
            end,
            base_offset,
            strategy,
            heading_path,
        ));
        if end >= total {
            break;
        }
        start = end.saturating_sub(overlap);
    }
    out
}

pub(super) fn line_starts(cleaned: &[char]) -> Vec<usize> {
    let mut starts = vec![0usize];
    for (index, c) in cleaned.iter().enumerate() {
        if *c == '\n' {
            starts.push(index + 1);
        }
    }
    starts
}

#[cfg(test)]
mod tests {
    use super::markdown::MARKDOWN_MIN_SECTION_CHARS;
    use super::*;

    fn texts(chunks: &[Chunk]) -> Vec<String> {
        chunks.iter().map(|chunk| chunk.text.clone()).collect()
    }

    #[test]
    fn empty_and_whitespace_only_produce_nothing() {
        for text in ["", "   ", "\n\t\r\n", "\u{1c}\u{1f}"] {
            for strategy in CHUNK_STRATEGIES {
                assert!(
                    typed_chunks(text, strategy, 1200, 160).is_empty(),
                    "{text:?}"
                );
            }
        }
    }

    #[test]
    fn plain_reproduces_the_legacy_walk_and_strips_without_collapsing() {
        let chunks = typed_chunks("  a   b  ", "plain", 1200, 160);
        assert_eq!(texts(&chunks), vec!["a   b".to_string()]);
        assert_eq!(chunks[0].meta.start_char, 0);
        assert_eq!(chunks[0].meta.heading_path, None);
        assert_eq!(chunks[0].meta.strategy, "plain");
    }

    #[test]
    fn an_unknown_strategy_is_plain() {
        let known = typed_chunks("abcdef", "plain", 4, 1);
        assert_eq!(typed_chunks("abcdef", "sideways", 4, 1), known);
        assert_eq!(typed_chunks("abcdef", "", 4, 1), known);
    }

    #[test]
    fn size_and_overlap_are_coerced_the_way_python_coerces_them() {
        assert_eq!(texts(&typed_chunks("abc", "plain", 0, 5)), ["a", "b", "c"]);
        assert_eq!(texts(&typed_chunks("abc", "plain", -9, 5)), ["a", "b", "c"]);
        assert_eq!(texts(&typed_chunks("abcde", "plain", 3, -4)), ["abc", "de"]);
        assert_eq!(
            texts(&typed_chunks("abcd", "plain", 2, 99)),
            ["ab", "bc", "cd"]
        );
    }

    #[test]
    fn offsets_are_characters_so_multibyte_text_slices_identically() {
        let chunks = typed_chunks("가나다라마바사", "plain", 3, 1);
        assert_eq!(texts(&chunks), ["가나다", "다라마", "마바사"]);
        assert_eq!(
            chunks.iter().map(|c| c.meta.start_char).collect::<Vec<_>>(),
            [0, 2, 4]
        );
        assert_eq!(chunks[0].text.len(), 9);
        assert_eq!(chunks[0].text.chars().count(), 3);
    }

    #[test]
    fn meta_fields_only_claim_a_heading_when_there_is_one() {
        let chunk = Chunk {
            text: "x".into(),
            meta: ChunkMeta {
                strategy: "markdown".into(),
                start_char: 7,
                heading_path: Some("A > B".into()),
            },
        };
        let fields = chunk_meta_fields(&chunk);
        assert_eq!(fields["strategy"], Value::from("markdown"));
        assert_eq!(fields["start_char"], Value::from(7));
        assert_eq!(fields["heading_path"], Value::from("A > B"));
        let bare = Chunk {
            text: "x".into(),
            meta: ChunkMeta {
                strategy: String::new(),
                start_char: 0,
                heading_path: Some(String::new()),
            },
        };
        let fields = chunk_meta_fields(&bare);
        assert_eq!(fields["strategy"], Value::from("plain"));
        assert!(!fields.contains_key("heading_path"));
    }

    #[test]
    fn defaults_match_the_python_defaults() {
        assert_eq!(DEFAULT_CHUNK_SIZE, 1200);
        assert_eq!(DEFAULT_CHUNK_OVERLAP, 160);
        assert_eq!(MARKDOWN_MIN_SECTION_CHARS, 200);
        let long = "가".repeat(3000);
        assert_eq!(
            typed_chunks_default(&long, "plain"),
            typed_chunks(&long, "plain", 1200, 160)
        );
    }
}
