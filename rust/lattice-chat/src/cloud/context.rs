use std::collections::BTreeSet;
use std::sync::OnceLock;

use fancy_regex::Regex;
use lattice_core::embeddings::LocalEmbeddingModel;
use lattice_retrieval::hybrid::HybridOptions;
use lattice_retrieval::shape::context_quality_signal;
use lattice_retrieval::temporal::ranked_hybrid_search;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::boundary::{blocked_reason, blocked_reason_for_policy, HybridPolicy, NetworkMode};
use crate::pyvalue::{field, text as py_text};

const PREFERRED_TYPES: [&str; 8] = [
    "Decision", "Concept", "Task", "Document", "File", "CodeFile", "Person", "Feature",
];

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

pub fn rough_token_estimate(text: &str) -> i64 {
    if text.is_empty() {
        return 0;
    }
    (text.chars().count().div_ceil(4) as i64).max(1)
}

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

pub fn build_minimal_context(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    message: &str,
    mode: NetworkMode,
    top_k: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
    now_secs: f64,
) -> MinimalContext {
    build_minimal_context_inner(
        conn,
        model,
        message,
        mode,
        top_k,
        allowed_workspaces,
        now_secs,
        None,
        None,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn build_minimal_context_with_policy(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    message: &str,
    mode: NetworkMode,
    top_k: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
    now_secs: f64,
    policy: &HybridPolicy,
) -> MinimalContext {
    build_minimal_context_inner(
        conn,
        model,
        message,
        mode,
        top_k,
        allowed_workspaces,
        now_secs,
        Some(policy),
        None,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn build_minimal_context_with_policy_and_query(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    message: &str,
    mode: NetworkMode,
    top_k: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
    now_secs: f64,
    policy: &HybridPolicy,
    query_embedding: Option<lattice_retrieval::QueryVectorOverride>,
    semantic_degraded: Option<String>,
) -> MinimalContext {
    build_minimal_context_inner(
        conn,
        model,
        message,
        mode,
        top_k,
        allowed_workspaces,
        now_secs,
        Some(policy),
        query_embedding,
        semantic_degraded,
    )
}

#[allow(clippy::too_many_arguments)]
fn build_minimal_context_inner(
    conn: &Connection,
    model: &LocalEmbeddingModel,
    message: &str,
    _mode: NetworkMode,
    top_k: i64,
    allowed_workspaces: Option<&BTreeSet<String>>,
    now_secs: f64,
    policy: Option<&HybridPolicy>,
    query_embedding: Option<lattice_retrieval::QueryVectorOverride>,
    semantic_degraded: Option<String>,
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
        query_embedding,
        semantic_degraded,
        ..Default::default()
    };
    let ranking = lattice_retrieval::temporal::TemporalRanking::from_env();
    let (matches, quality) = match ranked_hybrid_search(conn, model, &query, &options, &ranking) {
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
        .filter(|node| match policy {
            Some(resolved) => blocked_reason_for_policy(node, resolved).is_none(),
            None => blocked_reason(node).is_none(),
        })
        .collect();
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
        lines.push(
            format!("- [{node_type}] {title}: {summary}")
                .trim_end_matches([':', ' '])
                .to_string(),
        );
    }

    let compact = lattice_core::redact_pii_and_secrets(&lines.join("\n"));
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
