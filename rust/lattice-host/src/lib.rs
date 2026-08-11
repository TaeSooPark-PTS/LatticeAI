//! `lattice-host` — the Rust supervisor and IPC/API gateway for Lattice AI.
//!
//! Phase 1 of `docs/v11.4.0_RUST_FOUNDATION_PLAN.md`. Two independent pieces
//! live here, both usable as a library (`src-tauri` consumes them as a path
//! dependency; the `lattice-host` binary is an opt-in front door):
//!
//! * [`supervisor`] — resolves the Python worker command with the same four
//!   rules the desktop shell uses today (env override → `ltcai` on PATH →
//!   importable `latticeai.cli.entrypoint` → bundled tree), spawns it with the
//!   pinned safety-off environment, gates on an HTTP `GET /health` probe
//!   instead of a bare TCP connect, restarts it with exponential backoff after
//!   a crash, and stops it gracefully (SIGTERM, then SIGKILL after a grace
//!   period).
//! * [`gateway`] — an axum router bound strictly to loopback that answers
//!   `/host/health` and `/host/status` itself, serves `/rust/search/{hybrid,
//!   keyword,vector}` natively out of `lattice-retrieval` against the
//!   read-only store, and reverse-proxies everything else to the worker with
//!   the response body streamed (so SSE keeps flowing).
//!
//! Nothing in this crate depends on `tauri`, so the whole thing builds and
//! tests on a bare CI runner.

#![warn(missing_docs)]
#![warn(clippy::all)]

pub mod gateway;
pub mod supervisor;

pub use gateway::{
    bind_loopback, build_router, serve_gateway, GatewayError, GatewayState, StatusProvider,
};
pub use supervisor::{
    resolve_worker_command, BackoffPolicy, CommandOrigin, HostProbe, ResolveError, StaticProbe,
    Supervisor, SupervisorConfig, SupervisorError, SystemProbe, WorkerCommand, WorkerStatus,
};

/// Version of this crate, kept in lockstep with the product version by
/// `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
