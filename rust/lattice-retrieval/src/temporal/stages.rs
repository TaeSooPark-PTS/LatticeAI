use std::cmp::Ordering;

use lattice_core::pytext::round6;
use lattice_core::temporal_state::node_temporal_states;
use lattice_core::{parse_iso, CoreError};
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::plan::TemporalPlan;
use super::prefilter::{explicit_validity, Validity};
use super::SortBy;
use crate::bm25::KnowledgeLifecycleStatus;
use crate::shape::parent_node_id;

fn score_of(item: &Value) -> f64 {
    item.get("score").and_then(Value::as_f64).unwrap_or(0.0)
}

fn set_score(item: &mut Value, score: f64) {
    if let Some(object) = item.as_object_mut() {
        object.insert("score".into(), json!(round6(score)));
    }
}

fn object_of(item: &Value) -> Map<String, Value> {
    item.as_object().cloned().unwrap_or_default()
}

fn owner_id(item: &Value) -> String {
    parent_node_id(&object_of(item))
}

fn own_id(item: &Value) -> String {
    item.get("node_id")
        .or_else(|| item.get("id"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn updated_epoch(item: &Value) -> Option<f64> {
    parse_iso(item.get("updated_at").and_then(Value::as_str))
}

fn by_score_desc(matches: &mut [Value]) {
    matches.sort_by(|a, b| {
        score_of(b)
            .partial_cmp(&score_of(a))
            .unwrap_or(Ordering::Equal)
    });
}

fn renumber(matches: &mut [Value]) {
    for (index, item) in matches.iter_mut().enumerate() {
        if let Some(object) = item.as_object_mut() {
            if object.contains_key("rank") {
                object.insert("rank".into(), json!(index + 1));
            }
        }
    }
}

fn recency_class_of(report: &Value) -> bool {
    report.get("query_class").and_then(Value::as_str) == Some("recency")
}

impl TemporalPlan {
    fn passes_gate(&self, item: &Value, max_score: f64) -> bool {
        let promoted = item
            .get("rerank_promoted")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let score = score_of(item);
        promoted || (score > 0.0 && score + 1e-12 >= self.ranking.gate.min_ratio * max_score)
    }

    fn gated(&self, matches: &[Value], max_score: f64) -> usize {
        matches
            .iter()
            .filter(|item| self.passes_gate(item, max_score))
            .count()
    }

    fn in_window(&self, item: &Value) -> bool {
        let Some(window) = self.window.as_ref() else {
            return true;
        };
        let owner = owner_id(item);
        let ids = self.window_ids.as_ref();
        ids.is_some_and(|ids| ids.contains(&owner))
            || (owner == own_id(item) && updated_epoch(item).is_some_and(|e| window.contains(e)))
    }

    pub fn pre_rerank(
        &mut self,
        conn: &Connection,
        matches: Vec<Value>,
        decay_in_engine: bool,
        recency_class: bool,
    ) -> Result<Vec<Value>, CoreError> {
        let ids: Vec<String> = matches.iter().map(owner_id).collect();
        let as_of_epoch = self.as_of_epoch();
        let states = node_temporal_states(conn, &ids, as_of_epoch.unwrap_or(self.now_secs))?;
        let validity = match as_of_epoch {
            Some(_) => explicit_validity(conn, &ids)?,
            None => Default::default(),
        };
        let penalty = KnowledgeLifecycleStatus::Superseded.multiplier();
        let mut annotated: Vec<(Value, bool)> = Vec::with_capacity(matches.len());
        for mut item in matches {
            let owner = owner_id(&item);
            let state = states.get(&owner).cloned().unwrap_or_default();
            let bounds = validity.get(&owner).copied().unwrap_or_default();
            let superseded = match (as_of_epoch, bounds.until) {
                (Some(at), Some(until)) => until <= at,
                _ => state.is_superseded(),
            };
            if !decay_in_engine {
                let multiplier = self.ranking.decay.multiplier(
                    item.get("updated_at").and_then(Value::as_str),
                    self.now_secs,
                    recency_class,
                );
                let decayed = score_of(&item) * multiplier;
                set_score(&mut item, decayed);
                if let Some(object) = item.as_object_mut() {
                    object.insert("recency_decay".into(), json!(multiplier));
                }
            }
            if let Some(object) = item.as_object_mut() {
                let status = match (as_of_epoch, superseded) {
                    (Some(_), true) => "superseded",
                    (Some(_), false) => "active",
                    _ if state.status.is_empty() => "active",
                    _ => state.status,
                };
                object.insert("status".into(), json!(status));
                let mut temporal = state.as_json();
                if let Some(block) = temporal.as_object_mut() {
                    block.insert("status".into(), json!(status));
                }
                object.insert("temporal".into(), temporal);
            }
            if as_of_epoch.is_some() && !self.valid_at_as_of(bounds) {
                if let Some(object) = item.as_object_mut() {
                    object.insert("as_of_invalid".into(), json!(true));
                }
            }
            annotated.push((item, superseded));
        }
        let superseded_total = annotated.iter().filter(|(_, sup)| *sup).count();
        let all_superseded = superseded_total > 0 && superseded_total == annotated.len();
        let exclude = !self.historical && !all_superseded;
        let mut kept: Vec<Value> = Vec::with_capacity(annotated.len());
        for (mut item, superseded) in annotated {
            if superseded {
                if exclude {
                    continue;
                }
                let penalised = score_of(&item) * penalty;
                set_score(&mut item, penalised);
            }
            kept.push(item);
        }
        self.report.insert(
            "superseded".into(),
            json!({
                "policy": if exclude { "exclude" } else { "penalize" },
                "penalty": penalty,
                "found": superseded_total,
                "excluded": if exclude { superseded_total } else { 0 },
                "fallback": if all_superseded && !self.historical { Some("all_candidates_superseded") } else { None },
            }),
        );
        by_score_desc(&mut kept);
        let kept = self.restrict_to_validity(kept);
        Ok(self.restrict_to_window(kept))
    }

    fn valid_at_as_of(&self, bounds: Validity) -> bool {
        let Some(span) = self.as_of.as_ref() else {
            return true;
        };
        let starts_in_time = match (bounds.from, span.until) {
            (Some(from), Some(end)) => from < end,
            _ => true,
        };
        let still_valid = match (bounds.until, span.since) {
            (Some(until), Some(start)) => until > start,
            _ => true,
        };
        starts_in_time && still_valid
    }

    fn restrict_to_validity(&mut self, matches: Vec<Value>) -> Vec<Value> {
        if self.as_of.is_none() {
            return matches;
        }
        let invalid =
            |item: &Value| item.get("as_of_invalid").and_then(Value::as_bool) == Some(true);
        let candidates = matches.len();
        let valid = matches.iter().filter(|item| !invalid(item)).count();
        let fallback = (valid == 0 && candidates > 0).then_some("no_candidates_valid_at_as_of");
        self.report.insert(
            "validity".into(),
            json!({
                "applied": fallback.is_none(),
                "rule": "valid_from < as_of_until AND valid_to > as_of_since",
                "candidates": candidates,
                "valid": valid,
                "fallback": fallback,
            }),
        );
        let keep_all = fallback.is_some();
        matches
            .into_iter()
            .filter(|item| keep_all || !invalid(item))
            .map(|mut item| {
                if let Some(object) = item.as_object_mut() {
                    object.remove("as_of_invalid");
                }
                item
            })
            .collect()
    }

    fn restrict_to_window(&mut self, matches: Vec<Value>) -> Vec<Value> {
        if self.window.is_none() {
            self.report.insert(
                "prefilter".into(),
                json!({"applied": false, "reason": "no_time_intent"}),
            );
            return matches;
        }
        let candidates = matches.len();
        let max_score = matches.iter().map(score_of).fold(0.0, f64::max);
        let restricted: Vec<Value> = matches
            .iter()
            .filter(|item| self.in_window(item))
            .cloned()
            .collect();
        let nodes_in_window = self.window_ids.as_ref().map_or(0, |ids| ids.len());
        let fallback = if nodes_in_window == 0 {
            Some("no_documents_in_window")
        } else if self.gated(&restricted, max_score) == 0 {
            Some("no_relevant_matches_in_window")
        } else {
            None
        };
        self.report.insert(
            "prefilter".into(),
            json!({
                "applied": fallback.is_none(),
                "column": "updated_at",
                "candidates": candidates,
                "in_window": restricted.len(),
                "fallback": fallback,
            }),
        );
        if fallback.is_some() {
            matches
        } else {
            restricted
        }
    }

    pub fn finalize(&mut self, mut matches: Vec<Value>, limit: usize) -> Vec<Value> {
        let limit = limit.max(1);
        let candidates = matches.len();
        if self.sort == SortBy::Newest {
            let max_score = matches.iter().map(score_of).fold(0.0, f64::max);
            let cap = self.ranking.gate.top_k.min(limit);
            let mut relevant: Vec<Value> = matches
                .into_iter()
                .filter(|item| self.passes_gate(item, max_score))
                .take(cap)
                .collect();
            relevant.sort_by(|a, b| match (updated_epoch(a), updated_epoch(b)) {
                (Some(left), Some(right)) => right.partial_cmp(&left).unwrap_or(Ordering::Equal),
                (Some(_), None) => Ordering::Less,
                (None, Some(_)) => Ordering::Greater,
                (None, None) => Ordering::Equal,
            });
            matches = relevant;
        } else {
            matches.truncate(limit);
        }
        renumber(&mut matches);
        self.report.insert(
            "sort".into(),
            json!({
                "requested": self.ranking.sort_by.as_str(),
                "applied": self.sort.as_str(),
                "order": if self.sort == SortBy::Newest { "rerank_then_relevance_gate_then_updated_at_desc" } else { "rerank_then_relevance" },
                "top_k": self.ranking.gate.top_k,
                "min_relevance_ratio": self.ranking.gate.min_ratio,
                "candidates": candidates,
                "returned": matches.len(),
            }),
        );
        matches
    }

    pub fn apply_pre_rerank(
        &mut self,
        conn: &Connection,
        report: &mut Value,
        decay_in_engine: bool,
    ) -> Result<(), CoreError> {
        let recency_class = recency_class_of(report);
        let matches = take_matches(report);
        let processed = self.pre_rerank(conn, matches, decay_in_engine, recency_class)?;
        put_matches(report, processed);
        Ok(())
    }

    pub fn apply_finalize(&mut self, report: &mut Value, limit: usize) {
        let recency_class = recency_class_of(report);
        let matches = take_matches(report);
        let finished = self.finalize(matches, limit);
        put_matches(report, finished);
        if let Some(object) = report.as_object_mut() {
            if object.contains_key("query") {
                object.insert("query".into(), json!(self.original_query));
            }
            object.insert("sort_by".into(), json!(self.sort.as_str()));
            object.insert("temporal".into(), self.report_json(recency_class));
        }
    }
}

fn take_matches(report: &mut Value) -> Vec<Value> {
    report
        .get_mut("matches")
        .and_then(Value::as_array_mut)
        .map(std::mem::take)
        .unwrap_or_default()
}

fn put_matches(report: &mut Value, matches: Vec<Value>) {
    if let Some(object) = report.as_object_mut() {
        if object.contains_key("results") {
            object.insert("results".into(), Value::Array(matches.clone()));
        }
        object.insert("matches".into(), Value::Array(matches));
    }
}
