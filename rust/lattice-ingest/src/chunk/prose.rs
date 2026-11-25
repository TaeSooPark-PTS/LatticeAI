use super::{make_chunk, Chunk};
use crate::pystr::is_py_space;

const PROSE_TERMINATORS: [char; 7] = ['.', '!', '?', '。', '！', '？', '…'];
const PROSE_CLOSERS: [char; 8] = ['"', '\'', '”', '’', '」', '』', ')', ']'];
const PROSE_MIN_SPAN_DIVISOR: usize = 2;

fn strong_boundary_at(window: &[char], at: usize) -> Option<usize> {
    let mut cursor = at;
    while window
        .get(cursor)
        .is_some_and(|c| PROSE_TERMINATORS.contains(c))
    {
        cursor += 1;
    }
    if cursor > at {
        let mut after = cursor;
        while window.get(after).is_some_and(|c| PROSE_CLOSERS.contains(c)) {
            after += 1;
        }
        let mut spaces = after;
        while window.get(spaces).is_some_and(|c| is_py_space(*c)) {
            spaces += 1;
        }
        if spaces > after {
            return Some(spaces);
        }
        return None;
    }
    if window.get(at) != Some(&'\n') {
        return None;
    }
    let mut cursor = at + 1;
    while window.get(cursor).is_some_and(|c| *c == ' ' || *c == '\t') {
        cursor += 1;
    }
    (window.get(cursor) == Some(&'\n')).then_some(cursor + 1)
}

fn last_boundary(cleaned: &[char], lo: usize, hi: usize) -> Option<usize> {
    let hi = hi.min(cleaned.len());
    if lo >= hi {
        return None;
    }
    let window = &cleaned[lo..hi];
    let mut last: Option<usize> = None;
    let mut index = 0usize;
    while index < window.len() {
        match strong_boundary_at(window, index) {
            Some(end) => {
                last = Some(end);
                index = end;
            }
            None => index += 1,
        }
    }
    if let Some(end) = last.filter(|end| *end > 0) {
        return Some(lo + end);
    }
    window
        .iter()
        .rposition(|c| *c == '\n')
        .map(|index| lo + index + 1)
}

pub(super) fn chunks(cleaned: &[char], size: usize, overlap: usize) -> Vec<Chunk> {
    let total = cleaned.len();
    let min_span = (size / PROSE_MIN_SPAN_DIVISOR).max(1);
    let mut out = Vec::new();
    let mut start = 0usize;
    while start < total {
        let hard_end = total.min(start + size);
        let mut end = hard_end;
        if hard_end < total {
            if let Some(boundary) =
                last_boundary(cleaned, start + min_span, hard_end).filter(|b| *b > start)
            {
                end = boundary;
            }
        }
        out.push(make_chunk(cleaned, start, end, 0, "prose", None));
        if end >= total {
            break;
        }
        start = (start + 1).max(end.saturating_sub(overlap));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::typed_chunks;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    #[test]
    fn prose_backs_up_to_a_sentence_then_a_line_then_the_hard_cut() {
        let strong = typed_chunks("one two three. four five six seven", "prose", 20, 4);
        assert_eq!(strong[0].text, "one two three. ");
        let weak = typed_chunks("aaaa bbbb cccc\ndddd eeee ffff", "prose", 20, 4);
        assert_eq!(weak[0].text, "aaaa bbbb cccc\n");
        let out_of_reach = typed_chunks("aaaa bbbb\ncccc dddd eeee ffff", "prose", 20, 4);
        assert_eq!(out_of_reach[0].text, "aaaa bbbb\ncccc dddd ");
        let none = typed_chunks(&"z".repeat(50), "prose", 20, 4);
        assert_eq!(none[0].text.chars().count(), 20);
    }

    #[test]
    fn the_strong_boundary_allows_closers_and_needs_trailing_space() {
        let window = chars("he said \"stop!\" and left");
        assert_eq!(strong_boundary_at(&window, 13), Some(16));
        assert_eq!(strong_boundary_at(&chars("end."), 3), None);
        assert_eq!(strong_boundary_at(&chars("a\n \t\nb"), 1), Some(5));
        assert_eq!(strong_boundary_at(&chars("a\nb"), 1), None);
    }
}
