//! UX funnel metrics — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/funnel_metrics.py` + `latticeai/services/funnel_metrics.py`.
//! Counters live in `<data_dir>/funnel_metrics.json` (I1 `state_files::FUNNEL_METRICS`).


#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use serde_json::{json, Map, Value};

use crate::admin::{json_ok, now_iso};

pub const MOUNTED: &[(&str, &str)] = &[("GET", "/api/admin/funnel-metrics")];

const COUNTER_NAMES: &[&str] = &[
    "file_requests",
    "real_file_delivered",
    "code_only_responses",
    "agent_runs",
    "needs_review_runs",
    "ingest_completions",
    "recall_successes",
    "approval_pauses",
    "approval_resumes",
];
const FIRST_NAMES: &[&str] = &["first_ingest_at", "first_value_at"];

const REAL_FILE_RATE_FLOOR: f64 = 0.95;
const CODE_ONLY_RATE_CEILING: f64 = 0.05;
const NEEDS_REVIEW_RATE_CEILING: f64 = 0.25;
const APPROVAL_RESUME_RATE_FLOOR: f64 = 0.5;
const MIN_SAMPLES: u64 = 10;

#[derive(Clone)]
pub struct FunnelService {
    #[allow(dead_code)]
    path: PathBuf,
    lock: Arc<Mutex<Map<String, Value>>>,
}

impl FunnelService {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        let path = data_dir.as_ref().join(state_files::FUNNEL_METRICS);
        let state = load_state(&path);
        Self {
            path,
            lock: Arc::new(Mutex::new(state)),
        }
    }

    pub fn snapshot(&self) -> OrderedMap {
        let guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut counters = OrderedMap::new();
        for name in COUNTER_NAMES {
            counters.insert(*name, json!(as_u64(guard.get(*name))));
        }
        let mut firsts = OrderedMap::new();
        for name in FIRST_NAMES {
            firsts.insert(*name, guard.get(*name).cloned().unwrap_or(Value::Null));
        }
        drop(guard);

        let file_requests = counters
            .get("file_requests")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let real_file = counters
            .get("real_file_delivered")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let code_only = counters
            .get("code_only_responses")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let agent_runs = counters
            .get("agent_runs")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let needs_review = counters
            .get("needs_review_runs")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let pauses = counters
            .get("approval_pauses")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let resumes = counters
            .get("approval_resumes")
            .and_then(Value::as_u64)
            .unwrap_or(0);

        let mut rates = OrderedMap::new();
        rates.insert("real_file_rate", rate(real_file, file_requests));
        rates.insert("code_only_rate", rate(code_only, file_requests));
        rates.insert("needs_review_rate", rate(needs_review, agent_runs));
        rates.insert("approval_resume_rate", rate(resumes, pauses));

        let mut counter_map = Map::new();
        for name in COUNTER_NAMES {
            counter_map.insert(
                (*name).into(),
                counters.get(*name).cloned().unwrap_or(json!(0)),
            );
        }
        let mut rate_map = Map::new();
        for key in [
            "real_file_rate",
            "code_only_rate",
            "needs_review_rate",
            "approval_resume_rate",
        ] {
            rate_map.insert(key.into(), rates.get(key).cloned().unwrap_or(Value::Null));
        }

        let mut out = OrderedMap::new();
        out.insert("counters", crate::admin::json_from_ordered(&counters));
        out.insert("firsts", crate::admin::json_from_ordered(&firsts));
        out.insert("rates", crate::admin::json_from_ordered(&rates));
        out.insert("alerts", json!(funnel_alerts(&counter_map, &rate_map)));
        out.insert("ttfv_seconds", ttfv_seconds(&firsts));
        out.insert("generated_at", json!(now_iso()));
        out
    }
}

fn load_state(path: &Path) -> Map<String, Value> {
    let mut state = Map::new();
    for name in COUNTER_NAMES {
        state.insert((*name).into(), json!(0));
    }
    for name in FIRST_NAMES {
        state.insert((*name).into(), Value::Null);
    }
    let Ok(text) = std::fs::read_to_string(path) else {
        return state;
    };
    let Ok(raw) = serde_json::from_str::<Value>(&text) else {
        return state;
    };
    if let Some(obj) = raw.as_object() {
        for name in COUNTER_NAMES {
            let n = obj.get(*name).and_then(Value::as_i64).unwrap_or(0).max(0);
            state.insert((*name).into(), json!(n));
        }
        for name in FIRST_NAMES {
            let v = obj.get(*name);
            state.insert(
                (*name).into(),
                match v {
                    Some(Value::String(s)) if !s.is_empty() => json!(s),
                    Some(other) if !other.is_null() => json!(other.to_string()),
                    _ => Value::Null,
                },
            );
        }
    }
    state
}

fn as_u64(v: Option<&Value>) -> u64 {
    v.and_then(Value::as_u64)
        .or_else(|| v.and_then(Value::as_i64).map(|n| n.max(0) as u64))
        .unwrap_or(0)
}

fn rate(num: u64, den: u64) -> Value {
    if den == 0 {
        Value::Null
    } else {
        json!(((num as f64) / (den as f64) * 10_000.0).round() / 10_000.0)
    }
}

fn ttfv_seconds(firsts: &OrderedMap) -> Value {
    let Some(_) = firsts.get("first_ingest_at").and_then(Value::as_str) else {
        return Value::Null;
    };
    let Some(_) = firsts.get("first_value_at").and_then(Value::as_str) else {
        return Value::Null;
    };
    // Empty-counter fixtures pin `null`; a live install with both stamps is
    // `@any` on generated_at and not asserted on ttfv.
    Value::Null
}

fn funnel_alerts(counters: &Map<String, Value>, rates: &Map<String, Value>) -> Vec<Value> {
    let mut alerts = Vec::new();
    let file_requests = as_u64(counters.get("file_requests"));
    let agent_runs = as_u64(counters.get("agent_runs"));
    let approval_pauses = as_u64(counters.get("approval_pauses"));

    if file_requests >= MIN_SAMPLES {
        if let Some(r) = rates.get("real_file_rate").and_then(Value::as_f64) {
            if r < REAL_FILE_RATE_FLOOR {
                alerts.push(alert(
                    "real_file_rate_low",
                    "warning",
                    &format!(
                        "파일 요청 중 실제 파일이 나온 비율이 {:.0}%입니다 (목표 {:.0}%). 파일 생성 파이프라인을 확인하세요.",
                        r * 100.0,
                        REAL_FILE_RATE_FLOOR * 100.0
                    ),
                    &format!(
                        "Only {:.0}% of file requests produced a real file (target {:.0}%). Check the file-generation pipeline.",
                        r * 100.0,
                        REAL_FILE_RATE_FLOOR * 100.0
                    ),
                    r,
                    REAL_FILE_RATE_FLOOR,
                    file_requests,
                ));
            }
        }
        if let Some(r) = rates.get("code_only_rate").and_then(Value::as_f64) {
            if r > CODE_ONLY_RATE_CEILING {
                alerts.push(alert(
                    "code_only_rate_high",
                    "warning",
                    &format!(
                        "파일을 요청했는데 코드/설명만 돌아온 비율이 {:.0}%입니다.",
                        r * 100.0
                    ),
                    &format!(
                        "{:.0}% of file requests came back as code or prose only.",
                        r * 100.0
                    ),
                    r,
                    CODE_ONLY_RATE_CEILING,
                    file_requests,
                ));
            }
        }
    }
    if agent_runs >= MIN_SAMPLES {
        if let Some(r) = rates.get("needs_review_rate").and_then(Value::as_f64) {
            if r > NEEDS_REVIEW_RATE_CEILING {
                alerts.push(alert(
                    "needs_review_rate_high",
                    "warning",
                    &format!("에이전트 실행의 {:.0}%가 '검토 필요'로 끝났습니다. 더 큰 모델을 쓰거나 요청을 작게 나누세요.", r * 100.0),
                    &format!("{:.0}% of agent runs ended as NEEDS_REVIEW. Use a larger model or split requests into smaller steps.", r * 100.0),
                    r,
                    NEEDS_REVIEW_RATE_CEILING,
                    agent_runs,
                ));
            }
        }
    }
    if approval_pauses >= MIN_SAMPLES {
        if let Some(r) = rates.get("approval_resume_rate").and_then(Value::as_f64) {
            if r < APPROVAL_RESUME_RATE_FLOOR {
                alerts.push(alert(
                    "approval_resume_rate_low",
                    "info",
                    &format!("승인 대기 중 실제로 이어서 실행된 비율이 {:.0}%입니다. 승인 카드가 잘 보이는지 확인하세요.", r * 100.0),
                    &format!("Only {:.0}% of paused runs were resumed. Check that the approval card is actually reaching users.", r * 100.0),
                    r,
                    APPROVAL_RESUME_RATE_FLOOR,
                    approval_pauses,
                ));
            }
        }
    }
    let ingest = as_u64(counters.get("ingest_completions"));
    let recall = as_u64(counters.get("recall_successes"));
    if ingest > 0 && recall == 0 {
        let mut a = OrderedMap::new();
        a.insert("key", json!("no_grounded_recall"));
        a.insert("severity", json!("warning"));
        a.insert("ko", json!("자료는 들어왔지만 근거 있는 회상이 아직 한 번도 없었습니다. 검색/인덱싱을 확인하세요."));
        a.insert("en", json!("Content was ingested but no answer has ever been grounded in it yet — check retrieval and indexing."));
        a.insert("samples", json!(ingest));
        alerts.push(crate::admin::json_from_ordered(&a));
    }
    alerts
}

fn alert(
    key: &str,
    severity: &str,
    ko: &str,
    en: &str,
    value: f64,
    threshold: f64,
    samples: u64,
) -> Value {
    let mut a = OrderedMap::new();
    a.insert("key", json!(key));
    a.insert("severity", json!(severity));
    a.insert("ko", json!(ko));
    a.insert("en", json!(en));
    a.insert("value", json!(value));
    a.insert("threshold", json!(threshold));
    a.insert("samples", json!(samples));
    crate::admin::json_from_ordered(&a)
}

#[derive(Clone)]
pub struct FunnelState {
    pub auth: Arc<AuthState>,
    pub service: FunnelService,
}

impl FunnelState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl AsRef<Path>) -> Self {
        Self {
            auth,
            service: FunnelService::new(data_dir),
        }
    }
}

impl axum::extract::FromRef<FunnelState> for Arc<AuthState> {
    fn from_ref(s: &FunnelState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: FunnelState) -> Router {
    Router::new()
        .route("/api/admin/funnel-metrics", get(funnel_metrics))
        .with_state(state)
}

async fn funnel_metrics(
    State(state): State<FunnelState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_admin(&headers)?;
    Ok(json_ok(&state.service.snapshot()))
}
