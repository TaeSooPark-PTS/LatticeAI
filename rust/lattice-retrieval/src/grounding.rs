use std::collections::BTreeSet;

use lattice_core::LocalEmbeddingModel;
use serde::{Deserialize, Serialize};

use crate::bm25::tokenize_bm25_cjk;

pub const DEFAULT_MIN_QUERY_ALIGNMENT: f64 = 0.25;

pub const DEFAULT_MIN_CLAIM_SUPPORT_RATIO: f64 = 0.75;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EvidencePassage {
    pub id: String,
    pub title: String,
    pub text: String,
    pub is_superseded: bool,
    pub confidence: f64,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AbstentionReason {
    NoRetrievedEvidence,
    OnlySupersededEvidence,
    InsufficientQueryAlignment,
    UnsupportedAnswerClaims,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ClaimSupportVerdict {
    pub claim_text: String,
    pub supported: bool,
    pub support_score: f64,
    pub supporting_passage_ids: Vec<String>,
    pub leaked_superseded_ids: Vec<String>,
    pub numeric_mismatch: bool,
    pub polarity_mismatch: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GroundingVerdict {
    pub should_abstain: bool,
    pub abstention_reason: Option<AbstentionReason>,
    pub abstention_message_en: Option<String>,
    pub abstention_message_ko: Option<String>,
    pub query_alignment_score: f64,
    pub claim_support_ratio: f64,
    pub citation_precision: f64,
    pub citation_recall: f64,
    pub superseded_citation_leaks: usize,
    pub claim_verdicts: Vec<ClaimSupportVerdict>,
}

const STOPWORDS: &[&str] = &[
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "why",
    "how",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "to",
    "with",
    "about",
    "tell",
    "me",
    "please",
    "currently",
    "now",
    "무엇",
    "어떻게",
    "언제",
    "어디",
    "누구",
    "왜",
    "알려줘",
    "설명",
    "현재",
    "지금",
    "대한",
    "대해",
];

const NEGATION_MARKERS: &[&str] = &[
    "not",
    "never",
    "no",
    "none",
    "disabled",
    "forbid",
    "forbidden",
    "prohibited",
    "blocked",
    "cannot",
    "않",
    "없",
    "불가",
    "차단",
    "비활성",
    "금지",
    "아님",
    "아니",
];

fn content_tokens(text: &str) -> Vec<String> {
    let stop: BTreeSet<&str> = STOPWORDS.iter().copied().collect();
    tokenize_bm25_cjk(text)
        .into_iter()
        .filter(|t| {
            !t.starts_with("ko_bi:")
                && !t.starts_with("ja_bi:")
                && t.chars().count() >= 2
                && !stop.contains(t.as_str())
        })
        .collect()
}

fn extract_numbers(text: &str) -> BTreeSet<String> {
    let mut nums = BTreeSet::new();
    let mut cur = String::new();
    for ch in text.chars() {
        if ch.is_ascii_digit() || (ch == '.' && !cur.is_empty()) {
            cur.push(ch);
        } else if !cur.is_empty() {
            let trimmed = cur.trim_end_matches('.').to_string();
            if !trimmed.is_empty() {
                nums.insert(trimmed);
            }
            cur.clear();
        }
    }
    if !cur.is_empty() {
        let trimmed = cur.trim_end_matches('.').to_string();
        if !trimmed.is_empty() {
            nums.insert(trimmed);
        }
    }
    nums
}

fn has_negation(text: &str) -> bool {
    let lower = text.to_lowercase();
    for marker in NEGATION_MARKERS {
        if marker.is_ascii() {
            if lower
                .split(|c: char| !c.is_ascii_alphabetic())
                .any(|w| w == *marker)
            {
                return true;
            }
        } else if lower.contains(marker) {
            return true;
        }
    }
    false
}

fn split_clauses_lower(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let mut clauses = Vec::new();
    for seg in lower.split(['.', '!', '?', '。', ';', ':', '\n', ',', '—', '(', ')']) {
        let mut current = vec![seg.to_string()];
        for conj in [" and ", " but ", " while ", " whereas ", "면 ", "지만 "] {
            let mut next = Vec::new();
            for part in current {
                for piece in part.split(conj) {
                    let trimmed = piece.trim();
                    if !trimmed.is_empty() {
                        next.push(trimmed.to_string());
                    }
                }
            }
            current = next;
        }
        clauses.extend(current);
    }
    clauses
}

fn non_negation_tokens(text: &str) -> Vec<String> {
    content_tokens(text)
        .into_iter()
        .filter(|t| !has_negation(t))
        .collect()
}

fn has_polarity_conflict(claim: &str, title: &str, text: &str) -> bool {
    let mut affirmative_tokens = BTreeSet::new();
    let mut negated_tokens = BTreeSet::new();
    for clause in split_clauses_lower(title)
        .into_iter()
        .chain(split_clauses_lower(text))
    {
        let toks = non_negation_tokens(&clause);
        if has_negation(&clause) {
            negated_tokens.extend(toks);
        } else {
            affirmative_tokens.extend(toks);
        }
    }

    for c_clause in split_clauses_lower(claim) {
        let c_toks = non_negation_tokens(&c_clause);
        if c_toks.is_empty() {
            continue;
        }
        let target_pool = if has_negation(&c_clause) {
            &negated_tokens
        } else {
            &affirmative_tokens
        };
        let aligned = c_toks
            .iter()
            .filter(|t| target_pool.contains(t.as_str()))
            .count() as f64
            / c_toks.len() as f64;
        if aligned < 0.5 {
            return true;
        }
    }
    false
}

pub fn split_answer_claims(answer: &str) -> Vec<String> {
    let mut claims = Vec::new();
    for line in answer.lines() {
        let trimmed = line.trim().trim_start_matches(['-', '*', '•']).trim();
        if trimmed.is_empty() {
            continue;
        }
        for sentence in trimmed.split(['.', '!', '?', '。']) {
            let s = sentence.trim();
            if s.chars().count() >= 4 {
                claims.push(s.to_string());
            }
        }
    }
    claims
}

fn build_abstention_messages(reason: AbstentionReason) -> (String, String) {
    match reason {
        AbstentionReason::NoRetrievedEvidence => (
            "I do not know based on the current Brain: no relevant knowledge was found.".to_string(),
            "현재 브레인에 관련 근거 지식이 없어 답변할 수 없습니다.".to_string(),
        ),
        AbstentionReason::OnlySupersededEvidence => (
            "I cannot answer with active knowledge: only superseded historical records matched.".to_string(),
            "일치하는 기록이 모두 이전(대체된) 지식이어서 현재 사실로 단정할 수 없습니다.".to_string(),
        ),
        AbstentionReason::InsufficientQueryAlignment => (
            "I do not have sufficient grounded evidence in the Brain to answer this question.".to_string(),
            "질문에 답하기에 충분한 근거가 브레인에 저장되어 있지 않습니다.".to_string(),
        ),
        AbstentionReason::UnsupportedAnswerClaims => (
            "Abstaining because one or more synthesized claims could not be verified against grounded citations.".to_string(),
            "생성된 주장의 일부가 저장된 출처 근거로 검증되지 않아 답변을 보류합니다.".to_string(),
        ),
    }
}

pub fn verify_grounding_and_abstention(
    query: &str,
    passages: &[EvidencePassage],
    candidate_answer: Option<&str>,
    cited_ids: &[String],
) -> GroundingVerdict {
    if passages.is_empty() {
        let (en, ko) = build_abstention_messages(AbstentionReason::NoRetrievedEvidence);
        return GroundingVerdict {
            should_abstain: true,
            abstention_reason: Some(AbstentionReason::NoRetrievedEvidence),
            abstention_message_en: Some(en),
            abstention_message_ko: Some(ko),
            query_alignment_score: 0.0,
            claim_support_ratio: 0.0,
            citation_precision: 0.0,
            citation_recall: 0.0,
            superseded_citation_leaks: 0,
            claim_verdicts: Vec::new(),
        };
    }

    let active_passages: Vec<&EvidencePassage> =
        passages.iter().filter(|p| !p.is_superseded).collect();
    if active_passages.is_empty() {
        let (en, ko) = build_abstention_messages(AbstentionReason::OnlySupersededEvidence);
        return GroundingVerdict {
            should_abstain: true,
            abstention_reason: Some(AbstentionReason::OnlySupersededEvidence),
            abstention_message_en: Some(en),
            abstention_message_ko: Some(ko),
            query_alignment_score: 0.0,
            claim_support_ratio: 0.0,
            citation_precision: 0.0,
            citation_recall: 0.0,
            superseded_citation_leaks: cited_ids.len(),
            claim_verdicts: Vec::new(),
        };
    }

    let q_tokens = content_tokens(query);
    let active_token_set: BTreeSet<String> = active_passages
        .iter()
        .flat_map(|p| content_tokens(&format!("{} {}", p.title, p.text)))
        .collect();

    let lexical_alignment = if q_tokens.is_empty() {
        0.0
    } else {
        let hits = q_tokens
            .iter()
            .filter(|t| active_token_set.contains(t.as_str()))
            .count();
        hits as f64 / q_tokens.len() as f64
    };

    let embedder = LocalEmbeddingModel::new(384);
    let q_vec = embedder.embed(query);
    let max_vec_sim = active_passages
        .iter()
        .map(|p| {
            let d_vec = embedder.embed(&format!("{} {}", p.title, p.text));
            embedder.similarity(&q_vec, &d_vec).unwrap_or(0.0)
        })
        .fold(0.0_f64, f64::max);

    let query_alignment_score =
        (0.7 * lexical_alignment + 0.3 * max_vec_sim.max(0.0)).clamp(0.0, 1.0);
    if query_alignment_score < DEFAULT_MIN_QUERY_ALIGNMENT && lexical_alignment < 0.25 {
        let (en, ko) = build_abstention_messages(AbstentionReason::InsufficientQueryAlignment);
        return GroundingVerdict {
            should_abstain: true,
            abstention_reason: Some(AbstentionReason::InsufficientQueryAlignment),
            abstention_message_en: Some(en),
            abstention_message_ko: Some(ko),
            query_alignment_score,
            claim_support_ratio: 0.0,
            citation_precision: 0.0,
            citation_recall: 0.0,
            superseded_citation_leaks: 0,
            claim_verdicts: Vec::new(),
        };
    }

    let Some(answer_raw) = candidate_answer.map(str::trim).filter(|s| !s.is_empty()) else {
        return GroundingVerdict {
            should_abstain: false,
            abstention_reason: None,
            abstention_message_en: None,
            abstention_message_ko: None,
            query_alignment_score,
            claim_support_ratio: 1.0,
            citation_precision: 1.0,
            citation_recall: 1.0,
            superseded_citation_leaks: 0,
            claim_verdicts: Vec::new(),
        };
    };

    let claims = split_answer_claims(answer_raw);
    let mut claim_verdicts = Vec::with_capacity(claims.len());
    let mut all_supporting_ids: BTreeSet<String> = BTreeSet::new();

    for claim in claims {
        let c_tokens = content_tokens(&claim);
        let c_nums = extract_numbers(&claim);

        let mut best_score = 0.0_f64;
        let mut supporting_ids = Vec::new();
        let mut leaked_superseded = Vec::new();
        let mut numeric_ok = c_nums.is_empty();
        let mut polarity_mismatch = false;

        let active_numbers: BTreeSet<String> = active_passages
            .iter()
            .flat_map(|p| extract_numbers(&format!("{} {}", p.title, p.text)))
            .collect();
        if !c_nums.is_empty() && c_nums.iter().all(|n| active_numbers.contains(n)) {
            numeric_ok = true;
        }

        for p in passages {
            let p_full = format!("{} {}", p.title, p.text);
            let p_tokens: BTreeSet<String> = content_tokens(&p_full).into_iter().collect();
            if c_tokens.is_empty() {
                continue;
            }
            let overlap = c_tokens
                .iter()
                .filter(|t| p_tokens.contains(t.as_str()))
                .count() as f64
                / c_tokens.len() as f64;
            if overlap >= 0.5 {
                if p.is_superseded {
                    leaked_superseded.push(p.id.clone());
                } else if overlap >= 0.6 && has_polarity_conflict(&claim, &p.title, &p.text) {
                    polarity_mismatch = true;
                } else {
                    supporting_ids.push(p.id.clone());
                    all_supporting_ids.insert(p.id.clone());
                    best_score = best_score.max(overlap);
                }
            }
        }

        if supporting_ids.is_empty() && !c_tokens.is_empty() {
            let union_overlap = c_tokens
                .iter()
                .filter(|t| active_token_set.contains(t.as_str()))
                .count() as f64
                / c_tokens.len() as f64;
            if union_overlap >= 0.6 && numeric_ok && !polarity_mismatch {
                for p in &active_passages {
                    let p_tokens: BTreeSet<String> =
                        content_tokens(&format!("{} {}", p.title, p.text))
                            .into_iter()
                            .collect();
                    if c_tokens.iter().any(|t| p_tokens.contains(t.as_str())) {
                        supporting_ids.push(p.id.clone());
                        all_supporting_ids.insert(p.id.clone());
                    }
                }
                best_score = best_score.max(union_overlap);
            }
        }

        let numeric_mismatch = !numeric_ok;
        let supported = !supporting_ids.is_empty()
            && !numeric_mismatch
            && !polarity_mismatch
            && best_score >= 0.5;
        claim_verdicts.push(ClaimSupportVerdict {
            claim_text: claim,
            supported,
            support_score: best_score,
            supporting_passage_ids: supporting_ids,
            leaked_superseded_ids: leaked_superseded,
            numeric_mismatch,
            polarity_mismatch,
        });
    }

    let claim_support_ratio = if claim_verdicts.is_empty() {
        0.0
    } else {
        claim_verdicts.iter().filter(|v| v.supported).count() as f64 / claim_verdicts.len() as f64
    };

    let superseded_ids: BTreeSet<&str> = passages
        .iter()
        .filter(|p| p.is_superseded)
        .map(|p| p.id.as_str())
        .collect();
    let active_id_set: BTreeSet<&str> = active_passages.iter().map(|p| p.id.as_str()).collect();

    let superseded_citation_leaks = cited_ids
        .iter()
        .filter(|id| superseded_ids.contains(id.as_str()))
        .count();

    let citation_precision = if cited_ids.is_empty() {
        if all_supporting_ids.is_empty() {
            0.0
        } else {
            1.0
        }
    } else {
        let valid_cited = cited_ids
            .iter()
            .filter(|id| {
                active_id_set.contains(id.as_str()) && all_supporting_ids.contains(id.as_str())
            })
            .count();
        valid_cited as f64 / cited_ids.len() as f64
    };

    let citation_recall = if all_supporting_ids.is_empty() || cited_ids.is_empty() {
        0.0
    } else {
        let cited_set: BTreeSet<&str> = cited_ids.iter().map(String::as_str).collect();
        let covered = all_supporting_ids
            .iter()
            .filter(|id| cited_set.contains(id.as_str()))
            .count();
        covered as f64 / all_supporting_ids.len() as f64
    };

    let should_abstain =
        claim_support_ratio < DEFAULT_MIN_CLAIM_SUPPORT_RATIO || superseded_citation_leaks > 0;
    let (reason, msg_en, msg_ko) = if should_abstain {
        let (en, ko) = build_abstention_messages(AbstentionReason::UnsupportedAnswerClaims);
        (
            Some(AbstentionReason::UnsupportedAnswerClaims),
            Some(en),
            Some(ko),
        )
    } else {
        (None, None, None)
    };

    GroundingVerdict {
        should_abstain,
        abstention_reason: reason,
        abstention_message_en: msg_en,
        abstention_message_ko: msg_ko,
        query_alignment_score,
        claim_support_ratio,
        citation_precision,
        citation_recall,
        superseded_citation_leaks,
        claim_verdicts,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abstains_on_ungrounded_questions_and_catches_numeric_or_polarity_hallucinations() {
        let passages = vec![
            EvidencePassage {
                id: "doc:active".to_string(),
                title: "Lattice Host Port Policy".to_string(),
                text: "Lattice Host listens on loopback port 4824 and blocks external cloud egress when disabled.".to_string(),
                is_superseded: false,
                confidence: 0.95,
            },
            EvidencePassage {
                id: "doc:old".to_string(),
                title: "Legacy Server Port".to_string(),
                text: "Legacy Python server listened on port 8000.".to_string(),
                is_superseded: true,
                confidence: 0.5,
            },
        ];

        let ungrounded = verify_grounding_and_abstention(
            "프랑스 파리의 2026년 지하철 요금 인상률은 얼마인가요?",
            &passages,
            None,
            &[],
        );
        assert!(ungrounded.should_abstain);
        assert_eq!(
            ungrounded.abstention_reason,
            Some(AbstentionReason::InsufficientQueryAlignment)
        );

        let valid = verify_grounding_and_abstention(
            "What loopback port does Lattice Host listen on?",
            &passages,
            Some("Lattice Host listens on loopback port 4824."),
            &["doc:active".to_string()],
        );
        assert!(!valid.should_abstain);
        assert_eq!(valid.claim_support_ratio, 1.0);
        assert_eq!(valid.citation_precision, 1.0);
        assert_eq!(valid.superseded_citation_leaks, 0);

        let bad_num = verify_grounding_and_abstention(
            "What loopback port does Lattice Host listen on?",
            &passages,
            Some("Lattice Host listens on loopback port 9999."),
            &["doc:active".to_string()],
        );
        assert!(bad_num.should_abstain);
        assert!(bad_num.claim_verdicts[0].numeric_mismatch);

        let leaked_old = verify_grounding_and_abstention(
            "What loopback port does Lattice Host listen on?",
            &passages,
            Some("Lattice Host listens on loopback port 4824."),
            &["doc:active".to_string(), "doc:old".to_string()],
        );
        assert!(leaked_old.should_abstain);
        assert_eq!(leaked_old.superseded_citation_leaks, 1);

        let inverted_polarity = verify_grounding_and_abstention(
            "What loopback port does Lattice Host listen on?",
            &passages,
            Some("Lattice Host does not listen on loopback port 4824."),
            &["doc:active".to_string()],
        );
        assert!(inverted_polarity.should_abstain);
        assert!(inverted_polarity.claim_verdicts[0].polarity_mismatch);
    }
}
