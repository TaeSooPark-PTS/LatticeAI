use lattice_core::tokens::TokenPrefix;

use super::budget::TokenBudget;
use super::segments::Segment;

const PROSE_TERMINATORS: [char; 7] = ['.', '!', '?', '。', '！', '？', '…'];

fn boundary_rank(cleaned: &[char], at: usize) -> u8 {
    if at == 0 || at >= cleaned.len() {
        return 5;
    }
    let before = cleaned[at - 1];
    let here = cleaned[at];
    if before == '\n' && at >= 2 && cleaned[at - 2] == '\n' {
        return 4;
    }
    if before == '\n' {
        return 3;
    }
    if here.is_whitespace() && at >= 1 && PROSE_TERMINATORS.contains(&before) {
        return 2;
    }
    if before.is_whitespace() && !here.is_whitespace() {
        return 1;
    }
    0
}

fn best_cut(
    cleaned: &[char],
    prefix: &TokenPrefix,
    start: usize,
    limit_end: usize,
    floor_tokens: usize,
    ideal_tokens: usize,
) -> usize {
    let lo = prefix.advance(start, floor_tokens).max(start + 1);
    let hi = limit_end.max(lo);
    let ideal = prefix.advance(start, ideal_tokens).clamp(lo, hi);
    let mut best: Option<(u8, usize, usize)> = None;
    for at in lo..=hi {
        let rank = boundary_rank(cleaned, at);
        if rank == 0 {
            continue;
        }
        let distance = at.abs_diff(ideal);
        let better = match best {
            None => true,
            Some((best_rank, best_distance, _)) => {
                rank > best_rank || (rank == best_rank && distance < best_distance)
            }
        };
        if better {
            best = Some((rank, distance, at));
        }
    }
    best.map(|(_, _, at)| at).unwrap_or(hi)
}

fn split_segment(
    cleaned: &[char],
    prefix: &TokenPrefix,
    segment: &Segment,
    budget: &TokenBudget,
) -> Vec<Segment> {
    let mut out = Vec::new();
    let mut start = segment.start;
    let cap = budget.content_cap();
    while start < segment.end {
        if prefix.tokens(start, segment.end) <= cap {
            out.push(Segment {
                start,
                end: segment.end,
                ..segment.clone()
            });
            break;
        }
        let remaining = prefix.tokens(start, segment.end);
        let (floor_tokens, ideal_tokens) = if remaining < cap + budget.min {
            let half = remaining / 2;
            (half - half / 3, half)
        } else {
            (
                budget.min.min(budget.content_target()),
                budget.content_target(),
            )
        };
        let limit_end = prefix.advance(start, cap).min(segment.end).max(start + 1);
        let end = best_cut(
            cleaned,
            prefix,
            start,
            limit_end,
            floor_tokens,
            ideal_tokens,
        )
        .min(segment.end);
        out.push(Segment {
            start,
            end,
            ..segment.clone()
        });
        start = end;
    }
    out
}

fn label_of(open: &Segment, next: &Segment) -> (Option<String>, Option<String>) {
    (
        open.heading_path
            .clone()
            .or_else(|| next.heading_path.clone()),
        open.symbol_path
            .clone()
            .or_else(|| next.symbol_path.clone()),
    )
}

pub fn pack(
    cleaned: &[char],
    prefix: &TokenPrefix,
    segments: &[Segment],
    budget: &TokenBudget,
) -> Vec<Segment> {
    let cap = budget.content_cap();
    let target = budget.content_target();
    let mut out: Vec<Segment> = Vec::new();
    let mut open: Option<Segment> = None;
    for segment in segments.iter().filter(|s| s.end > s.start) {
        if prefix.tokens(segment.start, segment.end) > cap {
            out.extend(open.take());
            out.extend(split_segment(cleaned, prefix, segment, budget));
            continue;
        }
        open = Some(match open.take() {
            None => segment.clone(),
            Some(current) => {
                let joined = prefix.tokens(current.start, segment.end);
                let current_tokens = prefix.tokens(current.start, current.end);
                let small = current_tokens < budget.min;
                let fits =
                    joined <= cap && (small || (current.same_label(segment) && joined <= target));
                if fits && current.end == segment.start {
                    let (heading_path, symbol_path) = label_of(&current, segment);
                    Segment {
                        start: current.start,
                        end: segment.end,
                        heading_path,
                        symbol_path,
                    }
                } else {
                    out.push(current);
                    segment.clone()
                }
            }
        });
    }
    out.extend(open);
    if out.len() >= 2 {
        let last = out.len() - 1;
        let runt = prefix.tokens(out[last].start, out[last].end) < budget.min;
        let joined = prefix.tokens(out[last - 1].start, out[last].end);
        if runt && joined <= cap && out[last - 1].end == out[last].start {
            let tail = out.pop().expect("len checked");
            if let Some(previous) = out.last_mut() {
                previous.end = tail.end;
            }
        }
    }
    out
}

fn overlap_start(
    cleaned: &[char],
    prefix: &TokenPrefix,
    previous: &Segment,
    overlap: usize,
) -> usize {
    let raw = prefix
        .retreat(previous.end, overlap)
        .max(previous.start + 1);
    let mut at = raw;
    while at < previous.end {
        if at > 0 && cleaned[at - 1].is_whitespace() && !cleaned[at].is_whitespace() {
            return at;
        }
        at += 1;
    }
    raw.min(previous.end)
}

pub fn apply_overlap(
    cleaned: &[char],
    prefix: &TokenPrefix,
    spans: &mut [Segment],
    overlap: usize,
) {
    if overlap == 0 {
        return;
    }
    for index in 1..spans.len() {
        let previous = spans[index - 1].clone();
        let current = &spans[index];
        if previous.end != current.start || !previous.same_label(current) {
            continue;
        }
        let start = overlap_start(cleaned, prefix, &previous, overlap);
        if start < previous.end {
            spans[index].start = start;
        }
    }
}

pub fn trimmed(cleaned: &[char], start: usize, end: usize) -> Option<(usize, usize)> {
    let mut lo = start;
    let mut hi = end.min(cleaned.len());
    while lo < hi && cleaned[lo].is_whitespace() {
        lo += 1;
    }
    while hi > lo && cleaned[hi - 1].is_whitespace() {
        hi -= 1;
    }
    (hi > lo).then_some((lo, hi))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    fn words(count: usize) -> String {
        (0..count)
            .map(|i| format!("word{i:04}"))
            .collect::<Vec<_>>()
            .join(" ")
    }

    #[test]
    fn an_oversized_segment_splits_inside_the_cap_on_good_boundaries() {
        let text = chars(&format!("{}.\n\n{}", words(300), words(300)));
        let prefix = TokenPrefix::new(&text);
        let budget = TokenBudget::FLAT;
        let spans = pack(&text, &prefix, &[Segment::plain(0, text.len())], &budget);
        assert!(spans.len() >= 2);
        for span in &spans {
            assert!(prefix.tokens(span.start, span.end) <= budget.content_cap());
        }
        assert_eq!(spans[0].start, 0);
        assert_eq!(spans.last().map(|s| s.end), Some(text.len()));
        for pair in spans.windows(2) {
            assert_eq!(pair[0].end, pair[1].start);
        }
    }

    #[test]
    fn small_segments_pack_forward_and_a_trailing_runt_merges_back() {
        let parts: Vec<String> = (0..12).map(|i| format!("{}\n\n", words(20 + i))).collect();
        let joined = parts.concat();
        let text = chars(&joined);
        let prefix = TokenPrefix::new(&text);
        let mut segments = Vec::new();
        let mut at = 0usize;
        for part in &parts {
            let len = part.chars().count();
            segments.push(Segment::plain(at, at + len));
            at += len;
        }
        let spans = pack(&text, &prefix, &segments, &TokenBudget::CHILD);
        assert!(spans.len() < segments.len());
        for span in &spans {
            assert!(prefix.tokens(span.start, span.end) <= TokenBudget::CHILD.content_cap());
        }
        let last = spans.last().expect("spans");
        assert!(
            spans.len() == 1 || prefix.tokens(last.start, last.end) >= TokenBudget::CHILD.min / 2
        );
    }

    #[test]
    fn different_sections_stay_apart_once_they_are_big_enough() {
        let a = format!("{}\n", words(170));
        let b = words(170);
        let text = chars(&format!("{a}{b}"));
        let prefix = TokenPrefix::new(&text);
        let split = a.chars().count();
        let segments = vec![
            Segment {
                heading_path: Some("A".into()),
                ..Segment::plain(0, split)
            },
            Segment {
                heading_path: Some("B".into()),
                ..Segment::plain(split, text.len())
            },
        ];
        let spans = pack(&text, &prefix, &segments, &TokenBudget::FLAT);
        assert_eq!(spans.len(), 2);
        assert_eq!(spans[1].heading_path.as_deref(), Some("B"));
        let mut overlapped = spans.clone();
        apply_overlap(&text, &prefix, &mut overlapped, 48);
        assert_eq!(overlapped, spans, "no overlap across a heading change");
    }

    #[test]
    fn overlap_backs_into_the_previous_span_of_the_same_section() {
        let text = chars(&words(900));
        let prefix = TokenPrefix::new(&text);
        let budget = TokenBudget::FLAT;
        let mut spans = pack(&text, &prefix, &[Segment::plain(0, text.len())], &budget);
        let before = spans.clone();
        apply_overlap(&text, &prefix, &mut spans, budget.overlap);
        for index in 1..spans.len() {
            assert!(spans[index].start < before[index].start);
            let shared = prefix.tokens(spans[index].start, before[index].start);
            assert!(
                shared >= budget.overlap / 2 && shared <= budget.overlap + 4,
                "{shared}"
            );
            assert!(prefix.tokens(spans[index].start, spans[index].end) <= budget.max);
            assert!(!text[spans[index].start].is_whitespace());
        }
        let mut untouched = before.clone();
        apply_overlap(&text, &prefix, &mut untouched, 0);
        assert_eq!(untouched, before);
    }

    #[test]
    fn trimming_drops_edge_whitespace_and_empty_spans() {
        let text = chars("  ab \n");
        assert_eq!(trimmed(&text, 0, text.len()), Some((2, 4)));
        assert_eq!(trimmed(&text, 0, 2), None);
        assert_eq!(boundary_rank(&text, 0), 5);
    }

    #[test]
    fn boundary_ranks_prefer_paragraphs_then_lines_then_sentences() {
        let text = chars("a.\n\nb\nc. d e");
        assert_eq!(boundary_rank(&text, 4), 4);
        assert_eq!(boundary_rank(&text, 6), 3);
        assert_eq!(boundary_rank(&text, 8), 2);
        assert_eq!(boundary_rank(&text, 9), 1);
        assert_eq!(boundary_rank(&text, 1), 0);
    }

    #[test]
    fn unbroken_text_is_hard_cut_at_the_cap() {
        let text = chars(&"x".repeat(5000));
        let prefix = TokenPrefix::new(&text);
        let spans = pack(
            &text,
            &prefix,
            &[Segment::plain(0, text.len())],
            &TokenBudget::CHILD,
        );
        assert!(spans.len() >= 4);
        for span in &spans {
            assert!(prefix.tokens(span.start, span.end) <= TokenBudget::CHILD.content_cap());
        }
    }
}
