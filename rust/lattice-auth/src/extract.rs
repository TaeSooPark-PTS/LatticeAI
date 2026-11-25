use std::sync::Arc;

use axum::extract::{FromRef, FromRequestParts};
use axum::http::request::Parts;
use axum::response::Response;

use crate::scope::workspace_scope_from_request;
use crate::state::{peer_of, AuthState, Identity};

#[derive(Debug, Clone)]
pub struct CurrentUser(pub Identity);

#[derive(Debug, Clone)]
pub struct AdminUser(pub Identity);

#[derive(Debug, Clone)]
pub struct RequestedWorkspace(pub Option<String>);

#[derive(Debug, Clone)]
pub struct ClientIp(pub String);

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
