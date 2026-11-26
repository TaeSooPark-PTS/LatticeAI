use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::intent::parse_time_intent;
use super::plan::TemporalPlan;
use super::{RecencyDecay, TemporalRanking};

#[derive(Debug, Clone)]
pub struct EngineCall {
    pub search_query: String,
    pub fetch_limit: i64,
    pub include_historical: bool,
    pub decay: RecencyDecay,
}

pub fn search_text_for(query: &str, now_secs: f64, ranking: &TemporalRanking) -> String {
    if ranking.parse_intent {
        parse_time_intent(query, now_secs).search_query
    } else {
        query.trim().to_string()
    }
}

pub fn prepare_ranked<F>(
    conn: &Connection,
    query: &str,
    now_secs: f64,
    ranking: &TemporalRanking,
    limit: i64,
    decay_in_engine: bool,
    engine: F,
) -> Result<(Value, TemporalPlan), CoreError>
where
    F: FnOnce(&EngineCall) -> Result<Value, CoreError>,
{
    let mut plan = TemporalPlan::resolve(conn, query, now_secs, ranking)?;
    let call = EngineCall {
        search_query: plan.search_query().to_string(),
        fetch_limit: plan.fetch_limit(limit),
        include_historical: plan.include_historical(),
        decay: ranking.decay,
    };
    let mut report = engine(&call)?;
    plan.apply_pre_rerank(conn, &mut report, decay_in_engine)?;
    Ok((report, plan))
}

pub fn ranked_search<F>(
    conn: &Connection,
    query: &str,
    now_secs: f64,
    ranking: &TemporalRanking,
    limit: i64,
    decay_in_engine: bool,
    engine: F,
) -> Result<Value, CoreError>
where
    F: FnOnce(&EngineCall) -> Result<Value, CoreError>,
{
    let (mut report, mut plan) = prepare_ranked(
        conn,
        query,
        now_secs,
        ranking,
        limit,
        decay_in_engine,
        engine,
    )?;
    plan.apply_finalize(&mut report, limit.max(1) as usize);
    Ok(report)
}

pub fn ranked_hybrid_search(
    conn: &Connection,
    model: &lattice_core::LocalEmbeddingModel,
    query: &str,
    options: &crate::hybrid::HybridOptions,
    ranking: &TemporalRanking,
) -> Result<Value, CoreError> {
    ranked_search(
        conn,
        query,
        options.now_secs,
        ranking,
        options.top_k,
        true,
        |call| {
            let widened = crate::hybrid::HybridOptions {
                top_k: call.fetch_limit,
                recency: Some(call.decay),
                ..options.clone()
            };
            crate::hybrid::hybrid_search(conn, model, &call.search_query, &widened)
        },
    )
}
