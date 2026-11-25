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
