//! The markdown strategy: heading sections, merged and windowed.
//!
//! `_markdown_section_spans` + `_merge_small_sections` + `_markdown_chunks`
//! from `lattice_brain/graph/_kg_common/text.py`, over character offsets.
//!
//! The 200-character merge floor is why heading-dense documents do not shatter
//! into confetti: a section under it is folded into the *next* one and keeps
//! the heading path that was in effect where it started, so a chunk never
//! claims a heading its first sentence did not live under.

use super::{line_starts, make_chunk, plain_windows, Chunk};
use crate::pystr::py_strip;

/// `_MARKDOWN_MIN_SECTION_CHARS` — sections under this merge forward.
pub const MARKDOWN_MIN_SECTION_CHARS: usize = 200;

/// `^(#{1,6}) (.*)$` at one line start → `(level, raw title)`.
///
/// `#{1,6}` is greedy and every character in the run is a `#`, so backtracking
/// can never find a space: the match exists iff the run is 1–6 long and the
/// next character is a literal space. Seven hashes is not a heading, and
/// neither is `#NoSpace`.
fn heading_at(cleaned: &[char], start: usize) -> Option<(usize, String)> {
    let mut level = 0usize;
    while cleaned.get(start + level) == Some(&'#') {
        level += 1;
    }
    if level == 0 || level > 6 || cleaned.get(start + level) != Some(&' ') {
        return None;
    }
    let body = start + level + 1;
    let mut end = body;
    while end < cleaned.len() && cleaned[end] != '\n' {
        end += 1;
    }
    Some((level, cleaned[body..end].iter().collect()))
}

/// `_markdown_section_spans` — `(start, end, heading_path)` for every section.
fn markdown_section_spans(cleaned: &[char]) -> Vec<(usize, usize, Option<String>)> {
    let mut spans: Vec<(usize, usize, Option<String>)> = Vec::new();
    let mut stack: Vec<(usize, String)> = Vec::new();
    let mut prev_start = 0usize;
    let mut prev_path: Option<String> = None;
    for offset in line_starts(cleaned) {
        let Some((level, title)) = heading_at(cleaned, offset) else {
            continue;
        };
        if offset > prev_start {
            spans.push((prev_start, offset, prev_path.clone()));
        }
        while stack.last().is_some_and(|(depth, _)| *depth >= level) {
            stack.pop();
        }
        stack.push((level, py_strip(&title).to_string()));
        prev_start = offset;
        let joined = stack
            .iter()
            .map(|(_, title)| title.as_str())
            .collect::<Vec<_>>()
            .join(" > ");
        // `" > ".join(...) or None`: a lone heading with an empty title joins
        // to "", which Python treats as "no path known".
        prev_path = (!joined.is_empty()).then_some(joined);
    }
    if cleaned.len() > prev_start {
        spans.push((prev_start, cleaned.len(), prev_path));
    }
    spans
}

/// `_merge_small_sections` — forward merge, with a backward merge for a
/// trailing runt.
fn merge_small_sections(
    spans: Vec<(usize, usize, Option<String>)>,
    min_chars: usize,
) -> Vec<(usize, usize, Option<String>)> {
    let mut merged: Vec<(usize, usize, Option<String>)> = Vec::new();
    let mut pending: Option<(usize, usize, Option<String>)> = None;
    for (start, end, path) in spans {
        pending = Some(match pending {
            None => (start, end, path),
            // A merged section keeps the heading path in effect at its start.
            Some((open, _, open_path)) => (open, end, open_path),
        });
        let span = pending.as_ref().expect("just assigned");
        if span.1 - span.0 >= min_chars {
            merged.push(pending.take().expect("just checked"));
        }
    }
    if let Some(span) = pending {
        match merged.last().cloned() {
            Some(last) if span.1 - span.0 < min_chars => {
                merged.pop();
                merged.push((last.0, span.1, last.2));
            }
            _ => merged.push(span),
        }
    }
    merged
}

/// `_markdown_chunks`.
pub(super) fn chunks(cleaned: &[char], size: usize, overlap: usize) -> Vec<Chunk> {
    let sections =
        merge_small_sections(markdown_section_spans(cleaned), MARKDOWN_MIN_SECTION_CHARS);
    let mut out = Vec::new();
    for (start, end, path) in sections {
        if end - start <= size {
            out.push(make_chunk(
                cleaned,
                start,
                end,
                0,
                "markdown",
                path.as_deref(),
            ));
        } else {
            out.extend(plain_windows(
                &cleaned[start..end],
                size,
                overlap,
                start,
                "markdown",
                path.as_deref(),
            ));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    #[test]
    fn headings_need_one_to_six_hashes_and_a_space() {
        assert!(heading_at(&chars("# a"), 0).is_some());
        assert_eq!(heading_at(&chars("###### a"), 0).map(|(l, _)| l), Some(6));
        assert!(heading_at(&chars("####### a"), 0).is_none());
        assert!(heading_at(&chars("#a"), 0).is_none());
        assert!(heading_at(&chars("a"), 0).is_none());
        assert_eq!(
            heading_at(&chars("# "), 0).map(|(_, t)| t),
            Some(String::new())
        );
        assert_eq!(
            heading_at(&chars("## t\nx"), 0).map(|(_, t)| t),
            Some("t".into())
        );
    }

    #[test]
    fn the_heading_stack_pops_to_the_current_level() {
        let doc = "# A\n## B\n### C\n## D\n# E\n";
        let paths: Vec<Option<String>> = markdown_section_spans(&chars(doc))
            .into_iter()
            .map(|(_, _, path)| path)
            .collect();
        assert_eq!(
            paths,
            vec![
                Some("A".to_string()),
                Some("A > B".to_string()),
                Some("A > B > C".to_string()),
                Some("A > D".to_string()),
                Some("E".to_string()),
            ]
        );
    }

    #[test]
    fn a_preamble_and_an_empty_title_both_carry_no_path() {
        let spans = markdown_section_spans(&chars("intro\n# A\nbody"));
        assert_eq!(spans[0].2, None);
        assert_eq!(spans[1].2, Some("A".to_string()));
        assert_eq!(markdown_section_spans(&chars("# \nbody"))[0].2, None);
        // Two empty titles still join to " > ", which is truthy.
        assert_eq!(
            markdown_section_spans(&chars("# \n## \nbody"))[1].2,
            Some(" > ".to_string())
        );
    }

    #[test]
    fn small_sections_merge_forward_and_a_trailing_runt_merges_backward() {
        let big = "x".repeat(MARKDOWN_MIN_SECTION_CHARS);
        let spans = vec![
            (0usize, 5usize, Some("first".to_string())),
            (5, 10, Some("second".to_string())),
            (10, 10 + big.len(), Some("third".to_string())),
            (10 + big.len(), 12 + big.len(), Some("runt".to_string())),
        ];
        let merged = merge_small_sections(spans, MARKDOWN_MIN_SECTION_CHARS);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0], (0, 12 + big.len(), Some("first".to_string())));
        // Nothing ever reached the floor: the pending span is emitted as is.
        let tiny = vec![(0usize, 3usize, None), (3, 6, Some("b".into()))];
        assert_eq!(
            merge_small_sections(tiny, MARKDOWN_MIN_SECTION_CHARS),
            vec![(0, 6, None)]
        );
    }
}
