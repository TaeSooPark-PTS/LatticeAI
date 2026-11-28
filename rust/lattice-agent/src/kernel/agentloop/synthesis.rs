use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::kernel::state::AgentState;
use crate::kernel::transcript::rag_grounding::extract_evidence_passages;
use crate::parse::pystr::{char_len, char_slice, is_truthy};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidenceSource {
    pub label: String,
    pub excerpt: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SynthesisCoverage {
    pub total_sources: usize,
    pub covered_sources: usize,
    pub missing_sources: Vec<String>,
    pub echoed_instruction: bool,
    pub is_stub: bool,
}

impl SynthesisCoverage {
    pub fn is_complete(&self) -> bool {
        !self.echoed_instruction && !self.is_stub && self.missing_sources.is_empty()
    }

    pub fn to_detail(&self) -> Value {
        json!({
            "total_sources": self.total_sources,
            "covered_sources": self.covered_sources,
            "missing_sources": self.missing_sources,
            "echoed_instruction": self.echoed_instruction,
            "is_stub": self.is_stub,
        })
    }
}

fn push_source(
    out: &mut Vec<EvidenceSource>,
    seen: &mut BTreeSet<String>,
    label: String,
    excerpt: String,
    max_sources: usize,
) {
    if out.len() >= max_sources {
        return;
    }
    let clean_label = label.trim().to_string();
    let clean_excerpt = excerpt.split_whitespace().collect::<Vec<_>>().join(" ");
    if clean_label.is_empty() || clean_excerpt.is_empty() {
        return;
    }
    let key = clean_label.to_lowercase();
    if seen.insert(key) {
        out.push(EvidenceSource {
            label: clean_label,
            excerpt: char_slice(&clean_excerpt, 240).to_string(),
        });
    }
}

pub fn collect_evidence_sources(transcript: &[Value], max_sources: usize) -> Vec<EvidenceSource> {
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    let cap = max_sources.max(1);

    for passage in extract_evidence_passages(transcript) {
        if passage.superseded {
            continue;
        }
        let label = passage.source.unwrap_or_else(|| passage.tool.clone());
        push_source(&mut out, &mut seen, label, passage.text, cap);
    }

    for step in transcript {
        if out.len() >= cap {
            break;
        }
        if step.get("state").and_then(Value::as_str) != Some(AgentState::Executing.as_str()) {
            continue;
        }
        if step.get("error").is_some_and(is_truthy) {
            continue;
        }
        let action = step.get("action").and_then(Value::as_str).unwrap_or("");
        if !matches!(action, "read_file" | "search_files" | "grep_search") {
            continue;
        }
        let label = step
            .get("args")
            .and_then(|a| a.get("path").or_else(|| a.get("query")))
            .and_then(Value::as_str)
            .unwrap_or(action)
            .to_string();
        let excerpt = match step.get("result") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Object(obj)) => ["content", "text", "preview", "summary", "matches"]
                .iter()
                .find_map(|k| obj.get(*k))
                .map(|v| match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .unwrap_or_default(),
            _ => String::new(),
        };
        push_source(&mut out, &mut seen, label, excerpt, cap);
    }

    out
}

pub fn build_evidence_scaffold(request: &str, transcript: &[Value], max_chars: usize) -> String {
    let sources = collect_evidence_sources(transcript, 8);
    if sources.len() < 2 {
        return String::new();
    }
    let mut lines = vec![
        "[SYNTHESIS SCAFFOLD — COVER ALL SOURCES]".to_string(),
        format!("Request: {}", request.trim()),
    ];
    for (idx, source) in sources.iter().enumerate() {
        lines.push(format!(
            "[S{}] ({}) {}",
            idx + 1,
            source.label,
            source.excerpt
        ));
    }
    lines.push(format!(
        "Checklist: Synthesize facts across all {} sources above into one clear answer and cite [S1]..[S{}] or filenames.",
        sources.len(),
        sources.len()
    ));
    let full = lines.join("\n");
    if max_chars == 0 || char_len(&full) <= max_chars {
        return full;
    }
    if max_chars <= 3 {
        return char_slice(&full, max_chars).to_string();
    }
    format!("{}...", char_slice(&full, max_chars - 3))
}

fn is_hangul(ch: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&ch) || ('\u{3131}'..='\u{318E}').contains(&ch)
}

fn informative_tokens(text: &str) -> BTreeSet<String> {
    let mut tokens = BTreeSet::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_alphanumeric() || is_hangul(ch) {
            for lc in ch.to_lowercase() {
                current.push(lc);
            }
        } else if !current.is_empty() {
            let ko = current.chars().any(is_hangul);
            let len = char_len(&current);
            if (ko && len >= 2) || (!ko && len >= 4) {
                tokens.insert(current.clone());
            }
            current.clear();
        }
    }
    if !current.is_empty() {
        let ko = current.chars().any(is_hangul);
        let len = char_len(&current);
        if (ko && len >= 2) || (!ko && len >= 4) {
            tokens.insert(current);
        }
    }
    tokens
}

pub fn check_synthesis_coverage(answer: &str, sources: &[EvidenceSource]) -> SynthesisCoverage {
    let trimmed = answer.trim();
    let echoed_instruction = crate::prompts::guided::contains_owned_instruction(trimmed);
    let is_stub = sources.len() >= 2 && char_len(trimmed) < 24;
    if sources.is_empty() {
        return SynthesisCoverage {
            total_sources: 0,
            covered_sources: 0,
            missing_sources: Vec::new(),
            echoed_instruction,
            is_stub,
        };
    }

    let answer_lower = trimmed.to_lowercase();
    let answer_tokens = informative_tokens(trimmed);
    let mut missing_sources = Vec::new();
    let mut covered_sources = 0usize;

    for (idx, source) in sources.iter().enumerate() {
        let num = idx + 1;
        let has_tag = answer_lower.contains(&format!("[s{num}]"))
            || answer_lower.contains(&format!("[{num}]"));
        let basename = source
            .label
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or(&source.label)
            .to_lowercase();
        let has_name = answer_lower.contains(&source.label.to_lowercase())
            || (!basename.is_empty() && basename.len() >= 3 && answer_lower.contains(&basename));
        let source_tokens = informative_tokens(&source.excerpt);
        let overlap = answer_tokens.intersection(&source_tokens).count();
        let has_content = if source_tokens.len() <= 1 {
            overlap >= 1
        } else {
            overlap >= 2
        };
        if has_tag || has_name || has_content {
            covered_sources += 1;
        } else {
            missing_sources.push(source.label.clone());
        }
    }

    SynthesisCoverage {
        total_sources: sources.len(),
        covered_sources,
        missing_sources,
        echoed_instruction,
        is_stub,
    }
}

pub fn build_synthesis_refinement_prompt(
    request: &str,
    draft: &str,
    scaffold: &str,
    missing: &[String],
) -> String {
    format!(
        "{scaffold}\n\n[USER REQUEST]\n{}\n\n[INCOMPLETE DRAFT]\n{}\n\n\
[MISSING SOURCES TO INCLUDE]\n{}\n\n\
Revise the answer so it integrates every source listed above (including the missing ones), \
cites [S1], [S2], ... or filenames, and contains no prompt instructions or preamble. \
Reply with the final answer text only.",
        request.trim(),
        draft.trim(),
        missing.join(", ")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scaffold_builds_numbered_blocks_for_multiple_sources() {
        let transcript = vec![
            json!({
                "state": "EXECUTING",
                "action": "read_file",
                "args": {"path": "docs/alpha.md"},
                "result": {"content": "Alpha runs on port 4825 with SQLite WAL storage."}
            }),
            json!({
                "state": "EXECUTING",
                "action": "read_file",
                "args": {"path": "docs/beta.md"},
                "result": {"content": "Beta uses HNSW vector indexing for fast recall."}
            }),
        ];
        let scaffold = build_evidence_scaffold("Summarize Alpha and Beta", &transcript, 1500);
        assert!(scaffold.contains("[S1] (docs/alpha.md)"));
        assert!(scaffold.contains("[S2] (docs/beta.md)"));
        assert!(scaffold.contains("all 2 sources"));
    }

    #[test]
    fn coverage_detects_missing_sources_echoed_instructions_and_stubs() {
        let sources = vec![
            EvidenceSource {
                label: "docs/alpha.md".into(),
                excerpt: "Alpha uses SQLite WAL on port 4825.".into(),
            },
            EvidenceSource {
                label: "docs/beta.md".into(),
                excerpt: "Beta uses HNSW vector indexing for recall.".into(),
            },
        ];

        let incomplete = check_synthesis_coverage(
            "Alpha runs on SQLite WAL using port 4825 as documented.",
            &sources,
        );
        assert!(!incomplete.is_complete());
        assert_eq!(incomplete.covered_sources, 1);
        assert_eq!(incomplete.missing_sources, vec!["docs/beta.md"]);

        let complete = check_synthesis_coverage(
            "Alpha runs on SQLite WAL [S1], while Beta uses HNSW vector indexing [S2].",
            &sources,
        );
        assert!(complete.is_complete());
        assert_eq!(complete.covered_sources, 2);

        let echoed = check_synthesis_coverage(
            "Write only the resulting file body — no preamble, no code fence. Alpha and Beta.",
            &sources,
        );
        assert!(echoed.echoed_instruction);
        assert!(!echoed.is_complete());

        let stub = check_synthesis_coverage("Done.", &sources);
        assert!(stub.is_stub);
        assert!(!stub.is_complete());
    }

    #[test]
    fn refinement_prompt_names_missing_sources() {
        let prompt = build_synthesis_refinement_prompt(
            "Compare alpha and beta",
            "Alpha is fast.",
            "[SYNTHESIS SCAFFOLD]\n[S1] alpha\n[S2] beta",
            &["docs/beta.md".to_string()],
        );
        assert!(prompt.contains("docs/beta.md"));
        assert!(prompt.contains("[MISSING SOURCES TO INCLUDE]"));
    }
}
