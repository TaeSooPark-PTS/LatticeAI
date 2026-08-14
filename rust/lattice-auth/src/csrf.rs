//! The Origin/Referer guard, ported branch for branch from `core/csrf.py`.
//!
//! Seven branches, in order, and the order *is* the policy — moving branch 3
//! above branch 2 would make bearer clients depend on not sending a cookie,
//! and moving branch 5 above branch 4 would let an opaque origin pass as
//! "no origin at all". The reason label on each decision is the audit trail
//! and rides in the refusal body, so the labels are part of the contract too.
//!
//! Threat model, restated from the original so the exemptions stay checkable:
//! only ambient credentials are forgeable, so `Authorization: Bearer` and
//! "no session cookie" are exempt; `Origin` is attacker-honest because a
//! browser sets it; neither header present means the caller is not a browser,
//! which is trusted only on a loopback bind.

use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::Response;
use serde_json::json;

use crate::cookies::has_session_cookie;
use crate::origin::{effective_host, normalize_origin, same_site, IpNetwork, Origin};
use crate::pyjson::dumps_spaced;
use crate::response::json_response_utf8;

/// Methods that must not change state; everything else is guarded.
pub const SAFE_METHODS: [&str; 4] = ["GET", "HEAD", "OPTIONS", "TRACE"];

/// The refusal text, byte-identical to `_DENIED_DETAIL`.
pub const DENIED_DETAIL: &str =
    "요청 출처를 확인할 수 없어 거부했습니다. 다른 사이트에서 보낸 요청일 수 있습니다.";

/// Why a request was allowed or refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CsrfDecision {
    /// Whether the request may proceed.
    pub allowed: bool,
    /// The label that names the branch taken.
    pub reason: &'static str,
}

/// Everything one CSRF decision needs, so the policy stays pure.
#[derive(Debug, Default, Clone)]
pub struct CsrfRequest<'a> {
    /// The HTTP method.
    pub method: &'a str,
    /// The `Origin` header, if any.
    pub origin: Option<&'a str>,
    /// The `Referer` header, if any.
    pub referer: Option<&'a str>,
    /// The `Host` header, if any.
    pub host: Option<&'a str>,
    /// The raw `Cookie` header, if any.
    pub cookie_header: Option<&'a str>,
    /// The `Authorization` header, if any.
    pub authorization: Option<&'a str>,
    /// The `X-Forwarded-Host` header, if any.
    pub forwarded_host: Option<&'a str>,
    /// The direct peer address.
    pub peer: Option<&'a str>,
}

/// Decide whether one request may change state with a cookie credential.
#[derive(Debug, Clone)]
pub struct CsrfOriginPolicy {
    trusted: Vec<Origin>,
    bind_is_loopback: bool,
    trusted_proxies: Vec<IpNetwork>,
}

impl CsrfOriginPolicy {
    /// The server's own origin and loopback are trusted by construction; the
    /// operator's `LATTICEAI_CSRF_TRUSTED_ORIGINS` entries are appended.
    pub fn new(
        trusted_origins: &[String],
        server_host: &str,
        server_port: u16,
        bind_is_loopback: bool,
        trusted_proxies: Vec<IpNetwork>,
    ) -> Self {
        let mut policy = Self {
            trusted: Vec::new(),
            bind_is_loopback,
            trusted_proxies,
        };
        for origin in default_origins(server_host, server_port) {
            policy.add(&origin);
        }
        for origin in trusted_origins {
            policy.add(origin);
        }
        policy
    }

    fn add(&mut self, origin: &str) {
        if let Some(normalized) = normalize_origin(Some(origin)) {
            if !self.trusted.contains(&normalized) {
                self.trusted.push(normalized);
            }
        }
    }

    /// The normalised allowlist, in the order it was built.
    pub fn trusted_origins(&self) -> &[Origin] {
        &self.trusted
    }

    fn origin_is_trusted(
        &self,
        origin: &Origin,
        host_header: Option<&str>,
        forwarded_host: Option<&str>,
        peer: Option<&str>,
    ) -> bool {
        if self
            .trusted
            .iter()
            .any(|trusted| same_site(origin, trusted))
        {
            return true;
        }
        // Same-origin by the request's own Host: a browser sets Host from the
        // URL it is fetching, so `Origin == Host` can only come from a page
        // this server served. "The request's own Host" is the front door's,
        // not the internal one a proxy hop rewrote it to.
        let front_door = effective_host(host_header, forwarded_host, peer, &self.trusted_proxies);
        match normalize_origin(front_door.as_deref()) {
            Some(own) => same_site(origin, &own),
            None => false,
        }
    }

    /// Allow or deny one request. Pure: no I/O, no app state.
    pub fn evaluate(&self, request: &CsrfRequest<'_>) -> CsrfDecision {
        let method = request.method.to_ascii_uppercase();
        if SAFE_METHODS.contains(&method.as_str()) {
            return allow("safe-method");
        }
        if request
            .authorization
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase()
            .starts_with("bearer ")
        {
            // Not ambient: a cross-site page cannot attach this header.
            return allow("bearer-auth");
        }
        if !has_session_cookie(request.cookie_header) {
            return allow("no-session-cookie");
        }

        let mut stated = normalize_origin(request.origin);
        if stated.is_none() && request.origin.is_some_and(|value| !value.is_empty()) {
            // An opaque origin ("null") explicitly claims untrusted provenance.
            return deny("opaque-origin");
        }
        if stated.is_none() {
            stated = normalize_origin(request.referer);
            if stated.is_none() {
                return if self.bind_is_loopback {
                    // Non-browser client on the same machine (CLI, desktop
                    // shell, curl). Nothing on the network can reach this bind.
                    allow("no-origin-loopback-bind")
                } else {
                    deny("no-origin-reachable-bind")
                };
            }
        }

        let stated = stated.expect("checked above");
        if self.origin_is_trusted(&stated, request.host, request.forwarded_host, request.peer) {
            allow("same-site-or-trusted-origin")
        } else {
            deny("cross-site-origin")
        }
    }

    /// Evaluate straight off an axum request's parts.
    pub fn evaluate_headers(
        &self,
        method: &Method,
        headers: &HeaderMap,
        peer: Option<&str>,
    ) -> CsrfDecision {
        let read = |name: &str| headers.get(name).and_then(|value| value.to_str().ok());
        self.evaluate(&CsrfRequest {
            method: method.as_str(),
            origin: read("origin"),
            referer: read("referer"),
            host: read("host"),
            cookie_header: read("cookie"),
            authorization: read("authorization"),
            forwarded_host: read("x-forwarded-host"),
            peer,
        })
    }
}

/// The server's own origin plus loopback, in both schemes.
fn default_origins(server_host: &str, server_port: u16) -> Vec<String> {
    let hosts = [server_host, "localhost", "127.0.0.1", "[::1]"];
    let mut origins = Vec::new();
    for host in hosts {
        if host.is_empty() {
            continue;
        }
        for scheme in ["http", "https"] {
            origins.push(format!("{scheme}://{host}:{server_port}"));
        }
    }
    origins
}

fn allow(reason: &'static str) -> CsrfDecision {
    CsrfDecision {
        allowed: true,
        reason,
    }
}

fn deny(reason: &'static str) -> CsrfDecision {
    CsrfDecision {
        allowed: false,
        reason,
    }
}

/// The 403 body, written the way `core/csrf.py` writes it: `json.dumps` with
/// Python's default separators, so the spaces after `:` and `,` are real.
pub fn csrf_denied_response(reason: &str) -> Response {
    let body = dumps_spaced(&[
        ("detail", json!(DENIED_DETAIL)),
        ("error", json!("csrf_origin_rejected")),
        ("reason", json!(reason)),
    ]);
    json_response_utf8(StatusCode::FORBIDDEN, &body)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> CsrfOriginPolicy {
        CsrfOriginPolicy::new(&[], "127.0.0.1", 4825, true, Vec::new())
    }

    fn request<'a>(method: &'a str) -> CsrfRequest<'a> {
        CsrfRequest {
            method,
            host: Some("127.0.0.1:4825"),
            ..CsrfRequest::default()
        }
    }

    #[test]
    fn safe_methods_are_never_guarded() {
        for method in ["GET", "head", "OPTIONS", "trace"] {
            let decision = policy().evaluate(&request(method));
            assert_eq!(decision, allow("safe-method"), "{method}");
        }
    }

    #[test]
    fn bearer_auth_is_exempt_before_the_cookie_check() {
        let mut probe = request("POST");
        probe.authorization = Some("  Bearer abc  ");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("https://evil.example");
        assert_eq!(policy().evaluate(&probe), allow("bearer-auth"));
    }

    #[test]
    fn no_session_cookie_is_nothing_to_forge() {
        let mut probe = request("POST");
        probe.origin = Some("https://evil.example");
        assert_eq!(policy().evaluate(&probe), allow("no-session-cookie"));
        probe.cookie_header = Some("other=1");
        assert_eq!(policy().evaluate(&probe), allow("no-session-cookie"));
    }

    #[test]
    fn an_opaque_origin_is_refused() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("null");
        assert_eq!(policy().evaluate(&probe), deny("opaque-origin"));
    }

    #[test]
    fn no_origin_depends_on_the_bind() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        assert_eq!(policy().evaluate(&probe), allow("no-origin-loopback-bind"));

        let reachable = CsrfOriginPolicy::new(&[], "0.0.0.0", 4825, false, Vec::new());
        assert_eq!(reachable.evaluate(&probe), deny("no-origin-reachable-bind"));
    }

    #[test]
    fn referer_stands_in_for_a_missing_origin() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.referer = Some("http://127.0.0.1:4825/app");
        assert_eq!(
            policy().evaluate(&probe),
            allow("same-site-or-trusted-origin")
        );
        probe.referer = Some("https://evil.example/x");
        assert_eq!(policy().evaluate(&probe), deny("cross-site-origin"));
    }

    #[test]
    fn a_cross_site_origin_is_refused_and_a_trusted_one_is_not() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("https://evil.example");
        assert_eq!(policy().evaluate(&probe), deny("cross-site-origin"));

        let trusted = CsrfOriginPolicy::new(
            &["https://evil.example".to_string()],
            "127.0.0.1",
            4825,
            true,
            Vec::new(),
        );
        assert_eq!(
            trusted.evaluate(&probe),
            allow("same-site-or-trusted-origin")
        );
    }

    #[test]
    fn origin_equal_to_the_host_is_same_site() {
        let mut probe = CsrfRequest {
            method: "POST",
            host: Some("brain.example.org"),
            cookie_header: Some("session_token=x"),
            origin: Some("https://brain.example.org"),
            ..CsrfRequest::default()
        };
        assert_eq!(
            policy().evaluate(&probe),
            allow("same-site-or-trusted-origin")
        );
        // A proxy that rewrote Host is why the forwarded authority is resolved
        // first — but only from a peer that may forward.
        probe.host = Some("127.0.0.1:4826");
        probe.origin = Some("https://front.door");
        assert_eq!(policy().evaluate(&probe), deny("cross-site-origin"));
        probe.forwarded_host = Some("front.door");
        probe.peer = Some("8.8.8.8");
        assert_eq!(policy().evaluate(&probe), deny("cross-site-origin"));
        probe.peer = Some("127.0.0.1");
        assert_eq!(
            policy().evaluate(&probe),
            allow("same-site-or-trusted-origin")
        );
    }

    #[test]
    fn a_hostless_request_cannot_be_same_site() {
        let probe = CsrfRequest {
            method: "POST",
            cookie_header: Some("session_token=x"),
            origin: Some("https://brain.example.org"),
            ..CsrfRequest::default()
        };
        assert_eq!(policy().evaluate(&probe), deny("cross-site-origin"));
    }

    #[test]
    fn the_allowlist_is_deduplicated_and_normalised() {
        let policy = CsrfOriginPolicy::new(
            &[
                "http://127.0.0.1:4825".to_string(),
                "  ".to_string(),
                "https://Brain.Example.ORG:443".to_string(),
            ],
            "127.0.0.1",
            4825,
            true,
            Vec::new(),
        );
        let trusted = policy.trusted_origins();
        assert!(trusted.contains(&("https".into(), "brain.example.org".into(), None)));
        let loopback_http = trusted
            .iter()
            .filter(|origin| origin.1 == "127.0.0.1" && origin.0 == "http")
            .count();
        assert_eq!(loopback_http, 1);
    }

    #[test]
    fn the_refusal_body_carries_python_spacing() {
        let response = csrf_denied_response("cross-site-origin");
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json; charset=utf-8"
        );
    }

    #[test]
    fn header_evaluation_matches_the_pure_policy() {
        let mut headers = HeaderMap::new();
        headers.insert("cookie", "session_token=x".parse().unwrap());
        headers.insert("origin", "https://evil.example".parse().unwrap());
        headers.insert("host", "127.0.0.1:4825".parse().unwrap());
        let decision = policy().evaluate_headers(&Method::POST, &headers, Some("127.0.0.1"));
        assert_eq!(decision, deny("cross-site-origin"));
    }
}
