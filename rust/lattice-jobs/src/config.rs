use std::path::{Path, PathBuf};
use std::time::Duration;

pub const INTERVAL_ENV: &str = "LATTICEAI_JOBS_INTERVAL";
pub const AUTORESUME_ENV: &str = "LATTICEAI_JOBS_AUTORESUME";

pub const DEFAULT_INTERVAL: Duration = Duration::from_secs(60);
pub const MIN_INTERVAL: Duration = Duration::from_secs(5);
pub const MAX_BACKOFF: Duration = Duration::from_secs(600);
pub const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(120);
pub const DEFAULT_DRAIN_LIMIT: u32 = 25;
pub const RESUME_SCAN_LIMIT: u32 = 20;
pub const DEFAULT_HISTORY: usize = 20;

#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    pub worker_origin: String,
    pub interval: Duration,
    pub autoresume: bool,
    pub drain_limit: u32,
    pub request_timeout: Duration,
    pub max_backoff: Duration,
    pub db_path: PathBuf,
    pub history: usize,
}

impl SchedulerConfig {
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

    pub fn from_env(origin: impl Into<String>) -> Self {
        let interval = std::env::var(INTERVAL_ENV).ok();
        let autoresume = std::env::var(AUTORESUME_ENV).ok();
        Self::from_values(origin, interval.as_deref(), autoresume.as_deref())
    }

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

    pub fn with_interval(mut self, interval: Duration) -> Self {
        self.interval = interval.max(MIN_INTERVAL);
        self
    }

    pub fn with_autoresume(mut self, autoresume: bool) -> Self {
        self.autoresume = autoresume;
        self
    }

    pub fn with_drain_limit(mut self, limit: u32) -> Self {
        self.drain_limit = limit;
        self
    }

    pub fn with_request_timeout(mut self, timeout: Duration) -> Self {
        self.request_timeout = timeout;
        self
    }

    pub fn with_db_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.db_path = path.into();
        self
    }

    pub fn with_max_backoff(mut self, cap: Duration) -> Self {
        self.max_backoff = cap;
        self
    }

    pub fn with_history(mut self, history: usize) -> Self {
        self.history = history;
        self
    }

    pub fn db(&self) -> &Path {
        &self.db_path
    }

    pub fn worker_url(&self, path: &str) -> String {
        format!("{}{}", self.worker_origin, path)
    }
}

pub fn parse_interval(raw: Option<&str>) -> Duration {
    let Some(text) = raw.map(str::trim).filter(|text| !text.is_empty()) else {
        return DEFAULT_INTERVAL;
    };
    match text.parse::<u64>() {
        Ok(seconds) => Duration::from_secs(seconds).max(MIN_INTERVAL),
        Err(_) => DEFAULT_INTERVAL,
    }
}

pub fn parse_flag(raw: Option<&str>, default: bool) -> bool {
    let Some(text) = raw else { return default };
    match text.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => default,
    }
}

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
