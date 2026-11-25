use std::collections::{BTreeSet, HashSet};
use std::sync::OnceLock;

use fancy_regex::Regex;
use lattice_core::pytext::{round_to, strip, truncate_chars};
use rusqlite::Connection;
use serde_json::Value;

use super::store::{py_text, stable_id};
use crate::history::{self, HistoryScope};

const MIN_PATTERN_COUNT: i64 = 2;
const MAX_HISTORY: i64 = 4000;
const SIGNATURE_SIMILARITY: f64 = 0.6;
const MIN_SUGGESTION_CONFIDENCE: f64 = 0.35;
const KG_GROUNDING_LIMIT: i64 = 5;
const UNGROUNDED_PENALTY: f64 = 0.15;

const STOPWORDS: [&str; 45] = [
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "me",
    "my",
    "you",
    "please",
    "can",
    "could",
    "would",
    "it",
    "this",
    "that",
    "do",
    "does",
    "what",
    "how",
    "about",
    "좀",
    "그",
    "이",
    "저",
    "것",
    "거",
    "게",
    "내",
    "제",
    "나",
    "너",
    "우리",
    "해줘",
    "해주세요",
    "주세요",
    "합니다",
    "있어",
];

const STOPWORDS_TAIL: [&str; 6] = ["있나", "있는지", "어떻게", "뭐야", "알려줘", "보여줘"];

const QUESTION_HINT: &str = r"(?i)(\?|어때|뭐야|뭐가|뭘까|알려줘|보여줘|정리해|요약해|정리 좀|요약 좀|해줘|what|how|why|when|where|status|summar|remind|list|show me|tell me)";

const TOKEN_PATTERN: &str = r"[\w가-힣]+";

const INTENT_RULES: [(&str, &str, &str); 3] = [
    (
        "digest",
        r"(?i)(오늘|하루|today|daily).{0,12}(정리|요약|digest|summary|기억|메모)|(정리|요약).{0,8}(해줘|해 줘|부탁)|summar(y|ize)",
        "daily-memory-digest",
    ),
    (
        "project_review",
        r"(?i)(프로젝트|project|진행|progress|status|상태|주간|weekly|이번 주)",
        "weekly-project-review",
    ),
    (
        "follow_up",
        r"(?i)(리마인드|remind|챙겨|잊지|deadline|마감|follow.?up|나중에|까먹)",
        "follow-up-radar",
    ),
];

pub(crate) struct Suggestion {
    pub(crate) id: String,
    pub(crate) kind: &'static str,
    pub(crate) title: String,
    pub(crate) installed: bool,
}

pub(crate) struct Request<'a> {
    pub(crate) user_email: &'a str,
    pub(crate) workspace_id: Option<&'a str>,
    pub(crate) state: &'a Value,
    pub(crate) enable_graph: bool,
}

pub(crate) fn suggestions(conn: &Connection, request: &Request<'_>) -> Vec<Suggestion> {
    let patterns = question_patterns(conn, request);
    let (by_suggestion, by_recipe) = installed_workflows(request.state, request.workspace_id);

    let mut items: Vec<Suggestion> = Vec::new();
    let mut seen_recipes: HashSet<&'static str> = HashSet::new();
    for pattern in &patterns {
        if let Some(recipe) = pattern.recipe_id {
            if seen_recipes.contains(recipe) {
                continue;
            }
        }
        let grounded = kg_related_count(conn, &pattern.representative, request);
        let confidence = question_confidence(
            pattern.count,
            pattern.examples.len(),
            pattern.recipe_id,
            grounded,
        );
        if confidence < MIN_SUGGESTION_CONFIDENCE {
            continue;
        }
        if let Some(recipe) = pattern.recipe_id {
            seen_recipes.insert(recipe);
        }
        let suggestion_id = stable_id("sug-q", &pattern.id);
        let installed = by_suggestion.contains(&suggestion_id)
            || pattern
                .recipe_id
                .is_some_and(|recipe| by_recipe.contains(recipe));
        items.push(Suggestion {
            id: suggestion_id,
            kind: "recurring_question",
            title: pattern.representative.clone(),
            installed,
        });
    }

    for source in super::store::local_sources(conn).unwrap_or_default() {
        if source.indexed <= 0 {
            continue;
        }
        if source_confidence(source.indexed, source.watch_enabled) < MIN_SUGGESTION_CONFIDENCE {
            continue;
        }
        let seed = if !source.id.is_empty() {
            source.id.clone()
        } else {
            source.root_path.clone()
        };
        let suggestion_id = stable_id("sug-src", &seed);
        let title = if !source.label.is_empty() {
            source.label
        } else if !source.root_path.is_empty() {
            source.root_path
        } else {
            "knowledge folder".to_string()
        };
        items.push(Suggestion {
            installed: by_suggestion.contains(&suggestion_id),
            id: suggestion_id,
            kind: "knowledge_source",
            title,
        });
    }
    items
}

fn question_confidence(
    count: i64,
    distinct_examples: usize,
    recipe_id: Option<&str>,
    kg_related: Option<i64>,
) -> f64 {
    let mut score = 0.3 + 0.5 * f64::min(1.0, (count - 1) as f64 / 4.0);
    score += f64::min(0.15, 0.05 * distinct_examples as f64);
    if recipe_id.is_some() {
        score += 0.15;
    }
    match kg_related {
        Some(related) if related != 0 => score += f64::min(0.2, 0.05 * related as f64),
        Some(_) => score -= UNGROUNDED_PENALTY,
        None => {}
    }
    round_to(score.clamp(0.0, 1.0), 2)
}

fn source_confidence(indexed: i64, watch_enabled: bool) -> f64 {
    let mut score = 0.25 + 0.6 * f64::min(1.0, indexed as f64 / 25.0);
    if watch_enabled {
        score += 0.1;
    }
    round_to(score.min(1.0), 2)
}

fn kg_related_count(conn: &Connection, text: &str, request: &Request<'_>) -> Option<i64> {
    if !request.enable_graph {
        return None;
    }
    let allowed = request
        .workspace_id
        .map(|id| BTreeSet::from([id.to_string()]));
    let report = crate::keyword::search(
        conn,
        &truncate_chars(text, 200),
        KG_GROUNDING_LIMIT,
        allowed.as_ref(),
        request.workspace_id.is_none(),
    )
    .ok()?;
    Some(
        report
            .get("matches")
            .and_then(Value::as_array)
            .map(|matches| matches.len() as i64)
            .unwrap_or(0),
    )
}

fn installed_workflows(
    state: &Value,
    workspace_id: Option<&str>,
) -> (HashSet<String>, HashSet<String>) {
    let mut by_suggestion = HashSet::new();
    let mut by_recipe = HashSet::new();
    for workflow in super::store::workflows(state, workspace_id) {
        let metadata = workflow.get("metadata").cloned().unwrap_or(Value::Null);
        let created_from = metadata.get("created_from").and_then(Value::as_str);
        let suggestion_id = py_text(metadata.get("suggestion_id"));
        let recipe_id = py_text(metadata.get("recipe_id"));
        if created_from == Some("automation_suggestion") && !suggestion_id.is_empty() {
            by_suggestion.insert(suggestion_id);
        } else if created_from == Some("brain_automation_recipe") && !recipe_id.is_empty() {
            by_recipe.insert(recipe_id);
        }
    }
    (by_suggestion, by_recipe)
}

struct Pattern {
    id: String,
    representative: String,
    count: i64,
    recipe_id: Option<&'static str>,
    examples: Vec<String>,
}

struct Cluster {
    representative: String,
    signature: BTreeSet<String>,
    count: i64,
    last_asked: String,
    examples: Vec<String>,
}

fn question_patterns(conn: &Connection, request: &Request<'_>) -> Vec<Pattern> {
    let mut clusters: Vec<Cluster> = Vec::new();
    for (content, timestamp) in user_questions(conn, request) {
        let tokens = tokenize(&content);
        if tokens.len() < 2 {
            continue;
        }
        let signature: BTreeSet<String> = tokens.into_iter().collect();
        let matched = clusters
            .iter_mut()
            .find(|cluster| similarity(&cluster.signature, &signature) >= SIGNATURE_SIMILARITY);
        let Some(cluster) = matched else {
            clusters.push(Cluster {
                representative: content.clone(),
                signature,
                count: 1,
                last_asked: timestamp,
                examples: vec![content],
            });
            continue;
        };
        cluster.count += 1;
        cluster.signature.extend(signature);
        if timestamp >= cluster.last_asked {
            cluster.last_asked = timestamp;
            cluster.representative = content.clone();
        }
        if !cluster.examples.contains(&content) {
            cluster.examples.push(content);
        }
    }

    let mut recurring: Vec<&Cluster> = clusters
        .iter()
        .filter(|cluster| cluster.count >= MIN_PATTERN_COUNT)
        .collect();
    recurring.sort_by(|left, right| {
        (right.count, right.last_asked.as_str()).cmp(&(left.count, left.last_asked.as_str()))
    });
    recurring
        .into_iter()
        .take(20)
        .map(|cluster| {
            let matched = intent_rules()
                .iter()
                .position(|rule| rule.is_match(&cluster.representative).unwrap_or(false));
            let signature: Vec<&str> = cluster.signature.iter().map(String::as_str).collect();
            Pattern {
                id: stable_id("pat", &signature.join(" ")),
                representative: truncate_chars(&cluster.representative, 160),
                count: cluster.count,
                recipe_id: matched.map(|index| INTENT_RULES[index].2),
                examples: cluster.examples.iter().take(3).cloned().collect(),
            }
        })
        .collect()
}

fn user_questions(conn: &Connection, request: &Request<'_>) -> Vec<(String, String)> {
    let scope = HistoryScope {
        user_email: Some(request.user_email.to_string()),
        allowed_workspaces: request.workspace_id.map(|id| vec![id.to_string()]),
        include_legacy_global: request.workspace_id.is_none(),
    };
    let Ok(items) = history::history(conn, None, Some(MAX_HISTORY), &scope) else {
        return Vec::new();
    };
    let hint = question_hint();
    items
        .into_iter()
        .filter(|item| item.get("role").and_then(Value::as_str) == Some("user"))
        .filter_map(|item| {
            let content = strip(&py_text(item.get("content")));
            if content.is_empty() || content.chars().count() < 6 || content.starts_with('/') {
                return None;
            }
            if !hint.is_match(&content).unwrap_or(false) {
                return None;
            }
            Some((content, py_text(item.get("timestamp"))))
        })
        .collect()
}

fn tokenize(text: &str) -> Vec<String> {
    let lowered = text.to_lowercase();
    token_pattern()
        .find_iter(&lowered)
        .filter_map(Result::ok)
        .map(|found| found.as_str().to_string())
        .filter(|token| token.chars().count() > 1 && !is_stopword(token))
        .collect()
}

fn is_stopword(token: &str) -> bool {
    STOPWORDS.contains(&token) || STOPWORDS_TAIL.contains(&token)
}

fn token_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| crate::build_pattern(TOKEN_PATTERN))
}

fn question_hint() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| crate::build_pattern(QUESTION_HINT))
}

fn intent_rules() -> &'static [Regex; 3] {
    static PATTERNS: OnceLock<[Regex; 3]> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        [
            crate::build_pattern(INTENT_RULES[0].1),
            crate::build_pattern(INTENT_RULES[1].1),
            crate::build_pattern(INTENT_RULES[2].1),
        ]
    })
}

fn similarity(left: &BTreeSet<String>, right: &BTreeSet<String>) -> f64 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }
    let intersection = left.intersection(right).count() as f64;
    let union = left.union(right).count() as f64;
    intersection / union
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_auth::OrderedMap;
    use serde_json::json;

    use super::super::store::clip;

    fn section_items(items: &[Suggestion]) -> (i64, Vec<Value>) {
        let open: Vec<&Suggestion> = items.iter().filter(|item| !item.installed).collect();
        let top = open
            .iter()
            .take(3)
            .map(|item| {
                let mut entry = OrderedMap::new();
                entry.insert("id", Value::String(item.id.clone()));
                entry.insert("kind", Value::String(item.kind.to_string()));
                entry.insert(
                    "title",
                    Value::String(clip(&Value::String(item.title.clone()), 120)),
                );
                serde_json::to_value(&entry).unwrap_or(Value::Null)
            })
            .collect();
        (open.len() as i64, top)
    }

    #[test]
    fn tokens_drop_stopwords_and_single_characters() {
        let tokens = tokenize("오늘 내 프로젝트 status 좀 알려줘 a");
        assert!(tokens.contains(&"프로젝트".to_string()));
        assert!(tokens.contains(&"status".to_string()));
        assert!(!tokens.contains(&"좀".to_string()), "stopword");
        assert!(!tokens.contains(&"알려줘".to_string()), "stopword");
        assert!(!tokens.contains(&"a".to_string()), "len(token) > 1");
    }

    #[test]
    fn similarity_is_jaccard_and_empty_sides_score_zero() {
        let left: BTreeSet<String> = ["a", "b", "c"].iter().map(|s| s.to_string()).collect();
        let right: BTreeSet<String> = ["b", "c", "d"].iter().map(|s| s.to_string()).collect();
        assert!((similarity(&left, &right) - 0.5).abs() < 1e-12);
        assert_eq!(similarity(&BTreeSet::new(), &right), 0.0);
    }

    #[test]
    fn a_grounding_count_of_zero_is_charged_and_none_is_not() {
        let ungrounded = question_confidence(2, 1, None, Some(0));
        let unknown = question_confidence(2, 1, None, None);
        assert!(
            (unknown - 0.47).abs() < 1e-12,
            "0.3 + 0.125 + 0.05 = 0.475 → 0.47 (CPython round)"
        );
        assert!(
            (ungrounded - 0.32).abs() < 1e-12,
            "0.475 - 0.15 = 0.325 → 0.32 (CPython round)"
        );
        assert!(
            ungrounded < MIN_SUGGESTION_CONFIDENCE && unknown >= MIN_SUGGESTION_CONFIDENCE,
            "the penalty is what makes the floor reachable"
        );
    }

    #[test]
    fn confidence_climbs_with_repeats_examples_recipe_and_grounding() {
        assert!(
            (question_confidence(5, 3, Some("daily-memory-digest"), Some(4)) - 1.0).abs() < 1e-12
        );
        assert!((source_confidence(25, true) - 0.95).abs() < 1e-12);
        assert!((source_confidence(1, false) - 0.27).abs() < 1e-12);
        assert!(source_confidence(1, false) < MIN_SUGGESTION_CONFIDENCE);
    }

    #[test]
    fn installed_workflows_index_by_provenance() {
        let state = json!({"workflows": [
            {"id": "w1", "metadata": {"created_from": "automation_suggestion", "suggestion_id": "sug-q-1"}},
            {"id": "w2", "metadata": {"created_from": "brain_automation_recipe", "recipe_id": "follow-up-radar"}},
            {"id": "w3", "metadata": {"created_from": "automation_suggestion"}},
            {"id": "w4"},
        ]});
        let (by_suggestion, by_recipe) = installed_workflows(&state, None);
        assert!(by_suggestion.contains("sug-q-1"));
        assert!(by_recipe.contains("follow-up-radar"));
        assert_eq!(
            by_suggestion.len(),
            1,
            "a blank suggestion_id registers nothing"
        );
    }

    #[test]
    fn the_section_counts_only_uninstalled_and_shows_three() {
        let items: Vec<Suggestion> = (0..5)
            .map(|index| Suggestion {
                id: format!("sug-{index}"),
                kind: "recurring_question",
                title: format!("question {index}"),
                installed: index == 0,
            })
            .collect();
        let (count, top) = section_items(&items);
        assert_eq!(count, 4);
        assert_eq!(top.len(), 3);
        assert_eq!(
            top[0]["id"], "sug-1",
            "the installed one is gone, not skipped"
        );
        assert_eq!(top[0]["kind"], "recurring_question");
    }
}
