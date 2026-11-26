use std::collections::HashSet;

use lattice_core::{parse_iso, CoreError};
use rusqlite::Connection;
use serde_json::{json, Value};

use super::plan::TemporalPlan;
use super::{SortBy, TemporalRanking};
use crate::shape::parent_node_id;

pub const SLOT_KEY: &str = "slot";

#[derive(Debug)]
pub struct RankedCandidates {
    pub matches: Vec<Value>,
    pub sort: SortBy,
    pub report: Value,
}

pub fn candidate(slot: usize, id: &str, score: f64, updated_at: Option<&str>) -> Value {
    json!({
        SLOT_KEY: slot,
        "id": id,
        "node_id": id,
        "score": score,
        "updated_at": updated_at,
    })
}

pub fn slot_of(item: &Value) -> Option<usize> {
    item.get(SLOT_KEY)
        .and_then(Value::as_u64)
        .map(|slot| slot as usize)
}

pub fn rank_candidates(
    conn: Option<&Connection>,
    query: &str,
    now_secs: f64,
    ranking: &TemporalRanking,
    candidates: Vec<Value>,
    limit: usize,
) -> Result<RankedCandidates, CoreError> {
    let scratch;
    let conn = match conn {
        Some(conn) => conn,
        None => {
            scratch = Connection::open_in_memory()?;
            &scratch
        }
    };
    let mut plan = TemporalPlan::resolve(conn, query, now_secs, ranking)?;
    if let Some(window) = plan.window.as_ref() {
        let inside: HashSet<String> = candidates
            .iter()
            .filter(|item| {
                parse_iso(item.get("updated_at").and_then(Value::as_str))
                    .is_some_and(|epoch| window.contains(epoch))
            })
            .filter_map(|item| item.as_object().map(parent_node_id))
            .collect();
        plan.window_ids
            .get_or_insert_with(HashSet::new)
            .extend(inside);
    }
    let mut report = json!({ "matches": candidates });
    plan.apply_pre_rerank(conn, &mut report, false)?;
    plan.apply_finalize(&mut report, limit.max(1));
    let matches = report
        .get_mut("matches")
        .and_then(Value::as_array_mut)
        .map(std::mem::take)
        .unwrap_or_default();
    Ok(RankedCandidates {
        matches,
        sort: plan.sort,
        report: report.get("temporal").cloned().unwrap_or(Value::Null),
    })
}
