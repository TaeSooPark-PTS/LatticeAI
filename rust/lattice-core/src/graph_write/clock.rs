//! The four clock readings the write path takes, behind one seam.
//!
//! Python reaches them as free functions (`_now()`, `time.time()`,
//! `os.getpid()`, `time.perf_counter()`), which is why the golden generator has
//! to monkey-patch eleven module namespaces to pin them. The Rust side takes
//! them as a trait instead, so the parity replay configures a clock rather than
//! rewriting the engine's globals — and so a frozen clock is a *test* fixture
//! and never a production code path.
//!
//! [`SystemClock::now_iso`] and Python's `lattice_brain.utils.now_iso()` both
//! emit **offset-free UTC** timestamps (`YYYY-MM-DDTHH:MM:SS`, `gmtoff: 0`).
//! Keeping the offset-free second-precision shape preserves string-comparison
//! compatibility with existing persisted rows while unifying Rust and Python on
//! UTC so non-UTC hosts (e.g. UTC+9) never observe timezone skew.

use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// Everything the write engine asks the outside world about time.
pub trait Clock: Send + Sync + std::fmt::Debug {
    /// `lattice_brain.utils.now_iso()` — offset-free UTC, second resolution.
    fn now_iso(&self) -> String;
    /// `time.time()` — seeds `vector_index_operations.id`.
    fn unix_time(&self) -> f64;
    /// `os.getpid()` — the other half of that seed.
    fn pid(&self) -> u32;
    /// `time.perf_counter()` — measures `duration_ms`, never stored identity.
    fn perf_counter(&self) -> f64;
}

/// The real clock.
#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_iso(&self) -> String {
        iso_seconds_for(naive_local_secs_now())
    }

    fn unix_time(&self) -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs_f64())
            .unwrap_or(0.0)
    }

    fn pid(&self) -> u32 {
        std::process::id()
    }

    fn perf_counter(&self) -> f64 {
        // CPython's `perf_counter` is an arbitrary monotonic origin; only
        // differences are meaningful, which is exactly what an epoch-relative
        // reading provides.
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs_f64())
            .unwrap_or(0.0)
    }
}

/// A clock pinned for a parity replay.
///
/// `unix_time` walks a recorded sequence, one value per call, because it seeds
/// a primary key: a clock that stands still would make the second rebuild
/// collide with the first. Past the end of the sequence it repeats the last
/// value rather than inventing one — a replay that asks more often than the
/// recording did has diverged, and should fail on the collision.
#[derive(Debug)]
pub struct FrozenClock {
    now: String,
    times: Mutex<std::collections::VecDeque<f64>>,
    last_time: Mutex<f64>,
    pid: u32,
    perf: f64,
}

impl FrozenClock {
    /// A clock pinned to `now`, handing out `times` in order.
    pub fn new(now: impl Into<String>, times: Vec<f64>, pid: u32, perf: f64) -> Self {
        Self {
            now: now.into(),
            last_time: Mutex::new(times.last().copied().unwrap_or(0.0)),
            times: Mutex::new(times.into_iter().collect()),
            pid,
            perf,
        }
    }
}

impl Clock for FrozenClock {
    fn now_iso(&self) -> String {
        self.now.clone()
    }

    fn unix_time(&self) -> f64 {
        let mut queue = self.times.lock().expect("frozen clock queue poisoned");
        match queue.pop_front() {
            Some(value) => {
                *self.last_time.lock().expect("frozen clock tail poisoned") = value;
                value
            }
            None => *self.last_time.lock().expect("frozen clock tail poisoned"),
        }
    }

    fn pid(&self) -> u32 {
        self.pid
    }

    fn perf_counter(&self) -> f64 {
        self.perf
    }
}

/// `lattice_brain.utils.now_iso()` (`YYYY-MM-DDTHH:MM:SS`) for a UTC epoch second.
pub fn iso_seconds_for(secs: i64) -> String {
    let (year, month, day) = civil_from_days(secs.div_euclid(86_400));
    let time_of_day = secs.rem_euclid(86_400);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        time_of_day / 3600,
        (time_of_day % 3600) / 60,
        time_of_day % 60,
    )
}

fn naive_local_secs_now() -> i64 {
    let utc = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    naive_local_secs(utc).unwrap_or(utc)
}

/// Broken-down UTC wall-clock fields for a UTC epoch second.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LocalWallParts {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub gmtoff: i64,
}

/// Convert a UTC epoch second to broken-down UTC wall-clock parts using pure
/// Rust arithmetic (zero `unsafe`, zero `libc`, identical across Unix and
/// Windows).
pub fn local_wall_parts(utc_secs: i64) -> Option<LocalWallParts> {
    let (year, month, day) = civil_from_days(utc_secs.div_euclid(86_400));
    let time_of_day = utc_secs.rem_euclid(86_400);
    let year = i32::try_from(year).ok()?;
    Some(LocalWallParts {
        year,
        month,
        day,
        hour: (time_of_day / 3600) as u32,
        minute: ((time_of_day % 3600) / 60) as u32,
        second: (time_of_day % 60) as u32,
        gmtoff: 0,
    })
}

/// Format a UTC epoch second as `YYYY-MM-DDTHH:MM:SS` (pure Rust, cross-platform).
pub fn local_iso_for(utc_secs: i64) -> Option<String> {
    let parts = local_wall_parts(utc_secs)?;
    Some(format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        parts.year, parts.month, parts.day, parts.hour, parts.minute, parts.second
    ))
}

/// Format a UTC epoch second as RFC 3339 (`YYYY-MM-DDTHH:MM:SSZ`).
pub fn rfc3339_utc_for(utc_secs: i64) -> String {
    format!("{}Z", iso_seconds_for(utc_secs))
}

/// UTC seconds since epoch for a UTC epoch second (pure Rust, cross-platform).
pub fn naive_local_secs(utc_secs: i64) -> Option<i64> {
    Some(utc_secs)
}

/// UTC offset (seconds) for a naive wall-clock timestamp — `0.0` under UTC-canonical timestamps.
pub fn local_offset_for_naive_wall(_naive_secs: f64) -> f64 {
    0.0
}

/// Howard Hinnant's `civil_from_days`: days since 1970-01-01 → `(y, m, d)`.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    (year + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_seconds_matches_pythons_isoformat() {
        // Cross-checked against `datetime.fromtimestamp(…, UTC).isoformat()`.
        assert_eq!(iso_seconds_for(1_785_542_400), "2026-08-01T00:00:00");
        assert_eq!(iso_seconds_for(1_785_931_200), "2026-08-05T12:00:00");
        assert_eq!(iso_seconds_for(0), "1970-01-01T00:00:00");
    }

    #[test]
    fn frozen_clock_walks_its_recorded_sequence_then_holds() {
        let clock = FrozenClock::new("2026-08-01T12:00:00", vec![1.0, 2.0], 7, 0.0);
        assert_eq!(clock.unix_time(), 1.0);
        assert_eq!(clock.unix_time(), 2.0);
        assert_eq!(clock.unix_time(), 2.0);
        assert_eq!(clock.now_iso(), "2026-08-01T12:00:00");
        assert_eq!(clock.pid(), 7);
        assert_eq!(clock.perf_counter(), 0.0);
    }

    #[test]
    fn the_system_clock_answers_all_four_readings() {
        let clock = SystemClock;
        assert_eq!(clock.now_iso().len(), 19);
        assert!(clock.unix_time() > 1_600_000_000.0);
        assert!(clock.pid() > 0);
        assert!(clock.perf_counter() > 0.0);
    }

    #[test]
    fn system_clock_and_wall_parts_are_utc_canonical() {
        let utc_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock after epoch")
            .as_secs() as i64;
        let parts = local_wall_parts(utc_secs).expect("utc parts");
        assert_eq!(parts.gmtoff, 0);
        assert_eq!(local_offset_for_naive_wall(utc_secs as f64), 0.0);
        assert_eq!(naive_local_secs(utc_secs), Some(utc_secs));

        let iso = local_iso_for(utc_secs).expect("utc iso");
        assert_eq!(iso, iso_seconds_for(utc_secs));
        assert_eq!(rfc3339_utc_for(utc_secs), format!("{iso}Z"));

        // SystemClock::now_iso() must match current UTC (within 2s tolerance),
        // never shifted by a host local offset such as UTC+9.
        let now_stamp = SystemClock.now_iso();
        let candidates: Vec<String> = (-2..=2)
            .map(|delta| iso_seconds_for(utc_secs + delta))
            .collect();
        assert!(
            candidates.contains(&now_stamp),
            "expected SystemClock::now_iso() ({now_stamp}) within ±2s of UTC ({iso})"
        );
    }
}
