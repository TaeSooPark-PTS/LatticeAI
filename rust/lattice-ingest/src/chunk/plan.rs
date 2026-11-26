use lattice_core::tokens::TokenPrefix;

use super::ast::CodeLanguage;
use super::budget::{ChunkMode, ChunkingConfig, TokenBudget};
use super::contextual::{context_prefix, document_topics, MAX_TOPICS};
use super::pack::{apply_overlap, pack, trimmed};
use super::segments::{segment, Segment};
use crate::pystr::py_strip;
use crate::strategy::chunk_strategy_for;

pub const TOKEN_CHUNKER: &str = "token_v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannedChunk {
    pub text: String,
    pub start_char: usize,
    pub end_char: usize,
    pub strategy: &'static str,
    pub heading_path: Option<String>,
    pub symbol_path: Option<String>,
    pub token_count: usize,
    pub parent_index: Option<usize>,
    pub ast_language: Option<&'static str>,
    pub context_prefix: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannedParent {
    pub text: String,
    pub start_char: usize,
    pub end_char: usize,
    pub heading_path: Option<String>,
    pub symbol_path: Option<String>,
    pub token_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkPlan {
    pub mode: ChunkMode,
    pub strategy: &'static str,
    pub children: Vec<PlannedChunk>,
    pub parents: Vec<PlannedParent>,
}

struct Planner<'a> {
    cleaned: &'a [char],
    prefix: TokenPrefix,
    strategy: &'static str,
    ast_language: Option<&'static str>,
    title: &'a str,
    contextual: bool,
}

impl Planner<'_> {
    fn finish(&self, spans: Vec<Segment>, parent_index: Option<usize>) -> Vec<PlannedChunk> {
        spans
            .into_iter()
            .filter_map(|span| {
                let (start, end) = trimmed(self.cleaned, span.start, span.end)?;
                let text: String = self.cleaned[start..end].iter().collect();
                let prefix = self.contextual.then(|| {
                    context_prefix(
                        self.title,
                        span.heading_path.as_deref(),
                        span.symbol_path.as_deref(),
                        &document_topics(&text, MAX_TOPICS),
                    )
                });
                Some(PlannedChunk {
                    text,
                    start_char: start,
                    end_char: end,
                    strategy: self.strategy,
                    heading_path: span.heading_path,
                    symbol_path: span.symbol_path,
                    token_count: self.prefix.tokens(start, end),
                    parent_index,
                    ast_language: self.ast_language,
                    context_prefix: prefix.filter(|p| !p.is_empty()),
                })
            })
            .collect()
    }

    fn windows(&self, segments: &[Segment], budget: &TokenBudget) -> Vec<Segment> {
        let mut spans = pack(self.cleaned, &self.prefix, segments, budget);
        apply_overlap(self.cleaned, &self.prefix, &mut spans, budget.overlap);
        spans
    }
}

fn clip(segments: &[Segment], start: usize, end: usize) -> Vec<Segment> {
    segments
        .iter()
        .filter(|s| s.end > start && s.start < end)
        .map(|s| Segment {
            start: s.start.max(start),
            end: s.end.min(end),
            ..s.clone()
        })
        .collect()
}

pub fn plan_chunks(
    text: &str,
    filename: &str,
    mime: &str,
    title: &str,
    config: &ChunkingConfig,
) -> ChunkPlan {
    let strategy = chunk_strategy_for(filename, mime);
    let cleaned: Vec<char> = py_strip(text).chars().collect();
    let mut plan = ChunkPlan {
        mode: config.mode,
        strategy,
        children: Vec::new(),
        parents: Vec::new(),
    };
    if cleaned.is_empty() {
        return plan;
    }
    let prefix = TokenPrefix::new(&cleaned);
    let language = config
        .ast
        .then(|| CodeLanguage::from_filename(filename))
        .flatten();
    let split_over = match config.mode {
        ChunkMode::ParentChild => config.parent.content_cap(),
        ChunkMode::Flat => config.flat.content_cap(),
    };
    let segmentation = segment(&cleaned, &prefix, strategy, language, split_over);
    let display_title = if py_strip(title).is_empty() {
        filename
    } else {
        title
    };
    let planner = Planner {
        cleaned: &cleaned,
        prefix,
        strategy,
        ast_language: segmentation.ast_language,
        title: display_title,
        contextual: config.contextual,
    };
    match config.mode {
        ChunkMode::Flat => {
            let spans = planner.windows(&segmentation.segments, &config.flat);
            plan.children = planner.finish(spans, None);
        }
        ChunkMode::ParentChild => {
            let parent_spans = pack(
                &cleaned,
                &planner.prefix,
                &segmentation.segments,
                &config.parent,
            );
            for parent in parent_spans {
                let Some((start, end)) = trimmed(&cleaned, parent.start, parent.end) else {
                    continue;
                };
                let inside = clip(&segmentation.segments, parent.start, parent.end);
                let spans = planner.windows(&inside, &config.child);
                let index = plan.parents.len();
                let children = planner.finish(spans, Some(index));
                if children.is_empty() {
                    continue;
                }
                let sole_child = children.len() == 1
                    && children[0].start_char == start
                    && children[0].end_char == end;
                if sole_child {
                    plan.children.extend(children.into_iter().map(|mut child| {
                        child.parent_index = None;
                        child
                    }));
                    continue;
                }
                plan.parents.push(PlannedParent {
                    text: cleaned[start..end].iter().collect(),
                    start_char: start,
                    end_char: end,
                    heading_path: parent.heading_path,
                    symbol_path: parent.symbol_path,
                    token_count: planner.prefix.tokens(start, end),
                });
                plan.children.extend(children);
            }
        }
    }
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words(count: usize, seed: &str) -> String {
        (0..count)
            .map(|i| format!("{seed}{}", i % 97))
            .collect::<Vec<_>>()
            .join(" ")
    }

    fn flat() -> ChunkingConfig {
        ChunkingConfig {
            mode: ChunkMode::Flat,
            ..ChunkingConfig::default()
        }
    }

    #[test]
    fn flat_chunks_respect_the_token_window_for_every_strategy() {
        let long = format!(
            "{}.\n\n{}.\n\n{}",
            words(400, "alpha"),
            words(400, "beta"),
            words(400, "gamma")
        );
        let markdown = format!(
            "# One\n{}\n\n# Two\n{}",
            words(500, "delta"),
            words(500, "eps")
        );
        let code = (0..80)
            .map(|i| format!("def f{i}(x):\n    return x + {i} * compute(x, {i})\n"))
            .collect::<Vec<_>>()
            .join("\n");
        for (text, name) in [
            (long.as_str(), "notes.txt"),
            (markdown.as_str(), "guide.md"),
            (code.as_str(), "mod.py"),
            (long.as_str(), "blob.bin"),
        ] {
            let plan = plan_chunks(text, name, "", "Title", &flat());
            assert!(plan.parents.is_empty());
            assert!(plan.children.len() >= 2, "{name}");
            for chunk in &plan.children {
                assert!(
                    chunk.token_count <= TokenBudget::FLAT.max,
                    "{name} {}",
                    chunk.token_count
                );
                assert!(chunk.parent_index.is_none());
            }
            let body: Vec<&PlannedChunk> = plan.children.iter().rev().skip(1).collect();
            assert!(
                body.iter()
                    .all(|c| c.token_count >= TokenBudget::FLAT.min / 2),
                "{name}"
            );
        }
    }

    #[test]
    fn parent_child_links_small_children_to_large_parents() {
        let text = format!(
            "{}\n\n{}\n\n{}",
            words(700, "a"),
            words(700, "b"),
            words(700, "c")
        );
        let plan = plan_chunks(&text, "doc.txt", "", "Doc", &ChunkingConfig::default());
        assert_eq!(plan.mode, ChunkMode::ParentChild);
        assert!(!plan.parents.is_empty());
        for parent in &plan.parents {
            assert!(
                parent.token_count <= TokenBudget::PARENT.max,
                "{}",
                parent.token_count
            );
        }
        for child in &plan.children {
            assert!(
                child.token_count <= TokenBudget::CHILD.max,
                "{}",
                child.token_count
            );
            let parent = &plan.parents[child.parent_index.expect("linked")];
            assert!(child.start_char >= parent.start_char && child.end_char <= parent.end_char);
            assert!(parent.text.contains(&child.text));
        }
        let parents_with_children = (0..plan.parents.len())
            .filter(|i| plan.children.iter().any(|c| c.parent_index == Some(*i)))
            .count();
        assert_eq!(parents_with_children, plan.parents.len());
    }

    #[test]
    fn a_short_note_is_one_child_without_a_redundant_parent() {
        let plan = plan_chunks(
            "hello world from lattice",
            "note.md",
            "",
            "Note",
            &ChunkingConfig::default(),
        );
        assert!(plan.parents.is_empty());
        assert_eq!(plan.children.len(), 1);
        assert_eq!(plan.children[0].text, "hello world from lattice");
        assert_eq!(
            plan.children[0].context_prefix.as_deref(),
            Some("Document: Note")
        );
        assert!(
            plan_chunks("   ", "a.md", "", "", &ChunkingConfig::default())
                .children
                .is_empty()
        );
    }

    #[test]
    fn code_files_carry_ast_symbols_into_the_prefix() {
        let source = (0..30)
            .map(|i| {
                format!("pub fn handler_{i}(input: &str) -> usize {{\n    input.len() + {i}\n}}\n")
            })
            .collect::<Vec<_>>()
            .join("\n");
        let plan = plan_chunks(&source, "src/handlers.rs", "", "handlers.rs", &flat());
        assert!(plan.children.iter().all(|c| c.ast_language == Some("rust")));
        let first = &plan.children[0];
        assert_eq!(first.symbol_path.as_deref(), Some("fn handler_0"));
        assert!(first
            .context_prefix
            .as_deref()
            .is_some_and(|p| p.contains("Symbol: fn handler_0")));
        let off = ChunkingConfig {
            ast: false,
            contextual: false,
            ..flat()
        };
        let fallback = plan_chunks(&source, "src/handlers.rs", "", "handlers.rs", &off);
        assert!(fallback.children.iter().all(|c| c.ast_language.is_none()));
        assert!(fallback.children.iter().all(|c| c.context_prefix.is_none()));
    }

    #[test]
    fn markdown_sections_keep_their_heading_path() {
        let text = format!(
            "# Setup\n{}\n## Install\n{}",
            words(600, "setup"),
            words(600, "install")
        );
        let plan = plan_chunks(&text, "guide.md", "", "", &flat());
        assert!(plan
            .children
            .iter()
            .any(|c| c.heading_path.as_deref() == Some("Setup > Install")));
        assert!(plan.children[0]
            .context_prefix
            .as_deref()
            .is_some_and(|p| p.starts_with("Document: guide.md | Section: Setup")));
    }

    #[test]
    fn editing_one_section_leaves_the_other_prefixes_unchanged() {
        let one = format!("# One\n{}", words(400, "alpha"));
        let two = format!("# Two\n{}", words(400, "beta"));
        let before = plan_chunks(&format!("{one}\n{two}"), "g.md", "", "G", &flat());
        let after = plan_chunks(
            &format!("{one}\n# Two\n{}", words(400, "gamma")),
            "g.md",
            "",
            "G",
            &flat(),
        );
        let first = |plan: &ChunkPlan| -> Vec<(String, Option<String>)> {
            plan.children
                .iter()
                .filter(|c| c.heading_path.as_deref() == Some("One"))
                .map(|c| (c.text.clone(), c.context_prefix.clone()))
                .collect()
        };
        assert!(!first(&before).is_empty());
        assert_eq!(first(&before), first(&after));
    }

    #[test]
    fn korean_text_is_measured_by_density_not_characters() {
        let text = "지식 그래프는 문서와 개념을 연결합니다. ".repeat(120);
        let plan = plan_chunks(&text, "memo.txt", "", "메모", &flat());
        for chunk in &plan.children {
            assert!(chunk.token_count <= TokenBudget::FLAT.max);
            assert!(
                chunk.text.chars().count() < 600,
                "{}",
                chunk.text.chars().count()
            );
        }
        assert!(plan.children.len() >= 4);
    }
}
