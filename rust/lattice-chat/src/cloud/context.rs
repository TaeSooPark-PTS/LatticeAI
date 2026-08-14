use std::collections::BTreeSet;
use std::sync::OnceLock;

use fancy_regex::Regex;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_retrieval::hybrid::{hybrid_search, HybridOptions};
use lattice_retrieval::shape::context_quality_signal;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::boundary::{blocked_reason, NetworkMode};
use crate::pyvalue::{field, text as py_text};
use crate::redact::redact_secret_text;

/// `build_minimal_context`'s soft type preference.
const PREFERRED_TYPES: [&str; 8] = [
    "Decision", "Concept", "Task", "Document", "File", "CodeFile", "Person", "Feature",
];

/// What may be sent to a cloud LLM.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct MinimalContext {
    pub query: String,
    pub keywords: Vec<String>,
    pub node_ids: Vec<String>,
    pub compact_text: String,
    pub nodes: Vec<Value>,
    pub token_estimate: i64,
    pub quality: Value,
}

impl MinimalContext {
    /// `MinimalContext.to_dict`.
    pub fn to_value(&self) -> Value {
        json!({
            "query": self.query,
            "keywords": self.keywords,
            "node_ids": self.node_ids,
            "compact_text": self.compact_text,
            "nodes": self.nodes,
            "token_estimate": self.token_estimate,
            "quality": self.quality,
        })
    }

    /// The node titles the `hybrid_context` frame reports.
    pub fn titles(&self) -> Vec<String> {
        self.nodes
            .iter()
            .map(|node| {
                let title = field(node, "title");
                if title.is_empty() {
                    field(node, "id")
                } else {
                    title
                }
            })
            .collect()
    }
}

/// `_rough_token_estimate` — ~4 characters per token for mixed EN/KO.
pub fn rough_token_estimate(text: &str) -> i64 {
    if text.is_empty() {
        return 0;
    }
    // Python measures characters, and so does this: a Korean summary is half
    // the tokens of its UTF-8 byte length.
    (text.chars().count().div_ceil(4) as i64).max(1)
}

/// `_extract_keywords` — deterministic, dependency-free candidates.
pub fn extract_keywords(message: &str, limit: usize) -> Vec<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let pattern = RE.get_or_init(|| Regex::new(r"[\w가-힣]{2,}").expect("pattern compiles"));
    let text = message.trim();
    if text.is_empty() {
        return Vec::new();
    }
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut out: Vec<String> = Vec::new();
    let mut position = 0usize;
    while let Ok(Some(found)) = pattern.find_from_pos(text, position) {
        position = found.end().max(found.start() + 1);
        let token = found.as_str();
        if seen.insert(token.to_lowercase()) {
            out.push(token.to_string());
            if out.len() >= limit {
                break;
            }
        }
    }
    out
}

fn score_of(node: &Value) -> f64 {
    node.get("score").and_then(Value::as_f64).unwrap_or(0.0)
}

fn node_id_of(node: &Value) -> String {
    let node_id = field(node, "node_id");
    if node_id.is_empty() {
        field(node, "id")
    } else {
        node_id
    }
}

/// `build_minimal_context` — the smallest useful set of local nodes.
///
/// `mode` is carried for symmetry with the Python signature; the selection is
/// mode-invariant (the hard blocks apply in every mode) and the *caller* is the
/// one that decides whether the result may be transmitted.
pub fn build_minimal_context(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    message: &str,
    _mode: NetworkMode,
    top_k: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
    now_secs: f64,
) -> MinimalContext {
    let query = message.trim().to_string();
    let keywords = extract_keywords(&query, 12);
    let empty = MinimalContext {
        query: query.clone(),
        keywords: keywords.clone(),
        quality: json!({
            "mode": "none", "nodes": 0, "limited": true,
            "reason": "no store or empty query",
        }),
        ..Default::default()
    };
    if query.is_empty() {
        return empty;
    }

    let options = HybridOptions {
        top_k: (top_k * 2).max(top_k),
        allowed_workspaces: allowed_workspaces.cloned(),
        include_legacy_global: false,
        now_secs,
        ..Default::default()
    };
    // Fail closed: an unusable retrieval sends nothing rather than falling back
    // to a broader, unfiltered context.
    let (matches, quality) = match hybrid_search(conn, model, &query, &options) {
        Ok(result) => {
            let matches: Vec<Value> = result
                .get("matches")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mode = result
                .get("mode")
                .and_then(Value::as_str)
                .filter(|mode| !mode.is_empty())
                .unwrap_or("hybrid")
                .to_string();
            let quality = context_quality_signal(&mode, matches.len() as i64, None, None);
            (matches, Some(quality))
        }
        Err(_) => (Vec::new(), None),
    };

    let mut selected: Vec<Value> = matches
        .into_iter()
        .filter(|node| blocked_reason(node).is_none())
        .collect();
    // `sort` in Python is stable, and so is `sort_by` here — the third key only
    // breaks ties the first two leave.
    selected.sort_by(|left, right| {
        let rank = |node: &Value| {
            let node_type = field(node, "type");
            i32::from(!PREFERRED_TYPES.contains(&node_type.as_str()))
        };
        rank(left)
            .cmp(&rank(right))
            .then_with(|| {
                (-score_of(left))
                    .partial_cmp(&-score_of(right))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| node_id_of(left).cmp(&node_id_of(right)))
    });
    let keep = top_k.clamp(1, 12) as usize;
    selected.truncate(keep);

    let mut lines: Vec<String> = Vec::new();
    let mut node_ids: Vec<String> = Vec::new();
    for node in &selected {
        let node_id = node_id_of(node);
        if node_id.is_empty() {
            continue;
        }
        node_ids.push(node_id.clone());
        let title = {
            let title = field(node, "title");
            if title.is_empty() {
                node_id.clone()
            } else {
                title
            }
        };
        let summary: String = py_text(node.get("summary").unwrap_or(&Value::Null))
            .chars()
            .take(400)
            .collect();
        let node_type = {
            let node_type = field(node, "type");
            if node_type.is_empty() {
                "Node".to_string()
            } else {
                node_type
            }
        };
        // `.rstrip(": ")` strips the *characters* ':' and ' ' — so an empty
        // summary loses the separator and a trailing colon in a title would go
        // with it. Python's behaviour, reproduced.
        lines.push(
            format!("- [{node_type}] {title}: {summary}")
                .trim_end_matches([':', ' '])
                .to_string(),
        );
    }

    let compact = redact_secret_text(&lines.join("\n"));
    let token_estimate = rough_token_estimate(&compact);
    MinimalContext {
        quality: quality.unwrap_or_else(|| {
            json!({
                "mode": "none",
                "nodes": selected.len(),
                "limited": selected.len() <= 1,
            })
        }),
        query,
        keywords,
        node_ids,
        compact_text: compact,
        nodes: selected,
        token_estimate,
    }
}
