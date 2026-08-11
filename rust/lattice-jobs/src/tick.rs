//! One tick: drain the embed queue, and optionally resume one ingestion job.
//!
//! Every call here is error-tolerant by construction. The scheduler runs
//! unattended against a worker that may be booting, restarting, or gone, so a
//! failed request is data (a [`TickReport`] with a reason, and a longer delay
//! before the next attempt) rather than an error that propagates.
//!
//! ## Which ingestion jobs are resumable
//!
//! Read against `lattice_brain/ingestion_jobs.py` and
//! `lattice_brain/ingestion/jobs_api.py`, because the wire schema alone does
//! not say:
//!
//! * `processed` is `len(done_indices)` — **successes only**. `failed` counts
//!   the failures of the *last* run and is reset to 0 when a run starts, so
//!   remaining work is `total - processed`, never `total - processed - failed`
//!   (a failed item is retried on resume, exactly as it should be).
//! * A run ends `completed` (all items done), `partial` (some) or `failed`
//!   (none). A process that died mid-run leaves a `running` row, which the
//!   store restores as `partial` or `queued` depending on recorded progress.
//!
//! So this scheduler resumes **`partial` and `failed` jobs with work left**,
//! and deliberately not:
//!
//! * `running` — the worker refuses it anyway (`already_running`).
//! * `completed` — nothing to resume.
//! * `queued` — indistinguishable, from outside, between "crashed before the
//!   first item" and "enqueued a second ago with a live background task about
//!   to run it". Resuming the second would run the same items twice. A
//!   genuinely abandoned queued job is left for a human, which is the honest
//!   trade: this timer never invents a second writer.

use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use reqwest::Client;
use serde::Serialize;
use serde_json::Value;

use crate::config::{SchedulerConfig, RESUME_SCAN_LIMIT};

/// What one `POST /api/index/drain` reported, verbatim.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct DrainOutcome {
    /// Nodes claimed from the backlog.
    pub claimed: u64,
    /// Nodes embedded successfully.
    pub indexed: u64,
    /// Nodes returned to `pending` for another attempt.
    pub retried: u64,
    /// Nodes that exhausted their retry budget.
    pub failed: u64,
    /// The queue's own explanation, when it had one.
    pub detail: Option<String>,
}

impl DrainOutcome {
    /// Read the tick keys out of the worker's answer.
    ///
    /// Missing counters are zeros rather than an error: the endpoint's shape is
    /// the queue's, and a future key must not turn a working drain into a
    /// failed tick.
    pub fn from_json(value: &Value) -> Self {
        Self {
            claimed: number(value, "claimed"),
            indexed: number(value, "indexed"),
            retried: number(value, "retried"),
            failed: number(value, "failed"),
            detail: value
                .get("detail")
                .and_then(Value::as_str)
                .map(str::to_string),
        }
    }

    /// Whether this drain actually moved anything.
    pub fn did_work(&self) -> bool {
        self.claimed > 0
    }
}

/// The few fields of an ingestion job the resume decision needs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct JobView {
    /// Job identifier.
    pub job_id: String,
    /// `queued` | `running` | `completed` | `failed` | `partial`.
    pub status: String,
    /// Items in the job.
    pub total: u64,
    /// Items completed successfully (`len(done_indices)`).
    pub processed: u64,
}

impl JobView {
    /// Parse one entry of `GET /api/ingestion/jobs`; `None` without an id.
    pub fn from_json(value: &Value) -> Option<Self> {
        let job_id = value.get("job_id").and_then(Value::as_str)?.to_string();
        if job_id.is_empty() {
            return None;
        }
        Some(Self {
            job_id,
            status: value
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            total: number(value, "total"),
            processed: number(value, "processed"),
        })
    }

    /// Items with no successful outcome yet.
    pub fn remaining(&self) -> u64 {
        self.total.saturating_sub(self.processed)
    }

    /// Whether this scheduler may resume it — see the module docs.
    ///
    /// The id has to survive being pasted into a URL path. Real ids are
    /// `bg_ingest_0007` (`ingestion_jobs.py`), so this rejects nothing the
    /// product produces; it exists so that a hand-edited or migrated row can
    /// never make this crate POST to a path it did not mean.
    pub fn is_resumable(&self) -> bool {
        matches!(self.status.as_str(), "partial" | "failed")
            && self.remaining() > 0
            && is_url_safe(&self.job_id)
    }
}

/// Whether an id is safe to interpolate into a URL path unescaped.
fn is_url_safe(job_id: &str) -> bool {
    !job_id.is_empty()
        && job_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
}

/// The one job to resume this tick, oldest first, skipping stalled ones.
///
/// `progress` remembers `processed` as it stood when this scheduler last
/// resumed each job. A job that comes back with no more items done than that
/// is one our resume could not advance — a corrupt file, a source that is
/// gone — and re-resuming it every minute forever would be a loop that looks
/// like work. It is skipped until something else moves it forward.
///
/// One job per tick, deliberately: the worker runs a resume in a background
/// task, and handing it the entire backlog at once would turn a scheduler into
/// a thundering herd.
pub fn pick_resumable<'a>(
    jobs: &'a [JobView],
    progress: &HashMap<String, u64>,
) -> Option<&'a JobView> {
    jobs.iter()
        .rev() // the worker lists newest first; the oldest backlog waited longest
        .find(|job| {
            job.is_resumable()
                && progress
                    .get(&job.job_id)
                    .is_none_or(|last| job.processed > *last)
        })
}

/// One resume request and the worker's answer to it.
///
/// The answer is kept rather than reduced to a boolean: `already_running` and
/// `nothing_to_resume` are the worker disagreeing with this scheduler's view of
/// the job, and that disagreement is the interesting part of the record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResumeOutcome {
    /// The job asked about.
    pub job_id: String,
    /// `resuming` | `already_running` | `nothing_to_resume` | `unknown`.
    pub status: String,
}

/// What one tick did, kept in the history ring and answered by `/host/jobs`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TickReport {
    /// When the tick started, in unix milliseconds (no timezone crate in this
    /// workspace, and a relative age would be meaningless in a stored ring).
    pub at_ms: u64,
    /// Whether the drain succeeded. This — not the resume — drives the backoff.
    pub ok: bool,
    /// The drain's counters, when it answered.
    pub drain: Option<DrainOutcome>,
    /// Jobs this tick asked the worker to resume, and what it answered.
    pub resumed: Vec<ResumeOutcome>,
    /// Why the drain failed, when it did.
    pub error: Option<String>,
    /// Why the autoresume pass failed, when it did. Kept apart from `error`
    /// because an autoresume failure must not stretch the drain's schedule.
    pub resume_error: Option<String>,
    /// How long the whole tick took.
    pub duration_ms: u64,
}

impl TickReport {
    /// An empty report stamped now.
    pub fn started() -> Self {
        Self {
            at_ms: now_ms(),
            ok: false,
            drain: None,
            resumed: Vec::new(),
            error: None,
            resume_error: None,
            duration_ms: 0,
        }
    }
}

/// `POST {worker}/api/index/drain` with the configured limit.
pub async fn drain(client: &Client, config: &SchedulerConfig) -> Result<DrainOutcome, String> {
    let body = format!("{{\"limit\":{}}}", config.drain_limit);
    let value = post_json(client, config, &config.worker_url("/api/index/drain"), body).await?;
    Ok(DrainOutcome::from_json(&value))
}

/// `GET {worker}/api/ingestion/jobs?limit=…` as parsed job views.
pub async fn list_jobs(client: &Client, config: &SchedulerConfig) -> Result<Vec<JobView>, String> {
    let url = config.worker_url(&format!("/api/ingestion/jobs?limit={RESUME_SCAN_LIMIT}"));
    let request = client.get(url).timeout(config.request_timeout);
    let value = read_json(request, "GET /api/ingestion/jobs").await?;
    Ok(value
        .get("jobs")
        .and_then(Value::as_array)
        .map(|jobs| jobs.iter().filter_map(JobView::from_json).collect())
        .unwrap_or_default())
}

/// `POST {worker}/api/ingestion/jobs/{id}/resume` → the status it answered
/// (`resuming` | `already_running` | `nothing_to_resume`).
pub async fn resume(
    client: &Client,
    config: &SchedulerConfig,
    job_id: &str,
) -> Result<String, String> {
    let url = config.worker_url(&format!("/api/ingestion/jobs/{job_id}/resume"));
    let value = post_json(client, config, &url, "{}".to_string()).await?;
    Ok(value
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string())
}

async fn post_json(
    client: &Client,
    config: &SchedulerConfig,
    url: &str,
    body: String,
) -> Result<Value, String> {
    let request = client
        .post(url)
        // The workspace pins reqwest without the `json` feature (the gateway
        // streams bytes and never deserialises), so the header is set by hand.
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .timeout(config.request_timeout)
        .body(body);
    read_json(request, url).await
}

async fn read_json(request: reqwest::RequestBuilder, what: &str) -> Result<Value, String> {
    let response = request
        .send()
        .await
        .map_err(|err| format!("{what} failed: {err}"))?;
    let status = response.status();
    let text = response
        .text()
        .await
        .map_err(|err| format!("{what} answered {status} with an unreadable body: {err}"))?;
    if !status.is_success() {
        return Err(format!(
            "{what} answered {status}: {}",
            truncate(text.trim(), 200)
        ));
    }
    serde_json::from_str(&text).map_err(|err| format!("{what} answered malformed JSON: {err}"))
}

fn truncate(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    text.chars().take(max).collect::<String>() + "…"
}

fn number(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or(0)
}

/// Milliseconds since the unix epoch.
pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn view(status: &str, total: u64, processed: u64) -> JobView {
        JobView {
            job_id: format!("job-{status}-{processed}"),
            status: status.to_string(),
            total,
            processed,
        }
    }

    #[test]
    fn the_tick_counters_are_read_verbatim() {
        let outcome = DrainOutcome::from_json(&json!({
            "claimed": 5, "indexed": 3, "retried": 1, "failed": 1,
            "detail": null, "limit": 25, "queue": {"pending": 0},
        }));

        assert_eq!(
            outcome,
            DrainOutcome {
                claimed: 5,
                indexed: 3,
                retried: 1,
                failed: 1,
                detail: None,
            }
        );
        assert!(outcome.did_work());
    }

    #[test]
    fn a_drain_with_nothing_to_do_is_still_a_successful_drain() {
        let outcome = DrainOutcome::from_json(&json!({
            "claimed": 0, "indexed": 0, "retried": 0, "failed": 0,
            "detail": "this store has no background vector queue",
        }));

        assert!(!outcome.did_work());
        assert_eq!(
            outcome.detail.as_deref(),
            Some("this store has no background vector queue")
        );
    }

    #[test]
    fn missing_counters_read_as_zero_rather_than_failing_the_tick() {
        let outcome = DrainOutcome::from_json(&json!({}));
        assert_eq!(outcome, DrainOutcome::default());
    }

    #[test]
    fn a_job_without_an_id_is_not_a_job() {
        assert!(JobView::from_json(&json!({"status": "partial"})).is_none());
        assert!(JobView::from_json(&json!({"job_id": ""})).is_none());
    }

    #[test]
    fn remaining_counts_successes_not_attempts() {
        // `failed` is reset each run and its items are retried, so it must not
        // be subtracted from what is left.
        let job = JobView::from_json(&json!({
            "job_id": "j1", "status": "partial", "total": 10, "processed": 4, "failed": 3,
        }))
        .expect("job");

        assert_eq!(job.remaining(), 6);
        assert!(job.is_resumable());
    }

    #[test]
    fn only_partial_and_failed_jobs_with_work_left_are_resumable() {
        assert!(view("partial", 10, 4).is_resumable());
        assert!(view("failed", 10, 0).is_resumable());

        assert!(!view("running", 10, 4).is_resumable());
        assert!(!view("queued", 10, 0).is_resumable());
        assert!(!view("completed", 10, 10).is_resumable());
        // Nothing left to do, whatever the status says.
        assert!(!view("partial", 10, 10).is_resumable());
        assert!(!view("failed", 0, 0).is_resumable());
        assert!(!view("", 5, 0).is_resumable());
    }

    #[test]
    fn an_id_that_would_rewrite_the_url_is_never_resumed() {
        for hostile in ["../../health", "a b", "a/b", "j?x=1", ""] {
            let job = JobView {
                job_id: hostile.to_string(),
                status: "partial".into(),
                total: 3,
                processed: 1,
            };
            assert!(!job.is_resumable(), "{hostile:?} must not be addressable");
        }
        // What the product actually produces still passes.
        assert!(JobView {
            job_id: "bg_ingest_0007".into(),
            status: "partial".into(),
            total: 3,
            processed: 1,
        }
        .is_resumable());
    }

    #[test]
    fn the_oldest_resumable_job_is_the_one_picked() {
        // The worker lists newest first.
        let jobs = vec![
            view("running", 3, 1),
            view("partial", 8, 2),
            view("failed", 4, 0),
        ];

        let chosen = pick_resumable(&jobs, &HashMap::new()).expect("a job");

        assert_eq!(chosen.status, "failed");
    }

    #[test]
    fn a_job_our_last_resume_could_not_advance_is_skipped() {
        let jobs = vec![view("partial", 8, 2)];
        let mut progress = HashMap::new();
        progress.insert(jobs[0].job_id.clone(), 2);

        assert!(pick_resumable(&jobs, &progress).is_none());

        // Something moved it forward: it is worth another attempt.
        let moved = vec![view("partial", 8, 5)];
        progress.insert(moved[0].job_id.clone(), 2);
        assert!(pick_resumable(&moved, &progress).is_some());
    }

    #[test]
    fn nothing_resumable_picks_nothing() {
        let jobs = vec![view("completed", 3, 3), view("running", 3, 1)];
        assert!(pick_resumable(&jobs, &HashMap::new()).is_none());
        assert!(pick_resumable(&[], &HashMap::new()).is_none());
    }

    #[test]
    fn a_fresh_report_is_stamped_and_empty() {
        let report = TickReport::started();

        assert!(!report.ok);
        assert!(report.drain.is_none());
        assert!(report.resumed.is_empty());
        assert!(
            report.at_ms > 1_700_000_000_000,
            "at_ms must be a real clock"
        );

        let json = serde_json::to_value(&report).expect("json");
        for key in [
            "at_ms",
            "ok",
            "drain",
            "resumed",
            "error",
            "resume_error",
            "duration_ms",
        ] {
            assert!(json.get(key).is_some(), "missing field {key}");
        }
    }

    #[test]
    fn long_worker_errors_are_truncated_before_they_reach_the_ring() {
        let long = "x".repeat(500);
        assert_eq!(truncate(&long, 200).chars().count(), 201);
        assert_eq!(truncate("short", 200), "short");
        assert_eq!(truncate("한글도 문자 단위로", 4), "한글도 …");
    }
}
