//! The timer itself: state, one tick, and the loop that repeats it.
//!
//! Why `sleep` in a loop rather than `tokio::time::Interval`: an `Interval` has
//! one fixed period, and this schedule is not fixed — a failing worker stretches
//! it (see [`crate::schedule::Backoff`]). `Interval` would also try to *catch
//! up* after a slow tick, firing back to back, which is the opposite of what a
//! drain that just timed out needs.
//!
//! The first tick runs immediately on spawn. The moment a backlog most needs
//! attention is right after a restart, and waiting a full interval to discover
//! that is a minute of a user watching nothing happen.

use std::collections::{HashMap, VecDeque};
use std::future::Future;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use reqwest::Client;
use serde::Serialize;

use crate::config::SchedulerConfig;
use crate::index_api;
use crate::queue::{read_counts, QueueCounts};
use crate::schedule::Backoff;
use crate::tick::{self, pick_resumable, DrainOutcome, ResumeOutcome, TickReport};
use lattice_core::graph_write::GraphWriter;

/// The scheduler could not be built.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobsError(String);

impl std::fmt::Display for JobsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for JobsError {}

/// Everything `/host/jobs` answers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SchedulerSnapshot {
    /// Whether the periodic loop is running. `false` means the routes still
    /// work and nothing is on a timer — an honest "manual only".
    pub enabled: bool,
    /// Configured seconds between successful ticks.
    pub interval: u64,
    /// Whether interrupted ingestion jobs are resumed too.
    pub autoresume: bool,
    /// The worker being ticked.
    pub worker_origin: String,
    /// The most recent tick, or `null` before the first one.
    pub last_tick: Option<TickReport>,
    /// Seconds until the next tick, or `null` when nothing has ticked yet.
    pub next_due_in: Option<u64>,
    /// Whether the delay is currently stretched by consecutive failures.
    pub backing_off: bool,
    /// The last few ticks, newest first.
    pub recent_ticks: Vec<TickReport>,
    /// The embed backlog, read straight from SQLite.
    pub queue_counts: QueueCounts,
}

struct Inner {
    backoff: Backoff,
    history: VecDeque<TickReport>,
    last_tick_at: Option<Instant>,
    resume_progress: HashMap<String, u64>,
}

/// Drives the worker's background work on a timer.
pub struct Scheduler {
    config: SchedulerConfig,
    client: Client,
    inner: Mutex<Inner>,
    running: AtomicBool,
    graph: Option<GraphWriter>,
}

impl Scheduler {
    /// A scheduler with its own HTTP client.
    pub fn new(config: SchedulerConfig) -> Result<Self, JobsError> {
        let client = Client::builder()
            .no_proxy()
            .connect_timeout(Duration::from_secs(5))
            .build()
            .map_err(|err| JobsError(err.to_string()))?;
        Ok(Self::with_client(config, client))
    }

    /// A scheduler reusing an existing client (the host's, so the loopback
    /// connection pool is shared).
    pub fn with_client(config: SchedulerConfig, client: Client) -> Self {
        let backoff = Backoff::new(config.interval, config.max_backoff);
        Self {
            config,
            client,
            inner: Mutex::new(Inner {
                backoff,
                history: VecDeque::new(),
                last_tick_at: None,
                resume_progress: HashMap::new(),
            }),
            running: AtomicBool::new(false),
            graph: None,
        }
    }

    /// Point ticks at the native drain (W3b) instead of POSTing the worker.
    pub fn with_graph(mut self, graph: GraphWriter) -> Self {
        self.graph = Some(graph);
        self
    }

    /// What this scheduler was configured with.
    pub fn config(&self) -> &SchedulerConfig {
        &self.config
    }

    /// Whether the periodic loop is running.
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// The delay before the next tick, backoff included.
    pub fn next_delay(&self) -> Duration {
        self.lock().backoff.delay()
    }

    /// Run one tick now: drain, then (opt-in) resume one ingestion job.
    ///
    /// Never fails. A worker that is down produces a report saying so, a longer
    /// delay, and one line in the host log.
    pub async fn tick(&self) -> TickReport {
        let started = Instant::now();
        let mut report = TickReport::started();
        match self.drain().await {
            Ok(outcome) => {
                report.ok = true;
                if outcome.did_work() {
                    log(&format!(
                        "drained {} node(s): {} indexed, {} retried, {} failed",
                        outcome.claimed, outcome.indexed, outcome.retried, outcome.failed
                    ));
                }
                report.drain = Some(outcome);
            }
            Err(err) => {
                report.error = Some(err);
            }
        }
        if self.config.autoresume {
            match self.autoresume().await {
                Ok(resumed) => report.resumed = resumed,
                Err(err) => report.resume_error = Some(err),
            }
        }
        report.duration_ms = started.elapsed().as_millis() as u64;
        self.record(report.clone());
        report
    }

    async fn drain(&self) -> Result<DrainOutcome, String> {
        if let Some(graph) = self.graph.clone() {
            let db = self.config.db_path.clone();
            let limit = self.config.drain_limit.max(1) as usize;
            return tokio::task::spawn_blocking(move || index_api::drain_queue(&graph, &db, limit))
                .await
                .map_err(|error| error.to_string());
        }
        tick::drain(&self.client, &self.config).await
    }

    /// The `/host/jobs` payload, with the queue counted off the event loop.
    pub async fn snapshot(&self) -> SchedulerSnapshot {
        let db = self.config.db_path.clone();
        // One small SQLite read, but this process also serves the product's
        // whole API surface; blocking calls belong on the blocking pool.
        let counts = tokio::task::spawn_blocking(move || read_counts(&db))
            .await
            .unwrap_or_else(|err| QueueCounts::unavailable(format!("queue read failed: {err}")));
        self.status(counts)
    }

    /// The `/host/jobs` payload assembled around counts the caller already has.
    pub fn status(&self, queue_counts: QueueCounts) -> SchedulerSnapshot {
        let inner = self.lock();
        let next_due_in = inner.last_tick_at.map(|at| {
            let remaining = inner.backoff.delay().saturating_sub(at.elapsed());
            (remaining.as_millis() as u64).div_ceil(1_000)
        });
        SchedulerSnapshot {
            enabled: self.running.load(Ordering::SeqCst),
            interval: self.config.interval.as_secs(),
            autoresume: self.config.autoresume,
            worker_origin: self.config.worker_origin.clone(),
            last_tick: inner.history.front().cloned(),
            next_due_in,
            backing_off: inner.backoff.is_backing_off(),
            recent_ticks: inner.history.iter().cloned().collect(),
            queue_counts,
        }
    }

    /// Start the periodic loop; it stops when `shutdown` resolves.
    pub fn spawn<F>(self: Arc<Self>, shutdown: F) -> tokio::task::JoinHandle<()>
    where
        F: Future<Output = ()> + Send + 'static,
    {
        tokio::spawn(async move {
            self.running.store(true, Ordering::SeqCst);
            tokio::pin!(shutdown);
            loop {
                self.tick().await;
                let delay = self.next_delay();
                tokio::select! {
                    _ = &mut shutdown => break,
                    _ = tokio::time::sleep(delay) => {}
                }
            }
            self.running.store(false, Ordering::SeqCst);
        })
    }

    async fn autoresume(&self) -> Result<Vec<ResumeOutcome>, String> {
        let jobs = tick::list_jobs(&self.client, &self.config).await?;
        let progress = self.lock().resume_progress.clone();
        let Some(job) = pick_resumable(&jobs, &progress) else {
            return Ok(Vec::new());
        };
        let status = tick::resume(&self.client, &self.config, &job.job_id).await?;
        // Remember how far it had got when we asked, so a resume that changes
        // nothing is not re-issued every minute for ever.
        self.lock()
            .resume_progress
            .insert(job.job_id.clone(), job.processed);
        log(&format!(
            "resume {} ({} of {} done): {status}",
            job.job_id, job.processed, job.total
        ));
        Ok(vec![ResumeOutcome {
            job_id: job.job_id.clone(),
            status,
        }])
    }

    fn record(&self, report: TickReport) {
        let mut inner = self.lock();
        if report.ok {
            inner.backoff.succeeded();
        } else {
            inner.backoff.failed();
            let delay = inner.backoff.delay().as_secs();
            let reason = report.error.clone().unwrap_or_else(|| "unknown".into());
            log(&format!("tick failed ({reason}); next attempt in {delay}s"));
        }
        if let Some(err) = report.resume_error.as_deref() {
            log(&format!("autoresume skipped: {err}"));
        }
        inner.last_tick_at = Some(Instant::now());
        inner.history.push_front(report);
        let cap = self.config.history.max(1);
        while inner.history.len() > cap {
            inner.history.pop_back();
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        // A poisoned lock here would mean a panic inside `record`, which holds
        // the guard across no user code at all. Recovering keeps a status route
        // answering instead of turning one panic into a permanently 500 host.
        self.inner.lock().unwrap_or_else(|err| err.into_inner())
    }
}

impl std::fmt::Debug for Scheduler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Scheduler")
            .field("worker_origin", &self.config.worker_origin)
            .field("interval", &self.config.interval)
            .field("autoresume", &self.config.autoresume)
            .field("running", &self.is_running())
            .finish()
    }
}

fn log(message: &str) {
    eprintln!("lattice-jobs: {message}");
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tick::DrainOutcome;

    fn scheduler() -> Scheduler {
        Scheduler::new(
            SchedulerConfig::new("http://127.0.0.1:1")
                .with_interval(Duration::from_secs(60))
                .with_history(3),
        )
        .expect("scheduler")
    }

    fn report(ok: bool) -> TickReport {
        let mut report = TickReport::started();
        report.ok = ok;
        report.drain = ok.then(DrainOutcome::default);
        report.error = (!ok).then(|| "worker down".to_string());
        report
    }

    #[test]
    fn a_fresh_scheduler_has_ticked_nothing_and_is_not_running() {
        let scheduler = scheduler();
        let snapshot = scheduler.status(QueueCounts::unavailable("no store"));

        assert!(!snapshot.enabled);
        assert_eq!(snapshot.interval, 60);
        assert!(!snapshot.autoresume);
        assert_eq!(snapshot.last_tick, None);
        assert_eq!(snapshot.next_due_in, None);
        assert!(!snapshot.backing_off);
        assert!(snapshot.recent_ticks.is_empty());
        assert!(!snapshot.queue_counts.available);
        assert!(format!("{scheduler:?}").contains("127.0.0.1:1"));
    }

    #[test]
    fn the_history_keeps_the_newest_ticks_and_drops_the_rest() {
        let scheduler = scheduler();
        for _ in 0..5 {
            scheduler.record(report(true));
        }
        let mut last = report(true);
        last.duration_ms = 42;
        scheduler.record(last);

        let snapshot = scheduler.status(QueueCounts::unavailable("no store"));

        assert_eq!(snapshot.recent_ticks.len(), 3, "history is capped");
        assert_eq!(snapshot.last_tick.expect("a tick").duration_ms, 42);
        assert_eq!(snapshot.recent_ticks[0].duration_ms, 42, "newest first");
    }

    #[test]
    fn failures_stretch_the_schedule_and_a_success_restores_it() {
        let scheduler = scheduler();

        scheduler.record(report(false));
        assert_eq!(scheduler.next_delay(), Duration::from_secs(120));
        scheduler.record(report(false));
        assert_eq!(scheduler.next_delay(), Duration::from_secs(240));
        assert!(scheduler.status(QueueCounts::unavailable("x")).backing_off);

        scheduler.record(report(true));

        assert_eq!(scheduler.next_delay(), Duration::from_secs(60));
        let snapshot = scheduler.status(QueueCounts::unavailable("x"));
        assert!(!snapshot.backing_off);
        // A tick has happened, so the next one is now datable.
        assert!(snapshot.next_due_in.expect("due") <= 60);
    }

    #[test]
    fn a_history_of_zero_still_remembers_the_last_tick() {
        let scheduler =
            Scheduler::new(SchedulerConfig::new("http://x").with_history(0)).expect("scheduler");
        scheduler.record(report(true));

        let snapshot = scheduler.status(QueueCounts::unavailable("x"));

        assert_eq!(snapshot.recent_ticks.len(), 1);
        assert!(snapshot.last_tick.is_some());
    }

    #[test]
    fn the_error_type_reads_like_an_error() {
        let err = JobsError("no client".into());
        assert_eq!(err.to_string(), "no client");
        assert!(format!("{err:?}").contains("JobsError"));
    }
}
