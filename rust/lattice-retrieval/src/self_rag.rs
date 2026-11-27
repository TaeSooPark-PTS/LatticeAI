use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex;
use lattice_core::graph_write::chunk_parents::ChunkParentRow;
use serde_json::{json, Value};
use std::sync::OnceLock;

use crate::context_parents::parent_id_of;
use crate::grounding::{
    build_abstention_messages, content_tokens, verify_grounding_and_abstention, AbstentionReason,
    EvidencePassage, GroundingVerdict,
};
use crate::query_plan::content_terms;

pub const SELF_RAG_ENV: &str = "LATTICEAI_SELF_RAG";
pub const MIN_SEMANTIC_SUPPORT: f64 = 0.6;
pub const MIN_TERM_COVERAGE: f64 = 0.5;

const REFERENCE_WORDS: &[&str] = &[
    "note",
    "notes",
    "doc",
    "docs",
    "document",
    "documents",
    "file",
    "files",
    "record",
    "records",
    "brain",
    "knowledge",
    "base",
    "graph",
    "wiki",
    "journal",
    "meeting",
    "minutes",
    "saved",
    "wrote",
    "written",
    "decide",
    "decided",
    "record",
    "recorded",
    "documented",
    "according",
    "summarize",
    "summary",
    "find",
    "search",
    "say",
    "says",
    "said",
    "내",
    "나의",
    "우리",
    "저의",
    "제",
    "내가",
    "우리가",
    "노트",
    "메모",
    "문서",
    "파일",
    "기록",
    "자료",
    "회의록",
    "브레인",
    "지식",
    "일지",
    "위키",
    "노트에",
    "노트에서",
    "메모에서",
    "문서에서",
    "문서에",
    "기록에",
    "기록에서",
    "자료에서",
    "브레인에",
    "브레인에서",
    "저장한",
    "저장해둔",
    "기록한",
    "정리한",
    "적어둔",
    "써둔",
    "작성한",
    "요약",
    "요약해",
    "요약해줘",
    "찾아",
    "찾아줘",
    "검색해줘",
    "따르면",
    "나와",
    "나와있어",
    "적혀",
    "적혀있어",
];

struct Cues {
    reference: Regex,
    excluded: Regex,
}

fn cues() -> &'static Cues {
    static CUES: OnceLock<Cues> = OnceLock::new();
    CUES.get_or_init(|| Cues {
        reference: crate::build_pattern(
            r"(?i)\b(?:my|our)\s+(?:\w+\s+){0,2}?(?:notes?|docs?|documents?|files?|records?|meeting\s+(?:notes|minutes)|brain|knowledge\s*(?:base|graph)|wiki|journal)\b|\b(?:in|from|according\s+to|search)\s+(?:the|my|our)\s+(?:brain|notes|knowledge\s*base)\b|\bwhat\s+did\s+(?:i|we)\s+(?:decide|write|note|record|save|document)\b|(?:^|\s)(?:내|나의|우리|저의|제)\s*(?:\S+\s+){0,2}?(?:노트|메모|문서|파일|기록|자료|회의록|브레인|지식\s*그래프|일지|위키)|브레인(?:에서|에\s)|(?:내가|우리가)\s.{0,30}?(?:적어\s*[둔놓]|써\s*[둔놓]|기록(?:한|해)|저장(?:한|해)|정리(?:한|해\s*[둔놓])|결정(?:한|했))",
        ),
        excluded: crate::build_pattern(
            r"(?i)\b(?:write|create|draft|add|save|delete|remove|move|rename|organi[sz]e|how\s+(?:do|can|should|to)|just\s+(?:said|told|asked)|earlier|above)\b|작성해|만들어|추가해|저장해\s*줘|삭제|옮겨|바꿔|방법|어떻게\s*(?:해|하)|방금|아까|위에서",
        ),
    })
}

pub fn enabled() -> bool {
    std::env::var(SELF_RAG_ENV).map_or(true, |raw| {
        !matches!(
            raw.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        )
    })
}

pub fn requires_grounding(message: &str) -> bool {
    let cues = cues();
    cues.reference.is_match(message).unwrap_or(false)
        && !cues.excluded.is_match(message).unwrap_or(false)
}

fn topic_of(query: &str) -> Vec<String> {
    content_terms(query)
        .into_iter()
        .filter(|term| !REFERENCE_WORDS.contains(&term.to_lowercase().as_str()))
        .collect()
}

fn status_of(item: &Value) -> &str {
    item.get("temporal")
        .and_then(|temporal| temporal.get("status"))
        .or_else(|| item.get("status"))
        .and_then(Value::as_str)
        .unwrap_or("active")
}

fn passage_of(
    item: &Value,
    parents: &BTreeMap<String, ChunkParentRow>,
    historical: bool,
) -> EvidencePassage {
    let text = |key: &str| item.get(key).and_then(Value::as_str).unwrap_or_default();
    let body = parent_id_of(item)
        .and_then(|id| parents.get(id))
        .map(|row| row.text.clone())
        .unwrap_or_else(|| format!("{} {}", text("summary"), text("snippet")));
    EvidencePassage {
        id: text("id").to_string(),
        title: text("title").to_string(),
        text: body,
        is_superseded: !historical && status_of(item) == "superseded",
        confidence: item.get("score").and_then(Value::as_f64).unwrap_or(0.0),
    }
}

const LABEL_TYPES: [&str; 6] = ["Concept", "Feature", "Topic", "Code", "Error", "Person"];
const NON_EVIDENCE_PREFIXES: [&str; 4] = ["airesponse:", "cloud_turn:", "conversation:", "source:"];
const MESSAGE_PREFIX: &str = "message:";

pub fn is_evidence(item: &Value) -> bool {
    let text = |key: &str| {
        item.get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default()
    };
    let meta = |key: &str| {
        item.get("metadata")
            .and_then(|metadata| metadata.get(key))
            .and_then(Value::as_str)
            .unwrap_or_default()
    };
    let kind = text("type");
    let owner = if kind == "Chunk" {
        meta("source_node")
    } else {
        text("id")
    };
    if kind == "Conversation"
        || kind == "Source"
        || kind == "AIResponse"
        || NON_EVIDENCE_PREFIXES
            .iter()
            .any(|prefix| owner.starts_with(prefix))
        || meta("role") == "assistant"
    {
        return false;
    }
    if LABEL_TYPES.contains(&kind) {
        return !text("summary").is_empty() && text("summary") != text("title");
    }
    if kind == "Message" || owner.starts_with(MESSAGE_PREFIX) {
        let said = text("summary");
        return !said.ends_with(['?', '？']) && !requires_grounding(said);
    }
    true
}

#[derive(Debug, Clone, PartialEq)]
pub struct Sufficiency {
    pub sufficient: bool,
    pub reason: Option<AbstentionReason>,
    pub message_en: Option<String>,
    pub message_ko: Option<String>,
    pub term_coverage: f64,
    pub passages: usize,
}

impl Sufficiency {
    pub fn as_json(&self) -> Value {
        json!({
            "sufficient": self.sufficient,
            "reason": self.reason,
            "term_coverage": lattice_core::pytext::round4(self.term_coverage),
            "passages": self.passages,
        })
    }

    pub fn message(&self, language: &str) -> String {
        let (message, hint) = if language == "ko" {
            (
                self.message_ko.as_deref(),
                "관련 문서를 추가하거나 동기화한 뒤 다시 물어보시거나, 저장된 기록과 무관한 일반 질문으로 바꿔 주세요.",
            )
        } else {
            (
                self.message_en.as_deref(),
                "Add or sync the source and ask again, or ask a general question that does not depend on your saved records.",
            )
        };
        format!("{} {hint}", message.unwrap_or_default())
            .trim()
            .to_string()
    }

    fn verdict(
        sufficient: bool,
        verdict: GroundingVerdict,
        coverage: f64,
        passages: usize,
    ) -> Self {
        Self {
            sufficient,
            reason: if sufficient {
                None
            } else {
                verdict.abstention_reason
            },
            message_en: verdict.abstention_message_en.filter(|_| !sufficient),
            message_ko: verdict.abstention_message_ko.filter(|_| !sufficient),
            term_coverage: coverage,
            passages,
        }
    }
}

pub fn assess(
    query: &str,
    matches: &[Value],
    parents: &BTreeMap<String, ChunkParentRow>,
    historical: bool,
    semantic: bool,
) -> Sufficiency {
    let passages: Vec<EvidencePassage> = matches
        .iter()
        .map(|item| passage_of(item, parents, historical))
        .collect();
    let topic = topic_of(query);
    let topic_tokens: BTreeSet<String> = content_tokens(&topic.join(" ")).into_iter().collect();
    let evidence: BTreeSet<String> = passages
        .iter()
        .filter(|passage| !passage.is_superseded)
        .flat_map(|passage| content_tokens(&format!("{} {}", passage.title, passage.text)))
        .collect();
    let coverage = if topic_tokens.is_empty() {
        1.0
    } else {
        topic_tokens
            .iter()
            .filter(|token| evidence.contains(*token))
            .count() as f64
            / topic_tokens.len() as f64
    };
    let mut verdict = verify_grounding_and_abstention(&topic.join(" "), &passages, None, &[]);
    let semantic_support = semantic
        && matches.iter().any(|item| {
            item.get("source_scores")
                .and_then(|scores| scores.get("vector"))
                .and_then(Value::as_f64)
                .is_some_and(|score| score >= MIN_SEMANTIC_SUPPORT)
        });
    let supported = coverage >= MIN_TERM_COVERAGE || semantic_support;
    let sufficient = match verdict.abstention_reason {
        None | Some(AbstentionReason::InsufficientQueryAlignment) => supported,
        Some(_) => false,
    };
    if !sufficient && verdict.abstention_reason.is_none() {
        let (en, ko) = build_abstention_messages(AbstentionReason::InsufficientQueryAlignment);
        verdict.abstention_reason = Some(AbstentionReason::InsufficientQueryAlignment);
        verdict.abstention_message_en = Some(en);
        verdict.abstention_message_ko = Some(ko);
    }
    Sufficiency::verdict(sufficient, verdict, coverage, passages.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_questions_about_saved_records_require_grounding() {
        for message in [
            "내 노트에서 피닉스 포트 알려줘",
            "우리 회의록에 예산이 얼마로 나와 있어?",
            "내가 저장해 둔 배포 절차 요약해줘",
            "What do my notes say about the Phoenix port?",
            "What did we decide about pricing?",
            "According to the knowledge base, who owns billing?",
        ] {
            assert!(requires_grounding(message), "{message}");
        }
        for message in [
            "안녕",
            "고마워",
            "What is a knowledge graph?",
            "지식 그래프가 뭐야?",
            "파이썬 리스트 정렬 방법 알려줘",
            "How do I organize my notes?",
            "Write a poem about my files",
            "내 문서 폴더를 정리하는 방법",
            "brainstorm names for a cat",
            "What did I just say above in my notes?",
        ] {
            assert!(!requires_grounding(message), "{message}");
        }
    }

    #[test]
    fn evidence_that_covers_the_topic_is_sufficient_and_unrelated_is_not() {
        let matches = vec![json!({
            "id": "n1",
            "title": "Phoenix runbook",
            "summary": "피닉스 서비스 포트는 4824 이다",
        })];
        let ok = assess(
            "내 노트에서 피닉스 포트는 뭐야",
            &matches,
            &BTreeMap::new(),
            false,
            false,
        );
        assert!(ok.sufficient, "{ok:?}");
        let unrelated = assess(
            "내 노트에서 화성 이주 예산 알려줘",
            &matches,
            &BTreeMap::new(),
            false,
            false,
        );
        assert!(!unrelated.sufficient);
        assert_eq!(
            unrelated.reason,
            Some(AbstentionReason::InsufficientQueryAlignment)
        );
        assert!(unrelated.message("ko").contains("근거"));
        assert!(unrelated.message("en").contains("Brain"));
        let empty = assess("my notes", &[], &BTreeMap::new(), false, false);
        assert_eq!(empty.reason, Some(AbstentionReason::NoRetrievedEvidence));
        let listing = assess(
            "summarize my notes",
            &matches,
            &BTreeMap::new(),
            false,
            false,
        );
        assert!(listing.sufficient, "{listing:?}");
    }

    #[test]
    fn questions_replies_and_bare_labels_are_not_evidence() {
        for item in [
            json!({"id": "conversation:c", "type": "Conversation"}),
            json!({"id": "source:1", "type": "Source", "summary": "quota"}),
            json!({"id": "conversation:d", "type": "Chat", "summary": "quota"}),
            json!({"id": "cloud_turn:1", "type": "Chat", "summary": "quota is 40"}),
            json!({"id": "person:a", "type": "Person", "title": "a"}),
            json!({"id": "airesponse:1", "type": "AIResponse", "summary": "the quota is 40"}),
            json!({"id": "chunk:1", "type": "Chunk", "summary": "the quota is 40",
                   "metadata": {"source_node": "airesponse:1"}}),
            json!({"id": "message:1", "type": "Message",
                   "summary": "What do my notes say about the quota?"}),
            json!({"id": "chunk:2", "type": "Chunk", "summary": "내 노트에서 쿼터 알려줘",
                   "metadata": {"source_node": "message:1"}}),
            json!({"id": "concept:q", "type": "Concept", "title": "quota", "summary": ""}),
        ] {
            assert!(!is_evidence(&item), "{item}");
        }
        for item in [
            json!({"id": "message:2", "type": "Message", "summary": "The quota is forty crates"}),
            json!({"id": "doc:1", "type": "Document", "summary": "Why quotas matter?"}),
            json!({"id": "chunk:3", "type": "Chunk", "summary": "quota is forty",
                   "metadata": {"source_node": "doc:1"}}),
            json!({"id": "topic:q", "type": "Topic", "title": "quota", "summary": "forty crates"}),
        ] {
            assert!(is_evidence(&item), "{item}");
        }
    }

    #[test]
    fn entity_only_overlap_is_not_sufficient_evidence() {
        let matches = vec![
            json!({
                "id": "doc:atlas:current",
                "title": "Atlas (프로젝트 아틀라스) 2026 Operations Spec",
                "summary": "Atlas (프로젝트 아틀라스) runs on SQLite WAL inside Rust Core. The project owner is Lee Jiwon and the active service port is 4825.",
            }),
            json!({
                "id": "doc:atlas:arch",
                "title": "Atlas Architecture & Dependencies",
                "summary": "Rust Core depends on SQLite WAL for Atlas. Lee Jiwon reviews all schema migrations.",
            }),
        ];
        for query in [
            "What do my notes say about the rack number of Atlas's backup generator?",
            "What do my notes say about Atlas's SSH passphrase?",
            "내 노트에서 아틀라스 2029년 주가 전망 알려줘",
        ] {
            let verdict = assess(query, &matches, &BTreeMap::new(), false, false);
            assert!(!verdict.sufficient, "{query}: {verdict:?}");
            assert_eq!(
                verdict.reason,
                Some(AbstentionReason::InsufficientQueryAlignment)
            );
            assert!(verdict.message("en").contains("Brain"));
        }
        let answered = assess(
            "What do my notes say about Atlas's service port?",
            &matches,
            &BTreeMap::new(),
            false,
            false,
        );
        assert!(answered.sufficient, "{answered:?}");
        let owner = assess(
            "Atlas's owner according to my notes",
            &matches,
            &BTreeMap::new(),
            false,
            false,
        );
        assert!(owner.sufficient, "{owner:?}");
        let mut semantic = matches[0].clone();
        semantic["source_scores"] = json!({"vector": 0.9});
        let rescued = assess(
            "What do my notes say about Atlas's SSH passphrase?",
            &[semantic],
            &BTreeMap::new(),
            false,
            true,
        );
        assert!(rescued.sufficient, "{rescued:?}");
    }

    #[test]
    fn superseded_only_evidence_is_insufficient_unless_history_was_asked_for() {
        let matches = vec![json!({
            "id": "old",
            "title": "Okapi policy",
            "summary": "okapi refund window",
            "temporal": {"status": "superseded"},
        })];
        let current = assess("okapi refund", &matches, &BTreeMap::new(), false, false);
        assert_eq!(
            current.reason,
            Some(AbstentionReason::OnlySupersededEvidence)
        );
        assert!(assess("okapi refund", &matches, &BTreeMap::new(), true, false).sufficient);
    }
}
