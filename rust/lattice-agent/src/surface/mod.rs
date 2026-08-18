//! **Surface** — both edges of the HTTP boundary, and no decision of its own.
//!
//! Inbound: [`router`] mounts the stateless kernel endpoints
//! (`/rust/agent/{preflight,exec,contract}`) and [`looproutes`] mounts the run
//! endpoints (`/rust/agent/{run,resume,approvals}`), with [`runbody`] holding
//! the request and response *contracts* — the shapes a client may send and
//! will receive. Outbound: [`worker`] is the client this process reaches the
//! Python compute worker through, the only remaining call out of the kernel.
//!
//! Both edges are transport. Everything they carry is decided in
//! [`crate::kernel`] and performed in [`crate::tools`].
//!
//! ## What belongs here
//!
//! * A route, its handler, and the axum wiring that mounts it.
//! * A request body, a response body, or a conversion between one of those and
//!   the kernel's own types — that is what [`runbody`] is for.
//! * The shape of an error a client sees, including `bad_request`.
//! * The outbound seam to the compute worker, and its retry/error mapping.
//!
//! ## What must never go here
//!
//! * **A decision.** A handler that answers "is this allowed" itself has
//!   forked [`crate::kernel::permission`]. Handlers translate, call, and
//!   translate back.
//! * **A tool implementation.** See [`crate::tools`].
//! * **Business state.** The surface is stateless per request; what a run
//!   knows lives in [`crate::kernel::state::AgentRunContext`] and, across a
//!   pause, in [`crate::kernel::runs`].
//!
//! ## Invariants
//!
//! 1. **One error shape for both routers.** A client that can parse a 400 from
//!    `/rust/agent/exec` can parse one from `/rust/agent/run`, because both go
//!    through `bad_request`.
//! 2. **A body that cannot be read is a 400, never a panic and never a
//!    default.** Filling in a missing field with a plausible value would let
//!    the kernel act on something the caller never sent.
//! 3. **The contract is additive.** These routes are consumed by
//!    `lattice-host`, the VS Code extension and the OpenAPI fragment; renaming
//!    or removing a field is a release-gated change, and the drift gate will
//!    say so.
//! 4. **The surface never widens the kernel.** If a route needs a capability
//!    the kernel does not expose, the answer is a kernel change reviewed on its
//!    own terms — not a shortcut taken in a handler.

pub mod looproutes;
pub mod router;
pub mod runbody;
pub mod worker;

/// The 422-shaped 400 both routers answer for a body they cannot read.
///
/// One body for `/rust/agent/{preflight,exec}` and `/rust/agent/{run,resume}`:
/// a client that parses one parses the other.
pub(crate) fn bad_request(detail: impl Into<String>) -> axum::response::Response {
    use axum::response::IntoResponse;
    (
        axum::http::StatusCode::BAD_REQUEST,
        axum::Json(serde_json::json!({
            "error": "invalid_request",
            "detail": detail.into(),
        })),
    )
        .into_response()
}
