//! The scheduler against a fake worker: what it calls, what it does when the
//! answer is a 500, and what `/host/jobs` says about all of it.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use std::sync::Arc;
use std::time::Duration;

use common::{client, jobs_payload, json, queue_fixture, wait_until, Behaviour, FakeWorker};
use lattice_jobs::{router, Scheduler, SchedulerConfig};

const DRAIN: &str = "/api/index/drain";
const JOBS: &str = "/api/ingestion/jobs";

fn config(worker: &FakeWorker) -> SchedulerConfig {
    SchedulerConfig::new(worker.origin())
        .with_request_timeout(Duration::from_secs(5))
        .with_db_path("/nonexistent/knowledge_graph.sqlite")
}

fn scheduler(config: SchedulerConfig) -> Arc<Scheduler> {
    Arc::new(Scheduler::with_client(config, client()))
}

// ── the tick calls the endpoint this release added ───────────────────────────

#[tokio::test]
async fn a_tick_posts_the_configured_limit_to_the_drain_endpoint() {
    let worker = FakeWorker::start().await;
    let scheduler = scheduler(config(&worker).with_drain_limit(7));

    let report = scheduler.tick().await;

    let calls = worker.requests_to(DRAIN);
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].method, "POST");
    assert_eq!(calls[0].header("content-type"), Some("application/json"));
    assert_eq!(calls[0].body_text(), r#"{"limit":7}"#);
    assert!(report.ok);
    worker.shutdown();
}

#[tokio::test]
async fn the_workers_counters_are_carried_through_verbatim() {
    let worker = FakeWorker::start().await;

    let report = scheduler(config(&worker)).tick().await;

    let drain = report.drain.expect("a drain");
    assert_eq!(
        (drain.claimed, drain.indexed, drain.retried, drain.failed),
        (2, 1, 1, 0)
    );
    assert_eq!(report.error, None);
    assert!(report.resumed.is_empty(), "autoresume is off by default");
    worker.shutdown();
}

#[tokio::test]
async fn a_drain_that_claimed_nothing_is_still_a_healthy_tick() {
    let worker = FakeWorker::start_with(Behaviour {
        drain_body: r#"{"claimed":0,"indexed":0,"retried":0,"failed":0,"detail":null}"#.into(),
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(config(&worker));

    let report = scheduler.tick().await;

    assert!(report.ok);
    assert_eq!(scheduler.next_delay(), scheduler.config().interval);
    worker.shutdown();
}

// ── failure: the schedule stretches, then snaps back ─────────────────────────

#[tokio::test]
async fn a_5xx_backs_the_schedule_off_by_doubling_and_a_recovery_resets_it() {
    let worker = FakeWorker::start_with(Behaviour {
        drain_status: 500,
        drain_body: r#"{"detail":"the queue exploded"}"#.into(),
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(config(&worker).with_interval(Duration::from_secs(60)));

    let first = scheduler.tick().await;
    assert!(!first.ok);
    assert!(first.error.expect("a reason").contains("500"));
    assert_eq!(scheduler.next_delay(), Duration::from_secs(120));

    scheduler.tick().await;
    assert_eq!(scheduler.next_delay(), Duration::from_secs(240));
    scheduler.tick().await;
    assert_eq!(scheduler.next_delay(), Duration::from_secs(480));

    worker.set(|behaviour| behaviour.drain_status = 200);
    let recovered = scheduler.tick().await;

    assert!(recovered.ok);
    assert_eq!(scheduler.next_delay(), Duration::from_secs(60));
    worker.shutdown();
}

#[tokio::test]
async fn the_backoff_stops_growing_at_the_cap() {
    let worker = FakeWorker::start_with(Behaviour {
        drain_status: 503,
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(
        config(&worker)
            .with_interval(Duration::from_secs(60))
            .with_max_backoff(Duration::from_secs(600)),
    );

    for _ in 0..10 {
        scheduler.tick().await;
    }

    assert_eq!(scheduler.next_delay(), Duration::from_secs(600));
    worker.shutdown();
}

#[tokio::test]
async fn a_worker_that_is_not_there_is_a_failed_tick_not_a_panic() {
    // Bind and drop, so the port is almost certainly refusing connections.
    let dead = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let origin = format!("http://{}", dead.local_addr().expect("addr"));
    drop(dead);
    let scheduler = scheduler(
        SchedulerConfig::new(origin)
            .with_request_timeout(Duration::from_millis(500))
            .with_db_path("/nonexistent/knowledge_graph.sqlite"),
    );

    let report = scheduler.tick().await;

    assert!(!report.ok);
    assert!(report.drain.is_none());
    assert!(report.error.expect("a reason").contains("/api/index/drain"));
}

#[tokio::test]
async fn a_worker_answering_nonsense_is_a_failed_tick_with_the_reason() {
    let worker = FakeWorker::start_with(Behaviour {
        drain_body: "not json at all".into(),
        ..Behaviour::default()
    })
    .await;

    let report = scheduler(config(&worker)).tick().await;

    assert!(!report.ok);
    assert!(report.error.expect("a reason").contains("malformed JSON"));
    worker.shutdown();
}

// ── autoresume: opt-in, and conservative when it is on ───────────────────────

#[tokio::test]
async fn autoresume_stays_out_of_the_way_until_it_is_asked_for() {
    let worker = FakeWorker::start_with(Behaviour {
        jobs_body: jobs_payload(&[("j1", "partial", 10, 4)]),
        ..Behaviour::default()
    })
    .await;

    scheduler(config(&worker)).tick().await;

    assert_eq!(worker.count_to(JOBS), 0, "the jobs list is not even read");
    worker.shutdown();
}

#[tokio::test]
async fn autoresume_reads_the_env_switch() {
    let worker = FakeWorker::start().await;

    let off = SchedulerConfig::from_values(worker.origin(), None, None);
    let on = SchedulerConfig::from_values(worker.origin(), None, Some("1"));

    assert!(!off.autoresume);
    assert!(on.autoresume);
    worker.shutdown();
}

#[tokio::test]
async fn autoresume_resumes_the_oldest_job_with_work_left() {
    let worker = FakeWorker::start_with(Behaviour {
        jobs_body: jobs_payload(&[
            ("newest-running", "running", 10, 2),
            ("newer-partial", "partial", 10, 4),
            ("oldest-failed", "failed", 6, 0),
            ("done", "completed", 3, 3),
        ]),
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(config(&worker).with_autoresume(true));

    let report = scheduler.tick().await;

    let listed = worker.requests_to(JOBS);
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].query(), Some("limit=20"));
    let resumes = worker.requests_to("/api/ingestion/jobs/oldest-failed/resume");
    assert_eq!(resumes.len(), 1, "one resume, and it is the oldest");
    assert_eq!(report.resumed.len(), 1);
    assert_eq!(report.resumed[0].job_id, "oldest-failed");
    assert_eq!(report.resumed[0].status, "resuming");
    assert_eq!(report.resume_error, None);
    worker.shutdown();
}

#[tokio::test]
async fn a_job_a_resume_could_not_advance_is_not_resumed_again() {
    let worker = FakeWorker::start_with(Behaviour {
        jobs_body: jobs_payload(&[("stuck", "failed", 5, 0)]),
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(config(&worker).with_autoresume(true));

    scheduler.tick().await;
    let second = scheduler.tick().await;

    assert_eq!(worker.count_to("/api/ingestion/jobs/stuck/resume"), 1);
    assert!(second.resumed.is_empty());

    // Something moved it forward: it is worth another attempt.
    worker.set(|behaviour| behaviour.jobs_body = jobs_payload(&[("stuck", "partial", 5, 2)]));
    let third = scheduler.tick().await;

    assert_eq!(third.resumed.len(), 1);
    worker.shutdown();
}

#[tokio::test]
async fn nothing_resumable_means_no_resume_request_at_all() {
    let worker = FakeWorker::start_with(Behaviour {
        jobs_body: jobs_payload(&[("running", "running", 4, 1), ("done", "completed", 4, 4)]),
        ..Behaviour::default()
    })
    .await;

    let report = scheduler(config(&worker).with_autoresume(true))
        .tick()
        .await;

    assert_eq!(worker.count_to(JOBS), 1);
    assert!(report.resumed.is_empty());
    assert_eq!(report.resume_error, None);
    worker.shutdown();
}

#[tokio::test]
async fn a_failing_jobs_list_never_fails_the_drain() {
    let worker = FakeWorker::start_with(Behaviour {
        jobs_status: 401,
        jobs_body: r#"{"detail":"sign in first"}"#.into(),
        ..Behaviour::default()
    })
    .await;
    let scheduler = scheduler(config(&worker).with_autoresume(true));

    let report = scheduler.tick().await;

    assert!(report.ok, "the drain succeeded; autoresume is a passenger");
    assert!(report.resume_error.expect("a reason").contains("401"));
    // The schedule follows the drain, not the passenger.
    assert_eq!(scheduler.next_delay(), scheduler.config().interval);
    worker.shutdown();
}

// ── the routes ───────────────────────────────────────────────────────────────

struct TestHost {
    base: String,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    handle: tokio::task::JoinHandle<()>,
}

impl TestHost {
    async fn start(state: Arc<Scheduler>) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind host");
        let addr = listener.local_addr().expect("addr");
        let (tx, rx) = tokio::sync::oneshot::channel();
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, router(state))
                .with_graceful_shutdown(async {
                    let _ = rx.await;
                })
                .await;
        });
        Self {
            base: format!("http://{addr}"),
            shutdown: Some(tx),
            handle,
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base, path)
    }

    async fn stop(mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        let _ = tokio::time::timeout(Duration::from_secs(5), self.handle).await;
    }
}

#[tokio::test]
async fn host_jobs_answers_the_documented_shape_before_anything_has_ticked() {
    let worker = FakeWorker::start().await;
    let dir = tempfile::tempdir().expect("tempdir");
    let db = queue_fixture(
        dir.path(),
        &[("a", "pending"), ("b", "running"), ("c", "done")],
    );
    let host = TestHost::start(scheduler(
        config(&worker)
            .with_db_path(&db)
            .with_interval(Duration::from_secs(90)),
    ))
    .await;

    let payload = json(
        client()
            .get(host.url("/host/jobs"))
            .send()
            .await
            .expect("request"),
    )
    .await;

    assert_eq!(payload["enabled"], serde_json::json!(false));
    assert_eq!(payload["interval"], serde_json::json!(90));
    assert_eq!(payload["autoresume"], serde_json::json!(false));
    assert_eq!(payload["worker_origin"], serde_json::json!(worker.origin()));
    assert_eq!(payload["last_tick"], serde_json::Value::Null);
    assert_eq!(payload["next_due_in"], serde_json::Value::Null);
    assert_eq!(payload["recent_ticks"], serde_json::json!([]));
    // Counted straight from SQLite, with the worker never asked.
    assert_eq!(
        payload["queue_counts"]["available"],
        serde_json::json!(true)
    );
    assert_eq!(
        payload["queue_counts"]["counts"]["pending"],
        serde_json::json!(1)
    );
    assert_eq!(payload["queue_counts"]["pending"], serde_json::json!(2));
    assert_eq!(worker.requests().len(), 0);

    host.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_counts_stay_readable_while_the_worker_is_down() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db = queue_fixture(dir.path(), &[("a", "pending")]);
    let host = TestHost::start(scheduler(
        SchedulerConfig::new("http://127.0.0.1:1").with_db_path(&db),
    ))
    .await;

    let payload = json(
        client()
            .get(host.url("/host/jobs"))
            .send()
            .await
            .expect("request"),
    )
    .await;

    assert_eq!(payload["queue_counts"]["pending"], serde_json::json!(1));
    host.stop().await;
}

#[tokio::test]
async fn a_brain_with_no_store_reports_an_unavailable_queue_not_zero() {
    let host = TestHost::start(scheduler(
        SchedulerConfig::new("http://127.0.0.1:1").with_db_path("/nonexistent/kg.sqlite"),
    ))
    .await;

    let payload = json(
        client()
            .get(host.url("/host/jobs"))
            .send()
            .await
            .expect("request"),
    )
    .await;

    assert_eq!(
        payload["queue_counts"]["available"],
        serde_json::json!(false)
    );
    assert!(payload["queue_counts"]["detail"].is_string());
    host.stop().await;
}

#[tokio::test]
async fn posting_a_tick_runs_one_now_and_answers_with_it() {
    let worker = FakeWorker::start().await;
    let host = TestHost::start(scheduler(config(&worker).with_drain_limit(11))).await;

    let payload = json(
        client()
            .post(host.url("/host/jobs/tick"))
            .send()
            .await
            .expect("request"),
    )
    .await;

    assert_eq!(worker.count_to(DRAIN), 1);
    assert_eq!(worker.requests_to(DRAIN)[0].body_text(), r#"{"limit":11}"#);
    assert_eq!(payload["last_tick"]["ok"], serde_json::json!(true));
    assert_eq!(
        payload["last_tick"]["drain"]["claimed"],
        serde_json::json!(2)
    );
    assert_eq!(payload["recent_ticks"].as_array().expect("array").len(), 1);
    // A tick has happened, so the next one is now datable.
    assert!(payload["next_due_in"].as_u64().expect("seconds") <= 60);

    host.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_history_ring_keeps_only_the_last_few_ticks() {
    let worker = FakeWorker::start().await;
    let host = TestHost::start(scheduler(config(&worker).with_history(2))).await;
    let http = client();

    for _ in 0..4 {
        let _ = http.post(host.url("/host/jobs/tick")).send().await;
    }
    let payload = json(
        http.get(host.url("/host/jobs"))
            .send()
            .await
            .expect("request"),
    )
    .await;

    assert_eq!(worker.count_to(DRAIN), 4);
    assert_eq!(payload["recent_ticks"].as_array().expect("array").len(), 2);
    host.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn an_unknown_jobs_path_is_not_answered_by_this_router() {
    let worker = FakeWorker::start().await;
    let host = TestHost::start(scheduler(config(&worker))).await;

    let response = client()
        .get(host.url("/host/jobs/nope"))
        .send()
        .await
        .expect("request");

    assert_eq!(response.status().as_u16(), 404);
    host.stop().await;
    worker.shutdown();
}

// ── the loop ─────────────────────────────────────────────────────────────────

#[tokio::test]
async fn the_loop_ticks_immediately_on_spawn_and_stops_when_asked() {
    let worker = FakeWorker::start().await;
    let scheduler = scheduler(config(&worker));
    let (tx, rx) = tokio::sync::oneshot::channel();

    let handle = Arc::clone(&scheduler).spawn(async {
        let _ = rx.await;
    });

    // The moment a backlog most needs attention is right after a restart, so
    // the first tick does not wait for the first interval.
    assert!(
        wait_until(Duration::from_secs(5), || worker.count_to(DRAIN) >= 1).await,
        "the loop never ticked"
    );
    assert!(wait_until(Duration::from_secs(1), || scheduler.is_running()).await);
    assert!(
        scheduler
            .status(lattice_jobs::QueueCounts::unavailable("x"))
            .enabled,
        "a running loop must say so"
    );

    let _ = tx.send(());
    let stopped = tokio::time::timeout(Duration::from_secs(5), handle).await;

    assert!(stopped.is_ok(), "the loop ignored the shutdown signal");
    assert!(!scheduler.is_running());
    worker.shutdown();
}
