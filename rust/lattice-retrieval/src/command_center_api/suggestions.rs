//! `AutomationIntelligenceService.suggestions`, as far as the briefing reads it.
//!
//! `_suggestion_section` keeps only the suggestions that are **not** installed,
//! publishes their number, and renders `id` / `kind` / `title` for the first
//! three. Everything else the service returns (`confidence_factors`, `reason`,
//! `cadence`, `low_confidence`, `quality`, `consent`) is unreachable from this
//! family's two responses and is therefore not materialised.
//!
//! What *is* reproduced in full is every computation that decides **which**
//! suggestions exist and in **what order**: the question clustering, the intent
//! rules, the KG grounding count, and the confidence floor. Confidence is
//! rounded to two places before the comparison, exactly as Python rounds it, and
//! `if kg_related:` is Python truthiness — a grounding count of **0** takes the
//! `elif kg_related == 0` penalty branch, while `None` (graph unreadable) takes
//! neither.

use std::collections::{BTreeSet, HashSet};
use std::sync::OnceLock;

use fancy_regex::Regex;
use lattice_core::pytext::{round_to, strip, truncate_chars};
use rusqlite::Connection;
use serde_json::Value;

use super::store::{py_text, stable_id};
use crate::history::{self, HistoryScope};

/// `_MIN_PATTERN_COUNT`.
const MIN_PATTERN_COUNT: i64 = 2;
/// `_MAX_HISTORY`.
const MAX_HISTORY: i64 = 4000;
/// `_SIGNATURE_SIMILARITY`.
const SIGNATURE_SIMILARITY: f64 = 0.6;
/// `_MIN_SUGGESTION_CONFIDENCE`.
const MIN_SUGGESTION_CONFIDENCE: f64 = 0.35;
/// `_KG_GROUNDING_LIMIT`.
const KG_GROUNDING_LIMIT: i64 = 5;
/// `_UNGROUNDED_PENALTY`.
const UNGROUNDED_PENALTY: f64 = 0.15;

/// `_STOPWORDS`, in the file's own order (membership only).
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

/// The stopwords the Python set spells with more than one token per line — the
/// tail of the same literal, kept separate only to stay inside one line width.
const STOPWORDS_TAIL: [&str; 6] = ["있나", "있는지", "어떻게", "뭐야", "알려줘", "보여줘"];

/// `_QUESTION_HINT_RE`, with `re.IGNORECASE` spelled inline.
const QUESTION_HINT: &str = r"(?i)(\?|어때|뭐야|뭐가|뭘까|알려줘|보여줘|정리해|요약해|정리 좀|요약 좀|해줘|what|how|why|when|where|status|summar|remind|list|show me|tell me)";

/// `_tokens`' pattern. Python's `\w` is Unicode-aware for `str` patterns and so
/// is this crate's, which is why the explicit Hangul range adds nothing but is
/// carried anyway — it is what the original spells.
const TOKEN_PATTERN: &str = r"[\w가-힣]+";

/// `_INTENT_RULES` — `(intent, pattern, recipe_id)`, in order; first match wins.
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

/// One suggestion, reduced to what `_suggestion_section` renders.
pub(crate) struct Suggestion {
    /// `_stable_id("sug-q" | "sug-src", …)`.
    pub(crate) id: String,
    /// `"recurring_question"` or `"knowledge_source"`.
    pub(crate) kind: &'static str,
    /// The untruncated title; the section clips it to 120 characters.
    pub(crate) title: String,
    /// Whether a workflow already carries this suggestion's provenance.
    pub(crate) installed: bool,
}

/// Everything the suggestion pass needs from the request.
pub(crate) struct Request<'a> {
    pub(crate) user_email: &'a str,
    pub(crate) workspace_id: Option<&'a str>,
    /// The Workspace OS state document, already loaded.
    pub(crate) state: &'a Value,
    /// `enable_graph and knowledge_graph is not None`.
    pub(crate) enable_graph: bool,
}

/// `AutomationIntelligenceService.suggestions(...)["suggestions"]`.
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
        // `by_suggestion.get(id) or (by_recipe.get(recipe) if recipe else None)`
        // — an entry only exists for a workflow whose metadata is non-empty, so
        // the `or` never trips over a falsy-but-present workflow.
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

/// `_question_confidence` — the rounded score the floor is compared against.
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
        // `if kg_related:` — a count of 0 is falsy and falls to the `elif`.
        Some(related) if related != 0 => score += f64::min(0.2, 0.05 * related as f64),
        Some(_) => score -= UNGROUNDED_PENALTY,
        None => {}
    }
    round_to(score.clamp(0.0, 1.0), 2)
}

/// `_source_confidence` — note there is no lower clamp here, only `min(1.0, …)`.
fn source_confidence(indexed: i64, watch_enabled: bool) -> f64 {
    let mut score = 0.25 + 0.6 * f64::min(1.0, indexed as f64 / 25.0);
    if watch_enabled {
        score += 0.1;
    }
    round_to(score.min(1.0), 2)
}

/// `_kg_related_count` — `None` means "grounding was impossible", not "zero".
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

/// `_installed_workflows`, as the two membership sets the caller reads.
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

/// One clustered recurring question, after `as_dict()`.
struct Pattern {
    id: String,
    representative: String,
    count: i64,
    recipe_id: Option<&'static str>,
    /// Already `[:3]`; only its length reaches the score.
    examples: Vec<String>,
}

/// The mutable cluster `question_patterns` grows.
struct Cluster {
    representative: String,
    signature: BTreeSet<String>,
    count: i64,
    last_asked: String,
    examples: Vec<String>,
}

/// `question_patterns(...)["patterns"]`.
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
        // Union keeps the cluster stable as phrasing drifts.
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
    // `sort(key=(count, last_asked), reverse=True)` — stable, so equal keys keep
    // the order they were discovered in.
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

/// `_user_questions` — `(content, timestamp)` for every question-shaped turn.
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

/// `_tokens`.
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

/// The three compiled patterns are process-wide: `question_patterns` runs them
/// once per cluster and `tokenize` once per turn, and recompiling a backtracking
/// regex on every message would dominate the whole read.
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

/// `_similarity` — Jaccard, `0.0` when either side is empty.
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

    /// `_suggestion_section` — count the uninstalled, render the first three.
    ///
    /// The briefing renders its sections through the command-center handlers;
    /// this stays here as the shape the section assertion below pins.
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
        // The cheapest recurring question: asked twice, one phrasing, no recipe.
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
