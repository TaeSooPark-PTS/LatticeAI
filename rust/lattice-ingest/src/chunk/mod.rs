//! `lattice_brain/graph/_kg_common/text.py` — typed chunking, ported exactly.
//!
//! Four strategies over one window budget:
//!
//! * **plain** is the legacy `_chunks` walk with `start_char` tracked. Its
//!   boundaries are a compatibility contract — chunk ids hash over the chunk
//!   text, so a moved boundary silently re-keys every chunk in the store.
//! * **markdown** splits at `^#{1,6} ` headings, merges sections under 200
//!   characters forward into the next one, and windows a section too big for
//!   one chunk.
//! * **code** packs contiguous segments (split at blank-line runs and
//!   declaration lines) greedily up to `size`, and windows any single segment
//!   past `size * 1.5`.
//! * **prose** ends each chunk at the last sentence or paragraph boundary
//!   inside the window, falling back to a line break and then to the hard cut.
//!
//! ## Characters, not bytes
//!
//! Python indexes `str` by code point: `cleaned[start:end]`, `len(cleaned)` and
//! `match.start()` are all character counts. Every function below therefore
//! works over `&[char]`, and `start_char` in the output is a character offset.
//! A byte-indexed port would produce different boundaries for any non-ASCII
//! text — and would panic outright inside a multi-byte sequence.
//!
//! ## Regexes, hand-rolled
//!
//! The four patterns Python uses (`^(#{1,6}) (.*)$`, the declaration-line
//! prefix, `\n\s*\n`, and the prose boundary alternation) are matched by hand
//! rather than by a regex engine. That is not a micro-optimisation: it keeps
//! the character-index arithmetic explicit, and it avoids the one place the two
//! engines genuinely disagree — Python's `\s` includes `\x1c`–`\x1f`, which the
//! Unicode `White_Space` property does not (see [`crate::pystr::is_py_space`]).

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
pub mod code;
pub mod markdown;
pub mod prose;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::pystr::py_strip;

/// `_chunks`/`typed_chunks` default window, in characters.
pub const DEFAULT_CHUNK_SIZE: i64 = 1200;
/// `_chunks`/`typed_chunks` default overlap, in characters.
pub const DEFAULT_CHUNK_OVERLAP: i64 = 160;
/// `_CHUNK_STRATEGIES` — anything else falls back to `plain`.
pub const CHUNK_STRATEGIES: [&str; 4] = ["plain", "markdown", "code", "prose"];

/// One chunk's provenance: which strategy produced it and where it starts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkMeta {
    /// `plain` | `markdown` | `code` | `prose`.
    pub strategy: String,
    /// Character offset of this chunk inside the stripped source text.
    pub start_char: usize,
    /// `" > "`-joined markdown heading path, or `None` when there is none.
    pub heading_path: Option<String>,
}

/// One chunk: the text and the provenance that lets a citation point at it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Chunk {
    /// The chunk text — an exact substring of the stripped source at
    /// `meta.start_char`.
    pub text: String,
    /// Where it came from.
    pub meta: ChunkMeta,
}

/// `typed_chunks(text, strategy=…, size=…, overlap=…)`.
///
/// `size` is coerced with `max(1, …)` and `overlap` clamped to
/// `[0, size - 1]`, exactly as Python does, so no argument can produce a
/// non-terminating walk. An unknown `strategy` falls back to `plain`.
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

/// `typed_chunks` with the product defaults (1200 / 160).
pub fn typed_chunks_default(text: &str, strategy: &str) -> Vec<Chunk> {
    typed_chunks(text, strategy, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP)
}

/// `typed_chunk_meta_fields(piece)` — the additive chunk-metadata fields.
///
/// `heading_path` appears only when it is truthy: honest absence over an empty
/// label. `strategy` falls back to `plain` for an empty string, mirroring
/// Python's `str(meta.get("strategy") or "plain")`.
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

/// `_plain_windows` — the legacy `_chunks` walk with `start_char` tracked.
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
        // Python's `max(0, end - overlap)`; overlap < size <= end here, so the
        // floor never bites, but it is the contract and it is free.
        start = end.saturating_sub(overlap);
    }
    out
}

/// Character offsets at which a `^` in `re.MULTILINE` can match.
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
        // size <= 0 becomes 1; overlap is clamped into [0, size - 1].
        assert_eq!(texts(&typed_chunks("abc", "plain", 0, 5)), ["a", "b", "c"]);
        assert_eq!(texts(&typed_chunks("abc", "plain", -9, 5)), ["a", "b", "c"]);
        assert_eq!(texts(&typed_chunks("abcde", "plain", 3, -4)), ["abc", "de"]);
        // overlap == size - 1 still advances by one character per window.
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
        // Byte lengths differ from character lengths, which is the whole point.
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
