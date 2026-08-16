//! `MemoryRecallMixin.recall` — one honestly comparable ranking over three tiers.
//!
//! Workspace memories and graph nodes are scored by the *same* lexical scorer
//! (the fraction of query tokens present), which is what makes cross-tier
//! ordering real rather than an artefact of per-tier constants. Vector
//! neighbours are then blended in: an existing graph row takes
//! `max(lexical, 0.4·lexical + 0.6·similarity)` and a new one
//! `max(lexical, 0.6·similarity)`.
//!
//! Three details decide whether a port is faithful:
//!
//! * `if r.get("vector_score")` is Python truthiness — a similarity of exactly
//!   `0.0` is falsy, so it would *not* add `"semantic"` to `evidence_kinds`.
//!   Unreachable in practice because `similarity <= 0` is skipped earlier, and
//!   reproduced anyway rather than "corrected".
//! * the quality gate drops zero-score rows **only** when at least one row
//!   scored, so it can never empty a recall.
//! * `results.sort(key=score, reverse=True)` is stable: rows that tie keep the
//!   order the tiers produced them in (workspace, then graph, then vector).

use std::collections::{BTreeSet, HashMap};

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::service::{text_or, MAX_RECALL_THUMBNAIL_CHARS};
use super::wsos;

/// One recall row, kept in Python's insertion order until it is rendered.
type Row = OrderedMap;

fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

/// `q.lower().split()` — Python's whitespace split, empties dropped.
fn query_tokens(query: &str) -> Vec<String> {
    query
        .to_lowercase()
        .split_whitespace()
        .map(str::to_string)
        .collect()
}

/// `_matched_terms(*texts)` — every query token present in the joined haystack.
fn matched_terms(tokens: &[String], texts: &[&str]) -> Vec<String> {
    let haystack = texts.join(" ").to_lowercase();
    tokens
        .iter()
        .filter(|token| haystack.contains(token.as_str()))
        .cloned()
        .collect()
}

/// `_lexical_score(matched)` — the fraction of query tokens present.
fn lexical_score(tokens: &[String], matched: &[String]) -> f64 {
    if tokens.is_empty() {
        return 0.0;
    }
    lattice_core::pytext::round4(matched.len() as f64 / tokens.len() as f64)
}

/// `constants._visual_fields` — caption and inline thumbnail, when present.
fn visual_fields(hit: &Value, row: &mut Row) {
    let Some(metadata) = hit.get("metadata").and_then(Value::as_object) else {
        return;
    };
    let caption = lattice_core::pytext::strip(
        metadata
            .get("caption")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    if !caption.is_empty() {
        row.insert(
            "caption",
            Value::String(lattice_core::truncate_chars(&caption, 400)),
        );
    }
    let thumbnail = metadata
        .get("thumbnail")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if thumbnail.starts_with("data:image/")
        && thumbnail.chars().count() <= MAX_RECALL_THUMBNAIL_CHARS
    {
        row.insert("thumbnail", Value::String(thumbnail.to_string()));
    }
}

fn snippet(text: &str) -> Value {
    Value::String(lattice_core::truncate_chars(text, 240))
}

fn terms(matched: &[String]) -> Value {
    Value::Array(matched.iter().map(|t| Value::String(t.clone())).collect())
}

/// One tier read that failed, reported instead of swallowed.
struct Failure {
    source: &'static str,
    detail: String,
}

/// `MemoryRecallMixin.recall`, over a connection the caller already holds.
pub fn recall(
    conn: &Connection,
    state: &Value,
    graph_enabled: bool,
    query: &str,
    user_email: &str,
    workspace_id: Option<&str>,
    limit: i64,
) -> Result<OrderedMap, CoreError> {
    let q = lattice_core::pytext::strip(query);
    let tokens = query_tokens(&q);
    let mut results: Vec<Row> = Vec::new();
    let mut errors: Vec<Failure> = Vec::new();

    // ── workspace tier ──────────────────────────────────────────────────
    let user = Some(user_email).filter(|value| !value.is_empty());
    for memory in wsos::search_memories(state, &q, user, limit, workspace_id) {
        let tags = wsos::tag_text(&memory);
        let matched = matched_terms(
            &tokens,
            &[
                text_or(&memory, "content", ""),
                &tags,
                text_or(&memory, "kind", ""),
            ],
        );
        let mut row = Row::new();
        row.insert("source", Value::String("workspace".to_string()));
        row.insert("id", memory.get("id").cloned().unwrap_or(Value::Null));
        row.insert(
            "title",
            Value::String(super::service::nonempty_or(&memory, "kind", "memory")),
        );
        row.insert("snippet", snippet(text_or(&memory, "content", "")));
        row.insert("kind", memory.get("kind").cloned().unwrap_or(Value::Null));
        row.insert("score", Value::from(lexical_score(&tokens, &matched)));
        row.insert("matched_terms", terms(&matched));
        row.insert(
            "tags",
            match memory.get("tags") {
                Some(Value::Array(tags)) => Value::Array(tags.clone()),
                _ => Value::Array(Vec::new()),
            },
        );
        results.push(row);
    }

    // ── graph tier ──────────────────────────────────────────────────────
    let allowed: Option<BTreeSet<String>> =
        workspace_id.map(|workspace| [workspace.to_string()].into_iter().collect());
    if graph_enabled && !q.is_empty() {
        match crate::keyword::search(conn, &q, limit, allowed.as_ref(), false) {
            Ok(payload) => {
                let hits = payload
                    .get("matches")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                for hit in hits.iter().take(limit.max(0) as usize) {
                    let matched = matched_terms(
                        &tokens,
                        &[
                            text_or(hit, "title", ""),
                            text_or(hit, "name", ""),
                            text_or(hit, "summary", ""),
                            text_or(hit, "content", ""),
                        ],
                    );
                    let mut row = Row::new();
                    row.insert("source", Value::String("graph".to_string()));
                    row.insert(
                        "id",
                        hit.get("id")
                            .cloned()
                            .filter(|value| !value.is_null())
                            .or_else(|| hit.get("node_id").cloned())
                            .unwrap_or(Value::Null),
                    );
                    row.insert(
                        "title",
                        Value::String(first_nonempty(hit, &["title", "name"], "node")),
                    );
                    row.insert(
                        "snippet",
                        snippet(&first_nonempty(hit, &["summary", "content"], "")),
                    );
                    row.insert(
                        "kind",
                        Value::String(super::service::nonempty_or(hit, "type", "node")),
                    );
                    row.insert("score", Value::from(lexical_score(&tokens, &matched)));
                    row.insert("matched_terms", terms(&matched));
                    visual_fields(hit, &mut row);
                    results.push(row);
                }
            }
            Err(error) => errors.push(Failure {
                source: "graph",
                detail: error.to_string(),
            }),
        }
    }

    // ── vector tier (v9.3.0 hybrid recall) ──────────────────────────────
    let mut vector_used = false;
    if graph_enabled && !q.is_empty() {
        let model = lattice_core::LocalEmbeddingModel::from_env();
        let hits = match crate::vector::vector_search(conn, &model, &q, limit, 0.0) {
            Ok(payload) => {
                vector_used = true;
                let matches = payload
                    .get("matches")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                // The vector index is global, so a scoped call filters the
                // matches down to visible nodes before they influence anything.
                if allowed.is_some() && !matches.is_empty() {
                    lattice_core::filter_scoped_nodes(
                        conn,
                        matches,
                        allowed.as_ref(),
                        false,
                        |hit| text_or(hit, "node_id", "").to_string(),
                    )?
                } else {
                    matches
                }
            }
            Err(error) => {
                errors.push(Failure {
                    source: "vector",
                    detail: error.to_string(),
                });
                Vec::new()
            }
        };
        let mut by_node_id: HashMap<String, usize> = HashMap::new();
        for (index, row) in results.iter().enumerate() {
            if row.get("source") == Some(&Value::String("graph".to_string())) {
                by_node_id.insert(
                    crate::shape::py_str(row.get("id").unwrap_or(&Value::Null)),
                    index,
                );
            }
        }
        for hit in &hits {
            let node_id = {
                let raw = text_or(hit, "node_id", "");
                if raw.is_empty() {
                    crate::shape::py_str(hit.get("id").unwrap_or(&Value::Null))
                } else {
                    raw.to_string()
                }
            };
            let node_id = if node_id == "None" {
                String::new()
            } else {
                node_id
            };
            let similarity = lattice_core::pytext::round4(
                hit.get("score").and_then(Value::as_f64).unwrap_or(0.0),
            );
            if similarity <= 0.0 {
                continue;
            }
            let locator = hit
                .get("metadata")
                .and_then(Value::as_object)
                .and_then(|metadata| metadata.get("locator"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            match by_node_id.get(&node_id).copied() {
                Some(index) => {
                    let existing = &mut results[index];
                    let previous_vector = existing
                        .get("vector_score")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0);
                    existing.insert("vector_score", Value::from(previous_vector.max(similarity)));
                    let previous = existing.get("score").and_then(Value::as_f64).unwrap_or(0.0);
                    let blended = previous.max(0.4 * previous + 0.6 * similarity);
                    existing.insert("score", Value::from(lattice_core::pytext::round4(blended)));
                    if !locator.is_empty()
                        && existing
                            .get("locator")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .is_empty()
                    {
                        existing.insert("locator", Value::String(locator));
                    }
                }
                None => {
                    let matched = matched_terms(
                        &tokens,
                        &[text_or(hit, "title", ""), text_or(hit, "summary", "")],
                    );
                    let mut row = Row::new();
                    row.insert("source", Value::String("graph".to_string()));
                    row.insert(
                        "id",
                        if node_id.is_empty() {
                            hit.get("id").cloned().unwrap_or(Value::Null)
                        } else {
                            Value::String(node_id.clone())
                        },
                    );
                    row.insert(
                        "title",
                        Value::String(super::service::nonempty_or(hit, "title", "node")),
                    );
                    row.insert("snippet", snippet(text_or(hit, "summary", "")));
                    row.insert(
                        "kind",
                        Value::String(super::service::nonempty_or(hit, "type", "node")),
                    );
                    row.insert(
                        "score",
                        Value::from(lattice_core::pytext::round4(
                            lexical_score(&tokens, &matched).max(0.6 * similarity),
                        )),
                    );
                    row.insert("matched_terms", terms(&matched));
                    row.insert("vector_score", Value::from(similarity));
                    if !locator.is_empty() {
                        row.insert("locator", Value::String(locator));
                    }
                    visual_fields(hit, &mut row);
                    results.push(row);
                    if !node_id.is_empty() {
                        by_node_id.insert(node_id, results.len() - 1);
                    }
                }
            }
        }
    }

    // ── quality gate ────────────────────────────────────────────────────
    let candidates = results.len() as i64;
    let scored = |row: &Row| row.get("score").and_then(Value::as_f64).unwrap_or(0.0);
    if !tokens.is_empty() && results.iter().any(|row| scored(row) > 0.0) {
        results.retain(|row| scored(row) > 0.0);
    }
    for row in results.iter_mut() {
        let score = row.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        row.insert(
            "confidence",
            Value::String(
                if score >= 0.65 {
                    "high"
                } else if score >= 0.3 {
                    "medium"
                } else {
                    "low"
                }
                .to_string(),
            ),
        );
        let mut evidence: Vec<Value> = Vec::new();
        if row
            .get("matched_terms")
            .and_then(Value::as_array)
            .is_some_and(|terms| !terms.is_empty())
        {
            evidence.push(Value::String("lexical".to_string()));
        }
        // Python truthiness: 0.0 is falsy, so a zero similarity claims nothing.
        if row
            .get("vector_score")
            .and_then(Value::as_f64)
            .unwrap_or(0.0)
            != 0.0
        {
            evidence.push(Value::String("semantic".to_string()));
        }
        row.insert("evidence_kinds", Value::Array(evidence));
    }
    results.sort_by(|left, right| {
        scored(right)
            .partial_cmp(&scored(left))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let passed = results.len() as i64;
    let cap = limit.clamp(1, 100) as usize;

    let mut gate = OrderedMap::new();
    gate.insert("candidates", Value::from(candidates));
    gate.insert("passed", Value::from(passed));
    gate.insert("filtered", Value::from(candidates - passed));
    gate.insert(
        "gate",
        Value::String(
            if vector_used {
                "hybrid-evidence/v2"
            } else {
                "lexical-evidence/v1"
            }
            .to_string(),
        ),
    );

    let mut out = OrderedMap::new();
    out.insert("query", Value::String(q));
    out.insert(
        "results",
        Value::Array(results.iter().take(cap).map(json).collect()),
    );
    out.insert("count", Value::from(passed));
    out.insert("source", Value::String("live".to_string()));
    out.insert(
        "status",
        Value::String(if errors.is_empty() { "ok" } else { "degraded" }.to_string()),
    );
    out.insert(
        "errors",
        Value::Array(
            errors
                .iter()
                .map(|failure| {
                    let mut row = OrderedMap::new();
                    row.insert("source", Value::String(failure.source.to_string()));
                    row.insert("detail", Value::String(failure.detail.clone()));
                    json(&row)
                })
                .collect(),
        ),
    );
    out.insert("quality_gate", json(&gate));
    Ok(out)
}

/// `hit.get(a) or hit.get(b) or fallback` on string fields.
fn first_nonempty(hit: &Value, keys: &[&str], fallback: &str) -> String {
    for key in keys {
        if let Some(text) = hit
            .get(*key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            return text.to_string();
        }
    }
    fallback.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_lexical_scorer_is_the_fraction_of_query_tokens_present() {
        let tokens = query_tokens("Retrieval  Fusion");
        assert_eq!(tokens, vec!["retrieval", "fusion"]);
        let matched = matched_terms(&tokens, &["Hybrid retrieval decision", ""]);
        assert_eq!(matched, vec!["retrieval"]);
        assert_eq!(lexical_score(&tokens, &matched), 0.5);
        assert_eq!(lexical_score(&[], &[]), 0.0, "no query means no score");
        let all = matched_terms(&tokens, &["retrieval fusion"]);
        assert_eq!(lexical_score(&tokens, &all), 1.0);
    }

    #[test]
    fn visual_fields_only_carry_a_real_caption_and_a_real_thumbnail() {
        let mut row = Row::new();
        visual_fields(
            &serde_json::json!({"metadata": {"caption": "  a photo  ", "thumbnail": "data:image/png;base64,x"}}),
            &mut row,
        );
        assert_eq!(row.get("caption"), Some(&Value::String("a photo".into())));
        assert!(row.contains_key("thumbnail"));
        let mut bare = Row::new();
        visual_fields(
            &serde_json::json!({"metadata": {"caption": "  ", "thumbnail": "http://x"}}),
            &mut bare,
        );
        assert!(
            bare.is_empty(),
            "an empty caption and a non-data thumbnail add nothing"
        );
        let mut none = Row::new();
        visual_fields(&serde_json::json!({"metadata": 3}), &mut none);
        assert!(none.is_empty());
    }

    #[test]
    fn a_first_nonempty_read_falls_all_the_way_through() {
        let hit = serde_json::json!({"title": "", "name": "Named"});
        assert_eq!(first_nonempty(&hit, &["title", "name"], "node"), "Named");
        assert_eq!(first_nonempty(&hit, &["summary"], "node"), "node");
    }
}
