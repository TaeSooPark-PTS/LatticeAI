//! When the next tick is due.
//!
//! One rule, kept in its own file because it is the part a reader will want to
//! check by hand: a successful tick is followed by the configured interval, and
//! each consecutive failure doubles that delay up to a cap. A worker that is
//! down therefore costs one request a minute for a minute, then one every two,
//! four, eight … up to one every ten minutes — instead of a request a minute
//! forever against something that is not answering.
//!
//! The reset is on *success*, not on elapsed time: a worker that answers once
//! has proven the thing the backoff was waiting for.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::time::Duration;

/// Doubling delay with a floor at the interval and a ceiling at the cap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Backoff {
    interval: Duration,
    cap: Duration,
    current: Duration,
}

impl Backoff {
    /// A backoff sitting at its healthy delay.
    ///
    /// A cap below the interval would mean "back off to sooner than normal";
    /// it is raised to the interval so the delay can never shrink on failure.
    pub fn new(interval: Duration, cap: Duration) -> Self {
        Self {
            interval,
            cap: cap.max(interval),
            current: interval,
        }
    }

    /// The delay before the next tick.
    pub fn delay(&self) -> Duration {
        self.current
    }

    /// The healthy delay, whatever the current one is.
    pub fn interval(&self) -> Duration {
        self.interval
    }

    /// Whether the delay is currently stretched by failures.
    pub fn is_backing_off(&self) -> bool {
        self.current > self.interval
    }

    /// A tick answered: back to the configured interval.
    pub fn succeeded(&mut self) {
        self.current = self.interval;
    }

    /// A tick failed: double the delay, never past the cap.
    pub fn failed(&mut self) {
        let doubled = self.current.checked_mul(2).unwrap_or(self.cap);
        self.current = doubled.min(self.cap);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backoff() -> Backoff {
        Backoff::new(Duration::from_secs(60), Duration::from_secs(600))
    }

    #[test]
    fn a_healthy_scheduler_ticks_on_the_interval() {
        let backoff = backoff();
        assert_eq!(backoff.delay(), Duration::from_secs(60));
        assert_eq!(backoff.interval(), Duration::from_secs(60));
        assert!(!backoff.is_backing_off());
    }

    #[test]
    fn each_failure_doubles_the_delay_up_to_the_cap() {
        let mut backoff = backoff();
        let mut seen = Vec::new();
        for _ in 0..8 {
            backoff.failed();
            seen.push(backoff.delay().as_secs());
        }
        assert_eq!(seen, vec![120, 240, 480, 600, 600, 600, 600, 600]);
        assert!(backoff.is_backing_off());
    }

    #[test]
    fn one_success_pays_off_the_whole_penalty() {
        let mut backoff = backoff();
        for _ in 0..5 {
            backoff.failed();
        }
        assert_eq!(backoff.delay(), Duration::from_secs(600));

        backoff.succeeded();

        assert_eq!(backoff.delay(), Duration::from_secs(60));
        assert!(!backoff.is_backing_off());
    }

    #[test]
    fn the_floor_interval_backs_off_from_five_seconds() {
        let mut backoff = Backoff::new(Duration::from_secs(5), Duration::from_secs(600));
        let mut seen = Vec::new();
        for _ in 0..9 {
            backoff.failed();
            seen.push(backoff.delay().as_secs());
        }
        assert_eq!(seen, vec![10, 20, 40, 80, 160, 320, 600, 600, 600]);
    }

    #[test]
    fn a_cap_below_the_interval_never_shortens_the_delay() {
        let mut backoff = Backoff::new(Duration::from_secs(60), Duration::from_secs(1));
        assert_eq!(backoff.delay(), Duration::from_secs(60));
        backoff.failed();
        assert_eq!(backoff.delay(), Duration::from_secs(60));
    }

    #[test]
    fn an_absurd_delay_cannot_overflow_into_a_short_one() {
        let mut backoff = Backoff::new(Duration::MAX, Duration::MAX);
        backoff.failed();
        assert_eq!(backoff.delay(), Duration::MAX);
    }
}
