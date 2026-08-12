//! HTTP health probing.
//!
//! Replaces the desktop shell's TCP-connect probe: a socket that accepts a
//! connection proves only that *something* is listening, not that the worker
//! finished booting. The worker serves `GET /health` unauthenticated (see
//! `latticeai/api/health.py`), so any 2xx is the honest signal.

use std::fmt;
use std::time::{Duration, Instant};

use reqwest::Client;
use serde_json::Value;

/// Build the HTTP client used for health probes and for every non-proxy call
/// the host makes to the worker (the jobs scheduler, the agent seam).
///
/// `no_proxy()` matters: a machine-wide `HTTP_PROXY` must never intercept
/// loopback traffic to our own worker.
///
/// This client follows redirects, which is right for an API caller and *wrong*
/// for a reverse proxy — see [`proxy_client`].
pub fn http_client() -> Result<Client, reqwest::Error> {
    Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(5))
        .build()
}

/// Build the HTTP client the **reverse proxy** forwards with.
///
/// Identical to [`http_client`] but with redirect following switched off, and
/// that difference is load-bearing. A client that follows a 3xx internally
/// hands the caller only the *final* response, so every header the redirect
/// itself carried is destroyed: the invite gate's `Set-Cookie` (which makes
/// `GET /?code=…` a dead end through the gateway), the SSO login cookie, and
/// the `Location: /app#/route` of all twelve legacy deep links. A proxy must
/// pass a redirect through and let the browser decide.
pub fn proxy_client() -> Result<Client, reqwest::Error> {
    Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .build()
}

/// The health URL for a worker origin.
pub fn health_url(origin: &str) -> String {
    format!("{}/health", origin.trim_end_matches('/'))
}

/// What one `GET /health` said.
///
/// The body is kept (when the worker answered 2xx with JSON) because `/health`
/// is also where the worker states its access posture, and the gateway gates
/// its native lanes on that — see [`crate::gateway::posture`].
#[derive(Debug, Clone, Default, PartialEq)]
pub struct HealthReport {
    /// The worker answered with a 2xx.
    pub healthy: bool,
    /// The parsed body of a healthy answer, when it was JSON.
    pub body: Option<Value>,
}

/// One probe, keeping whatever the worker said about itself.
pub async fn probe_health(client: &Client, origin: &str) -> HealthReport {
    let Ok(response) = client.get(health_url(origin)).send().await else {
        return HealthReport::default();
    };
    if !response.status().is_success() {
        return HealthReport::default();
    }
    let body = response
        .bytes()
        .await
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    HealthReport {
        healthy: true,
        body,
    }
}

/// One probe. `true` iff the worker answered with a 2xx.
pub async fn check_health(client: &Client, origin: &str) -> bool {
    probe_health(client, origin).await.healthy
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
        let report = probe_health(&client, "http://127.0.0.1:1").await;
        assert_eq!(report, HealthReport::default());
        assert!(report.body.is_none(), "nothing answered, nothing to read");
    }

    #[test]
    fn the_proxy_client_does_not_follow_redirects() {
        // Both clients build; the difference is asserted end to end in
        // `tests/gateway_proxy.rs` (a 308 must reach the caller intact).
        assert!(proxy_client().is_ok());
        assert!(http_client().is_ok());
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
