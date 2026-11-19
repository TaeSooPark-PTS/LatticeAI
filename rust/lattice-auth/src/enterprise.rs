//! Enterprise tenant isolation, OIDC/SSO policy validation, SCIM 2.0
//! provisioning guards, and security audit sink hooks.

use std::collections::HashSet;

use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::config::{host_is_loopback, SsoSettings};
use crate::users::normalize_email;

pub use crate::scim::{
    apply_scim_user, authorize_scim_request, ScimConfig, ScimPolicyError, ScimProvisionResult,
    ScimUserProvision,
};

/// HTTP header used to assert an optional tenant boundary on multi-tenant gateways.
pub const TENANT_HEADER: &str = "x-tenant-id";

/// Structured security audit sink invoked by [`AuthState`] on auth, tenant,
/// SSO, and SCIM policy decisions.
pub trait AuthAuditSink: Send + Sync {
    /// Record an authentication/authorization security event.
    fn record(&self, event: &str, fields: &Value);
}

/// In-memory audit recorder for tests and diagnostics.
#[derive(Debug, Default)]
pub struct MemoryAuditSink {
    events: std::sync::Mutex<Vec<(String, Value)>>,
}

impl MemoryAuditSink {
    /// Create an empty recorder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Snapshot every recorded `(event, fields)` pair in order.
    pub fn events(&self) -> Vec<(String, Value)> {
        self.events.lock().expect("audit lock").clone()
    }
}

impl AuthAuditSink for MemoryAuditSink {
    fn record(&self, event: &str, fields: &Value) {
        self.events
            .lock()
            .expect("audit lock")
            .push((event.to_string(), fields.clone()));
    }
}

/// Constant-time byte slice comparison so tokens, nonces, and OAuth states do
/// not leak prefix lengths via timing side channels.
pub fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    let max_len = a.len().max(b.len());
    let mut diff = a.len() ^ b.len();
    for idx in 0..max_len {
        let lhs = *a.get(idx).unwrap_or(&0);
        let rhs = *b.get(idx).unwrap_or(&0);
        diff |= usize::from(lhs ^ rhs);
    }
    diff == 0
}

/// Validate a tenant/organization identifier so it cannot carry path traversal
/// or control characters.
pub fn is_valid_tenant_id(raw: &str) -> bool {
    let trimmed = raw.trim();
    if trimmed.is_empty() || trimmed.len() > 128 {
        return false;
    }
    if trimmed == "." || trimmed == ".." || trimmed.contains("..") {
        return false;
    }
    trimmed
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

/// Extract a requested tenant identifier from `x-tenant-id` header or query.
pub fn requested_tenant(headers: &HeaderMap, query_tenant: Option<&str>) -> Option<String> {
    if let Some(raw) = headers
        .get(TENANT_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return Some(raw.to_string());
    }
    query_tenant
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

/// Read the primary tenant identifier stored on a `users.json` record.
pub fn tenant_of_user(record: &Map<String, Value>) -> Option<String> {
    for key in ["tenant_id", "org_id"] {
        if let Some(value) = record
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            return Some(value.to_string());
        }
    }
    None
}

/// All tenant identifiers a user record explicitly belongs to.
pub fn allowed_tenants_of_user(record: &Map<String, Value>) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for key in ["tenant_id", "org_id"] {
        if let Some(value) = record
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            if seen.insert(value.to_string()) {
                out.push(value.to_string());
            }
        }
    }
    if let Some(items) = record.get("tenants").and_then(Value::as_array) {
        for item in items {
            if let Some(value) = item
                .as_str()
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                if seen.insert(value.to_string()) {
                    out.push(value.to_string());
                }
            }
        }
    }
    out
}

/// Reason a tenant boundary check failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TenantError {
    InvalidTenantId(String),
    CrossTenantDenied { email: String, requested: String },
    TenantMembershipRequired { email: String, requested: String },
}

impl std::fmt::Display for TenantError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TenantError::InvalidTenantId(tenant) => {
                write!(formatter, "invalid tenant identifier '{tenant}'")
            }
            TenantError::CrossTenantDenied { email, requested } => write!(
                formatter,
                "user '{email}' is not authorized for tenant '{requested}'"
            ),
            TenantError::TenantMembershipRequired { email, requested } => write!(
                formatter,
                "user '{email}' has no tenant membership for '{requested}'"
            ),
        }
    }
}

/// Verify that `email` (with optional `users.json` record) may operate under
/// `requested_tenant`, returning the effective tenant id on success.
pub fn verify_tenant_boundary(
    email: &str,
    record: Option<&Map<String, Value>>,
    requested_tenant: Option<&str>,
) -> Result<Option<String>, TenantError> {
    let requested = requested_tenant
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if let Some(req) = requested {
        if !is_valid_tenant_id(req) {
            return Err(TenantError::InvalidTenantId(req.to_string()));
        }
    }

    let Some(record) = record else {
        if let Some(req) = requested {
            return Err(TenantError::TenantMembershipRequired {
                email: email.to_string(),
                requested: req.to_string(),
            });
        }
        return Ok(None);
    };

    let primary = tenant_of_user(record);
    let allowed = allowed_tenants_of_user(record);
    match requested {
        Some(req) => {
            if allowed.is_empty() {
                return Err(TenantError::TenantMembershipRequired {
                    email: email.to_string(),
                    requested: req.to_string(),
                });
            }
            if !allowed.iter().any(|candidate| candidate == req) {
                return Err(TenantError::CrossTenantDenied {
                    email: email.to_string(),
                    requested: req.to_string(),
                });
            }
            Ok(Some(req.to_string()))
        }
        None => Ok(primary),
    }
}

/// Turn a [`TenantError`] into a `403 Forbidden` HTTP response.
pub fn tenant_denied_response(err: &TenantError) -> Response {
    let status = match err {
        TenantError::InvalidTenantId(_) => StatusCode::BAD_REQUEST,
        TenantError::CrossTenantDenied { .. } | TenantError::TenantMembershipRequired { .. } => {
            StatusCode::FORBIDDEN
        }
    };
    (
        status,
        Json(json!({
            "error": "tenant_isolation_violation",
            "detail": err.to_string(),
        })),
    )
        .into_response()
}

/// Hardened SSO/OIDC policy settings beyond raw [`SsoSettings`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OidcPolicy {
    pub require_https: bool,
    pub require_pkce: bool,
    pub require_verified_email: bool,
    pub allowed_domains: Vec<String>,
    pub allowed_issuers: Vec<String>,
    pub max_clock_skew_secs: u64,
}

impl Default for OidcPolicy {
    fn default() -> Self {
        Self {
            require_https: true,
            require_pkce: true,
            require_verified_email: true,
            allowed_domains: Vec::new(),
            allowed_issuers: Vec::new(),
            max_clock_skew_secs: 60,
        }
    }
}

/// Callback binding inputs verified against an OIDC ID token's claims.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OidcCallbackCheck {
    pub expected_state: String,
    pub actual_state: String,
    pub expected_nonce: String,
    pub code_verifier_present: bool,
    pub expected_tenant: Option<String>,
}

/// Parsed ID-token claims verified before issuing a session.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OidcClaims {
    pub iss: String,
    pub sub: String,
    pub aud: Vec<String>,
    pub azp: Option<String>,
    pub exp: u64,
    pub iat: Option<u64>,
    pub nonce: Option<String>,
    pub email: Option<String>,
    pub email_verified: Option<bool>,
    pub tenant_id: Option<String>,
}

/// Failure from SSO configuration or OIDC claim validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OidcPolicyError {
    InvalidConfiguration(String),
    InsecureEndpoint(String),
    StateMismatch,
    NonceMismatch,
    PkceRequired,
    IssuerMismatch { expected: String, actual: String },
    AudienceMismatch,
    TokenExpired,
    EmailNotVerified,
    DomainNotAllowed(String),
    TenantMismatch,
}

impl std::fmt::Display for OidcPolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OidcPolicyError::InvalidConfiguration(msg) => write!(formatter, "{msg}"),
            OidcPolicyError::InsecureEndpoint(field) => {
                write!(
                    formatter,
                    "SSO {field} must use https:// for non-loopback hosts"
                )
            }
            OidcPolicyError::StateMismatch => write!(formatter, "OIDC state verification failed"),
            OidcPolicyError::NonceMismatch => write!(formatter, "OIDC nonce verification failed"),
            OidcPolicyError::PkceRequired => write!(formatter, "PKCE code_verifier is required"),
            OidcPolicyError::IssuerMismatch { expected, actual } => {
                write!(
                    formatter,
                    "OIDC issuer mismatch: expected '{expected}', got '{actual}'"
                )
            }
            OidcPolicyError::AudienceMismatch => {
                write!(formatter, "OIDC audience/azp does not match client_id")
            }
            OidcPolicyError::TokenExpired => write!(formatter, "OIDC token has expired"),
            OidcPolicyError::EmailNotVerified => {
                write!(formatter, "OIDC email_verified claim must be true")
            }
            OidcPolicyError::DomainNotAllowed(domain) => {
                write!(
                    formatter,
                    "email domain '{domain}' is not in allowed_domains"
                )
            }
            OidcPolicyError::TenantMismatch => {
                write!(
                    formatter,
                    "OIDC token tenant_id does not match expected tenant"
                )
            }
        }
    }
}

fn validate_endpoint_url(
    field: &str,
    raw: &str,
    require_https: bool,
) -> Result<(), OidcPolicyError> {
    let trimmed = raw.trim();
    if trimmed.is_empty()
        || trimmed.contains('#')
        || trimmed.chars().any(|c| c.is_whitespace() || c.is_control())
    {
        return Err(OidcPolicyError::InvalidConfiguration(format!(
            "invalid SSO {field} URL"
        )));
    }
    let (scheme, rest) = trimmed.split_once("://").ok_or_else(|| {
        OidcPolicyError::InvalidConfiguration(format!("SSO {field} must include scheme"))
    })?;
    let scheme_lower = scheme.to_ascii_lowercase();
    if scheme_lower != "https" && scheme_lower != "http" {
        return Err(OidcPolicyError::InvalidConfiguration(format!(
            "unsupported scheme '{scheme}' in SSO {field}"
        )));
    }
    let authority = rest.split('/').next().unwrap_or("");
    if authority.is_empty() || authority.contains('@') {
        return Err(OidcPolicyError::InvalidConfiguration(format!(
            "SSO {field} has invalid authority"
        )));
    }
    let host = strip_host_port(authority);
    if require_https && scheme_lower != "https" && !host_is_loopback(host) {
        return Err(OidcPolicyError::InsecureEndpoint(field.to_string()));
    }
    Ok(())
}

fn strip_host_port(authority: &str) -> &str {
    if let Some(rest) = authority.strip_prefix('[') {
        if let Some((host, _)) = rest.split_once(']') {
            return host;
        }
    }
    authority.split(':').next().unwrap_or(authority)
}

fn issuer_origin_from_discovery(discovery_url: &str) -> String {
    let trimmed = discovery_url.trim().trim_end_matches('/');
    trimmed
        .strip_suffix("/.well-known/openid-configuration")
        .unwrap_or(trimmed)
        .to_string()
}

/// Validate configured [`SsoSettings`] against enterprise transport & OIDC rules.
pub fn validate_sso_settings(
    sso: &SsoSettings,
    policy: &OidcPolicy,
) -> Result<(), OidcPolicyError> {
    if !sso.enabled {
        return Ok(());
    }
    if sso.provider_name.trim().is_empty() {
        return Err(OidcPolicyError::InvalidConfiguration(
            "SSO provider_name is required when enabled".into(),
        ));
    }
    if sso.client_id.trim().is_empty() {
        return Err(OidcPolicyError::InvalidConfiguration(
            "SSO client_id is required when enabled".into(),
        ));
    }
    validate_endpoint_url("discovery_url", &sso.discovery_url, policy.require_https)?;
    validate_endpoint_url("redirect_uri", &sso.redirect_uri, policy.require_https)?;
    let has_openid = sso
        .scopes
        .split(|c: char| c.is_whitespace() || c == ',')
        .any(|scope| scope.eq_ignore_ascii_case("openid"));
    if !has_openid {
        return Err(OidcPolicyError::InvalidConfiguration(
            "SSO scopes must include 'openid'".into(),
        ));
    }
    Ok(())
}

/// Validate an OIDC login callback and ID-token claims before minting a session.
pub fn validate_oidc_claims(
    sso: &SsoSettings,
    policy: &OidcPolicy,
    check: &OidcCallbackCheck,
    claims: &OidcClaims,
    now_secs: u64,
) -> Result<(), OidcPolicyError> {
    validate_sso_settings(sso, policy)?;

    if check.expected_state.is_empty()
        || !constant_time_eq(
            check.expected_state.as_bytes(),
            check.actual_state.as_bytes(),
        )
    {
        return Err(OidcPolicyError::StateMismatch);
    }

    let claim_nonce = claims.nonce.as_deref().unwrap_or("");
    if check.expected_nonce.is_empty()
        || !constant_time_eq(check.expected_nonce.as_bytes(), claim_nonce.as_bytes())
    {
        return Err(OidcPolicyError::NonceMismatch);
    }

    if policy.require_pkce && !check.code_verifier_present {
        return Err(OidcPolicyError::PkceRequired);
    }

    let actual_iss = claims.iss.trim().trim_end_matches('/');
    if actual_iss.is_empty() {
        return Err(OidcPolicyError::IssuerMismatch {
            expected: issuer_origin_from_discovery(&sso.discovery_url),
            actual: String::new(),
        });
    }
    if !policy.allowed_issuers.is_empty() {
        let matched = policy
            .allowed_issuers
            .iter()
            .any(|allowed| allowed.trim().trim_end_matches('/') == actual_iss);
        if !matched {
            return Err(OidcPolicyError::IssuerMismatch {
                expected: policy.allowed_issuers.join(", "),
                actual: actual_iss.to_string(),
            });
        }
    } else {
        let expected_iss = issuer_origin_from_discovery(&sso.discovery_url);
        if actual_iss != expected_iss {
            return Err(OidcPolicyError::IssuerMismatch {
                expected: expected_iss,
                actual: actual_iss.to_string(),
            });
        }
    }

    if !claims.aud.iter().any(|aud| aud == sso.client_id.trim()) {
        return Err(OidcPolicyError::AudienceMismatch);
    }
    if claims.aud.len() > 1 && claims.azp.as_deref() != Some(sso.client_id.trim()) {
        return Err(OidcPolicyError::AudienceMismatch);
    }

    if claims.exp.saturating_add(policy.max_clock_skew_secs) <= now_secs {
        return Err(OidcPolicyError::TokenExpired);
    }

    let email = claims
        .email
        .as_deref()
        .map(normalize_email)
        .unwrap_or_default();
    if policy.require_verified_email
        && (claims.email_verified != Some(true) || !email.contains('@'))
    {
        return Err(OidcPolicyError::EmailNotVerified);
    }

    if !policy.allowed_domains.is_empty() {
        let domain = email.split_once('@').map(|(_, d)| d).unwrap_or("");
        let allowed = policy
            .allowed_domains
            .iter()
            .any(|candidate| candidate.trim().eq_ignore_ascii_case(domain));
        if !allowed {
            return Err(OidcPolicyError::DomainNotAllowed(domain.to_string()));
        }
    }

    if let Some(expected_tenant) = check
        .expected_tenant
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        if claims.tenant_id.as_deref().map(str::trim) != Some(expected_tenant) {
            return Err(OidcPolicyError::TenantMismatch);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tenant_boundary_rejects_cross_tenant_and_traversal() {
        let mut record = Map::new();
        record.insert("tenant_id".into(), json!("acme"));
        assert_eq!(
            verify_tenant_boundary("alice@acme.test", Some(&record), Some("acme")),
            Ok(Some("acme".into()))
        );
        assert!(matches!(
            verify_tenant_boundary("alice@acme.test", Some(&record), Some("globex")),
            Err(TenantError::CrossTenantDenied { .. })
        ));
        assert!(matches!(
            verify_tenant_boundary("alice@acme.test", Some(&record), Some("../globex")),
            Err(TenantError::InvalidTenantId(_))
        ));
        // Unassigned user cannot claim arbitrary tenant.
        let bare = Map::new();
        assert!(matches!(
            verify_tenant_boundary("anon@example.test", Some(&bare), Some("acme")),
            Err(TenantError::TenantMembershipRequired { .. })
        ));
    }

    #[test]
    fn oidc_policy_enforces_https_pkce_state_nonce_issuer_aud_and_tenant() {
        let sso = SsoSettings {
            enabled: true,
            provider_name: "Okta".into(),
            discovery_url: "https://idp.example.com/.well-known/openid-configuration".into(),
            client_id: "lattice-client".into(),
            client_secret: "secret".into(),
            redirect_uri: "https://app.example.com/auth/callback".into(),
            scopes: "openid profile email".into(),
        };
        let policy = OidcPolicy {
            allowed_domains: vec!["example.com".into()],
            ..OidcPolicy::default()
        };
        assert!(validate_sso_settings(&sso, &policy).is_ok());

        let mut insecure = sso.clone();
        insecure.discovery_url = "http://idp.example.com/.well-known/openid-configuration".into();
        assert!(matches!(
            validate_sso_settings(&insecure, &policy),
            Err(OidcPolicyError::InsecureEndpoint(_))
        ));

        let check = OidcCallbackCheck {
            expected_state: "state-123".into(),
            actual_state: "state-123".into(),
            expected_nonce: "nonce-xyz".into(),
            code_verifier_present: true,
            expected_tenant: Some("acme".into()),
        };
        let claims = OidcClaims {
            iss: "https://idp.example.com".into(),
            sub: "sub-1".into(),
            aud: vec!["lattice-client".into()],
            azp: None,
            exp: 2_000,
            iat: Some(1_000),
            nonce: Some("nonce-xyz".into()),
            email: Some("Alice@Example.com".into()),
            email_verified: Some(true),
            tenant_id: Some("acme".into()),
        };
        assert!(validate_oidc_claims(&sso, &policy, &check, &claims, 1_500).is_ok());

        let mut bad_tenant = claims.clone();
        bad_tenant.tenant_id = Some("other".into());
        assert_eq!(
            validate_oidc_claims(&sso, &policy, &check, &bad_tenant, 1_500),
            Err(OidcPolicyError::TenantMismatch)
        );

        let mut bad_domain = claims.clone();
        bad_domain.email = Some("eve@evil.org".into());
        assert!(matches!(
            validate_oidc_claims(&sso, &policy, &check, &bad_domain, 1_500),
            Err(OidcPolicyError::DomainNotAllowed(_))
        ));
    }
}
