//! `lattice-platform` — the product's platform routes, native (v11.6.0).
//!
//! Scaffold committed by the integrator; the Wave-1/Wave-2 work packages
//! fill the family modules in. Reads and platform-state writes are native;
//! knowledge-graph writes stay delegated to the Python AI worker seam
//! (single writer). Every module below owns exactly one route family, so
//! two work packages never edit the same file; only the integrator edits
//! this file and `Cargo.toml`.

pub mod admin;
pub mod agent_registry;
pub mod agents;
pub mod automation;
pub mod change_proposals;
pub mod computer_use;
pub mod features;
pub mod funnel_metrics;
pub mod hooks;
pub mod invitations;
pub mod marketplace;
pub mod mcp;
pub mod models_catalog;
pub mod network;
pub mod network_boundary;
pub mod permission_mode;
pub mod permissions;
pub mod plugins;
pub mod portability;
pub mod project_sessions;
pub mod realtime;
pub mod review_queue;
pub mod security_dashboard;
pub mod setup;
pub mod static_ui;
pub mod tools;
pub mod ui_redirects;
pub mod voice;
pub mod workflow_designer;
pub mod workspace;
pub mod workspace_scope;

/// Placeholder retained until the first family module lands.
pub fn crate_ready() -> bool {
    true
}
