//! `time.time()` as an injectable seam.
//!
//! Session expiry, the sliding-window IP limiter and the per-user token bucket
//! all read the wall clock, and all three are things a fixture has to be able
//! to replay. A frozen clock is the difference between a parity test that
//! proves the refill arithmetic and one that hopes the machine was fast enough.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// Seconds since the Unix epoch, as a float — Python's `time.time()`.
#[derive(Clone)]
pub struct Clock {
    frozen: Option<Arc<AtomicU64>>,
}

impl std::fmt::Debug for Clock {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("Clock")
            .field("now", &self.now())
            .field("frozen", &self.frozen.is_some())
            .finish()
    }
}

impl Default for Clock {
    fn default() -> Self {
        Self::system()
    }
}

impl Clock {
    /// The real wall clock.
    pub fn system() -> Self {
        Self { frozen: None }
    }

    /// A clock that stands still at `seconds` until [`Clock::advance`].
    pub fn frozen(seconds: f64) -> Self {
        Self {
            frozen: Some(Arc::new(AtomicU64::new(seconds.to_bits()))),
        }
    }

    /// The current time in seconds.
    pub fn now(&self) -> f64 {
        match &self.frozen {
            Some(cell) => f64::from_bits(cell.load(Ordering::Relaxed)),
            None => SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|elapsed| elapsed.as_secs_f64())
                .unwrap_or(0.0),
        }
    }

    /// Move a frozen clock forward. A no-op on the system clock.
    pub fn advance(&self, seconds: f64) {
        if let Some(cell) = &self.frozen {
            let next = f64::from_bits(cell.load(Ordering::Relaxed)) + seconds;
            cell.store(next.to_bits(), Ordering::Relaxed);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_system_clock_is_after_2020() {
        assert!(Clock::system().now() > 1_577_836_800.0);
    }

    #[test]
    fn a_frozen_clock_only_moves_when_told_to() {
        let clock = Clock::frozen(1_000.5);
        assert_eq!(clock.now(), 1_000.5);
        assert_eq!(clock.now(), 1_000.5);
        clock.advance(2.25);
        assert_eq!(clock.now(), 1_002.75);
        assert!(format!("{clock:?}").contains("frozen: true"));
    }

    #[test]
    fn advancing_the_system_clock_does_nothing() {
        let clock = Clock::default();
        let before = clock.now();
        clock.advance(1_000.0);
        assert!(clock.now() - before < 1.0);
    }
}
