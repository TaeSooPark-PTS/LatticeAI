use std::time::{Duration, Instant};

use lattice_core::pytext::{round6, truncate_chars};
use lattice_core::worker::{WorkerRetryPolicy, WorkerSeamClient};
use serde_json::{json, Map, Value};

pub const RERANK_PATH: &str = "/worker/rerank";
pub const RERANK_ENV: &str = "LATTICEAI_RERANK";
pub const RERANK_TIMEOUT_ENV: &str = "LATTICEAI_RERANK_TIMEOUT_MS";
pub const RERANK_CANDIDATES_ENV: &str = "LATTICEAI_RERANK_CANDIDATES";
pub const RERANK_PROMOTE_ENV: &str = "LATTICEAI_RERANK_PROMOTE";
pub const DEFAULT_RERANK_TIMEOUT_MS: u64 = 2_000;
pub const DEFAULT_RERANK_CANDIDATES: usize = 20;
pub const DEFAULT_RERANK_PROMOTE: usize = 5;
pub const MAX_RERANK_CANDIDATES: usize = 64;
pub const RERANK_DOCUMENT_CHARS: usize = 1_200;
pub const STRATEGY_CROSS_ENCODER: &str = "cross_encoder";
pub const STRATEGY_RRF: &str = "rrf";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RerankConfig {
    pub enabled: bool,
    pub timeout: Duration,
    pub candidates: usize,
    pub promote: usize,
}

impl Default for RerankConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            timeout: Duration::from_millis(DEFAULT_RERANK_TIMEOUT_MS),
            candidates: DEFAULT_RERANK_CANDIDATES,
            promote: DEFAULT_RERANK_PROMOTE,
        }
    }
}

fn parse_switch(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "0" | "false" | "no" | "off" | "disabled" => Some(false),
        "1" | "true" | "yes" | "on" | "auto" | "enabled" => Some(true),
        _ => None,
    }
}

impl RerankConfig {
    pub fn from_env() -> Self {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Self {
        let defaults = Self::default();
        let number = |key: &str| {
            lookup(key)
                .and_then(|raw| raw.trim().parse::<u64>().ok())
                .filter(|value| *value > 0)
        };
        let enabled = lookup(RERANK_ENV)
            .and_then(|raw| parse_switch(&raw))
            .unwrap_or(defaults.enabled);
        let timeout_ms = number(RERANK_TIMEOUT_ENV)
            .unwrap_or(DEFAULT_RERANK_TIMEOUT_MS)
            .clamp(50, 15_000);
        let candidates = number(RERANK_CANDIDATES_ENV)
            .map(|value| value as usize)
            .unwrap_or(defaults.candidates)
            .clamp(2, MAX_RERANK_CANDIDATES);
        let promote = number(RERANK_PROMOTE_ENV)
            .map(|value| value as usize)
            .unwrap_or(defaults.promote)
            .clamp(1, candidates);
        Self {
            enabled,
            timeout: Duration::from_millis(timeout_ms),
            candidates,
            promote,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RerankOutcome {
    pub applied: bool,
    pub strategy: &'static str,
    pub reason: Option<String>,
    pub model_id: Option<String>,
    pub candidates: usize,
    pub promoted: Vec<String>,
    pub elapsed_ms: u64,
}

impl RerankOutcome {
    fn fallback(reason: impl Into<String>, candidates: usize, started: Instant) -> Self {
        Self {
            applied: false,
            strategy: STRATEGY_RRF,
            reason: Some(reason.into()),
            model_id: None,
            candidates,
            promoted: Vec::new(),
            elapsed_ms: started.elapsed().as_millis() as u64,
        }
    }

    pub fn as_json(&self) -> Value {
        let mut out = Map::new();
        out.insert("applied".into(), json!(self.applied));
        out.insert("strategy".into(), json!(self.strategy));
        out.insert("reason".into(), json!(self.reason));
        out.insert("model_id".into(), json!(self.model_id));
        out.insert("candidates".into(), json!(self.candidates));
        out.insert("promoted".into(), json!(self.promoted));
        out.insert("elapsed_ms".into(), json!(self.elapsed_ms));
        Value::Object(out)
    }
}

fn text_field(item: &Value, key: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
        .to_string()
}

pub fn candidate_text(item: &Value) -> String {
    let title = text_field(item, "title");
    let body = ["summary", "snippet", "text", "content"]
        .iter()
        .map(|key| text_field(item, key))
        .find(|value| !value.is_empty())
        .unwrap_or_default();
    let joined = match (title.is_empty(), body.is_empty()) {
        (false, false) => format!("{title}: {body}"),
        (false, true) => title,
        (true, _) => body,
    };
    truncate_chars(&joined, RERANK_DOCUMENT_CHARS)
}

fn match_id(item: &Value) -> String {
    item.get("id")
        .or_else(|| item.get("node_id"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

pub fn apply_rerank_scores(
    matches: &mut Vec<Value>,
    scores: &[f64],
    promote: usize,
) -> Vec<String> {
    let rescored = scores.len().min(matches.len());
    if rescored == 0 || promote == 0 {
        return Vec::new();
    }
    let mut order: Vec<usize> = (0..rescored).collect();
    order.sort_by(|left, right| {
        scores[*right]
            .partial_cmp(&scores[*left])
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(left.cmp(right))
    });
    let promoted_indices: Vec<usize> = order.into_iter().take(promote.min(rescored)).collect();
    for (index, item) in matches.iter_mut().enumerate().take(rescored) {
        if let Some(object) = item.as_object_mut() {
            object.insert("rerank_score".into(), json!(round6(scores[index])));
            object.insert(
                "rerank_promoted".into(),
                json!(promoted_indices.contains(&index)),
            );
        }
    }
    let original = std::mem::take(matches);
    let mut promoted_ids = Vec::with_capacity(promoted_indices.len());
    for index in &promoted_indices {
        promoted_ids.push(match_id(&original[*index]));
        matches.push(original[*index].clone());
    }
    for (index, item) in original.into_iter().enumerate() {
        if !promoted_indices.contains(&index) {
            matches.push(item);
        }
    }
    promoted_ids
}

fn parse_scores(payload: &Value, expected: usize) -> Result<Vec<f64>, String> {
    let rows = payload
        .get("scores")
        .and_then(Value::as_array)
        .ok_or_else(|| "rerank response missing scores".to_string())?;
    if rows.len() != expected {
        return Err(format!(
            "rerank returned {} scores for {expected} candidates",
            rows.len()
        ));
    }
    rows.iter()
        .map(|value| {
            value
                .as_f64()
                .filter(|number| number.is_finite())
                .ok_or_else(|| "rerank score is not a finite number".to_string())
        })
        .collect()
}

pub async fn rerank_matches(
    seam: Option<&WorkerSeamClient>,
    query: &str,
    matches: &mut Vec<Value>,
    config: &RerankConfig,
) -> RerankOutcome {
    let started = Instant::now();
    let candidates = matches.len().min(config.candidates);
    if !config.enabled {
        return RerankOutcome::fallback("rerank_disabled", candidates, started);
    }
    let query = query.trim();
    if query.is_empty() || candidates < 2 {
        return RerankOutcome::fallback("rerank_too_few_candidates", candidates, started);
    }
    let Some(seam) = seam else {
        return RerankOutcome::fallback("rerank_seam_unavailable", candidates, started);
    };
    let documents: Vec<String> = matches[..candidates].iter().map(candidate_text).collect();
    if documents.iter().all(String::is_empty) {
        return RerankOutcome::fallback("rerank_candidates_without_text", candidates, started);
    }
    let client = seam.clone().with_timeout(config.timeout);
    let body = json!({"query": query, "documents": documents});
    let policy = WorkerRetryPolicy {
        max_retries: 0,
        ..WorkerRetryPolicy::default()
    };
    let call = client.post_idempotent_compute_with_policy(RERANK_PATH, &body, policy);
    let payload = match tokio::time::timeout(config.timeout, call).await {
        Err(_) => {
            return RerankOutcome::fallback(
                format!("rerank_timeout_after_{}ms", config.timeout.as_millis()),
                candidates,
                started,
            )
        }
        Ok(Err(error)) => {
            return RerankOutcome::fallback(format!("rerank_failed: {error}"), candidates, started)
        }
        Ok(Ok(payload)) => payload,
    };
    let scores = match parse_scores(&payload, candidates) {
        Ok(scores) => scores,
        Err(reason) => {
            return RerankOutcome::fallback(
                format!("rerank_invalid: {reason}"),
                candidates,
                started,
            )
        }
    };
    let promoted = apply_rerank_scores(matches, &scores, config.promote);
    RerankOutcome {
        applied: true,
        strategy: STRATEGY_CROSS_ENCODER,
        reason: None,
        model_id: payload
            .get("model_id")
            .and_then(Value::as_str)
            .map(str::to_string),
        candidates,
        promoted,
        elapsed_ms: started.elapsed().as_millis() as u64,
    }
}

pub async fn rerank_report(
    seam: Option<&WorkerSeamClient>,
    query: &str,
    report: &mut Value,
    config: &RerankConfig,
) -> RerankOutcome {
    let taken = report
        .get_mut("matches")
        .and_then(Value::as_array_mut)
        .map(std::mem::take);
    let had_matches = taken.is_some();
    let mut matches = taken.unwrap_or_default();
    let outcome = rerank_matches(seam, query, &mut matches, config).await;
    if let Some(object) = report.as_object_mut() {
        if had_matches {
            object.insert("matches".into(), Value::Array(matches));
        }
        object.insert("rerank".into(), outcome.as_json());
    }
    outcome
}

#[cfg(test)]
#[path = "rerank_tests.rs"]
mod tests;
