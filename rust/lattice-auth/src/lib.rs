//! `lattice-auth` — native identity for the One Door gateway (v11.6.0).
//!
//! Everything the Python worker knew about *who is calling*, ported so the
//! Rust front door can answer it itself: users, sessions, login/logout/
//! register, roles and capabilities, workspace scoping, the Origin-based CSRF
//! decision, and both rate limiters.
//!
//! # Where the state lives
//!
//! The same files Python uses, so a running install migrates in place rather
//! than starting over:
//!
//! | What | Path |
//! |---|---|
//! | accounts | `<data_dir>/users.json` |
//! | sessions | `<data_dir>/sessions.json` |
//! | SSO config | `<data_dir>/sso_config.json` (read-only here) |
//!
//! `<data_dir>` is `LATTICEAI_DATA_DIR`, else `~/.ltcai` —
//! [`lattice_core::resolve_data_dir`], the one resolver.
//!
//! # The rule clients depend on
//!
//! On a **loopback bind with authentication off**, the caller is the trusted
//! local owner: [`AuthState::require_user`] answers with the *empty* identity
//! and [`AuthState::get_user_role`] projects it as `owner`. The VS Code
//! extension sends no cookie, no bearer token and no CSRF token and depends on
//! exactly this branch, together with the CSRF guard's `no-session-cookie`
//! short-circuit. Neither may be tightened without breaking it.
//!
//! # What a route package uses
//!
//! ```no_run
//! use std::sync::Arc;
//! use lattice_auth::{AuthConfig, AuthState};
//!
//! let auth: Arc<AuthState> = AuthState::new(AuthConfig::from_env().with_stored_sso());
//!
//! // Guards, callable directly …
//! # let headers = axum::http::HeaderMap::new();
//! let identity = auth.require_user(&headers);
//! # let _ = identity;
//! ```
//!
//! … or as extractors ([`CurrentUser`], [`AdminUser`], [`RequestedWorkspace`],
//! [`ClientIp`]) once the package's own state exposes `Arc<AuthState>` through
//! `axum::extract::FromRef`.
//!
//! # Messages
//!
//! Refusal bodies are byte-identical to the Python ones, routed directly
//! through canonical [`lattice_core::messages`] via [`messages`].

// Every guard answers `Result<T, axum::response::Response>`: the error *is* the
// HTTP refusal, already rendered with the status and bytes the Python original
// produced. `axum::Response` is 128 bytes, which trips `result_large_err`, and
// the lint's remedy — boxing — would be a step backwards here: `Box<Response>`
// is not `IntoResponse`, so every handler and every extractor rejection would
// have to unbox before returning, and the type would stop saying "this is the
// answer the caller sends". Allowed once, at the crate root, deliberately.
#![allow(clippy::result_large_err)]

pub mod atomic;
pub mod body;
pub mod clock;
pub mod config;
pub mod cookies;
pub mod csrf;
pub mod enterprise;
pub mod extract;
pub mod messages;
pub mod middleware;
pub mod origin;
pub mod password;
pub mod policy;
pub mod pyjson;
pub mod ratelimit;
pub mod response;
pub mod routes;
pub mod scim;
pub mod scope;
pub mod sessions;
pub mod setcookie;
pub mod state;
pub mod users;

pub use clock::Clock;
pub use config::{AuthConfig, SsoSettings};
pub use cookies::SESSION_COOKIE_NAME;
pub use csrf::{CsrfDecision, CsrfOriginPolicy, CsrfReason, CsrfRequest};
pub use enterprise::{
    allowed_tenants_of_user, apply_scim_user, authorize_scim_request, constant_time_eq,
    is_valid_tenant_id, requested_tenant, tenant_denied_response, tenant_of_user,
    validate_oidc_claims, validate_sso_settings, verify_tenant_boundary, AuthAuditSink,
    MemoryAuditSink, OidcCallbackCheck, OidcClaims, OidcPolicy, OidcPolicyError, ScimConfig,
    ScimPolicyError, ScimProvisionResult, ScimUserProvision, TenantError, TENANT_HEADER,
};
pub use extract::{AdminUser, ClientIp, CurrentUser, RequestedWorkspace};
pub use middleware::csrf_guard;
pub use password::{hash_password, stored_is_hashed, verify_password, PasswordError};
pub use policy::{capabilities_for_role, check_role, normalize_role, role_has_capability};
pub use pyjson::OrderedMap;
pub use ratelimit::RateLimiter;
pub use routes::{router, router_with_csrf, AUTH_PATHS};
pub use scope::{
    requested_workspace, resolve_workspace_scope, workspace_scope_from_request, ScopeMode,
    WorkspaceResolver, WORKSPACE_HEADER, WORKSPACE_PARAM,
};
pub use sessions::SessionStore;
pub use state::{peer_of, AuthState, Identity, InviteGate, PeerAddr};
pub use users::{normalize_email, stable_user_id, UserStore, Users};
