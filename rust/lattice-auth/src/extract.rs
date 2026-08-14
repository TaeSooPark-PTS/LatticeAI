//! axum extractors, so a Wave-2 handler declares what it needs in its
//! signature instead of remembering to call a guard.
//!
//! Every extractor's rejection **is** the guard's refusal — the same status and
//! the same body a Python handler produced by raising out of `require_user` /
//! `require_admin`. Nothing here re-decides anything; they are thin wrappers
//! over [`AuthState`], which stays callable directly for handlers that take a
//! whole `Request`.
//!
//! Wiring: the route package's own state must expose the shared
//! `Arc<AuthState>` through `FromRef`, e.g.
//!
//! ```ignore
//! #[derive(Clone)]
//! struct PlatformState {
//!     auth: std::sync::Arc<lattice_auth::AuthState>,
//! }
//!
//! impl axum::extract::FromRef<PlatformState> for std::sync::Arc<lattice_auth::AuthState> {
//!     fn from_ref(state: &PlatformState) -> Self {
//!         std::sync::Arc::clone(&state.auth)
//!     }
//! }
//! ```

use std::sync::Arc;

use axum::async_trait;
use axum::extract::{FromRef, FromRequestParts};
use axum::http::request::Parts;
use axum::response::Response;

use crate::scope::workspace_scope_from_request;
use crate::state::{peer_of, AuthState, Identity};

/// The caller, after `require_user`. Rejects 401 when a session is required
/// and absent; resolves to the empty-identity owner on a trusted local bind.
#[derive(Debug, Clone)]
pub struct CurrentUser(pub Identity);

/// The caller, after `require_admin`. Rejects 403.
#[derive(Debug, Clone)]
pub struct AdminUser(pub Identity);

/// The workspace this request names — header first, then query — *unresolved*.
///
/// Authorization needs a [`crate::scope::WorkspaceResolver`], which lives in
/// the crate that owns workspace membership; call
/// [`crate::scope::resolve_workspace_scope`] there. This extractor is the
/// "what did they ask for" half, which is what most read routes want.
#[derive(Debug, Clone)]
pub struct RequestedWorkspace(pub Option<String>);

/// The address rate limits and audit records key on.
#[derive(Debug, Clone)]
pub struct ClientIp(pub String);

#[async_trait]
impl<S> FromRequestParts<S> for CurrentUser
where
    Arc<AuthState>: FromRef<S>,
    S: Send + Sync,
{
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let auth = Arc::<AuthState>::from_ref(state);
        auth.require_user(&parts.headers).map(CurrentUser)
    }
}

#[async_trait]
impl<S> FromRequestParts<S> for AdminUser
where
    Arc<AuthState>: FromRef<S>,
    S: Send + Sync,
{
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let auth = Arc::<AuthState>::from_ref(state);
        auth.require_admin(&parts.headers).map(AdminUser)
    }
}

#[async_trait]
impl<S> FromRequestParts<S> for RequestedWorkspace
where
    S: Send + Sync,
{
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        Ok(Self(workspace_scope_from_request(
            &parts.headers,
            parts.uri.query(),
        )))
    }
}

#[async_trait]
impl<S> FromRequestParts<S> for ClientIp
where
    Arc<AuthState>: FromRef<S>,
    S: Send + Sync,
{
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let auth = Arc::<AuthState>::from_ref(state);
        let peer = peer_of(parts);
        Ok(Self(auth.client_ip(&parts.headers, peer.as_deref())))
    }
}
