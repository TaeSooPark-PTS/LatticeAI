use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::post;
use axum::{Json, Router};
use tokio::net::TcpListener;

fn item(id: &str, title: &str, summary: &str, score: f64) -> Value {
    json!({"id": id, "title": title, "summary": summary, "score": score})
}

fn sample(count: usize) -> Vec<Value> {
    (0..count)
        .map(|index| {
            item(
                &format!("n{index}"),
                &format!("Title {index}"),
                &format!("Body {index}"),
                1.0 - index as f64 * 0.01,
            )
        })
        .collect()
}

fn ids(matches: &[Value]) -> Vec<String> {
    matches.iter().map(match_id).collect()
}

#[derive(Clone)]
struct Mock {
    calls: Arc<AtomicUsize>,
    mode: &'static str,
    delay: Duration,
}

async fn rerank_handler(
    State(mock): State<Mock>,
    Json(body): Json<Value>,
) -> (StatusCode, Json<Value>) {
    mock.calls.fetch_add(1, Ordering::SeqCst);
    if !mock.delay.is_zero() {
        tokio::time::sleep(mock.delay).await;
    }
    let documents = body["documents"].as_array().cloned().unwrap_or_default();
    match mock.mode {
        "unavailable" => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "reranker unavailable"})),
        ),
        "short" => (StatusCode::OK, Json(json!({"scores": [0.5]}))),
        "nan" => (
            StatusCode::OK,
            Json(json!({"scores": documents.iter().map(|_| "x").collect::<Vec<_>>()})),
        ),
        _ => {
            let scores: Vec<f64> = (0..documents.len()).map(|index| index as f64).collect();
            (
                StatusCode::OK,
                Json(
                    json!({"scores": scores, "model_id": "cross-encoder:BAAI/bge-reranker-v2-m3"}),
                ),
            )
        }
    }
}

async fn spawn(mode: &'static str, delay: Duration) -> (WorkerSeamClient, Arc<AtomicUsize>) {
    let calls = Arc::new(AtomicUsize::new(0));
    let app = Router::new()
        .route(RERANK_PATH, post(rerank_handler))
        .with_state(Mock {
            calls: Arc::clone(&calls),
            mode,
            delay,
        });
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    (
        WorkerSeamClient::new(format!("http://{addr}")).unwrap(),
        calls,
    )
}

#[test]
fn config_defaults_rescore_twenty_and_promote_five() {
    let config = RerankConfig::from_lookup(|_| None);
    assert!(config.enabled);
    assert_eq!(config.candidates, 20);
    assert_eq!(config.promote, 5);
    assert_eq!(
        config.timeout,
        Duration::from_millis(DEFAULT_RERANK_TIMEOUT_MS)
    );
}

#[test]
fn config_reads_switch_and_clamps_numbers() {
    let config = RerankConfig::from_lookup(|key| match key {
        RERANK_ENV => Some("off".into()),
        RERANK_TIMEOUT_ENV => Some("1".into()),
        RERANK_CANDIDATES_ENV => Some("500".into()),
        RERANK_PROMOTE_ENV => Some("900".into()),
        _ => None,
    });
    assert!(!config.enabled);
    assert_eq!(config.timeout, Duration::from_millis(50));
    assert_eq!(config.candidates, MAX_RERANK_CANDIDATES);
    assert_eq!(config.promote, MAX_RERANK_CANDIDATES);
    let garbled = RerankConfig::from_lookup(|key| match key {
        RERANK_ENV => Some("maybe".into()),
        RERANK_PROMOTE_ENV => Some("abc".into()),
        _ => None,
    });
    assert!(garbled.enabled);
    assert_eq!(garbled.promote, DEFAULT_RERANK_PROMOTE);
}

#[test]
fn candidate_text_joins_title_and_first_body_field() {
    assert_eq!(
        candidate_text(&item("a", "Title", "Summary", 0.0)),
        "Title: Summary"
    );
    assert_eq!(
        candidate_text(&json!({"title": "", "snippet": "only snippet"})),
        "only snippet"
    );
    assert_eq!(candidate_text(&json!({"title": "bare"})), "bare");
    let long = "가".repeat(RERANK_DOCUMENT_CHARS * 2);
    assert_eq!(
        candidate_text(&json!({"text": long})).chars().count(),
        RERANK_DOCUMENT_CHARS
    );
}

#[test]
fn promoted_candidates_lead_and_the_rest_keep_fusion_order() {
    let mut matches = sample(8);
    let scores = [0.1, 0.9, 0.2, 0.95, 0.3, 0.0];
    let promoted = apply_rerank_scores(&mut matches, &scores, 3);
    assert_eq!(promoted, vec!["n3", "n1", "n4"]);
    assert_eq!(
        ids(&matches),
        vec!["n3", "n1", "n4", "n0", "n2", "n5", "n6", "n7"]
    );
    assert_eq!(matches[0]["rerank_score"], json!(0.95));
    assert_eq!(matches[0]["rerank_promoted"], json!(true));
    assert_eq!(matches[3]["rerank_promoted"], json!(false));
    assert!(matches[6].get("rerank_score").is_none());
    assert_eq!(matches[0]["score"], sample(8)[3]["score"]);
}

#[test]
fn ties_keep_fusion_order_and_empty_scores_change_nothing() {
    let mut matches = sample(4);
    let promoted = apply_rerank_scores(&mut matches, &[0.5, 0.5, 0.5, 0.5], 2);
    assert_eq!(promoted, vec!["n0", "n1"]);
    let mut untouched = sample(3);
    assert!(apply_rerank_scores(&mut untouched, &[], 3).is_empty());
    assert_eq!(ids(&untouched), vec!["n0", "n1", "n2"]);
}

#[tokio::test]
async fn disabled_or_missing_seam_keeps_rrf_order() {
    let mut matches = sample(5);
    let off = RerankConfig {
        enabled: false,
        ..RerankConfig::default()
    };
    let outcome = rerank_matches(None, "query", &mut matches, &off).await;
    assert_eq!(outcome.reason.as_deref(), Some("rerank_disabled"));
    let outcome = rerank_matches(None, "query", &mut matches, &RerankConfig::default()).await;
    assert!(!outcome.applied);
    assert_eq!(outcome.strategy, STRATEGY_RRF);
    assert_eq!(outcome.reason.as_deref(), Some("rerank_seam_unavailable"));
    let mut single = sample(1);
    let outcome = rerank_matches(None, "query", &mut single, &RerankConfig::default()).await;
    assert_eq!(outcome.reason.as_deref(), Some("rerank_too_few_candidates"));
    assert_eq!(ids(&matches), vec!["n0", "n1", "n2", "n3", "n4"]);
}

#[tokio::test]
async fn cross_encoder_scores_promote_the_best_of_the_top_twenty() {
    let (seam, calls) = spawn("ok", Duration::ZERO).await;
    let mut report = json!({"query": "q", "matches": sample(25)});
    let outcome = rerank_report(Some(&seam), "q", &mut report, &RerankConfig::default()).await;
    assert!(outcome.applied, "{outcome:?}");
    assert_eq!(outcome.strategy, STRATEGY_CROSS_ENCODER);
    assert_eq!(outcome.candidates, 20);
    assert_eq!(outcome.promoted, vec!["n19", "n18", "n17", "n16", "n15"]);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let order = ids(report["matches"].as_array().unwrap());
    assert_eq!(&order[..6], &["n19", "n18", "n17", "n16", "n15", "n0"]);
    assert_eq!(order.len(), 25);
    assert_eq!(report["rerank"]["applied"], json!(true));
    assert_eq!(
        report["rerank"]["model_id"],
        json!("cross-encoder:BAAI/bge-reranker-v2-m3")
    );
}

#[tokio::test]
async fn a_slow_reranker_times_out_into_rrf_order() {
    let (seam, _calls) = spawn("ok", Duration::from_millis(600)).await;
    let config = RerankConfig {
        timeout: Duration::from_millis(80),
        ..RerankConfig::default()
    };
    let mut report = json!({"matches": sample(6)});
    let started = Instant::now();
    let outcome = rerank_report(Some(&seam), "q", &mut report, &config).await;
    assert!(started.elapsed() < Duration::from_millis(500));
    assert!(!outcome.applied);
    let reason = outcome.reason.unwrap();
    assert!(
        reason.starts_with("rerank_timeout") || reason.starts_with("rerank_failed"),
        "{reason}"
    );
    assert_eq!(
        ids(report["matches"].as_array().unwrap()),
        vec!["n0", "n1", "n2", "n3", "n4", "n5"]
    );
    assert_eq!(report["rerank"]["strategy"], json!("rrf"));
}

#[tokio::test]
async fn unavailable_or_malformed_replies_fall_back_to_rrf() {
    for (mode, prefix) in [
        ("unavailable", "rerank_failed"),
        ("short", "rerank_invalid"),
        ("nan", "rerank_invalid"),
    ] {
        let (seam, _calls) = spawn(mode, Duration::ZERO).await;
        let mut matches = sample(4);
        let outcome =
            rerank_matches(Some(&seam), "q", &mut matches, &RerankConfig::default()).await;
        assert!(!outcome.applied, "{mode}");
        assert!(outcome.reason.unwrap().starts_with(prefix), "{mode}");
        assert_eq!(ids(&matches), vec!["n0", "n1", "n2", "n3"]);
    }
}

#[tokio::test]
async fn reports_without_matches_only_gain_a_rerank_block() {
    let mut report = json!({"detail": "none"});
    let outcome = rerank_report(None, "q", &mut report, &RerankConfig::default()).await;
    assert!(!outcome.applied);
    assert!(report.get("matches").is_none());
    assert_eq!(report["rerank"]["applied"], json!(false));
    let mut blank = json!({"matches": [{"id": "a"}, {"id": "b"}]});
    let (seam, calls) = spawn("ok", Duration::ZERO).await;
    let outcome = rerank_report(Some(&seam), "q", &mut blank, &RerankConfig::default()).await;
    assert_eq!(
        outcome.reason.as_deref(),
        Some("rerank_candidates_without_text")
    );
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}
