use super::{line_starts, make_chunk, plain_windows, Chunk};
use crate::pystr::is_py_space;

const CODE_DECLARATION_PREFIXES: [&str; 7] = [
    "def ",
    "class ",
    "function ",
    "export ",
    "const ",
    "public ",
    "private ",
];

fn blank_run_ends(cleaned: &[char]) -> Vec<usize> {
    let mut ends = Vec::new();
    let total = cleaned.len();
    let mut index = 0usize;
    while index < total {
        if cleaned[index] != '\n' {
            index += 1;
            continue;
        }
        let mut greedy = index + 1;
        while greedy < total && is_py_space(cleaned[greedy]) {
            greedy += 1;
        }
        let mut cursor = greedy;
        let mut matched = None;
        while cursor > index + 1 {
            cursor -= 1;
            if cleaned[cursor] == '\n' {
                matched = Some(cursor);
                break;
            }
        }
        match matched {
            Some(at) => {
                ends.push(at + 1);
                index = at + 1;
            }
            None => index += 1,
        }
    }
    ends
}

fn declaration_line_starts(cleaned: &[char]) -> Vec<usize> {
    line_starts(cleaned)
        .into_iter()
        .filter(|start| {
            CODE_DECLARATION_PREFIXES
                .iter()
                .any(|prefix| starts_with(cleaned, *start, prefix))
        })
        .collect()
}

fn starts_with(cleaned: &[char], at: usize, prefix: &str) -> bool {
    prefix
        .chars()
        .enumerate()
        .all(|(offset, expected)| cleaned.get(at + offset) == Some(&expected))
}

pub(super) fn code_segment_spans(cleaned: &[char]) -> Vec<(usize, usize)> {
    let mut boundaries: std::collections::BTreeSet<usize> =
        [0usize, cleaned.len()].into_iter().collect();
    boundaries.extend(blank_run_ends(cleaned));
    boundaries.extend(declaration_line_starts(cleaned));
    let ordered: Vec<usize> = boundaries.into_iter().collect();
    ordered.windows(2).map(|pair| (pair[0], pair[1])).collect()
}

pub(super) fn chunks(cleaned: &[char], size: usize, overlap: usize) -> Vec<Chunk> {
    let hard_limit = size * 3 / 2;
    let mut out = Vec::new();
    let mut pack: Option<(usize, usize)> = None;
    for (start, end) in code_segment_spans(cleaned) {
        if end - start > hard_limit {
            if let Some((open, close)) = pack.take() {
                out.push(make_chunk(cleaned, open, close, 0, "code", None));
            }
            out.extend(plain_windows(
                &cleaned[start..end],
                size,
                overlap,
                start,
                "code",
                None,
            ));
            continue;
        }
        pack = Some(match pack {
            None => (start, end),
            Some((open, _)) if end - open <= size => (open, end),
            Some((open, close)) => {
                out.push(make_chunk(cleaned, open, close, 0, "code", None));
                (start, end)
            }
        });
    }
    if let Some((open, close)) = pack {
        out.push(make_chunk(cleaned, open, close, 0, "code", None));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    fn texts(chunks: &[Chunk]) -> Vec<String> {
        chunks.iter().map(|chunk| chunk.text.clone()).collect()
    }

    #[test]
    fn blank_runs_are_one_match_per_run() {
        assert_eq!(blank_run_ends(&chars("a\n\n\n\nb")), vec![5]);
        assert_eq!(blank_run_ends(&chars("a\n\n\n\nb\n \t \nc")), vec![5, 11]);
        assert_eq!(blank_run_ends(&chars("a\nb\nc")), Vec::<usize>::new());
        assert_eq!(blank_run_ends(&chars("a\n\u{1c}\nb")), vec![4]);
    }

    #[test]
    fn declaration_lines_start_a_segment() {
        let source = "x = 1\ndef a():\n    pass\nclass B:\n    pass\n";
        let starts = declaration_line_starts(&chars(source));
        assert_eq!(starts, vec![6, 24]);
        assert!(declaration_line_starts(&chars("  def a():")).is_empty());
        assert!(declaration_line_starts(&chars("definitely")).is_empty());
    }

    #[test]
    fn a_segment_past_one_and_a_half_windows_is_flushed_and_windowed() {
        let monster = "y".repeat(40);
        let source = format!("def a():\n    1\n\n\n{monster}\n\n\ndef b():\n    2");
        let produced = chunks(&chars(&source), 20, 5);
        assert!(produced.len() >= 4, "{:?}", texts(&produced));
        assert!(produced.iter().all(|chunk| chunk.meta.strategy == "code"));
        assert!(produced
            .iter()
            .all(|chunk| chunk.meta.heading_path.is_none()));
        let source_chars = chars(&source);
        for chunk in &produced {
            let start = chunk.meta.start_char;
            let end = start + chunk.text.chars().count();
            let slice: String = source_chars[start..end].iter().collect();
            assert_eq!(slice, chunk.text);
        }
    }
}
