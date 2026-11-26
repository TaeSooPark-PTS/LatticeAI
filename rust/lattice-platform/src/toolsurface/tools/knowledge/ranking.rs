use std::path::Path;
use std::time::UNIX_EPOCH;

use axum::response::Response;
use lattice_core::graph_write::clock::local_iso_for;
use lattice_retrieval::routes::naive_local_now;
use lattice_retrieval::temporal::external::{candidate, slot_of};
use lattice_retrieval::temporal::{rank_candidates, search_text_for, TemporalRanking};
use serde_json::{json, Value};

use super::super::tool_err;

pub(super) const CANDIDATE_CAP: usize = 100;

pub(super) fn search_text(query: &str, temporal: Option<&TemporalRanking>) -> String {
    match temporal {
        Some(ranking) => search_text_for(query, naive_local_now(), ranking),
        None => query.to_string(),
    }
}

fn modified_iso(path: &Path) -> Option<String> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    let secs = modified.duration_since(UNIX_EPOCH).ok()?.as_secs();
    local_iso_for(i64::try_from(secs).ok()?)
}

pub(super) fn apply_ranking(
    result: &mut Value,
    mut items: Vec<Value>,
    query: &str,
    temporal: Option<&TemporalRanking>,
    max_results: usize,
) -> Result<(), Response> {
    let Some(ranking) = temporal else {
        items.truncate(max_results);
        result["results"] = Value::Array(items);
        return Ok(());
    };
    let mut candidates = Vec::with_capacity(items.len());
    for (slot, item) in items.iter_mut().enumerate() {
        let updated = item
            .get("path")
            .and_then(Value::as_str)
            .and_then(|path| modified_iso(Path::new(path)));
        item["updated_at"] = json!(updated);
        let id = item
            .get("relative_path")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let score = item.get("score").and_then(Value::as_f64).unwrap_or(1.0);
        candidates.push(candidate(slot, id, score, updated.as_deref()));
    }
    let ranked = rank_candidates(
        None,
        query,
        naive_local_now(),
        ranking,
        candidates,
        max_results,
    )
    .map_err(|_| tool_err("Knowledge search failed."))?;
    let mut slots: Vec<Option<Value>> = items.into_iter().map(Some).collect();
    let ordered: Vec<Value> = ranked
        .matches
        .iter()
        .filter_map(|entry| {
            let mut item = slots.get_mut(slot_of(entry)?)?.take()?;
            item["status"] = entry.get("status").cloned().unwrap_or(Value::Null);
            Some(item)
        })
        .collect();
    result["results"] = Value::Array(ordered);
    result["sort_by"] = json!(ranked.sort.as_str());
    result["temporal"] = ranked.report;
    Ok(())
}
