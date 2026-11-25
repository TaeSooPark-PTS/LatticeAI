#![warn(clippy::all)]
#![allow(clippy::result_large_err)]

pub mod config;
pub mod drain;
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
pub use drain::{
    adaptive_concurrency, adaptive_drain_limit, drain_plan, embed_batches, is_worker_busy_error,
    is_worker_busy_status, DrainPlan, EMBED_BATCH, MAX_INFLIGHT,
};
pub use queue::{read_counts, QueueCounts, VECTOR_JOB_STATUSES};
pub use routes::router;
pub use schedule::Backoff;
pub use scheduler::{JobsError, Scheduler, SchedulerSnapshot};
pub use tick::{DrainOutcome, JobView, ResumeOutcome, TickReport};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    #[test]
    fn the_version_is_the_workspace_version() {
        let workspace_manifest = include_str!("../../Cargo.toml");
        let version_line = format!("version = \"{}\"", super::VERSION);
        assert!(
            workspace_manifest.contains(&version_line),
            "crate version {} is not the workspace version",
            super::VERSION
        );
    }
}
