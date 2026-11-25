use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::kernel::state::AgentState;
use crate::parse::pystr::{char_len, char_slice, is_truthy};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidencePassage {
    pub tool: String,
    pub source: Option<String>,
    pub text: String,
    pub superseded: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroundingDefect {
    ZeroActiveEvidenceHallucination,
    SupersededFactLeak,
    NumericMismatch,
    PolarityMismatch,
    MissingCitations,
}

impl GroundingDefect {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ZeroActiveEvidenceHallucination => "zero_active_evidence_hallucination",
            Self::SupersededFactLeak => "superseded_fact_leak",
            Self::NumericMismatch => "numeric_mismatch",
            Self::PolarityMismatch => "polarity_mismatch",
            Self::MissingCitations => "missing_citations",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct GroundingReport {
    pub defects: Vec<GroundingDefect>,
    pub active_passages: usize,
    pub superseded_passages: usize,
    pub details: Vec<String>,
}

impl GroundingReport {
    pub fn is_clean(&self) -> bool {
        self.defects.is_empty()
    }

    pub fn has(&self, d: GroundingDefect) -> bool {
        self.defects.contains(&d)
    }

    pub fn to_detail(&self) -> Value {
        json!({
            "clean": self.is_clean(),
            "defects": self.defects.iter().map(|d| d.as_str()).collect::<Vec<_>>(),
            "active_passages": self.active_passages,
            "superseded_passages": self.superseded_passages,
            "details": self.details,
        })
    }
}

fn normalized_tool_name(step: &Value) -> Option<&str> {
    let raw = step.get("action").and_then(Value::as_str)?;
    Some(raw.strip_prefix("mcp.").unwrap_or(raw))
}

fn is_evidence_tool(tool: &str) -> bool {
    matches!(
        tool,
        "knowledge_search"
            | "knowledge_graph_search"
            | "knowledge_graph_context"
            | "obsidian_search"
            | "web_search"
            | "web_fetch"
    )
}

fn is_successful_evidence_step(step: &Value) -> Option<&str> {
    if let Some(state) = step.get("state").and_then(Value::as_str) {
        if state != AgentState::Executing.as_str() {
            return None;
        }
    }
    if step.get("error").is_some_and(is_truthy) {
        return None;
    }
    let tool = normalized_tool_name(step)?;
    if !is_evidence_tool(tool) {
        return None;
    }
    step.get("result")?;
    Some(tool)
}

fn first_nonempty_str(obj: &Value, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(s) = obj.get(*key).and_then(Value::as_str) {
            let trimmed = s.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

fn item_superseded(obj: &Value) -> bool {
    obj.get("is_superseded")
        .and_then(Value::as_bool)
        .or_else(|| obj.get("superseded").and_then(Value::as_bool))
        .unwrap_or(false)
}

pub fn extract_evidence_passages(transcript: &[Value]) -> Vec<EvidencePassage> {
    let mut passages = Vec::new();
    for step in transcript {
        let Some(tool) = is_successful_evidence_step(step) else {
            continue;
        };
        let Some(result) = step.get("result") else {
            continue;
        };
        if let Some(text) = result.as_str() {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                passages.push(EvidencePassage {
                    tool: tool.to_string(),
                    source: step
                        .get("args")
                        .and_then(|a| first_nonempty_str(a, &["url", "path", "query"])),
                    text: trimmed.to_string(),
                    superseded: false,
                });
            }
            continue;
        }
        if !result.is_object() {
            continue;
        }

        let mut extracted_from_list = false;
        for key in [
            "results", "items", "hits", "matches", "nodes", "facts", "passages",
        ] {
            let Some(items) = result.get(key).and_then(Value::as_array) else {
                continue;
            };
            for item in items {
                let Some(text) = first_nonempty_str(
                    item,
                    &[
                        "text",
                        "content",
                        "content_excerpt",
                        "preview",
                        "snippet",
                        "summary",
                        "description",
                        "body",
                        "excerpt",
                        "markdown",
                    ],
                ) else {
                    continue;
                };
                let source = first_nonempty_str(
                    item,
                    &[
                        "path",
                        "relative_path",
                        "url",
                        "final_url",
                        "source",
                        "title",
                        "id",
                        "node_id",
                    ],
                );
                passages.push(EvidencePassage {
                    tool: tool.to_string(),
                    source,
                    text,
                    superseded: item_superseded(item),
                });
                extracted_from_list = true;
            }
        }

        if !extracted_from_list {
            if let Some(text) = first_nonempty_str(
                result,
                &[
                    "content",
                    "content_excerpt",
                    "text",
                    "body",
                    "markdown",
                    "html_text",
                    "excerpt",
                    "summary",
                    "context",
                    "snippet",
                ],
            ) {
                let source =
                    first_nonempty_str(result, &["url", "final_url", "path", "source", "title"])
                        .or_else(|| {
                            step.get("args")
                                .and_then(|a| first_nonempty_str(a, &["url", "path"]))
                        });
                passages.push(EvidencePassage {
                    tool: tool.to_string(),
                    source,
                    text,
                    superseded: item_superseded(result),
                });
            }
        }
    }
    passages
}

pub fn transcript_has_evidence(transcript: &[Value]) -> bool {
    transcript
        .iter()
        .any(|step| is_successful_evidence_step(step).is_some())
}

pub fn is_abstention(answer: &str) -> bool {
    let lower = answer.trim().to_lowercase();
    if lower.is_empty() {
        return false;
    }
    let en_markers = [
        "not found in evidence",
        "not found in the evidence",
        "no evidence found",
        "no active evidence",
        "insufficient evidence",
        "cannot be verified from the evidence",
        "not mentioned in the provided",
        "not supported by the evidence",
        "cannot answer from the available evidence",
        "don't have enough evidence",
        "do not have enough evidence",
        "unable to find",
        "no grounded evidence",
    ];
    let ko_markers = [
        "근거를 찾을 수 없",
        "근거가 없",
        "확인할 수 없",
        "제공된 자료에 없",
        "문서에서 찾을 수 없",
        "활성 근거가 없",
        "근거가 부족",
        "찾을 수 없습니다",
        "확인되지 않",
    ];
    en_markers.iter().any(|m| lower.contains(m)) || ko_markers.iter().any(|m| lower.contains(m))
}

fn has_hangul(text: &str) -> bool {
    text.chars()
        .any(|c| ('\u{AC00}'..='\u{D7A3}').contains(&c) || ('\u{3131}'..='\u{318E}').contains(&c))
}

pub fn abstention_message(request: &str) -> String {
    if has_hangul(request) {
        "제공된 활성 근거에서 해당 질문에 대한 확인 가능한 정보를 찾을 수 없습니다.".to_string()
    } else {
        "Not found in the available active evidence; cannot answer reliably without grounded sources.".to_string()
    }
}

fn extract_numbers(text: &str, skip_answer_formatting: bool) -> Vec<String> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if !bytes[i].is_ascii_digit() {
            i += 1;
            continue;
        }
        if i > 0 && bytes[i - 1].is_ascii_alphanumeric() {
            while i < bytes.len() && bytes[i].is_ascii_alphanumeric() {
                i += 1;
            }
            continue;
        }
        let start = i;
        while i < bytes.len()
            && (bytes[i].is_ascii_digit()
                || ((bytes[i] == b',' || bytes[i] == b'.')
                    && i + 1 < bytes.len()
                    && bytes[i + 1].is_ascii_digit()))
        {
            i += 1;
        }
        let end = i;
        if end < bytes.len() && bytes[end].is_ascii_alphabetic() {
            continue;
        }

        if skip_answer_formatting {
            let inside_brackets =
                start > 0 && bytes[start - 1] == b'[' && end < bytes.len() && bytes[end] == b']';
            if inside_brackets {
                continue;
            }
            let at_line_start = text[..start]
                .rsplit('\n')
                .next()
                .is_some_and(|prefix| prefix.trim().is_empty());
            let followed_by_list_sep = end + 1 < bytes.len()
                && (bytes[end] == b'.' || bytes[end] == b')')
                && bytes[end + 1].is_ascii_whitespace();
            if at_line_start && followed_by_list_sep {
                continue;
            }
        }

        let normalized = text[start..end].replace(',', "");
        if !normalized.is_empty() {
            out.push(normalized);
        }
    }
    out
}

fn extract_quoted_tokens(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for quote in ['"', '\'', '`'] {
        let mut rest = text;
        while let Some(start) = rest.find(quote) {
            let after_open = &rest[start + quote.len_utf8()..];
            let Some(end) = after_open.find(quote) else {
                break;
            };
            let inner = after_open[..end].trim();
            if char_len(inner) >= 3 && !inner.contains('\n') {
                out.push(inner.to_string());
            }
            rest = &after_open[end + quote.len_utf8()..];
        }
    }
    out
}

fn is_stopword(word: &str) -> bool {
    matches!(
        word,
        "the"
            | "and"
            | "for"
            | "that"
            | "this"
            | "with"
            | "from"
            | "have"
            | "has"
            | "had"
            | "was"
            | "were"
            | "are"
            | "not"
            | "but"
            | "can"
            | "will"
            | "all"
            | "any"
            | "use"
            | "used"
            | "using"
            | "into"
            | "about"
            | "only"
            | "also"
    )
}

fn content_tokens(text: &str) -> BTreeSet<String> {
    let mut tokens = BTreeSet::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_alphanumeric() || ('\u{AC00}'..='\u{D7A3}').contains(&ch) {
            for lc in ch.to_lowercase() {
                current.push(lc);
            }
        } else if !current.is_empty() {
            let len = char_len(&current);
            let has_ko = has_hangul(&current);
            if ((has_ko && len >= 2) || (!has_ko && len >= 3)) && !is_stopword(&current) {
                tokens.insert(current.clone());
            }
            current.clear();
        }
    }
    if !current.is_empty() {
        let len = char_len(&current);
        let has_ko = has_hangul(&current);
        if ((has_ko && len >= 2) || (!has_ko && len >= 3)) && !is_stopword(&current) {
            tokens.insert(current);
        }
    }
    tokens
}

fn contains_ascii_word(haystack_lower: &str, word: &str) -> bool {
    let hay = haystack_lower.as_bytes();
    let needle = word.as_bytes();
    if needle.is_empty() || needle.len() > hay.len() {
        return false;
    }
    (0..=hay.len() - needle.len()).any(|start| {
        &hay[start..start + needle.len()] == needle
            && (start == 0 || !hay[start - 1].is_ascii_alphanumeric())
            && {
                let after = start + needle.len();
                after == hay.len() || !hay[after].is_ascii_alphanumeric()
            }
    })
}

fn has_negation(text: &str) -> bool {
    let lower = text.to_lowercase();
    let en_words = [
        "not", "no", "never", "cannot", "can't", "won't", "don't", "doesn't", "didn't", "isn't",
        "aren't", "wasn't", "weren't", "disabled",
    ];
    if en_words.iter().any(|w| contains_ascii_word(&lower, w)) {
        return true;
    }
    let ko_markers = ["없", "아니", "않", "불가", "금지", "안 됨", "안됨", "못했"];
    ko_markers.iter().any(|m| lower.contains(m))
}

fn answer_cites_any_source(answer: &str, active: &[&EvidencePassage]) -> bool {
    for idx in 1..=active.len() {
        if answer.contains(&format!("[{idx}]")) {
            return true;
        }
    }
    let lower = answer.to_lowercase();
    for passage in active {
        let Some(src) = passage.source.as_deref() else {
            continue;
        };
        let src_lower = src.trim().to_lowercase();
        if src_lower.is_empty() {
            continue;
        }
        if lower.contains(&src_lower) {
            return true;
        }
        let basename = src_lower
            .trim_end_matches('/')
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or("");
        if basename.len() >= 3 && lower.contains(basename) {
            return true;
        }
    }
    false
}

pub fn verify_grounded_answer(answer: &str, passages: &[EvidencePassage]) -> GroundingReport {
    let active: Vec<&EvidencePassage> = passages.iter().filter(|p| !p.superseded).collect();
    let superseded: Vec<&EvidencePassage> = passages.iter().filter(|p| p.superseded).collect();

    let mut report = GroundingReport {
        defects: Vec::new(),
        active_passages: active.len(),
        superseded_passages: superseded.len(),
        details: Vec::new(),
    };

    let trimmed = answer.trim();
    let abstained = is_abstention(trimmed);

    if active.is_empty() && !trimmed.is_empty() && !abstained {
        report
            .defects
            .push(GroundingDefect::ZeroActiveEvidenceHallucination);
        report
            .details
            .push("zero active evidence passages but answer asserted content".into());
    }

    if !superseded.is_empty() && !trimmed.is_empty() {
        let mut active_nums = BTreeSet::new();
        let mut active_quotes = BTreeSet::new();
        for p in &active {
            for n in extract_numbers(&p.text, false) {
                active_nums.insert(n);
            }
            for q in extract_quoted_tokens(&p.text) {
                active_quotes.insert(q.to_lowercase());
            }
        }
        let answer_nums: BTreeSet<String> = extract_numbers(trimmed, true).into_iter().collect();
        let answer_lower = trimmed.to_lowercase();

        let mut leaked: Option<String> = None;
        for p in &superseded {
            for n in extract_numbers(&p.text, false) {
                if (n.len() >= 2 || n.contains('.'))
                    && !active_nums.contains(&n)
                    && answer_nums.contains(&n)
                {
                    leaked = Some(n);
                    break;
                }
            }
            if leaked.is_some() {
                break;
            }
            for q in extract_quoted_tokens(&p.text) {
                let q_lower = q.to_lowercase();
                if !active_quotes.contains(&q_lower) && answer_lower.contains(&q_lower) {
                    leaked = Some(q);
                    break;
                }
            }
            if leaked.is_some() {
                break;
            }
        }
        if let Some(tok) = leaked {
            report.defects.push(GroundingDefect::SupersededFactLeak);
            report
                .details
                .push(format!("answer contains superseded-only value: {tok}"));
        }
    }

    if active.is_empty() || trimmed.is_empty() || abstained {
        return report;
    }

    let active_nums: BTreeSet<String> = active
        .iter()
        .flat_map(|p| extract_numbers(&p.text, false))
        .collect();
    if !active_nums.is_empty() {
        let answer_nums = extract_numbers(trimmed, true);
        if let Some(missing) = answer_nums.into_iter().find(|n| !active_nums.contains(n)) {
            report.defects.push(GroundingDefect::NumericMismatch);
            report.details.push(format!(
                "answer number '{missing}' not found in active evidence"
            ));
        }
    }

    let passage_profiles: Vec<(BTreeSet<String>, bool)> = active
        .iter()
        .map(|p| (content_tokens(&p.text), has_negation(&p.text)))
        .collect();
    for sentence in trimmed.split(['.', '!', '?', '\n']) {
        let s = sentence.trim();
        if s.is_empty() {
            continue;
        }
        let s_tokens = content_tokens(s);
        if s_tokens.len() < 2 {
            continue;
        }
        let s_neg = has_negation(s);
        let overlapping: Vec<bool> = passage_profiles
            .iter()
            .filter(|(p_tokens, _)| s_tokens.intersection(p_tokens).count() >= 2)
            .map(|(_, p_neg)| *p_neg)
            .collect();
        if overlapping.is_empty() {
            continue;
        }
        let all_same_polarity = overlapping.iter().all(|&p| p == overlapping[0]);
        if all_same_polarity && overlapping[0] != s_neg {
            report.defects.push(GroundingDefect::PolarityMismatch);
            report.details.push(format!(
                "answer polarity ({}) contradicts active evidence ({})",
                if s_neg { "negated" } else { "affirmed" },
                if overlapping[0] {
                    "negated"
                } else {
                    "affirmed"
                }
            ));
            break;
        }
    }

    let sourced_count = active
        .iter()
        .filter(|p| p.source.as_deref().is_some_and(|s| !s.trim().is_empty()))
        .count();
    if sourced_count >= 2 && !answer_cites_any_source(trimmed, &active) {
        report.defects.push(GroundingDefect::MissingCitations);
        report.details.push(format!(
            "{sourced_count} active sourced passages available but answer includes no citation"
        ));
    }

    report
}

pub fn build_evidence_card(passages: &[EvidencePassage], max_chars: usize) -> String {
    let active: Vec<&EvidencePassage> = passages.iter().filter(|p| !p.superseded).collect();
    let superseded: Vec<&EvidencePassage> = passages.iter().filter(|p| p.superseded).collect();

    let mut lines = vec!["[ACTIVE GROUNDED EVIDENCE]".to_string()];
    if active.is_empty() {
        lines.push("(none)".to_string());
    } else {
        for (idx, p) in active.iter().enumerate() {
            let num = idx + 1;
            match p.source.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                Some(src) => lines.push(format!("[{num}] ({src}) {}", p.text.trim())),
                None => lines.push(format!("[{num}] {}", p.text.trim())),
            }
        }
    }

    if !superseded.is_empty() {
        lines.push("[SUPERSEDED - do not use]".to_string());
        for p in superseded {
            match p.source.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
                Some(src) => lines.push(format!("- ({src}) {}", p.text.trim())),
                None => lines.push(format!("- {}", p.text.trim())),
            }
        }
    }

    let full = lines.join("\n");
    if max_chars == 0 || char_len(&full) <= max_chars {
        return full;
    }
    if max_chars <= 3 {
        return char_slice(&full, max_chars).to_string();
    }
    format!("{}...", char_slice(&full, max_chars - 3))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn extracts_active_and_superseded_passages_across_evidence_tools() {
        let transcript = vec![
            json!({
                "state": "EXECUTING",
                "action": "knowledge_search",
                "result": {
                    "results": [
                        {"path": "notes/old.md", "preview": "Port is 4040.", "is_superseded": true},
                        {"path": "notes/new.md", "preview": "Port is 8443.", "is_superseded": false}
                    ]
                }
            }),
            json!({
                "state": "EXECUTING",
                "action": "web_fetch",
                "args": {"url": "https://example.com/spec"},
                "result": {"url": "https://example.com/spec", "content": "Timeout is 30 seconds."}
            }),
            json!({
                "state": "EXECUTING",
                "action": "knowledge_search",
                "error": "index missing",
                "result": {"results": [{"path": "x.md", "preview": "ignored"}]}
            }),
        ];
        assert!(transcript_has_evidence(&transcript));
        let passages = extract_evidence_passages(&transcript);
        assert_eq!(passages.len(), 3);
        assert!(passages[0].superseded);
        assert_eq!(passages[0].source.as_deref(), Some("notes/old.md"));
        assert!(!passages[1].superseded);
        assert_eq!(passages[2].tool, "web_fetch");
        assert_eq!(
            passages[2].source.as_deref(),
            Some("https://example.com/spec")
        );
    }

    #[test]
    fn zero_active_evidence_flags_assertion_but_allows_abstention() {
        let passages = vec![EvidencePassage {
            tool: "knowledge_search".into(),
            source: Some("old.md".into()),
            text: "Legacy port is 4040.".into(),
            superseded: true,
        }];
        let bad = verify_grounded_answer("The port is 8080.", &passages);
        assert!(bad.has(GroundingDefect::ZeroActiveEvidenceHallucination));
        assert!(!bad.is_clean());

        let good_en = verify_grounded_answer("Not found in evidence.", &passages);
        assert!(!good_en.has(GroundingDefect::ZeroActiveEvidenceHallucination));
        assert!(good_en.is_clean());

        let good_ko = verify_grounded_answer("제공된 근거를 찾을 수 없습니다.", &passages);
        assert!(good_ko.is_clean());
    }

    #[test]
    fn detects_superseded_leak_numeric_mismatch_polarity_and_missing_citations() {
        let passages = vec![
            EvidencePassage {
                tool: "knowledge_search".into(),
                source: Some("notes/old.md".into()),
                text: "The old port was 4040 with token \"legacy-tok\".".into(),
                superseded: true,
            },
            EvidencePassage {
                tool: "knowledge_search".into(),
                source: Some("notes/spec.md".into()),
                text: "The service port is 8443 and TLS is enabled.".into(),
                superseded: false,
            },
            EvidencePassage {
                tool: "obsidian_search".into(),
                source: Some("notes/ops.md".into()),
                text: "Max retries is 3.".into(),
                superseded: false,
            },
        ];

        let clean = verify_grounded_answer(
            "The service port is 8443 and TLS is enabled [1], with max retries 3 [2].",
            &passages,
        );
        assert!(clean.is_clean(), "unexpected defects: {:?}", clean.defects);

        let leak = verify_grounded_answer(
            "The service port is 4040 [1] using \"legacy-tok\".",
            &passages,
        );
        assert!(leak.has(GroundingDefect::SupersededFactLeak));
        assert!(leak.has(GroundingDefect::NumericMismatch));

        let num_bad = verify_grounded_answer("The service port is 9999 [1].", &passages);
        assert!(num_bad.has(GroundingDefect::NumericMismatch));

        let pol_bad = verify_grounded_answer(
            "The service port is 8443 and TLS is not enabled [1].",
            &passages,
        );
        assert!(pol_bad.has(GroundingDefect::PolarityMismatch));

        let cite_bad =
            verify_grounded_answer("The service port is 8443 and max retries is 3.", &passages);
        assert!(cite_bad.has(GroundingDefect::MissingCitations));
        assert_eq!(cite_bad.to_detail()["clean"], json!(false));
    }

    #[test]
    fn builds_evidence_card_and_bilingual_abstention() {
        let passages = vec![
            EvidencePassage {
                tool: "knowledge_search".into(),
                source: Some("new.md".into()),
                text: "Port is 8443.".into(),
                superseded: false,
            },
            EvidencePassage {
                tool: "knowledge_search".into(),
                source: Some("old.md".into()),
                text: "Port is 4040.".into(),
                superseded: true,
            },
        ];
        let card = build_evidence_card(&passages, 500);
        assert!(card.starts_with("[ACTIVE GROUNDED EVIDENCE]"));
        assert!(card.contains("[1] (new.md) Port is 8443."));
        assert!(card.contains("do not use"));
        assert!(card.contains("Port is 4040."));

        let capped = build_evidence_card(&passages, 40);
        assert!(char_len(&capped) <= 40);

        assert!(has_hangul(&abstention_message("포트 번호가 뭐야?")));
        assert!(!has_hangul(&abstention_message("What is the port?")));
    }
}
