//! HTTP health probing.
//!
//! Replaces the desktop shell's TCP-connect probe: a socket that accepts a
//! connection proves only that *something* is listening, not that the worker
//! finished booting. The worker serves `GET /health` unauthenticated (see
//! `latticeai/api/health.py`), so any 2xx is the honest signal.

use std::fmt;
use std::time::{Duration, Instant};

use reqwest::Client;

/// Build the HTTP client used for health probes and proxying.
///
/// `no_proxy()` matters: a machine-wide `HTTP_PROXY` must never intercept
/// loopback traffic to our own worker.
pub fn http_client() -> Result<Client, reqwest::Error> {
    Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(5))
        .build()
}

/// The health URL for a worker origin.
pub fn health_url(origin: &str) -> String {
    format!("{}/health", origin.trim_end_matches('/'))
}

/// One probe. `true` iff the worker answered with a 2xx.
pub async fn check_health(client: &Client, origin: &str) -> bool {
    match client.get(health_url(origin)).send().await {
        Ok(response) => response.status().is_success(),
        Err(_) => false,
    }
}

/// The health gate never opened before the deadline.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HealthTimeout {
    /// Origin that was probed.
    pub origin: String,
    /// How long we waited.
    pub waited: Duration,
}

impl fmt::Display for HealthTimeout {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "worker at {} did not answer GET /health within {:?}",
            self.origin, self.waited
        )
    }
}

impl std::error::Error for HealthTimeout {}

/// Poll `GET {origin}/health` every `interval` until it answers 2xx or
/// `deadline` elapses. Returns how long the gate took to open.
pub async fn wait_for_health(
    client: &Client,
    origin: &str,
    interval: Duration,
    deadline: Duration,
) -> Result<Duration, HealthTimeout> {
    let started = Instant::now();
    loop {
        if check_health(client, origin).await {
            return Ok(started.elapsed());
        }
        if started.elapsed() >= deadline {
            return Err(HealthTimeout {
                origin: origin.to_string(),
                waited: started.elapsed(),
            });
        }
        tokio::time::sleep(interval).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_url_normalises_trailing_slashes() {
        assert_eq!(
            health_url("http://127.0.0.1:4825"),
            "http://127.0.0.1:4825/health"
        );
        assert_eq!(
            health_url("http://127.0.0.1:4825/"),
            "http://127.0.0.1:4825/health"
        );
    }

    #[tokio::test]
    async fn a_dead_origin_is_never_healthy() {
        let client = http_client().expect("client");
        // Port 1 on loopback: reserved, nothing listens there.
        assert!(!check_health(&client, "http://127.0.0.1:1").await);
    }

    #[tokio::test]
    async fn wait_for_health_gives_up_at_the_deadline() {
        let client = http_client().expect("client");
        let err = wait_for_health(
            &client,
            "http://127.0.0.1:1",
            Duration::from_millis(10),
            Duration::from_millis(40),
        )
        .await
        .expect_err("must time out");
        assert!(err.to_string().contains("GET /health"));
        assert!(err.waited >= Duration::from_millis(40));
    }
}
