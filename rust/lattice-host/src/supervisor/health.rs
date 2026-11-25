use std::fmt;
use std::time::{Duration, Instant};

use reqwest::Client;
use serde_json::Value;

pub fn http_client() -> Result<Client, reqwest::Error> {
    Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(5))
        .build()
}

pub fn proxy_client() -> Result<Client, reqwest::Error> {
    Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .build()
}

pub fn health_url(origin: &str) -> String {
    format!("{}/health", origin.trim_end_matches('/'))
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct HealthReport {
    pub healthy: bool,
    pub body: Option<Value>,
}

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

pub async fn check_health(client: &Client, origin: &str) -> bool {
    probe_health(client, origin).await.healthy
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HealthTimeout {
    pub origin: String,
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
        assert!(!check_health(&client, "http://127.0.0.1:1").await);
        let report = probe_health(&client, "http://127.0.0.1:1").await;
        assert_eq!(report, HealthReport::default());
        assert!(report.body.is_none(), "nothing answered, nothing to read");
    }

    #[test]
    fn the_proxy_client_does_not_follow_redirects() {
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
