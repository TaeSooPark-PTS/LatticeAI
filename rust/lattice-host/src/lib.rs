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
//! v11.5.0 (`docs/v11.5.0_RUST_COMPLETE_PLAN.md`) makes the gateway the front
//! door rather than a side door. It now mounts four crates' router factories
//! ahead of that proxy fallthrough — `lattice-retrieval`'s graph/history/
//! context reads, `lattice-ingest`'s dry-run plan/chunk routes,
//! `lattice-agent`'s permission kernel, and `lattice-jobs`' scheduler status —
//! and the supervisor tells the worker to trust the gateway's origin so
//! cookie-authenticated writes work through it. See [`gateway::mounts`] for the
//! mount map.
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
    csrf_trusted_origins, resolve_worker_command, BackoffPolicy, CommandOrigin, HostProbe,
    ResolveError, StaticProbe, Supervisor, SupervisorConfig, SupervisorError, SystemProbe,
    WorkerCommand, WorkerStatus,
};

/// Version of this crate, kept in lockstep with the product version by
/// `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
