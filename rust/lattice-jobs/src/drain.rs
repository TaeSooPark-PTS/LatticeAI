//! Adaptive drain planning: how much to claim, how many requests to fly.
//!
//! The worker embed seam (`POST /worker/embed`) already accepts a `texts: []`
//! batch — one request, many vectors. What this crate used to do was the
//! opposite of that: one tick, one `POST /api/index/drain`, a fixed `limit`
//! of 25, then a minute of waiting. A deep backlog therefore drained at
//! 25 nodes/minute regardless of how idle the machine was.
//!
//! The planner here does not change any wire contract. It only chooses, from
//! a pending count the scheduler already reads, a `limit` (still in 1..=100)
//! and an in-flight width (1..=8). The drain handler still honours whatever
//! `limit` the caller posted. A worker that answers 429 / 503 is treated as
//! *busy* so the existing tick backoff stretches the schedule instead of
//! retrying immediately.

use crate::index_api::MAX_DRAIN_LIMIT;

/// Upper bound on concurrent `POST /api/index/drain` calls in one tick.
pub const MAX_INFLIGHT: usize = 8;
/// Default slice of texts handed to one `POST /worker/embed`.
///
/// The worker already batches internally; this is the HTTP-level chunk so a
/// 200-item claim does not become one enormous JSON body, and so two or four
/// of them can fly at once against a rate-limited seam.
pub const EMBED_BATCH: usize = 32;
/// Upper bound on concurrent `/worker/embed` calls for one drain.
pub const EMBED_INFLIGHT: usize = 4;
/// Seconds a `running` claim may sit before the next drain steals it back.
///
/// A host that died mid-write leaves rows in `running`. Without a sweeper
/// they stay owed forever. Two minutes is longer than one embed batch and
/// shorter than the ten-minute failure cap.
pub const STALE_RUNNING_SECS: u64 = 120;

/// What one tick should ask of the drain endpoint.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DrainPlan {
    /// `limit` posted on each drain request (1..=[`MAX_DRAIN_LIMIT`]).
    pub limit: u32,
    /// How many drain requests to fire at once (1..=[`MAX_INFLIGHT`]).
    pub inflight: usize,
    /// Texts per `/worker/embed` body when the seam is used.
    pub embed_batch: usize,
}

/// Plan a tick from a measured pending count and the configured floor.
///
/// `pending = None` means the store was unreadable — do not invent a deep
/// queue, just honour the configured limit with a single request.
pub fn drain_plan(pending: Option<u64>, configured: u32) -> DrainPlan {
    let limit = match pending {
        Some(count) => adaptive_drain_limit(count, configured),
        None => clamp_limit(configured),
    };
    let inflight = match pending {
        Some(count) => adaptive_concurrency(count, limit, MAX_INFLIGHT),
        None => 1,
    };
    DrainPlan {
        limit,
        inflight,
        embed_batch: EMBED_BATCH,
    }
}

/// Larger `limit` when the queue is deep; never outside 1..=100.
///
/// The configured value is the *floor* (clamped to the endpoint's range). A
/// queue smaller than that floor still posts the floor — empty ticks stay
/// cheap because they claim nothing. A queue between the floor and the cap
/// posts `pending` itself, so one request can empty a mid-size backlog. A
/// queue at or past the cap posts 100, and [`adaptive_concurrency`] fans out.
pub fn adaptive_drain_limit(pending: u64, configured: u32) -> u32 {
    let floor = clamp_limit(configured);
    let cap = MAX_DRAIN_LIMIT as u32;
    if pending <= u64::from(floor) {
        floor
    } else if pending >= u64::from(cap) {
        cap
    } else {
        pending as u32
    }
}

/// How many drain requests to fly given a pending count and the chosen limit.
pub fn adaptive_concurrency(pending: u64, limit: u32, max_inflight: usize) -> usize {
    if pending == 0 || limit == 0 {
        return 1;
    }
    let waves = pending.div_ceil(u64::from(limit)) as usize;
    waves.clamp(1, max_inflight.max(1))
}

/// Slice `items` into groups of at most `batch` for one HTTP embed each.
pub fn embed_batches<T: Clone>(items: &[T], batch: usize) -> Vec<Vec<T>> {
    let size = batch.max(1);
    items.chunks(size).map(<[T]>::to_vec).collect()
}

/// How many `/worker/embed` calls to fly for this many batches.
pub fn embed_inflight(batches: usize) -> usize {
    batches.clamp(1, EMBED_INFLIGHT).max(1)
}

/// HTTP statuses that mean "the worker is busy, back off" rather than "broken".
pub fn is_worker_busy_status(status: u16) -> bool {
    matches!(status, 429 | 503)
}

/// Whether an error string from [`crate::tick::drain`] is a busy worker.
pub fn is_worker_busy_error(error: &str) -> bool {
    error.contains(" 429")
        || error.contains(" 503")
        || error.contains("answered 429")
        || error.contains("answered 503")
}

fn clamp_limit(configured: u32) -> u32 {
    configured.clamp(1, MAX_DRAIN_LIMIT as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_shallow_or_unknown_queue_keeps_the_configured_limit_and_one_request() {
        assert_eq!(
            drain_plan(None, 25),
            DrainPlan {
                limit: 25,
                inflight: 1,
                embed_batch: EMBED_BATCH,
            }
        );
        assert_eq!(adaptive_drain_limit(0, 25), 25);
        assert_eq!(adaptive_drain_limit(10, 25), 25);
        assert_eq!(adaptive_drain_limit(25, 25), 25);
        assert_eq!(adaptive_concurrency(0, 25, 8), 1);
        assert_eq!(adaptive_concurrency(25, 25, 8), 1);
    }

    #[test]
    fn a_mid_queue_grows_the_limit_to_match_pending() {
        assert_eq!(adaptive_drain_limit(40, 25), 40);
        assert_eq!(adaptive_drain_limit(80, 25), 80);
        assert_eq!(adaptive_concurrency(80, 80, 8), 1);
    }

    #[test]
    fn a_deep_queue_caps_the_limit_and_fans_out() {
        assert_eq!(adaptive_drain_limit(200, 25), 100);
        assert_eq!(adaptive_drain_limit(10_000, 25), 100);
        assert_eq!(adaptive_concurrency(200, 100, 8), 2);
        assert_eq!(adaptive_concurrency(800, 100, 8), 8);
        assert_eq!(adaptive_concurrency(8_000, 100, 8), 8, "never past the cap");
        let plan = drain_plan(Some(400), 25);
        assert_eq!(plan.limit, 100);
        assert_eq!(plan.inflight, 4);
    }

    #[test]
    fn a_configured_limit_outside_the_endpoint_range_is_clamped() {
        assert_eq!(adaptive_drain_limit(0, 0), 1);
        assert_eq!(
            adaptive_drain_limit(5, 0),
            5,
            "floor is 1; pending still grows"
        );
        assert_eq!(adaptive_drain_limit(5, 250), 100);
        assert_eq!(drain_plan(None, 0).limit, 1);
        assert_eq!(drain_plan(None, 999).limit, 100);
    }

    #[test]
    fn embed_batches_split_evenly_and_keep_the_tail() {
        let items: Vec<u32> = (0..70).collect();
        let batches = embed_batches(&items, 32);
        assert_eq!(batches.len(), 3);
        assert_eq!(batches[0].len(), 32);
        assert_eq!(batches[1].len(), 32);
        assert_eq!(batches[2].len(), 6);
        assert!(embed_batches::<u32>(&[], 32).is_empty());
        assert_eq!(embed_batches(&[1, 2], 0).len(), 2, "zero batch becomes one");
        assert_eq!(embed_inflight(1), 1);
        assert_eq!(embed_inflight(9), EMBED_INFLIGHT);
    }

    #[test]
    fn busy_is_429_or_503_and_readable_from_the_tick_error() {
        assert!(is_worker_busy_status(429));
        assert!(is_worker_busy_status(503));
        assert!(!is_worker_busy_status(500));
        assert!(!is_worker_busy_status(200));
        assert!(is_worker_busy_error(
            "http://127.0.0.1:1/api/index/drain answered 429: rate limited"
        ));
        assert!(is_worker_busy_error(
            "http://127.0.0.1:1/api/index/drain answered 503: worker starting"
        ));
        assert!(!is_worker_busy_error(
            "http://127.0.0.1:1/api/index/drain answered 500: boom"
        ));
    }
}
