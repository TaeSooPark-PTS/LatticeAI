use lattice_core::tokens::TokenPrefix;

use super::ast::{ast_segments, CodeLanguage};

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Segment {
    pub start: usize,
    pub end: usize,
    pub heading_path: Option<String>,
    pub symbol_path: Option<String>,
}

impl Segment {
    pub fn plain(start: usize, end: usize) -> Self {
        Self {
            start,
            end,
            ..Self::default()
        }
    }

    pub fn same_label(&self, other: &Segment) -> bool {
        self.heading_path == other.heading_path && self.symbol_path == other.symbol_path
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segmentation {
    pub segments: Vec<Segment>,
    pub ast_language: Option<&'static str>,
}

pub fn paragraph_spans(cleaned: &[char]) -> Vec<(usize, usize)> {
    let total = cleaned.len();
    let mut spans = Vec::new();
    let mut start = 0usize;
    let mut index = 0usize;
    while index < total {
        if cleaned[index] != '\n' {
            index += 1;
            continue;
        }
        let mut cursor = index + 1;
        while cursor < total && matches!(cleaned[cursor], ' ' | '\t' | '\r') {
            cursor += 1;
        }
        if cursor < total && cleaned[cursor] == '\n' {
            while cursor < total && cleaned[cursor].is_whitespace() {
                cursor += 1;
            }
            if cursor > start {
                spans.push((start, cursor));
            }
            start = cursor;
            index = cursor;
        } else {
            index += 1;
        }
    }
    if total > start {
        spans.push((start, total));
    }
    spans
}

pub fn segment(
    cleaned: &[char],
    prefix: &TokenPrefix,
    strategy: &str,
    language: Option<CodeLanguage>,
    split_over_tokens: usize,
) -> Segmentation {
    if cleaned.is_empty() {
        return Segmentation {
            segments: Vec::new(),
            ast_language: None,
        };
    }
    match strategy {
        "markdown" => Segmentation {
            segments: super::markdown::markdown_section_spans(cleaned)
                .into_iter()
                .map(|(start, end, path)| Segment {
                    start,
                    end,
                    heading_path: path,
                    symbol_path: None,
                })
                .collect(),
            ast_language: None,
        },
        "code" => {
            if let Some(language) = language {
                if let Some(segments) = ast_segments(cleaned, prefix, language, split_over_tokens) {
                    return Segmentation {
                        segments,
                        ast_language: Some(language.label()),
                    };
                }
            }
            Segmentation {
                segments: super::code::code_segment_spans(cleaned)
                    .into_iter()
                    .map(|(start, end)| Segment::plain(start, end))
                    .collect(),
                ast_language: None,
            }
        }
        "prose" => Segmentation {
            segments: paragraph_spans(cleaned)
                .into_iter()
                .map(|(start, end)| Segment::plain(start, end))
                .collect(),
            ast_language: None,
        },
        _ => Segmentation {
            segments: vec![Segment::plain(0, cleaned.len())],
            ast_language: None,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    #[test]
    fn paragraphs_split_on_blank_lines_and_cover_the_text() {
        let text = chars("one\ntwo\n\n  \nthree\n\nfour");
        let spans = paragraph_spans(&text);
        assert_eq!(spans.len(), 3);
        assert_eq!(spans.first().map(|s| s.0), Some(0));
        assert_eq!(spans.last().map(|s| s.1), Some(text.len()));
        for pair in spans.windows(2) {
            assert_eq!(pair[0].1, pair[1].0);
        }
        assert_eq!(paragraph_spans(&chars("solo")), vec![(0, 4)]);
        assert!(paragraph_spans(&[]).is_empty());
    }

    #[test]
    fn each_strategy_yields_contiguous_segments() {
        let cases = [
            ("markdown", "# A\nbody\n## B\nmore"),
            ("code", "def a():\n    pass\n\ndef b():\n    pass\n"),
            ("prose", "First.\n\nSecond."),
            ("plain", "just text"),
        ];
        for (strategy, text) in cases {
            let cleaned = chars(text);
            let prefix = TokenPrefix::new(&cleaned);
            let out = segment(&cleaned, &prefix, strategy, None, 300);
            assert!(out.ast_language.is_none());
            assert_eq!(out.segments.first().map(|s| s.start), Some(0), "{strategy}");
            assert_eq!(
                out.segments.last().map(|s| s.end),
                Some(cleaned.len()),
                "{strategy}"
            );
        }
        let empty = segment(&[], &TokenPrefix::new(&[]), "plain", None, 300);
        assert!(empty.segments.is_empty());
        let headings: Vec<Option<String>> = segment(
            &chars("# A\nbody\n## B\nmore"),
            &TokenPrefix::new(&chars("# A\nbody\n## B\nmore")),
            "markdown",
            None,
            300,
        )
        .segments
        .into_iter()
        .map(|s| s.heading_path)
        .collect();
        assert_eq!(headings, vec![Some("A".into()), Some("A > B".into())]);
    }

    #[test]
    fn code_with_a_known_language_uses_the_syntax_tree() {
        let text = chars("fn a() {}\n\nfn b() {}\n");
        let prefix = TokenPrefix::new(&text);
        let out = segment(&text, &prefix, "code", Some(CodeLanguage::Rust), 300);
        assert_eq!(out.ast_language, Some("rust"));
        assert!(out
            .segments
            .iter()
            .any(|s| s.symbol_path.as_deref() == Some("fn b")));
        let labels = Segment {
            heading_path: Some("x".into()),
            ..Segment::plain(0, 1)
        };
        assert!(!labels.same_label(&Segment::plain(1, 2)));
    }
}
