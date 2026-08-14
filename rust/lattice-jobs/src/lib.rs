//! `lattice-jobs` — the timer that drives the Brain's background work.
//!
//! Phase 3a of `docs/v11.5.0_RUST_COMPLETE_PLAN.md`, and the closing of a gap
//! the product documented against itself. `FEATURE_STATUS.md:179-185`: *nothing
//! in the server drives the background embed queue yet.* The queue
//! (`vector_jobs`) has been durable since v11.1.0 — an ingest whose inline
//! embedding fails still lands, and the node is recorded as owed an embedding —
//! but the only drain was a Python method with no caller outside a test. Owed
//! work with nobody to do it is a promise, not a feature.
//!
//! This crate is the missing caller:
//!
//! * a [`Scheduler`] that ticks every minute by default
//!   (`LATTICEAI_JOBS_INTERVAL`, floored at five seconds),
//! * each tick calling the worker's new `POST /api/index/drain`,
//! * optionally resuming one interrupted ingestion job per tick
//!   (`LATTICEAI_JOBS_AUTORESUME=1`, off by default),
//! * a failing tick doubling its delay up to ten minutes and snapping back on
//!   the first success,
//! * and `GET /host/jobs` / `POST /host/jobs/tick` so the schedule is visible
//!   and forceable instead of being a thing that happens in the dark.
//!
//! ## Mounting it
//!
//! The crate owns no listener. A host merges the router and, if it wants the
//! timer rather than only the button, spawns the loop:
//!
//! ```text
//! let scheduler = Arc::new(Scheduler::new(SchedulerConfig::from_env(worker_origin))?);
//! let router = gateway_router.merge(lattice_jobs::router(Arc::clone(&scheduler)));
//! scheduler.spawn(shutdown_signal);   // omit this and `enabled` answers false
//! ```
//!
//! `enabled` in the `/host/jobs` payload is exactly "the loop is running", so a
//! host that mounts the routes and never spawns reports *manual only* instead
//! of implying a timer nobody started.
//!
//! ## Boundaries, stated rather than discovered
//!
//! * **The worker does the work.** Embedding, graph writes and ingestion all
//!   stay in Python — the single-writer rule this product is built on. This
//!   crate calls an HTTP endpoint on a timer and remembers what it answered.
//! * **Queue counts bypass the worker.** They come from the store directly,
//!   read-only ([`queue`]), because the moment someone asks how much work is
//!   owed is often the moment the worker is not answering.
//! * **The drain endpoint is authenticated** (`require_user` plus the workspace
//!   gate). On a single-user local install that is satisfied; on an install
//!   with authentication switched on, ticks answer 401, back off, and say so in
//!   `last_tick.error`. That is visible failure, not silent success, and wiring
//!   a host credential is deliberately left to whoever mounts this router.

#![warn(missing_docs)]
#![warn(clippy::all)]
// `index_api` answers `Result<T, axum::response::Response>` — the error is the
// rendered refusal, the convention `lattice-auth` set. Allowed once here.
#![allow(clippy::result_large_err)]

pub mod config;
pub mod index_api;
pub mod queue;
pub mod routes;
pub mod schedule;
pub mod scheduler;
pub mod tick;

pub use config::{
    parse_flag, parse_interval, SchedulerConfig, AUTORESUME_ENV, DEFAULT_DRAIN_LIMIT,
    DEFAULT_INTERVAL, INTERVAL_ENV, MAX_BACKOFF, MIN_INTERVAL,
};
pub use queue::{read_counts, QueueCounts, VECTOR_JOB_STATUSES};
pub use routes::router;
pub use schedule::Backoff;
pub use scheduler::{JobsError, Scheduler, SchedulerSnapshot};
pub use tick::{DrainOutcome, JobView, ResumeOutcome, TickReport};

/// Version of this crate, kept in lockstep with the product version by
/// `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    #[test]
    fn the_version_is_the_workspace_version() {
        assert!(super::VERSION.starts_with("11."), "got {}", super::VERSION);
    }
}
