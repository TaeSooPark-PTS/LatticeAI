//! The four clock readings the write path takes, behind one seam.
//!
//! Python reaches them as free functions (`_now()`, `time.time()`,
//! `os.getpid()`, `time.perf_counter()`), which is why the golden generator has
//! to monkey-patch eleven module namespaces to pin them. The Rust side takes
//! them as a trait instead, so the parity replay configures a clock rather than
//! rewriting the engine's globals — and so a frozen clock is a *test* fixture
//! and never a production code path.
//!
//! [`SystemClock::now_iso`] is `datetime.now().isoformat(timespec="seconds")` —
//! **naive local** time with no offset. Stamping UTC on a machine at UTC+9
//! would make every row nine hours old to the string comparisons every timeline
//! read in the product does against stamps Python wrote.

use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// Everything the write engine asks the outside world about time.
pub trait Clock: Send + Sync + std::fmt::Debug {
    /// `lattice_brain.utils.now_iso()` — naive local, second resolution.
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

/// `datetime.now().isoformat(timespec="seconds")` for a naive-local epoch second.
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

/// Broken-down local wall-clock fields for a UTC epoch second.
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

/// Convert a UTC epoch second to broken-down local wall-clock parts via `localtime_r(3)`.
#[cfg(unix)]
pub fn local_wall_parts(utc_secs: i64) -> Option<LocalWallParts> {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    if unsafe { libc::localtime_r(&stamp, &mut broken) }.is_null() {
        return None;
    }
    Some(LocalWallParts {
        year: broken.tm_year + 1900,
        month: broken.tm_mon as u32 + 1,
        day: broken.tm_mday as u32,
        hour: broken.tm_hour as u32,
        minute: broken.tm_min as u32,
        second: broken.tm_sec as u32,
        gmtoff: i64::from(broken.tm_gmtoff as i32),
    })
}

/// Non-unix fallback: no `localtime_r`, returns `None` so callers fall back honestly.
#[cfg(not(unix))]
pub fn local_wall_parts(_utc_secs: i64) -> Option<LocalWallParts> {
    None
}

/// `datetime.fromtimestamp(utc_secs).isoformat(timespec="seconds")` in local wall time.
pub fn local_iso_for(utc_secs: i64) -> Option<String> {
    let parts = local_wall_parts(utc_secs)?;
    Some(format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        parts.year, parts.month, parts.day, parts.hour, parts.minute, parts.second
    ))
}

/// Naive local seconds since epoch for a UTC epoch second.
pub fn naive_local_secs(utc_secs: i64) -> Option<i64> {
    #[cfg(unix)]
    {
        local_wall_parts(utc_secs).map(|parts| utc_secs + parts.gmtoff)
    }
    #[cfg(not(unix))]
    {
        Some(utc_secs)
    }
}

/// The UTC offset (in seconds) this machine had at a naive wall-clock timestamp.
#[cfg(unix)]
pub fn local_offset_for_naive_wall(naive_secs: f64) -> f64 {
    let stamp = naive_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `gmtime_r` fills the caller-owned `tm` we just zeroed; nothing
    // else is read or written. A null answer leaves the offset at zero.
    if unsafe { libc::gmtime_r(&stamp, &mut broken) }.is_null() {
        return 0.0;
    }
    broken.tm_isdst = -1;
    // SAFETY: `mktime` reads the `tm` we own and may normalise it in place.
    let back = unsafe { libc::mktime(&mut broken) };
    if back == -1 {
        return 0.0;
    }
    naive_secs - back as f64
}

/// Non-unix fallback for `local_offset_for_naive_wall`.
#[cfg(not(unix))]
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
}
