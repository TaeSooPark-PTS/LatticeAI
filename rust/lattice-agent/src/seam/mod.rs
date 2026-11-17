//! **Seam** — outbound compute port to the local Python worker.
//!
//! Separated from [`crate::surface`] (inbound HTTP routers and wire contracts)
//! in v13.0.0 so [`crate::kernel`] and [`crate::tools`] call downward into the
//! compute port and never reach upward into inbound HTTP transport.

pub mod worker;
