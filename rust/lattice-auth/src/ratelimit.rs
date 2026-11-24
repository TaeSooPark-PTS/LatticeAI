//! Bounded in-memory rate limiters with a unified HTTP 429 contract.
//!
//! * [`RateLimiter::check_ip`] is the sliding window guarding `/login`
//!   (10 per 300s) and `/register` (5 per 3600s), keyed on `(ip, action)`.
//! * [`RateLimiter::enforce`] is the per-user token bucket guarding seam and
//!   upload routes, keyed on `"{email}:{bucket}"`.
//!
//! Both maps are strictly capped (`MAX_ENTRIES`) with expired-first and
//! oldest-entry eviction to prevent memory exhaustion under distributed spray
//! attacks, recover safely from lock poison, and emit a consistent 429 response
//! carrying `Retry-After` and `"error": "rate_limited"`.

use std::collections::HashMap;
use std::sync::Mutex;

use axum::http::{header, HeaderValue, StatusCode};
use axum::response::Response;
use serde_json::json;

use crate::clock::Clock;
use crate::response::json_response;

/// Maximum tracked sliding-window or token-bucket entries per map.
pub const MAX_ENTRIES: usize = 10_000;
/// Idle age (seconds) after which a full token bucket may be pruned on cap.
const BUCKET_IDLE_PRUNE_SECS: f64 = 3600.0;

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

#[derive(Debug, Clone)]
struct WindowEntry {
    stamps: Vec<f64>,
    window_secs: f64,
}

/// Both limiters, sharing one clock.
#[derive(Debug)]
pub struct RateLimiter {
    clock: Clock,
    windows: Mutex<HashMap<(String, String), WindowEntry>>,
    buckets: Mutex<HashMap<String, Bucket>>,
    max_entries: usize,
}

impl RateLimiter {
    /// A limiter with no history and the default capacity cap.
    pub fn new(clock: Clock) -> Self {
        Self::with_capacity(clock, MAX_ENTRIES)
    }

    /// A limiter with an explicit per-map capacity cap (primarily for tests).
    pub fn with_capacity(clock: Clock, max_entries: usize) -> Self {
        Self {
            clock,
            windows: Mutex::new(HashMap::new()),
            buckets: Mutex::new(HashMap::new()),
            max_entries: max_entries.max(1),
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
        let mut guard = self
            .windows
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let calls: Vec<f64> = guard
            .get(&key)
            .map(|entry| {
                entry
                    .stamps
                    .iter()
                    .copied()
                    .filter(|stamp| *stamp > cutoff)
                    .collect()
            })
            .unwrap_or_default();
        if calls.len() >= max_calls {
            let earliest = calls.iter().copied().fold(now, f64::min);
            let retry_after = ((earliest + window_secs - now).ceil() as i64).max(1);
            return Err(ip_rate_limited(action, retry_after));
        }
        if !guard.contains_key(&key) && guard.len() >= self.max_entries {
            // 1. Prune expired windows first.
            guard.retain(|_, entry| {
                let entry_cutoff = now - entry.window_secs;
                entry.stamps.iter().any(|stamp| *stamp > entry_cutoff)
            });
            // 2. If still at capacity, evict the entry with the oldest last hit.
            if guard.len() >= self.max_entries {
                if let Some(oldest_key) = guard
                    .iter()
                    .min_by(|(_, a), (_, b)| {
                        let a_last = a.stamps.last().copied().unwrap_or(f64::NEG_INFINITY);
                        let b_last = b.stamps.last().copied().unwrap_or(f64::NEG_INFINITY);
                        a_last.total_cmp(&b_last)
                    })
                    .map(|(k, _)| k.clone())
                {
                    guard.remove(&oldest_key);
                }
            }
        }
        let mut calls = calls;
        calls.push(now);
        guard.insert(
            key,
            WindowEntry {
                stamps: calls,
                window_secs,
            },
        );
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
        let mut guard = self
            .buckets
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(bucket) = guard.get_mut(&key) {
            let elapsed = (now - bucket.timestamp).max(0.0);
            bucket.tokens = (bucket.tokens + elapsed * refill).min(cap as f64);
            bucket.timestamp = now;
            if bucket.tokens < 1.0 {
                let retry_after = (((1.0 - bucket.tokens) / refill) as i64).max(1);
                return Err(bucket_rate_limited(bucket_key, retry_after));
            }
            bucket.tokens -= 1.0;
            return Ok(());
        }
        if guard.len() >= self.max_entries {
            guard.retain(|_, b| now - b.timestamp < BUCKET_IDLE_PRUNE_SECS);
            if guard.len() >= self.max_entries {
                if let Some(oldest_key) = guard
                    .iter()
                    .min_by(|(_, a), (_, b)| a.timestamp.total_cmp(&b.timestamp))
                    .map(|(k, _)| k.clone())
                {
                    guard.remove(&oldest_key);
                }
            }
        }
        guard.insert(
            key,
            Bucket {
                tokens: (cap - 1) as f64,
                timestamp: now,
            },
        );
        Ok(())
    }
}

/// Unified 429 response for the sliding window limiter (`Retry-After` + `error: "rate_limited"`).
pub fn ip_rate_limited(action: &str, retry_after: i64) -> Response {
    let retry_after = retry_after.max(1);
    let detail = format!("Rate limit exceeded for {action}. Retry after {retry_after}s.");
    let value = HeaderValue::from_str(&retry_after.to_string())
        .unwrap_or_else(|_| HeaderValue::from_static("1"));
    json_response(
        StatusCode::TOO_MANY_REQUESTS,
        &json!({ "detail": detail, "error": "rate_limited", "retry_after": retry_after })
            .to_string(),
        Some((header::RETRY_AFTER, value)),
    )
}

/// Unified 429 response for the token bucket limiter (`Retry-After` + `error: "rate_limited"`).
pub fn bucket_rate_limited(bucket_key: &str, retry_after: i64) -> Response {
    let retry_after = retry_after.max(1);
    let detail = format!("Rate limit exceeded for {bucket_key}. Retry after {retry_after}s.");
    let value = HeaderValue::from_str(&retry_after.to_string())
        .unwrap_or_else(|_| HeaderValue::from_static("1"));
    json_response(
        StatusCode::TOO_MANY_REQUESTS,
        &json!({ "detail": detail, "error": "rate_limited", "retry_after": retry_after })
            .to_string(),
        Some((header::RETRY_AFTER, value)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_window_refuses_the_call_after_the_cap_with_retry_after() {
        let clock = Clock::frozen(1_000.0);
        let limiter = RateLimiter::new(clock.clone());
        for _ in 0..10 {
            assert!(limiter.check_ip("1.2.3.4", "login", 10, 300.0).is_ok());
        }
        let refused = limiter.check_ip("1.2.3.4", "login", 10, 300.0).unwrap_err();
        assert_eq!(refused.status(), StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(refused.headers().get(header::RETRY_AFTER).unwrap(), "300");

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
        let refused = limiter.check_ip("1.2.3.4", "login", 2, 300.0).unwrap_err();
        assert_eq!(refused.headers().get(header::RETRY_AFTER).unwrap(), "299");
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
    fn maps_are_bounded_and_evict_oldest_entries_under_spray() {
        let clock = Clock::frozen(1_000.0);
        let limiter = RateLimiter::with_capacity(clock.clone(), 4);
        for i in 0..20 {
            clock.advance(1.0);
            assert!(limiter
                .check_ip(&format!("10.0.0.{i}"), "login", 5, 300.0)
                .is_ok());
            assert!(limiter
                .enforce(&format!("u{i}@example.com"), "agent", true)
                .is_ok());
        }
        assert!(limiter.windows.lock().unwrap().len() <= 4);
        assert!(limiter.buckets.lock().unwrap().len() <= 4);
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
