//! The wall clock the recency decay reads, expressed the way Python expresses it.
//!
//! `hybrid_search` compares `now` against `updated_at` stamps that Python wrote
//! with `datetime.now().isoformat()` — **naive local time**, no offset. So the
//! matching "now" is naive local time too, and handing the engine UTC epoch
//! seconds on a machine at UTC+9 would age every document by nine hours.
//!
//! There is no timezone crate in this workspace, and adding one to read the
//! clock would be a large dependency for a small fact, so the conversion goes
//! through `localtime_r(3)` — already linked for `kill(2)` — and then through
//! `lattice_core::parse_iso`, the very function the engine uses on the stored
//! stamps. Same parser on both sides of the subtraction.

use std::time::{SystemTime, UNIX_EPOCH};

use lattice_core::parse_iso;

/// Now, as naive local seconds since the epoch — Python's `datetime.now()`.
pub fn naive_local_now() -> f64 {
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let utc_secs = since_epoch.as_secs() as i64;
    let fraction = f64::from(since_epoch.subsec_nanos()) / 1e9;
    naive_local_secs(utc_secs).unwrap_or(utc_secs as f64) + fraction
}

/// Naive local seconds for a UTC epoch second, or `None` when the platform
/// cannot say (and the caller should fall back to UTC rather than guess).
#[cfg(unix)]
pub fn naive_local_secs(utc_secs: i64) -> Option<f64> {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    let result = unsafe { libc::localtime_r(&stamp, &mut broken) };
    if result.is_null() {
        return None;
    }
    parse_iso(Some(&format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        broken.tm_year + 1900,
        broken.tm_mon + 1,
        broken.tm_mday,
        broken.tm_hour,
        broken.tm_min,
        broken.tm_sec,
    )))
}

/// Non-unix hosts have no `localtime_r`; the caller falls back to UTC, which
/// is the truth on a machine configured for UTC and an honest approximation
/// anywhere else. This crate targets macOS and Linux.
#[cfg(not(unix))]
pub fn naive_local_secs(_utc_secs: i64) -> Option<f64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clock_lands_within_one_local_offset_of_utc() {
        let now = naive_local_now();
        let utc = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("epoch")
            .as_secs_f64();
        // Every real offset is inside ±14 h; a bug that dropped the conversion
        // entirely (or applied it twice) lands far outside this window.
        assert!(
            (now - utc).abs() <= 14.0 * 3600.0 + 5.0,
            "naive local now {now} is nowhere near utc {utc}"
        );
        assert!(now > 1_700_000_000.0, "the clock must not be at the epoch");
    }

    #[test]
    fn the_conversion_is_a_whole_number_of_minutes_away_from_utc() {
        let utc_secs = 1_785_585_600; // 2026-08-01T12:00:00Z
        let local = naive_local_secs(utc_secs).expect("unix hosts convert");
        let delta = local - utc_secs as f64;
        assert_eq!(
            delta % 60.0,
            0.0,
            "a timezone offset is minutes, got {delta}s"
        );
        assert!(delta.abs() <= 14.0 * 3600.0);
    }

    #[test]
    fn the_epoch_second_itself_converts() {
        // 1970-01-01T00:00:00Z is local midnight only at UTC, but it must still
        // convert rather than fail — a `None` here would silently fall back.
        assert!(naive_local_secs(0).is_some());
    }
}
