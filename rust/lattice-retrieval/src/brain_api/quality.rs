//! `lattice_brain/quality.py` — the dormant quality layer these routes wire up.
//!
//! Only the two managers the Brain Intelligence service actually constructs are
//! here: [`MemoryQuality`] (`MemoryQualityManager`) and [`EdgeQuality`]
//! (`GraphEdgeQualityManager`), plus the two public signature helpers the graph
//! layer shares with them so memory-level and graph-level dedupe agree by
//! construction. The BM25 scorer, the fusion, the reranker, the context
//! assembler and the benchmark runner in that file are reachable from no route
//! in this family and are deliberately not carried over.
//!
//! Two details decide answers rather than shapes:
//!
//! * `detect_conflicts` appends its markers in a fixed order — the negation
//!   sweep over every candidate first, then the pairwise sweep in `(i, j)`
//!   order, touching *both* sides. The contradiction surface reads
//!   `candidate.conflicts` positionally to decide which memory is `left`, so a
//!   different append order is a different answer.
//! * `compute_quality_metrics` reads `edge.get("confidence", 0.5)` — the
//!   default applies to an **absent key**, so an edge that carries an explicit
//!   `0.0` confidence is scored at zero, not at the default.

use std::collections::{BTreeSet, HashMap};

use lattice_auth::OrderedMap;
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::pyutil::{self, round_to};

/// `_SIGNATURE_STOPWORDS`, verbatim.
const SIGNATURE_STOPWORDS: [&str; 21] = [
    "a", "an", "and", "for", "i", "is", "it", "mode", "the", "to", "user", "users", "does", "do",
    "not", "like", "likes", "prefer", "prefers", "want", "wants",
];

/// `MemoryQualityManager._NEGATION_PATTERNS`.
const NEGATION_PATTERNS: [&str; 8] = [
    "not",
    "does not",
    "don't",
    "do not",
    "싫어",
    "원하지 않",
    "하지 않",
    "반대",
];

/// `MemoryQualityManager._POSITIVE_PATTERNS`.
const POSITIVE_PATTERNS: [&str; 9] = [
    "prefers", "prefer", "likes", "like", "wants", "want", "좋아", "선호", "원해",
];

/// `detect_temporal_contradictions`'s negation vocabulary.
const TEMPORAL_NEGATIONS: [&str; 7] = [
    "not ",
    "반대",
    "거짓",
    "no longer",
    "never",
    "중단",
    "사용하지",
];

/// `quality.dedupe_key` — sha256 over the normalised head plus a length bucket.
pub fn dedupe_key(content: &str) -> String {
    let normalised = pyutil::head(&pyutil::normalised_words(&content.to_lowercase()), 200);
    let bucket = content.chars().count() / 50;
    let mut hasher = Sha256::new();
    hasher.update(format!("{normalised}|{bucket}").as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    pyutil::head(&hex, 16)
}

/// `quality.content_signature` — stopword-filtered tokens over three characters.
///
/// A `BTreeSet` rather than a hash set: Python's `set` iteration order is
/// seed-dependent, so the co-occurrence index built out of it has no order a
/// port could reproduce. Sorted order is the one deterministic choice, and it
/// changes only the tie order of equally similar pairs — never the set of
/// pairs, whose counts are order-independent.
pub fn content_signature(content: &str) -> BTreeSet<String> {
    pyutil::word_tokens(&content.to_lowercase())
        .into_iter()
        .filter(|token| token.chars().count() > 2 && !SIGNATURE_STOPWORDS.contains(&token.as_str()))
        .collect()
}

/// `_jaccard` — zero for an empty side, never a division by zero.
pub fn jaccard(left: &BTreeSet<String>, right: &BTreeSet<String>) -> f64 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    let union = left.union(right).count();
    if union == 0 {
        return 0.0;
    }
    left.intersection(right).count() as f64 / union as f64
}

/// `MemoryCandidate` — the four fields the conflict scan reads.
#[derive(Debug, Clone)]
pub struct MemoryCandidate {
    /// `m["id"]`, already stringified by the caller.
    pub id: String,
    /// `m["content"]`.
    pub content: String,
    /// The markers `detect_conflicts` appended, in order.
    pub conflicts: Vec<String>,
}

/// `MemoryQualityManager`, as the Brain Intelligence service uses it.
#[derive(Debug, Default, Clone, Copy)]
pub struct MemoryQuality;

impl MemoryQuality {
    /// `extract_candidates` — one candidate per row, in row order.
    pub fn extract_candidates(rows: &[Value]) -> Vec<MemoryCandidate> {
        rows.iter()
            .map(|row| MemoryCandidate {
                id: pyutil::field_text(row, "id"),
                content: pyutil::field_text(row, "content"),
                conflicts: Vec::new(),
            })
            .collect()
    }

    /// `detect_conflicts` — negation sweep, then the pairwise sweep, in order.
    pub fn detect_conflicts(candidates: &mut [MemoryCandidate]) {
        for candidate in candidates.iter_mut() {
            let lowered = candidate.content.to_lowercase();
            if NEGATION_PATTERNS
                .iter()
                .any(|pattern| lowered.contains(pattern))
            {
                candidate
                    .conflicts
                    .push("conflict:possible_negation".into());
            }
        }
        let signatures: Vec<BTreeSet<String>> = candidates
            .iter()
            .map(|candidate| content_signature(&candidate.content))
            .collect();
        let negative: Vec<bool> = candidates
            .iter()
            .map(|candidate| is_negative(&candidate.content))
            .collect();
        let positive: Vec<bool> = candidates
            .iter()
            .map(|candidate| is_positive(&candidate.content))
            .collect();
        let mut markers: Vec<(usize, String)> = Vec::new();
        for left in 0..candidates.len() {
            for right in left + 1..candidates.len() {
                if signatures[left].is_empty()
                    || signatures[left].intersection(&signatures[right]).count() < 2
                {
                    continue;
                }
                if negative[left] == negative[right] {
                    continue;
                }
                if !(positive[left] || positive[right]) {
                    continue;
                }
                markers.push((
                    left,
                    format!("conflict:contradicts:{}", candidates[right].id),
                ));
                markers.push((
                    right,
                    format!("conflict:contradicts:{}", candidates[left].id),
                ));
            }
        }
        for (at, marker) in markers {
            candidates[at].conflicts.push(marker);
        }
    }

    /// `dedupe` — the first candidate per signature survives.
    pub fn dedupe(candidates: &[MemoryCandidate]) -> Vec<String> {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut kept = Vec::new();
        for candidate in candidates {
            let key = dedupe_key(&candidate.content);
            if seen.insert(key) {
                kept.push(candidate.id.clone());
            }
        }
        kept
    }

    /// `detect_temporal_contradictions` — positives then negatives, flagged.
    ///
    /// Fires only when both groups exist *and* the rows carry more than one
    /// distinct timestamp; a single-instant batch cannot contradict itself in
    /// time.
    pub fn detect_temporal_contradictions(rows: &[Value]) -> Vec<Value> {
        let mut positives: Vec<&Value> = Vec::new();
        let mut negatives: Vec<&Value> = Vec::new();
        for row in rows {
            let content = pyutil::field_text(row, "content").to_lowercase();
            if TEMPORAL_NEGATIONS
                .iter()
                .any(|token| content.contains(token))
            {
                negatives.push(row);
            } else {
                positives.push(row);
            }
        }
        let mut stamps: BTreeSet<String> = BTreeSet::new();
        for row in rows {
            stamps.insert(stamp_key(row));
        }
        let mut flagged = Vec::new();
        if negatives.is_empty() || positives.is_empty() || stamps.len() <= 1 {
            return flagged;
        }
        for row in positives.into_iter().chain(negatives) {
            let mut copy = row.clone();
            if let Some(object) = copy.as_object_mut() {
                object.insert(
                    "proactive_flag".to_string(),
                    Value::String("contradiction:temporal_negation".to_string()),
                );
            }
            flagged.push(copy);
        }
        flagged
    }
}

/// `m.get("timestamp") or m.get("created_at") or 0`, as a set member.
fn stamp_key(row: &Value) -> String {
    for key in ["timestamp", "created_at"] {
        if let Some(value) = row.get(key) {
            if pyutil::truthy(value) {
                return format!("{}|{}", type_tag(value), pyutil::py_str(value));
            }
        }
    }
    "n|0".to_string()
}

fn type_tag(value: &Value) -> &'static str {
    match value {
        Value::Number(_) | Value::Bool(_) => "n",
        Value::String(_) => "s",
        _ => "o",
    }
}

fn is_negative(content: &str) -> bool {
    let lowered = content.to_lowercase();
    NEGATION_PATTERNS
        .iter()
        .any(|pattern| lowered.contains(pattern))
}

fn is_positive(content: &str) -> bool {
    let lowered = content.to_lowercase();
    POSITIVE_PATTERNS
        .iter()
        .any(|pattern| lowered.contains(pattern))
}

/// `GraphEdgeQualityManager`, on the two methods this family calls.
#[derive(Debug, Default, Clone, Copy)]
pub struct EdgeQuality;

impl EdgeQuality {
    /// `detect_duplicate_edges` — the ids of every repeat of a
    /// `(source, target, type)` triple after the first.
    pub fn detect_duplicate_edges(edges: &[Value]) -> Vec<String> {
        let mut seen: HashMap<String, ()> = HashMap::new();
        let mut duplicates = Vec::new();
        for edge in edges {
            let key = serde_json::to_string(&serde_json::json!([
                edge.get("source"),
                edge.get("target"),
                edge.get("type"),
            ]))
            .unwrap_or_default();
            if seen.insert(key, ()).is_some() {
                duplicates.push(pyutil::field_text(edge, "id"));
            }
        }
        duplicates
    }

    /// `compute_quality_metrics` — averages plus the duplicate rate.
    pub fn compute_quality_metrics(edges: &[Value]) -> OrderedMap {
        let mut out = OrderedMap::new();
        if edges.is_empty() {
            out.insert("avg_conf", Value::from(0.0));
            out.insert("avg_evidence", Value::from(0.0));
            out.insert("dup_rate", Value::from(0.0));
            return out;
        }
        let count = edges.len() as f64;
        let confidence: f64 = edges
            .iter()
            .map(|edge| match edge.get("confidence") {
                None => 0.5,
                Some(Value::Number(number)) => number.as_f64().unwrap_or(0.0),
                Some(Value::Bool(flag)) => f64::from(*flag),
                Some(_) => 0.0,
            })
            .sum();
        let evidence: f64 = edges
            .iter()
            .map(|edge| match edge.get("evidence") {
                Some(Value::Array(items)) => items.len() as f64,
                Some(Value::String(text)) => text.chars().count() as f64,
                Some(Value::Object(map)) => map.len() as f64,
                _ => 0.0,
            })
            .sum();
        let duplicates = Self::detect_duplicate_edges(edges).len() as f64;
        out.insert("avg_conf", Value::from(round_to(confidence / count, 3)));
        out.insert("avg_evidence", Value::from(round_to(evidence / count, 2)));
        out.insert("dup_rate", Value::from(round_to(duplicates / count, 3)));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, content: &str) -> Value {
        serde_json::json!({"id": id, "content": content, "score": 0.6, "source": "workspace"})
    }

    #[test]
    fn the_dedupe_key_collapses_whitespace_and_buckets_length() {
        assert_eq!(dedupe_key("Alpha  fusion"), dedupe_key("alpha fusion"));
        assert_ne!(dedupe_key("alpha fusion"), dedupe_key("beta fusion"));
        assert_eq!(dedupe_key("").len(), 16);
        // The length bucket is coarse: 50 characters apart is a different key.
        assert_ne!(dedupe_key("a"), dedupe_key(&"a".repeat(60)));
    }

    #[test]
    fn the_signature_drops_stopwords_and_short_tokens() {
        let signature = content_signature("The user prefers Light Mode 한글");
        assert_eq!(
            signature.iter().cloned().collect::<Vec<_>>(),
            vec!["light".to_string()]
        );
        assert!(jaccard(&signature, &BTreeSet::new()) == 0.0);
        assert_eq!(jaccard(&signature, &signature), 1.0);
    }

    #[test]
    fn a_preference_and_its_negation_mark_each_other_in_order() {
        let rows = [
            row("m1", "user prefers light mode always"),
            row("m2", "user does not like light mode always"),
        ];
        let mut candidates = MemoryQuality::extract_candidates(&rows);
        MemoryQuality::detect_conflicts(&mut candidates);
        assert_eq!(
            candidates[0].conflicts,
            vec!["conflict:contradicts:m2".to_string()]
        );
        assert_eq!(
            candidates[1].conflicts,
            vec![
                "conflict:possible_negation".to_string(),
                "conflict:contradicts:m1".to_string()
            ],
            "the negation sweep runs over every candidate before the pairwise one"
        );
    }

    #[test]
    fn agreeing_memories_raise_no_pair() {
        let rows = [
            row("m1", "user prefers light mode always"),
            row("m2", "user prefers light mode too"),
        ];
        let mut candidates = MemoryQuality::extract_candidates(&rows);
        MemoryQuality::detect_conflicts(&mut candidates);
        assert!(candidates.iter().all(|c| c.conflicts.is_empty()));
        assert_eq!(MemoryQuality::dedupe(&candidates).len(), 2);
        let same = [row("m1", "identical"), row("m2", "identical")];
        let candidates = MemoryQuality::extract_candidates(&same);
        assert_eq!(MemoryQuality::dedupe(&candidates), vec!["m1".to_string()]);
    }

    #[test]
    fn temporal_flagging_needs_both_polarities_and_two_instants() {
        let rows = [
            serde_json::json!({"id": "a", "content": "we use rust", "timestamp": "t1"}),
            serde_json::json!({"id": "b", "content": "we no longer use rust", "timestamp": "t2"}),
        ];
        let flagged = MemoryQuality::detect_temporal_contradictions(&rows);
        assert_eq!(flagged.len(), 2);
        assert_eq!(flagged[0]["id"], "a", "positives come first");
        assert_eq!(
            flagged[0]["proactive_flag"],
            "contradiction:temporal_negation"
        );
        let one_instant = [
            serde_json::json!({"id": "a", "content": "we use rust", "timestamp": "t1"}),
            serde_json::json!({"id": "b", "content": "we no longer use rust", "timestamp": "t1"}),
        ];
        assert!(MemoryQuality::detect_temporal_contradictions(&one_instant).is_empty());
        let all_positive = [serde_json::json!({"id": "a", "content": "ok", "timestamp": 1})];
        assert!(MemoryQuality::detect_temporal_contradictions(&all_positive).is_empty());
    }

    #[test]
    fn edge_metrics_default_an_absent_confidence_but_not_a_zero_one() {
        let edges = vec![
            serde_json::json!({"id": "e1", "source": "a", "target": "b", "type": "RELATED_TO"}),
            serde_json::json!({"id": "e2", "source": "a", "target": "b", "type": "RELATED_TO"}),
        ];
        let metrics = EdgeQuality::compute_quality_metrics(&edges);
        assert_eq!(metrics.get("avg_conf"), Some(&Value::from(0.5)));
        assert_eq!(metrics.get("avg_evidence"), Some(&Value::from(0.0)));
        assert_eq!(metrics.get("dup_rate"), Some(&Value::from(0.5)));
        assert_eq!(
            EdgeQuality::detect_duplicate_edges(&edges),
            vec!["e2".to_string()]
        );
        let explicit = vec![serde_json::json!({
            "id": "e1", "source": "a", "target": "b", "type": "X",
            "confidence": 0.0, "evidence": ["one", "two"],
        })];
        let metrics = EdgeQuality::compute_quality_metrics(&explicit);
        assert_eq!(metrics.get("avg_conf"), Some(&Value::from(0.0)));
        assert_eq!(metrics.get("avg_evidence"), Some(&Value::from(2.0)));
        let empty = EdgeQuality::compute_quality_metrics(&[]);
        assert_eq!(empty.get("avg_conf"), Some(&Value::from(0.0)));
        assert!(EdgeQuality::detect_duplicate_edges(&[]).is_empty());
    }
}
