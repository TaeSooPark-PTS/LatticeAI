//! What the scheduler is, expressed as data — plus the two environment
//! switches an operator gets.
//!
//! Both switches are parsed the way `latticeai/core/config.py` parses its own
//! (`_int`: blank or unparseable falls back to the default; `_bool`: the
//! `1/true/yes/on` and `0/false/no/off` sets, anything else the default), so an
//! operator who has learned one half of the product's environment has learned
//! this half too.
//!
//! The resolvers are pure functions over `Option<&str>`. Reading the process
//! environment inside them would make every test that wants a different
//! interval mutate global state, and those are exactly the tests that go
//! flaky under a parallel harness.

use std::path::{Path, PathBuf};
use std::time::Duration;

/// Seconds between ticks. Blank or unparseable → [`DEFAULT_INTERVAL`].
pub const INTERVAL_ENV: &str = "LATTICEAI_JOBS_INTERVAL";
/// Opt in to resuming interrupted ingestion jobs. Off unless set.
pub const AUTORESUME_ENV: &str = "LATTICEAI_JOBS_AUTORESUME";

/// One tick a minute: often enough that a backlog is measured in minutes, rare
/// enough that an idle Brain is not being asked hourly-many questions.
pub const DEFAULT_INTERVAL: Duration = Duration::from_secs(60);
/// Floor on the configured interval. A tick opens SQLite and can run an
/// embedder; a one-second timer would be a load generator, not a scheduler.
pub const MIN_INTERVAL: Duration = Duration::from_secs(5);
/// Ceiling on the failure backoff: ten minutes, then it stops growing, so a
/// worker that comes back is picked up within ten minutes at worst.
pub const MAX_BACKOFF: Duration = Duration::from_secs(600);
/// How long one drain may take. Embedding a full claim of nodes is slow and a
/// short timeout would abandon work the worker is still doing.
pub const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(120);
/// Nodes claimed per drain — the queue's own `DEFAULT_TICK_LIMIT`.
pub const DEFAULT_DRAIN_LIMIT: u32 = 25;
/// How many recent ingestion jobs the autoresume pass looks at.
pub const RESUME_SCAN_LIMIT: u32 = 20;
/// Tick reports kept for `/host/jobs`.
pub const DEFAULT_HISTORY: usize = 20;

/// Everything the scheduler needs to know, decided once at construction.
#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    /// Worker origin, e.g. `http://127.0.0.1:4825`. No trailing slash.
    pub worker_origin: String,
    /// Delay between successful ticks.
    pub interval: Duration,
    /// Whether a tick also resumes an interrupted ingestion job.
    pub autoresume: bool,
    /// `limit` sent to `POST /api/index/drain`.
    pub drain_limit: u32,
    /// Per-request timeout for the worker calls.
    pub request_timeout: Duration,
    /// Upper bound on the failure backoff.
    pub max_backoff: Duration,
    /// Store the read-only queue counts come from.
    pub db_path: PathBuf,
    /// How many tick reports `/host/jobs` remembers.
    pub history: usize,
}

impl SchedulerConfig {
    /// Defaults for a worker at `origin`, reading the environment-resolved
    /// store (`LATTICEAI_DATA_DIR`, else `~/.ltcai`).
    pub fn new(origin: impl Into<String>) -> Self {
        Self {
            worker_origin: normalize_origin(origin.into()),
            interval: DEFAULT_INTERVAL,
            autoresume: false,
            drain_limit: DEFAULT_DRAIN_LIMIT,
            request_timeout: DEFAULT_REQUEST_TIMEOUT,
            max_backoff: MAX_BACKOFF,
            db_path: lattice_core::graph_db_path(),
            history: DEFAULT_HISTORY,
        }
    }

    /// Defaults overlaid with [`INTERVAL_ENV`] and [`AUTORESUME_ENV`].
    pub fn from_env(origin: impl Into<String>) -> Self {
        let interval = std::env::var(INTERVAL_ENV).ok();
        let autoresume = std::env::var(AUTORESUME_ENV).ok();
        Self::from_values(origin, interval.as_deref(), autoresume.as_deref())
    }

    /// The pure half of [`SchedulerConfig::from_env`].
    pub fn from_values(
        origin: impl Into<String>,
        interval: Option<&str>,
        autoresume: Option<&str>,
    ) -> Self {
        Self {
            interval: parse_interval(interval),
            autoresume: parse_flag(autoresume, false),
            ..Self::new(origin)
        }
    }

    /// Override the tick interval, still honouring [`MIN_INTERVAL`].
    pub fn with_interval(mut self, interval: Duration) -> Self {
        self.interval = interval.max(MIN_INTERVAL);
        self
    }

    /// Turn the ingestion-job autoresume pass on or off.
    pub fn with_autoresume(mut self, autoresume: bool) -> Self {
        self.autoresume = autoresume;
        self
    }

    /// Override how many nodes one drain claims.
    pub fn with_drain_limit(mut self, limit: u32) -> Self {
        self.drain_limit = limit;
        self
    }

    /// Override the per-request timeout.
    pub fn with_request_timeout(mut self, timeout: Duration) -> Self {
        self.request_timeout = timeout;
        self
    }

    /// Point the queue counts at a specific store.
    pub fn with_db_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.db_path = path.into();
        self
    }

    /// Override the ceiling on the failure backoff.
    pub fn with_max_backoff(mut self, cap: Duration) -> Self {
        self.max_backoff = cap;
        self
    }

    /// Override how many tick reports are kept.
    pub fn with_history(mut self, history: usize) -> Self {
        self.history = history;
        self
    }

    /// The store the queue counts are read from.
    pub fn db(&self) -> &Path {
        &self.db_path
    }

    /// Absolute URL for a worker path (`/api/index/drain` → `http://…/api/index/drain`).
    pub fn worker_url(&self, path: &str) -> String {
        format!("{}{}", self.worker_origin, path)
    }
}

/// `LATTICEAI_JOBS_INTERVAL` → a delay, never below [`MIN_INTERVAL`].
///
/// Whole seconds only: the value is a poll period, and accepting `0.25` would
/// invite exactly the load the floor exists to prevent.
pub fn parse_interval(raw: Option<&str>) -> Duration {
    let Some(text) = raw.map(str::trim).filter(|text| !text.is_empty()) else {
        return DEFAULT_INTERVAL;
    };
    match text.parse::<u64>() {
        Ok(seconds) => Duration::from_secs(seconds).max(MIN_INTERVAL),
        Err(_) => DEFAULT_INTERVAL,
    }
}

/// `_bool` from `latticeai/core/config.py`, in Rust.
pub fn parse_flag(raw: Option<&str>, default: bool) -> bool {
    let Some(text) = raw else { return default };
    match text.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => default,
    }
}

/// Trim any trailing slash so `worker_url` never builds `http://host//path`.
fn normalize_origin(origin: String) -> String {
    origin.trim_end_matches('/').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_interval_defaults_to_a_minute() {
        for blank in [None, Some(""), Some("   ")] {
            assert_eq!(parse_interval(blank), DEFAULT_INTERVAL);
        }
    }

    #[test]
    fn an_unparseable_interval_falls_back_rather_than_panicking() {
        for junk in ["soon", "60s", "-5", "1.5"] {
            assert_eq!(parse_interval(Some(junk)), DEFAULT_INTERVAL);
        }
    }

    #[test]
    fn the_interval_floor_cannot_be_configured_away() {
        assert_eq!(parse_interval(Some("0")), MIN_INTERVAL);
        assert_eq!(parse_interval(Some("1")), MIN_INTERVAL);
        assert_eq!(parse_interval(Some("5")), MIN_INTERVAL);
        assert_eq!(parse_interval(Some(" 90 ")), Duration::from_secs(90));
    }

    #[test]
    fn the_flag_reads_the_same_words_python_reads() {
        for yes in ["1", "true", "TRUE", "Yes", " on "] {
            assert!(parse_flag(Some(yes), false), "{yes} must be true");
        }
        for no in ["0", "false", "No", "off"] {
            assert!(!parse_flag(Some(no), true), "{no} must be false");
        }
    }

    #[test]
    fn an_unrecognised_flag_keeps_the_default() {
        assert!(!parse_flag(Some("maybe"), false));
        assert!(parse_flag(Some("maybe"), true));
        assert!(!parse_flag(None, false));
        assert!(parse_flag(None, true));
    }

    #[test]
    fn autoresume_is_off_unless_asked_for() {
        assert!(!SchedulerConfig::from_values("http://x", None, None).autoresume);
        assert!(SchedulerConfig::from_values("http://x", None, Some("1")).autoresume);
    }

    #[test]
    fn urls_are_built_without_a_doubled_slash() {
        let config = SchedulerConfig::new("http://127.0.0.1:4825/");
        assert_eq!(
            config.worker_url("/api/index/drain"),
            "http://127.0.0.1:4825/api/index/drain"
        );
    }

    #[test]
    fn the_builders_carry_every_override() {
        let config = SchedulerConfig::new("http://127.0.0.1:1")
            .with_interval(Duration::from_secs(1))
            .with_autoresume(true)
            .with_drain_limit(7)
            .with_request_timeout(Duration::from_millis(250))
            .with_max_backoff(Duration::from_secs(30))
            .with_history(3)
            .with_db_path("/tmp/elsewhere/knowledge_graph.sqlite");

        // The floor applies to the builder too, not only to the env.
        assert_eq!(config.interval, MIN_INTERVAL);
        assert!(config.autoresume);
        assert_eq!(config.drain_limit, 7);
        assert_eq!(config.request_timeout, Duration::from_millis(250));
        assert_eq!(config.max_backoff, Duration::from_secs(30));
        assert_eq!(config.history, 3);
        assert_eq!(
            config.db(),
            Path::new("/tmp/elsewhere/knowledge_graph.sqlite")
        );
        assert!(format!("{config:?}").contains("elsewhere"));
    }

    #[test]
    fn the_default_store_is_the_one_the_product_reads() {
        assert_eq!(
            SchedulerConfig::new("http://x").db_path,
            lattice_core::graph_db_path()
        );
    }
}
