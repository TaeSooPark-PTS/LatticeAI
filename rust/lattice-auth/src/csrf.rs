use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::Response;
use serde_json::json;

use crate::cookies::has_session_cookie;
pub use crate::messages::{CSRF_DENIED_DETAIL as DENIED_DETAIL, CSRF_ERROR_CODE};
use crate::origin::{effective_host, normalize_origin, same_site, IpNetwork, Origin};
use crate::pyjson::dumps_spaced;
use crate::response::json_response_utf8;

pub const SAFE_METHODS: [&str; 4] = ["GET", "HEAD", "OPTIONS", "TRACE"];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CsrfReason {
    SafeMethod,
    BearerAuth,
    NoSessionCookie,
    OpaqueOrigin,
    NoOriginLoopbackBind,
    NoOriginReachableBind,
    SameSiteOrTrustedOrigin,
    CrossSiteOrigin,
}

impl CsrfReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::SafeMethod => "safe-method",
            Self::BearerAuth => "bearer-auth",
            Self::NoSessionCookie => "no-session-cookie",
            Self::OpaqueOrigin => "opaque-origin",
            Self::NoOriginLoopbackBind => "no-origin-loopback-bind",
            Self::NoOriginReachableBind => "no-origin-reachable-bind",
            Self::SameSiteOrTrustedOrigin => "same-site-or-trusted-origin",
            Self::CrossSiteOrigin => "cross-site-origin",
        }
    }
}

impl std::fmt::Display for CsrfReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CsrfDecision {
    pub allowed: bool,
    pub reason: CsrfReason,
}

#[derive(Debug, Default, Clone)]
pub struct CsrfRequest<'a> {
    pub method: &'a str,
    pub origin: Option<&'a str>,
    pub referer: Option<&'a str>,
    pub host: Option<&'a str>,
    pub cookie_header: Option<&'a str>,
    pub authorization: Option<&'a str>,
    pub forwarded_host: Option<&'a str>,
    pub peer: Option<&'a str>,
}

#[derive(Debug, Clone)]
pub struct CsrfOriginPolicy {
    trusted: Vec<Origin>,
    bind_is_loopback: bool,
    trusted_proxies: Vec<IpNetwork>,
}

impl CsrfOriginPolicy {
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
        let front_door = effective_host(host_header, forwarded_host, peer, &self.trusted_proxies);
        match normalize_origin(front_door.as_deref()) {
            Some(own) => same_site(origin, &own),
            None => false,
        }
    }

    pub fn evaluate(&self, request: &CsrfRequest<'_>) -> CsrfDecision {
        let method = request.method.to_ascii_uppercase();
        if SAFE_METHODS.contains(&method.as_str()) {
            return allow(CsrfReason::SafeMethod);
        }
        if request
            .authorization
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase()
            .starts_with("bearer ")
        {
            return allow(CsrfReason::BearerAuth);
        }
        if !has_session_cookie(request.cookie_header) {
            return allow(CsrfReason::NoSessionCookie);
        }

        let mut stated = normalize_origin(request.origin);
        if stated.is_none() && request.origin.is_some_and(|value| !value.is_empty()) {
            return deny(CsrfReason::OpaqueOrigin);
        }
        if stated.is_none() {
            stated = normalize_origin(request.referer);
            if stated.is_none() {
                return if self.bind_is_loopback {
                    allow(CsrfReason::NoOriginLoopbackBind)
                } else {
                    deny(CsrfReason::NoOriginReachableBind)
                };
            }
        }

        let stated = stated.expect("checked above");
        if self.origin_is_trusted(&stated, request.host, request.forwarded_host, request.peer) {
            allow(CsrfReason::SameSiteOrTrustedOrigin)
        } else {
            deny(CsrfReason::CrossSiteOrigin)
        }
    }

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

fn allow(reason: CsrfReason) -> CsrfDecision {
    CsrfDecision {
        allowed: true,
        reason,
    }
}

fn deny(reason: CsrfReason) -> CsrfDecision {
    CsrfDecision {
        allowed: false,
        reason,
    }
}

pub fn csrf_denied_response(reason: CsrfReason) -> Response {
    let body = dumps_spaced(&[
        ("detail", json!(DENIED_DETAIL)),
        ("error", json!(CSRF_ERROR_CODE)),
        ("reason", json!(reason.as_str())),
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
            assert_eq!(decision, allow(CsrfReason::SafeMethod), "{method}");
            assert_eq!(decision.reason.as_str(), "safe-method");
        }
    }

    #[test]
    fn bearer_auth_is_exempt_before_the_cookie_check() {
        let mut probe = request("POST");
        probe.authorization = Some("  Bearer abc  ");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("https://evil.example");
        assert_eq!(policy().evaluate(&probe), allow(CsrfReason::BearerAuth));
    }

    #[test]
    fn no_session_cookie_is_nothing_to_forge() {
        let mut probe = request("POST");
        probe.origin = Some("https://evil.example");
        assert_eq!(
            policy().evaluate(&probe),
            allow(CsrfReason::NoSessionCookie)
        );
        probe.cookie_header = Some("other=1");
        assert_eq!(
            policy().evaluate(&probe),
            allow(CsrfReason::NoSessionCookie)
        );
    }

    #[test]
    fn an_opaque_origin_is_refused() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("null");
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::OpaqueOrigin));
    }

    #[test]
    fn no_origin_depends_on_the_bind() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        assert_eq!(
            policy().evaluate(&probe),
            allow(CsrfReason::NoOriginLoopbackBind)
        );

        let reachable = CsrfOriginPolicy::new(&[], "0.0.0.0", 4825, false, Vec::new());
        assert_eq!(
            reachable.evaluate(&probe),
            deny(CsrfReason::NoOriginReachableBind)
        );
    }

    #[test]
    fn referer_stands_in_for_a_missing_origin() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.referer = Some("http://127.0.0.1:4825/app");
        assert_eq!(
            policy().evaluate(&probe),
            allow(CsrfReason::SameSiteOrTrustedOrigin)
        );
        probe.referer = Some("https://evil.example/x");
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::CrossSiteOrigin));
    }

    #[test]
    fn a_cross_site_origin_is_refused_and_a_trusted_one_is_not() {
        let mut probe = request("POST");
        probe.cookie_header = Some("session_token=x");
        probe.origin = Some("https://evil.example");
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::CrossSiteOrigin));

        let trusted = CsrfOriginPolicy::new(
            &["https://evil.example".to_string()],
            "127.0.0.1",
            4825,
            true,
            Vec::new(),
        );
        assert_eq!(
            trusted.evaluate(&probe),
            allow(CsrfReason::SameSiteOrTrustedOrigin)
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
            allow(CsrfReason::SameSiteOrTrustedOrigin)
        );
        probe.host = Some("127.0.0.1:4826");
        probe.origin = Some("https://front.door");
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::CrossSiteOrigin));
        probe.forwarded_host = Some("front.door");
        probe.peer = Some("8.8.8.8");
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::CrossSiteOrigin));
        probe.peer = Some("127.0.0.1");
        assert_eq!(
            policy().evaluate(&probe),
            allow(CsrfReason::SameSiteOrTrustedOrigin)
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
        assert_eq!(policy().evaluate(&probe), deny(CsrfReason::CrossSiteOrigin));
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
        let response = csrf_denied_response(CsrfReason::CrossSiteOrigin);
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
        assert_eq!(decision, deny(CsrfReason::CrossSiteOrigin));
    }
}
