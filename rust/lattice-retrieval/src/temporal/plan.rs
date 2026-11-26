use std::collections::HashSet;

use lattice_core::{parse_iso, CoreError};
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::intent::{parse_time_intent, TimeIntent, TimeWindow};
use super::prefilter::{version_anchor, window_node_ids};
use super::{SortBy, TemporalRanking, PIPELINE_ORDER};

pub const WIDENED_FETCH_FACTOR: i64 = 3;
pub const MAX_FETCH: i64 = 100;

#[derive(Debug, Clone)]
pub struct TemporalPlan {
    pub original_query: String,
    pub ranking: TemporalRanking,
    pub now_secs: f64,
    pub intent: TimeIntent,
    pub window: Option<TimeWindow>,
    pub window_source: Option<&'static str>,
    pub window_ids: Option<HashSet<String>>,
    pub sort: SortBy,
    pub historical: bool,
    pub report: Map<String, Value>,
}

fn explicit_window(ranking: &TemporalRanking) -> Option<TimeWindow> {
    let since = parse_iso(ranking.since.as_deref());
    let until = parse_iso(ranking.until.as_deref());
    (since.is_some() || until.is_some()).then(|| TimeWindow {
        since,
        until,
        label: "explicit".to_string(),
    })
}

impl TemporalPlan {
    pub fn resolve(
        conn: &Connection,
        query: &str,
        now_secs: f64,
        ranking: &TemporalRanking,
    ) -> Result<Self, CoreError> {
        let intent = if ranking.parse_intent {
            parse_time_intent(query, now_secs)
        } else {
            TimeIntent {
                search_query: query.trim().to_string(),
                ..TimeIntent::default()
            }
        };
        let mut report = Map::new();
        let mut window = explicit_window(ranking);
        let mut window_source = window.as_ref().map(|_| "explicit");
        if window.is_none() {
            if let Some(version) = intent.version.as_deref() {
                match version_anchor(conn, version)? {
                    Some((epoch, node_id)) => {
                        report.insert(
                            "version_anchor".into(),
                            json!({"version": version, "node_id": node_id, "status": "resolved"}),
                        );
                        window = Some(TimeWindow {
                            since: Some(epoch),
                            until: None,
                            label: "since_version".to_string(),
                        });
                        window_source = Some("version");
                    }
                    None => {
                        report.insert(
                            "version_anchor".into(),
                            json!({"version": version, "node_id": null, "status": "not_found"}),
                        );
                    }
                }
            }
        }
        if window.is_none() {
            window = intent.window.clone();
            window_source = window.as_ref().map(|_| "query");
        }
        let window_ids = match window.as_ref() {
            Some(found) => Some(window_node_ids(conn, found)?),
            None => None,
        };
        let sort = match ranking.sort_by {
            SortBy::Auto if intent.has_time_signal() || window.is_some() => SortBy::Newest,
            SortBy::Auto => SortBy::Relevance,
            explicit => explicit,
        };
        let historical = ranking.include_historical
            || intent.historical
            || window
                .as_ref()
                .is_some_and(|found| found.is_closed_before(now_secs));
        Ok(Self {
            original_query: query.trim().to_string(),
            ranking: ranking.clone(),
            now_secs,
            intent,
            window,
            window_source,
            window_ids,
            sort,
            historical,
            report,
        })
    }

    pub fn search_query(&self) -> &str {
        &self.intent.search_query
    }

    pub fn include_historical(&self) -> bool {
        self.historical
    }

    pub fn fetch_limit(&self, limit: i64) -> i64 {
        let limit = limit.clamp(1, MAX_FETCH);
        if self.window.is_some() {
            (limit * WIDENED_FETCH_FACTOR).clamp(limit, MAX_FETCH)
        } else {
            limit
        }
    }

    pub fn report_json(&self, recency_class: bool) -> Value {
        let mut out = self.report.clone();
        out.insert(
            "pipeline".into(),
            json!(PIPELINE_ORDER.iter().collect::<Vec<_>>()),
        );
        out.insert("search_query".into(), json!(self.intent.search_query));
        out.insert(
            "intent".into(),
            json!({
                "phrases": self.intent.phrases,
                "latest": self.intent.latest,
                "historical": self.intent.historical,
                "version": self.intent.version,
            }),
        );
        out.insert(
            "window".into(),
            match self.window.as_ref() {
                Some(found) => {
                    let mut value = found.as_json();
                    if let Some(object) = value.as_object_mut() {
                        object.insert("source".into(), json!(self.window_source));
                        object.insert(
                            "nodes_in_window".into(),
                            json!(self.window_ids.as_ref().map(HashSet::len)),
                        );
                    }
                    value
                }
                None => Value::Null,
            },
        );
        out.insert(
            "include_historical".into(),
            json!(self.ranking.include_historical),
        );
        out.insert(
            "recency_decay".into(),
            self.ranking.decay.as_json(recency_class),
        );
        Value::Object(out)
    }
}
