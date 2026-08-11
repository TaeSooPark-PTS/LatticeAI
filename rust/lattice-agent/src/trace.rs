//! `LoopTrace` — the machine-readable side channel of one run.
//!
//! A 1:1 port of `latticeai.core.agent_trace.LoopTrace`. The transcript mixes
//! model output, tool results and control decisions into one list; this is the
//! typed stream you can actually count, and `summary` is what the API response
//! and the weak-model harness read.
//!
//! One deliberate difference, recorded because it is observable: the `at` stamp
//! is **UTC** with an explicit `+00:00` offset, where Python stamps the
//! configured local zone. It is telemetry — no gate, verdict or transcript
//! comparison reads it, and the parity goldens strip it — so the port takes the
//! stamp it can produce without a timezone dependency rather than an
//! approximation of a local one.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::pystr::char_slice;

const MAX_EVENTS: usize = 500;

/// Typed event stream + summary counters for one agent run.
#[derive(Debug, Clone, Default)]
pub struct LoopTrace {
    pub events: Vec<Value>,
    pub truncated: u64,
    /// Fixed stamp for tests and fixtures; `None` reads the wall clock.
    pinned: Option<String>,
}

impl LoopTrace {
    /// A trace whose every event carries `stamp`, for deterministic output.
    pub fn pinned(stamp: impl Into<String>) -> Self {
        Self {
            pinned: Some(stamp.into()),
            ..Self::default()
        }
    }

    fn now(&self) -> String {
        self.pinned.clone().unwrap_or_else(utc_iso_now)
    }

    /// `record`: append one event unless the cap is reached, in which case the
    /// *count* of what was dropped is kept instead of the event.
    pub fn record(&mut self, phase: &str, kind: &str, details: &[(&str, Value)]) {
        if self.events.len() >= MAX_EVENTS {
            self.truncated += 1;
            return;
        }
        let mut event = Map::new();
        event.insert("phase".into(), json!(phase));
        event.insert("kind".into(), json!(kind));
        event.insert("at".into(), json!(self.now()));
        for (key, value) in details {
            // Python drops `None` details rather than recording a null.
            if !value.is_null() {
                event.insert((*key).into(), value.clone());
            }
        }
        self.events.push(Value::Object(event));
    }

    pub fn llm_call(&mut self, phase: &str, model: Option<&str>) {
        self.record(phase, "llm_call", &[("model", json!(model))]);
    }

    pub fn parse_error(&mut self, phase: &str, error: &str, recovered: bool) {
        self.record(
            phase,
            "parse_error",
            &[
                ("error", json!(char_slice(error, 200))),
                ("recovered", json!(recovered)),
            ],
        );
    }

    pub fn repair(&mut self, phase: &str, repairs: &[String]) {
        if repairs.is_empty() {
            return;
        }
        self.record(phase, "repair", &[("repairs", json!(repairs))]);
    }

    pub fn correction(&mut self, phase: &str, hint: &str) {
        self.record(
            phase,
            "correction",
            &[("hint", json!(char_slice(hint, 200)))],
        );
    }

    pub fn tool(&mut self, phase: &str, name: &str, outcome: &str, risk: Option<&str>) {
        self.record(
            phase,
            "tool",
            &[
                ("name", json!(name)),
                ("outcome", json!(outcome)),
                ("risk", json!(risk)),
            ],
        );
    }

    pub fn decision(&mut self, phase: &str, decision: &str, details: &[(&str, Value)]) {
        let mut all: Vec<(&str, Value)> = vec![("decision", json!(decision))];
        all.extend_from_slice(details);
        self.record(phase, "decision", &all);
    }

    pub fn retry(&mut self, phase: &str, attempt: u32) {
        self.record(phase, "retry", &[("attempt", json!(attempt))]);
    }

    /// The counters the API response and the eval harness consume.
    pub fn summary(&self) -> Value {
        let mut counts: BTreeMap<String, u64> = BTreeMap::new();
        let mut tool_outcomes: BTreeMap<String, u64> = BTreeMap::new();
        let mut repairs: BTreeMap<String, u64> = BTreeMap::new();
        let mut parse_errors = 0u64;
        let mut parse_recovered = 0u64;
        for event in &self.events {
            let kind = event["kind"].as_str().unwrap_or_default().to_string();
            *counts.entry(kind.clone()).or_default() += 1;
            match kind.as_str() {
                "tool" => {
                    let outcome = match event.get("outcome").and_then(Value::as_str) {
                        Some(outcome) if !outcome.is_empty() => outcome,
                        _ => "unknown",
                    };
                    *tool_outcomes.entry(outcome.into()).or_default() += 1;
                }
                "parse_error" => {
                    parse_errors += 1;
                    if event.get("recovered") == Some(&json!(true)) {
                        parse_recovered += 1;
                    }
                }
                "repair" => {
                    for name in event["repairs"].as_array().cloned().unwrap_or_default() {
                        *repairs
                            .entry(name.as_str().unwrap_or_default().into())
                            .or_default() += 1;
                    }
                }
                _ => {}
            }
        }
        let count_of = |kind: &str| counts.get(kind).copied().unwrap_or(0);
        json!({
            "events": self.events.len(),
            "truncated_events": self.truncated,
            "kind_counts": counts,
            "llm_calls": count_of("llm_call"),
            "parse_errors": parse_errors,
            "parse_recovered": parse_recovered,
            "corrections": count_of("correction"),
            "retries": count_of("retry"),
            "tool_outcomes": tool_outcomes,
            "repairs": repairs,
        })
    }
}

/// `datetime.now(UTC).isoformat()` without a calendar dependency.
fn utc_iso_now() -> String {
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let stamp = utc_iso_seconds(since_epoch.as_secs() as i64);
    format!("{stamp}.{:06}+00:00", since_epoch.subsec_micros())
}

/// Now, in UTC seconds since the epoch.
pub fn epoch_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// `isoformat(timespec="seconds")` for a UTC epoch second, **without** the
/// offset — callers append the one they mean.
pub fn utc_iso_seconds(epoch_secs: i64) -> String {
    let (year, month, day) = civil_from_days(epoch_secs.div_euclid(86_400));
    let time_of_day = epoch_secs.rem_euclid(86_400);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        time_of_day / 3600,
        (time_of_day % 3600) / 60,
        time_of_day % 60,
    )
}

/// Howard Hinnant's `civil_from_days`: days since 1970-01-01 → `(y, m, d)`.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    (year + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_carry_phase_kind_and_a_stamp_and_drop_nulls() {
        let mut trace = LoopTrace::pinned("2026-08-11T00:00:00+00:00");
        trace.llm_call("plan", Some("m1"));
        trace.llm_call("execute", None);
        assert_eq!(
            trace.events[0],
            json!({"phase": "plan", "kind": "llm_call", "at": "2026-08-11T00:00:00+00:00",
                   "model": "m1"})
        );
        assert!(
            trace.events[1].get("model").is_none(),
            "None is not recorded"
        );
    }

    #[test]
    fn an_empty_repair_list_records_nothing_at_all() {
        let mut trace = LoopTrace::pinned("t");
        trace.repair("execute", &[]);
        assert!(trace.events.is_empty());
        trace.repair("execute", &["fence".into()]);
        assert_eq!(trace.events.len(), 1);
    }

    #[test]
    fn the_event_cap_counts_what_it_drops() {
        let mut trace = LoopTrace::pinned("t");
        for _ in 0..(MAX_EVENTS + 7) {
            trace.retry("verify", 1);
        }
        assert_eq!(trace.events.len(), MAX_EVENTS);
        assert_eq!(trace.truncated, 7);
        assert_eq!(trace.summary()["truncated_events"], 7);
    }

    #[test]
    fn the_summary_reduces_to_the_counters_the_api_returns() {
        let mut trace = LoopTrace::pinned("t");
        trace.llm_call("plan", None);
        trace.repair("plan", &["fence".into(), "slice".into()]);
        trace.repair("execute", &["fence".into()]);
        trace.parse_error("execute", "bad", true);
        trace.parse_error("execute", "bad again", false);
        trace.correction("execute", "reply with JSON");
        trace.retry("verify", 1);
        trace.tool("execute", "write_file", "ok", Some("medium"));
        trace.tool("execute", "write_file", "blocked_destructive", None);
        let summary = trace.summary();
        assert_eq!(summary["llm_calls"], 1);
        assert_eq!(summary["parse_errors"], 2);
        assert_eq!(summary["parse_recovered"], 1);
        assert_eq!(summary["corrections"], 1);
        assert_eq!(summary["retries"], 1);
        assert_eq!(summary["repairs"], json!({"fence": 2, "slice": 1}));
        assert_eq!(
            summary["tool_outcomes"],
            json!({"ok": 1, "blocked_destructive": 1})
        );
        assert_eq!(summary["events"], 9);
    }

    #[test]
    fn long_strings_are_capped_where_python_caps_them() {
        let mut trace = LoopTrace::pinned("t");
        trace.parse_error("execute", &"가".repeat(400), false);
        trace.correction("execute", &"x".repeat(400));
        assert_eq!(
            trace.events[0]["error"]
                .as_str()
                .expect("e")
                .chars()
                .count(),
            200
        );
        assert_eq!(
            trace.events[1]["hint"].as_str().expect("h").chars().count(),
            200
        );
    }

    #[test]
    fn the_civil_calendar_matches_known_dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1), "a leap year start");
        assert_eq!(civil_from_days(19_782), (2024, 2, 29), "the leap day");
        assert_eq!(civil_from_days(20_675), (2026, 8, 10));
    }

    #[test]
    fn a_fixed_epoch_formats_the_way_isoformat_does() {
        assert_eq!(utc_iso_seconds(0), "1970-01-01T00:00:00");
        assert_eq!(utc_iso_seconds(1_786_000_000), "2026-08-06T07:06:40");
        assert!(epoch_now() > 1_700_000_000.0);
    }

    #[test]
    fn the_wall_clock_stamp_is_iso_8601_utc() {
        let stamp = utc_iso_now();
        assert_eq!(stamp.len(), "2026-08-11T00:00:00.000000+00:00".len());
        assert!(stamp.ends_with("+00:00"), "{stamp}");
        assert_eq!(&stamp[4..5], "-");
        assert_eq!(&stamp[10..11], "T");
    }
}
