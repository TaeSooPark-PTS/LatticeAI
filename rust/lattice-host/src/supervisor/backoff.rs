//! Restart backoff schedule.
//!
//! Exponential with a cap, and a "the worker actually stayed up" reset so a
//! long-lived worker that dies once restarts immediately instead of inheriting
//! yesterday's penalty.

use std::time::Duration;

/// Exponential backoff parameters for crash restarts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BackoffPolicy {
    /// Delay before the first restart.
    pub base: Duration,
    /// Upper bound for any single delay.
    pub cap: Duration,
    /// How many consecutive restarts to attempt before giving up.
    pub max_attempts: u32,
    /// A run that lasted at least this long resets the attempt counter.
    pub reset_after: Duration,
}

impl Default for BackoffPolicy {
    fn default() -> Self {
        Self {
            base: Duration::from_millis(500),
            cap: Duration::from_secs(30),
            max_attempts: 20,
            reset_after: Duration::from_secs(60),
        }
    }
}

impl BackoffPolicy {
    /// Delay before restart attempt `attempt` (1-based): `base * 2^(attempt-1)`
    /// clamped to `cap`. Attempt `0` is treated as attempt `1`.
    pub fn delay_for(&self, attempt: u32) -> Duration {
        let step = attempt.saturating_sub(1).min(32);
        let factor = 1u64 << step;
        let millis = (self.base.as_millis() as u64).saturating_mul(factor);
        Duration::from_millis(millis).min(self.cap)
    }

    /// Whether a further restart is allowed after `attempt` failures.
    pub fn may_retry(&self, attempt: u32) -> bool {
        attempt <= self.max_attempts
    }

    /// Whether a run of `uptime` counts as "healthy enough" to reset the
    /// attempt counter.
    pub fn resets(&self, uptime: Duration) -> bool {
        uptime >= self.reset_after
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> BackoffPolicy {
        BackoffPolicy {
            base: Duration::from_millis(500),
            cap: Duration::from_secs(30),
            max_attempts: 3,
            reset_after: Duration::from_secs(60),
        }
    }

    #[test]
    fn schedule_doubles_from_the_base() {
        let p = policy();
        let millis: Vec<u128> = (1..=8).map(|n| p.delay_for(n).as_millis()).collect();
        assert_eq!(
            millis,
            vec![500, 1000, 2000, 4000, 8000, 16000, 30000, 30000]
        );
    }

    #[test]
    fn attempt_zero_behaves_like_the_first_attempt() {
        assert_eq!(policy().delay_for(0), Duration::from_millis(500));
    }

    #[test]
    fn cap_is_never_exceeded_even_for_absurd_attempt_counts() {
        assert_eq!(policy().delay_for(u32::MAX), Duration::from_secs(30));
    }

    #[test]
    fn max_attempts_is_inclusive_then_stops() {
        let p = policy();
        assert!(p.may_retry(1));
        assert!(p.may_retry(3));
        assert!(!p.may_retry(4));
    }

    #[test]
    fn reset_threshold_is_inclusive() {
        let p = policy();
        assert!(!p.resets(Duration::from_secs(59)));
        assert!(p.resets(Duration::from_secs(60)));
    }

    #[test]
    fn defaults_match_the_documented_shape() {
        let d = BackoffPolicy::default();
        assert_eq!(d.base, Duration::from_millis(500));
        assert_eq!(d.cap, Duration::from_secs(30));
    }
}
