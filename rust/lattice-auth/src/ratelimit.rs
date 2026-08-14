//! Both limiters from `latticeai/core/security.py`, with their exact refusals.
//!
//! * [`RateLimiter::check_ip`] is the sliding window that guards `/login`
//!   (10 per 300s) and `/register` (5 per 3600s), keyed on `(ip, action)`. It
//!   answers 429 with a Korean literal and **no** `Retry-After`.
//! * [`RateLimiter::enforce`] is the per-user token bucket the seam and upload
//!   routes call, keyed on `"{email}:{bucket}"`. It answers 429 with an English
//!   sentence naming the bucket, **and** a `Retry-After`.
//!
//! Two details are easy to lose and both are load-bearing: a refused sliding
//! window does *not* write its pruned list back (so the window keeps every
//! timestamp it had), and a bucket's first-ever call is granted without any
//! refill arithmetic at all.

use std::collections::HashMap;
use std::sync::Mutex;

use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;
use serde_json::json;

use crate::clock::Clock;
use crate::messages::IP_RATE_LIMITED_LITERAL;
use crate::response::json_response;

/// `_RATE_LIMITS`: `(capacity, refill per second)` per bucket.
fn bucket_limits(bucket_key: &str) -> (i64, f64) {
    match bucket_key {
        "chat" => (30, 0.5),
        "agent" => (10, 0.1),
        "upload" => (20, 0.2),
        _ => (60, 1.0),
    }
}

/// `check_ip_rate_limit`'s limits for the two auth routes that call it.
pub const LOGIN_LIMIT: (usize, f64) = (10, 300.0);
/// Registration is the slower of the two windows.
pub const REGISTER_LIMIT: (usize, f64) = (5, 3600.0);

#[derive(Debug, Clone, Copy)]
struct Bucket {
    tokens: f64,
    timestamp: f64,
}

/// Both limiters, sharing one clock.
#[derive(Debug)]
pub struct RateLimiter {
    clock: Clock,
    windows: Mutex<HashMap<(String, String), Vec<f64>>>,
    buckets: Mutex<HashMap<String, Bucket>>,
}

impl RateLimiter {
    /// A limiter with no history.
    pub fn new(clock: Clock) -> Self {
        Self {
            clock,
            windows: Mutex::new(HashMap::new()),
            buckets: Mutex::new(HashMap::new()),
        }
    }

    /// `check_ip_rate_limit(ip, action, max_calls, window_secs)`.
    pub fn check_ip(
        &self,
        ip: &str,
        action: &str,
        max_calls: usize,
        window_secs: f64,
    ) -> Result<(), Response> {
        let now = self.clock.now();
        let cutoff = now - window_secs;
        let key = (ip.to_string(), action.to_string());
        let mut guard = self.windows.lock().expect("rate window lock");
        let calls: Vec<f64> = guard
            .get(&key)
            .map(|history| {
                history
                    .iter()
                    .copied()
                    .filter(|stamp| *stamp > cutoff)
                    .collect()
            })
            .unwrap_or_default();
        if calls.len() >= max_calls {
            // Deliberately not written back: the Python original raises before
            // it reassigns, so the untrimmed history stays.
            return Err(ip_rate_limited());
        }
        let mut calls = calls;
        calls.push(now);
        guard.insert(key, calls);
        Ok(())
    }

    /// `enforce_rate_limit(email, bucket_key, enabled=...)`.
    pub fn enforce(&self, email: &str, bucket_key: &str, enabled: bool) -> Result<(), Response> {
        if !enabled || email.is_empty() {
            return Ok(());
        }
        let (cap, refill) = bucket_limits(bucket_key);
        let key = format!("{email}:{bucket_key}");
        let now = self.clock.now();
        let mut guard = self.buckets.lock().expect("rate bucket lock");
        let Some(bucket) = guard.get_mut(&key) else {
            guard.insert(
                key,
                Bucket {
                    tokens: (cap - 1) as f64,
                    timestamp: now,
                },
            );
            return Ok(());
        };
        let elapsed = now - bucket.timestamp;
        bucket.tokens = (bucket.tokens + elapsed * refill).min(cap as f64);
        bucket.timestamp = now;
        if bucket.tokens < 1.0 {
            let retry_after = (((1.0 - bucket.tokens) / refill) as i64).max(1);
            return Err(bucket_rate_limited(bucket_key, retry_after));
        }
        bucket.tokens -= 1.0;
        Ok(())
    }
}

/// The sliding window's 429.
pub fn ip_rate_limited() -> Response {
    json_response(
        StatusCode::TOO_MANY_REQUESTS,
        &json!({ "detail": IP_RATE_LIMITED_LITERAL }).to_string(),
        None,
    )
}

/// The token bucket's 429, with the `Retry-After` the Python raise carries.
pub fn bucket_rate_limited(bucket_key: &str, retry_after: i64) -> Response {
    let detail = format!("Rate limit exceeded for {bucket_key}. Retry after {retry_after}s.");
    let value = HeaderValue::from_str(&retry_after.to_string())
        .unwrap_or_else(|_| HeaderValue::from_static("1"));
    json_response(
        StatusCode::TOO_MANY_REQUESTS,
        &json!({ "detail": detail }).to_string(),
        Some((header::RETRY_AFTER, value)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_window_refuses_the_call_after_the_cap() {
        let clock = Clock::frozen(1_000.0);
        let limiter = RateLimiter::new(clock.clone());
        for _ in 0..10 {
            assert!(limiter.check_ip("1.2.3.4", "login", 10, 300.0).is_ok());
        }
        let refused = limiter.check_ip("1.2.3.4", "login", 10, 300.0).unwrap_err();
        assert_eq!(refused.status(), StatusCode::TOO_MANY_REQUESTS);
        assert!(refused.headers().get(header::RETRY_AFTER).is_none());

        // A different key has its own window.
        assert!(limiter.check_ip("1.2.3.5", "login", 10, 300.0).is_ok());
        assert!(limiter.check_ip("1.2.3.4", "register", 5, 3600.0).is_ok());

        // Old calls fall out of the window.
        clock.advance(301.0);
        assert!(limiter.check_ip("1.2.3.4", "login", 10, 300.0).is_ok());
    }

    #[test]
    fn a_refused_window_keeps_its_history() {
        let clock = Clock::frozen(1_000.0);
        let limiter = RateLimiter::new(clock.clone());
        for _ in 0..2 {
            limiter.check_ip("1.2.3.4", "login", 2, 300.0).unwrap();
        }
        assert!(limiter.check_ip("1.2.3.4", "login", 2, 300.0).is_err());
        clock.advance(1.0);
        // Still two stamps inside the window, so still refused.
        assert!(limiter.check_ip("1.2.3.4", "login", 2, 300.0).is_err());
    }

    #[test]
    fn the_bucket_grants_its_capacity_then_refuses() {
        let clock = Clock::frozen(1_000.0);
        let limiter = RateLimiter::new(clock.clone());
        for _ in 0..10 {
            assert!(limiter.enforce("a@b.com", "agent", true).is_ok());
        }
        let refused = limiter.enforce("a@b.com", "agent", true).unwrap_err();
        assert_eq!(refused.status(), StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(refused.headers().get(header::RETRY_AFTER).unwrap(), "10");

        // Refill at 0.1/s: ten seconds buys exactly one call back.
        clock.advance(10.0);
        assert!(limiter.enforce("a@b.com", "agent", true).is_ok());
    }

    #[test]
    fn an_unknown_bucket_uses_the_default_limits() {
        assert_eq!(bucket_limits("nope"), (60, 1.0));
        assert_eq!(bucket_limits("chat"), (30, 0.5));
        assert_eq!(bucket_limits("upload"), (20, 0.2));
        let limiter = RateLimiter::new(Clock::frozen(1.0));
        for _ in 0..60 {
            assert!(limiter.enforce("a@b.com", "nope", true).is_ok());
        }
        assert!(limiter.enforce("a@b.com", "nope", true).is_err());
    }

    #[test]
    fn the_bucket_is_skipped_when_disabled_or_anonymous() {
        let limiter = RateLimiter::new(Clock::frozen(1.0));
        for _ in 0..100 {
            assert!(limiter.enforce("a@b.com", "agent", false).is_ok());
            assert!(limiter.enforce("", "agent", true).is_ok());
        }
    }

    #[test]
    fn the_bucket_body_names_the_bucket() {
        let response = bucket_rate_limited("upload", 4);
        assert_eq!(response.headers().get(header::RETRY_AFTER).unwrap(), "4");
        assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
    }
}
