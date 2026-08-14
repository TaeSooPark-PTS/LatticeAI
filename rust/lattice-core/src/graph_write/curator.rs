//! The curator's *decision* half — the part that is pure.
//!
//! Port of the three functions `curate_noise` calls out to in
//! `lattice_brain/graph/curator.py`: the relation-verb dictionary, the plan
//! that renames free-string verbs onto it, and the document-frequency rule that
//! decides which heuristic concept nodes are graph noise.
//!
//! These live here rather than crossing the worker seam because they are
//! arithmetic over rows this engine already has in hand — no model, no parser,
//! no corpus beyond the `edges`/`nodes` tables. The curator's *other* half
//! (topic extraction, clustering, promotion scoring in
//! `auto_build_graph_overlay`) is genuinely NLP and stays in the worker; it
//! reaches [`super::GraphWriter::curate`] as
//! [`super::types::CuratorOverlay`].

use serde_json::{Map, Value};

/// `curator.RELATION_VERB_GROUPS` — canonical label → its aliases.
const RELATION_VERB_GROUPS: &[(&str, &[&str])] = &[
    (
        "created",
        &[
            "creates",
            "create",
            "만들다",
            "만든",
            "만들었다",
            "만듦",
            "만들어냄",
            "생성함",
            "생성",
            "생성했다",
            "작성함",
            "작성",
            "작성했다",
        ],
    ),
    (
        "mentions",
        &["mention", "언급함", "언급", "언급했다", "언급됨"],
    ),
    (
        "contains",
        &["contain", "포함함", "포함", "포함했다", "포함됨"],
    ),
    (
        "uses",
        &[
            "use",
            "used",
            "사용함",
            "사용",
            "사용했다",
            "이용함",
            "이용",
        ],
    ),
    (
        "related_to",
        &[
            "related",
            "relates_to",
            "관련",
            "관련됨",
            "관련있음",
            "연관됨",
            "연관",
        ],
    ),
    (
        "fixed",
        &[
            "fixes",
            "fix",
            "수정함",
            "수정",
            "수정했다",
            "고침",
            "고쳤다",
        ],
    ),
    (
        "decided",
        &["decides", "decide", "결정함", "결정", "결정했다"],
    ),
    (
        "uploaded",
        &["uploads", "upload", "업로드함", "업로드", "올림", "올렸다"],
    ),
];

/// `curator._JOSA_SUFFIXES`, already in `sorted(key=len, reverse=True)` order.
///
/// Python's sort is stable, so suffixes of equal length keep their literal
/// order — which is why this is written out rather than sorted at runtime.
const JOSA_SUFFIXES: [&str; 41] = [
    // three syllables
    "으로는",
    "에서는",
    "에서의",
    "에게서",
    "이라는",
    "이라고",
    // two syllables, in the literal's own order
    "라는",
    "라고",
    "으로",
    "에서",
    "에게",
    "한테",
    "까지",
    "부터",
    "보다",
    "처럼",
    "마다",
    "조차",
    "밖에",
    "라도",
    "이나",
    "에는",
    "에도",
    "께서",
    "이란",
    // one syllable
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "에",
    "의",
    "도",
    "만",
    "로",
    "나",
    "께",
    "란",
];

/// `curator.build_relation_verb_index` — `{alias → canonical}`, canonicals map
/// to themselves.
pub fn build_relation_verb_index() -> Vec<(String, String)> {
    let mut index: Vec<(String, String)> = Vec::new();
    for (canonical, aliases) in RELATION_VERB_GROUPS {
        let canon = canonical.trim().to_lowercase();
        upsert(&mut index, canon.clone(), canon.clone());
        for alias in *aliases {
            upsert(&mut index, alias.trim().to_lowercase(), canon.clone());
        }
    }
    index
}

fn upsert(index: &mut Vec<(String, String)>, key: String, value: String) {
    match index.iter_mut().find(|(existing, _)| *existing == key) {
        Some(slot) => slot.1 = value,
        None => index.push((key, value)),
    }
}

fn lookup<'a>(index: &'a [(String, String)], key: &str) -> Option<&'a str> {
    index
        .iter()
        .find(|(existing, _)| existing == key)
        .map(|(_, value)| value.as_str())
}

/// `curator._strip_josa` — drop a trailing Korean particle, if any.
fn strip_josa(token: &str) -> String {
    if !token
        .chars()
        .any(|c| ('\u{AC00}'..='\u{D7A3}').contains(&c))
    {
        return token.to_string();
    }
    let length = token.chars().count();
    for suffix in JOSA_SUFFIXES {
        let suffix_length = suffix.chars().count();
        if token.ends_with(suffix) && length >= suffix_length + 2 {
            return token.chars().take(length - suffix_length).collect();
        }
    }
    token.to_string()
}

/// `curator.normalize_relation_verb` — identity for anything unknown.
pub fn normalize_relation_verb(verb: &str, index: &[(String, String)]) -> String {
    let raw = verb.trim();
    if raw.is_empty() {
        return raw.to_string();
    }
    let low = raw.to_lowercase();
    if let Some(canonical) = lookup(index, &low) {
        return canonical.to_string();
    }
    let stripped = strip_josa(&low);
    if let Some(canonical) = lookup(index, &stripped) {
        return canonical.to_string();
    }
    raw.to_string()
}

/// `curator._V4_ENUM_LABEL_RE` — `^[A-Z][A-Z0-9_]*$`.
///
/// The v4 write door mints SCREAMING_SNAKE_CASE enum labels; those are schema
/// taxonomy and must never be rewritten by a verb dictionary aimed at pre-v4
/// free strings.
fn is_v4_enum_label(label: &str) -> bool {
    let mut chars = label.chars();
    match chars.next() {
        Some(first) if first.is_ascii_uppercase() => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
}

/// `curator.plan_relation_normalization` — `{observed → canonical}`, in the
/// order the observed types arrived (Python builds an insertion-ordered dict).
pub fn plan_relation_normalization(
    edge_types: &[String],
    index: &[(String, String)],
) -> Vec<(String, String)> {
    let mut plan: Vec<(String, String)> = Vec::new();
    for edge_type in edge_types {
        if is_v4_enum_label(edge_type) {
            continue;
        }
        let canonical = normalize_relation_verb(edge_type, index);
        if !canonical.is_empty() && canonical != *edge_type {
            upsert(&mut plan, edge_type.clone(), canonical);
        }
    }
    plan
}

/// One concept row as `curate_noise` assembles it before deciding.
#[derive(Debug, Clone)]
pub struct ConceptStat {
    pub id: String,
    pub label: Option<String>,
    pub node_type: String,
    pub df: i64,
    pub heuristic: bool,
}

/// `curator.plan_concept_noise_reduction`'s verdict.
#[derive(Debug, Clone, Default)]
pub struct NoisePlan {
    pub remove: Vec<Value>,
    pub keep: Vec<Value>,
}

/// `curator.plan_concept_noise_reduction`.
///
/// Three rules, in Python's order: user-created nodes are untouchable whatever
/// their stats; below the frequency floor is noise; and — only once the corpus
/// is big enough to mean anything — a concept in more than `max_df_ratio` of
/// the documents separates nothing.
pub fn plan_concept_noise_reduction(
    concepts: &[ConceptStat],
    total_docs: i64,
    max_df_ratio: f64,
    min_doc_frequency: i64,
    min_corpus_docs: i64,
) -> NoisePlan {
    let total = total_docs.max(0);
    let mut plan = NoisePlan::default();
    for concept in concepts {
        let mut entry = Map::new();
        entry.insert("id".into(), Value::String(concept.id.clone()));
        entry.insert(
            "label".into(),
            concept
                .label
                .clone()
                .map(Value::String)
                .unwrap_or(Value::Null),
        );
        entry.insert("df".into(), Value::from(concept.df));
        entry.insert("heuristic".into(), Value::Bool(concept.heuristic));
        if !concept.heuristic {
            entry.insert(
                "reason".into(),
                Value::String("user_created_protected".into()),
            );
            plan.keep.push(Value::Object(entry));
            continue;
        }
        let df = concept.df;
        if df < min_doc_frequency {
            let ratio = if total != 0 {
                df as f64 / total as f64
            } else {
                0.0
            };
            entry.insert("df_ratio".into(), round4(ratio));
            entry.insert(
                "reason".into(),
                Value::String("below_frequency_floor".into()),
            );
            plan.remove.push(Value::Object(entry));
            continue;
        }
        if total >= min_corpus_docs {
            let ratio = df as f64 / total as f64;
            if ratio > max_df_ratio {
                entry.insert("df_ratio".into(), round4(ratio));
                entry.insert("reason".into(), Value::String("low_idf_ubiquitous".into()));
                plan.remove.push(Value::Object(entry));
                continue;
            }
        }
        entry.insert("reason".into(), Value::String("signal".into()));
        plan.keep.push(Value::Object(entry));
    }
    plan
}

fn round4(value: f64) -> Value {
    Value::from(crate::pytext::round_to(value, 4))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_verb_index_maps_canonicals_to_themselves() {
        let index = build_relation_verb_index();
        assert_eq!(lookup(&index, "created"), Some("created"));
        assert_eq!(lookup(&index, "만들다"), Some("created"));
        assert_eq!(lookup(&index, "작성함"), Some("created"));
        assert_eq!(lookup(&index, "포함함"), Some("contains"));
    }

    #[test]
    fn josa_is_stripped_only_when_two_syllables_survive() {
        assert_eq!(strip_josa("생성함을"), "생성함");
        // "은" would leave one character, so the token is left alone.
        assert_eq!(strip_josa("것은"), "것은");
        assert_eq!(strip_josa("mentions"), "mentions");
    }

    #[test]
    fn the_rename_plan_leaves_v4_enum_labels_alone() {
        let index = build_relation_verb_index();
        let plan = plan_relation_normalization(
            &[
                "MENTIONS".to_string(),
                "INDEXED_FROM".to_string(),
                "만들다".to_string(),
                "언급함".to_string(),
                "unknownverb".to_string(),
            ],
            &index,
        );
        assert_eq!(
            plan,
            vec![
                ("만들다".to_string(), "created".to_string()),
                ("언급함".to_string(), "mentions".to_string()),
            ]
        );
    }

    #[test]
    fn noise_planning_protects_user_created_nodes() {
        let concepts = vec![
            ConceptStat {
                id: "c1".into(),
                label: Some("mine".into()),
                node_type: "Concept".into(),
                df: 0,
                heuristic: false,
            },
            ConceptStat {
                id: "c2".into(),
                label: Some("orphan".into()),
                node_type: "Concept".into(),
                df: 0,
                heuristic: true,
            },
            ConceptStat {
                id: "c3".into(),
                label: Some("everywhere".into()),
                node_type: "Concept".into(),
                df: 9,
                heuristic: true,
            },
        ];
        let plan = plan_concept_noise_reduction(&concepts, 10, 0.8, 1, 5);
        let removed: Vec<&str> = plan
            .remove
            .iter()
            .map(|entry| entry["id"].as_str().unwrap())
            .collect();
        assert_eq!(removed, vec!["c2", "c3"]);
        assert_eq!(plan.keep[0]["reason"], "user_created_protected");
        assert_eq!(plan.remove[1]["df_ratio"], 0.9);
    }
}
