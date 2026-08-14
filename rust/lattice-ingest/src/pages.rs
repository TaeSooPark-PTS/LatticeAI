//! PDF page arithmetic: which page a chunk offset landed on.
//!
//! `read_document` joins extracted pages with `"\n\n"`, so page *k* starts at
//! `sum(chars[j] + 2 for j < k)` in the text that gets chunked. That `+2` is
//! the whole trick, and getting it wrong shifts every citation by two
//! characters per page — invisible on page one, a page off by page forty.
//!
//! Both functions answer `[]` / `None` rather than guessing when the structure
//! is malformed: a citation that claims the wrong page is worse than one that
//! claims no page.

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
use serde_json::Value;

/// `citation_locator`, already ported and golden-pinned in lattice-core.
pub use lattice_core::pytext::citation_locator;

/// `pdf_page_offsets(structure)` — the start offset of every page, or `[]`.
///
/// Anything malformed anywhere in the list poisons the whole answer, exactly as
/// Python's early `return []` does: a half-built offset table would put later
/// pages at confidently wrong positions.
pub fn pdf_page_offsets(structure: &Value) -> Vec<i64> {
    let Some(object) = structure.as_object() else {
        return Vec::new();
    };
    let Some(Value::Array(pages)) = object.get("pages") else {
        return Vec::new();
    };
    if pages.is_empty() {
        return Vec::new();
    }
    let mut offsets = Vec::with_capacity(pages.len());
    let mut cursor: i64 = 0;
    for page in pages {
        let Some(page) = page.as_object() else {
            return Vec::new();
        };
        // `isinstance(chars, bool)` is checked first in Python because `True`
        // is an `int` there; a JSON bool is simply not a number here, but the
        // refusal has to be the same one.
        let chars = match page.get("chars") {
            Some(Value::Number(number)) => number.as_f64().unwrap_or(-1.0),
            _ => return Vec::new(),
        };
        if chars < 0.0 {
            return Vec::new();
        }
        offsets.push(cursor);
        cursor += chars.trunc() as i64 + 2; // +2 for the "\n\n" page joiner
    }
    offsets
}

/// `page_for_offset(page_offsets, offset)` — the 1-based page, or `None`.
///
/// `None` means "before the first page" or "no offsets", which is honest
/// absence; the scan stops at the first page that starts after the offset, so
/// a non-monotonic table answers the same thing Python's `break` answers.
pub fn page_for_offset(page_offsets: &[i64], offset: i64) -> Option<i64> {
    if page_offsets.is_empty() {
        return None;
    }
    let mut page = 0i64;
    for (index, start) in page_offsets.iter().enumerate() {
        if offset >= *start {
            page = index as i64 + 1;
        } else {
            break;
        }
    }
    (page >= 1).then_some(page)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn offsets_accumulate_the_two_character_joiner() {
        let structure = json!({"pages": [{"chars": 100}, {"chars": 250}, {"chars": 40}]});
        assert_eq!(pdf_page_offsets(&structure), vec![0, 102, 354]);
        assert_eq!(
            pdf_page_offsets(&json!({"pages": [{"chars": 1200}]})),
            vec![0]
        );
        // Floats truncate, exactly as `int(chars)` does.
        assert_eq!(
            pdf_page_offsets(&json!({"pages": [{"chars": 10.9}, {"chars": 5.0}]})),
            vec![0, 12]
        );
    }

    #[test]
    fn anything_malformed_answers_nothing() {
        for structure in [
            json!({"meta": 1}),
            json!({"pages": {"chars": 5}}),
            json!({"pages": []}),
            json!({"pages": [{"chars": 5}, 7]}),
            json!({"pages": [{"chars": 5}, {}]}),
            json!({"pages": [{"chars": 5}, {"chars": -1}]}),
            json!({"pages": [{"chars": true}]}),
            json!({"pages": [{"chars": "5"}]}),
            json!([1, 2, 3]),
            json!(null),
        ] {
            assert!(pdf_page_offsets(&structure).is_empty(), "{structure}");
        }
    }

    #[test]
    fn a_page_is_the_last_start_at_or_before_the_offset() {
        let offsets = vec![0, 102, 354];
        assert_eq!(page_for_offset(&offsets, 0), Some(1));
        assert_eq!(page_for_offset(&offsets, 101), Some(1));
        assert_eq!(page_for_offset(&offsets, 102), Some(2));
        assert_eq!(page_for_offset(&offsets, 353), Some(2));
        assert_eq!(page_for_offset(&offsets, 354), Some(3));
        assert_eq!(page_for_offset(&offsets, 10_000), Some(3));
        assert_eq!(page_for_offset(&offsets, -1), None);
        assert_eq!(page_for_offset(&[], 5), None);
        // A zero-length page shares a start with the next one; the later page
        // wins, because the scan keeps assigning while `offset >= start`.
        assert_eq!(page_for_offset(&[0, 2, 12], 2), Some(2));
    }

    #[test]
    fn the_locator_comes_from_lattice_core_and_still_answers() {
        let mut meta = serde_json::Map::new();
        assert_eq!(citation_locator(&meta), "");
        meta.insert("page".into(), Value::from(3));
        meta.insert("page_end".into(), Value::from(5));
        assert_eq!(citation_locator(&meta), "p.3–5");
    }
}
