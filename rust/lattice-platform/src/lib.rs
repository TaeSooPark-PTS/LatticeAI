//! `lattice-platform` — the product's surface, natively (v11.6.0 One Door).
//!
//! This crate is **everything the product looks like**: 30 route families over
//! roughly 420 operations, covering the workspace, the Review Center, the tool
//! and MCP surface, portability, onboarding, the admin console and the bytes a
//! browser is handed first. Reads, platform-state writes **and** knowledge-graph
//! writes are native as of §W3b — every family that used to post to
//! `POST /worker/graph/mutate` now calls `GraphWriter`, and the host binds one
//! writer per process.
//!
//! # The rule that decides what lives here
//!
//! Three crates share the product's back end, and the line between them is
//! about *what kind of question* is being answered:
//!
//! | crate | question | example |
//! |---|---|---|
//! | **`lattice-platform`** (here) | *what does the product offer?* | `POST /api/knowledge-graph/export`, the Review Center, `/setup/scan` |
//! | `lattice-agent` (kernel) | *may this run, and what does it do?* | `block_reason_for_tool`, the loop's gates, the native `write_file` |
//! | `lattice-core` | *what is true?* | the graph, its one writer, the state files |
//!
//! So: a **surface** goes here. A **decision** goes to the kernel. **Truth**
//! goes to core. A handler in this crate that re-derives "is this allowed" has
//! forked `lattice_agent::kernel::permission`; a module here that opens the
//! graph directly has become a second writer. Both are the failure this
//! boundary exists to prevent.
//!
//! Compute — inference, the document parser/generator matrix, ASR — stays with
//! the Python worker and is reached over `lattice_core::worker::WorkerSeamClient`.
//!
//! # The map (v12.0.0)
//!
//! Seven domains. Before v12.0.0 these were 30 flat modules in one directory,
//! which said nothing about which of them belonged to the same idea. Each
//! domain's `mod.rs` states what belongs in it, what must never go in it, and
//! its invariants; `ARCHITECTURE.md` beside this file is the short version for
//! a first read.
//!
//! ```text
//!    browser ────────────────────────────┐
//!                                        ▼
//!   ┌──────────────────────────────────────────────────────────┐
//!   │ shell/         static_ui · ui_redirects                  │
//!   │                the SPA shell, its headers, every 308     │
//!   └──────────────────────────────────────────────────────────┘
//!    HTTP client ────────────────────────┐
//!                                        ▼
//!   ┌────────────────────────────┐ ┌───────────────────────────┐
//!   │ workspaceos/               │ │ governance/               │
//!   │   workspace ◄──────────────┼─┤   review_queue            │
//!   │   invitations   one store  │ │   change_proposals        │
//!   │   permissions              │ │   automation              │
//!   │   permission_mode          │ │   workflow_designer       │
//!   │   features                 │ │   hooks                   │
//!   │   project_sessions         │ └───────────────────────────┘
//!   │   realtime                 │ ┌───────────────────────────┐
//!   └────────────────────────────┘ │ toolsurface/              │
//!   ┌────────────────────────────┐ │   mcp ◄── tools · plugins │
//!   │ knowledge/                 │ │   marketplace · agents    │
//!   │   portability · network    │ │   agent_registry          │
//!   │   network_boundary · voice │ │   computer_use            │
//!   └────────────────────────────┘ └───────────────────────────┘
//!   ┌────────────────────────────┐ ┌───────────────────────────┐
//!   │ modelops/                  │ │ adminops/                 │
//!   │   models_catalog ◄─ setup  │ │   admin ◄─ security_dash. │
//!   │                            │ │         ◄─ funnel_metrics │
//!   └────────────────────────────┘ └───────────────────────────┘
//!                     │ every domain calls down, never up
//!   ┌──────────────┬──┴───────────┬──────────────┬─────────────┐
//!   │ lattice-auth │ lattice-agent│ lattice-core │ Python      │
//!   │ who is this  │ may it run   │ graph truth  │ worker:     │
//!   │              │              │              │ compute     │
//!   └──────────────┴──────────────┴──────────────┴─────────────┘
//! ```
//!
//! Read it top-down. `shell/` serves the app; the six product domains answer
//! it; each of them calls *down* into identity, decisions, truth or compute and
//! never sideways into a sibling's internals. The `◄─` arrows are the four
//! cross-domain couplings that carry meaning rather than a borrowed helper:
//! `governance` writes through `workspaceos`'s single store,
//! `toolsurface` speaks `mcp`'s HTTP vocabulary, `modelops`'s setup reads its
//! own catalog's host probe, and `adminops`' two readers share `admin`'s one
//! audit writer.
//!
//! # Conventions every family keeps
//!
//! * One module owns exactly one route family, so two work packages never edit
//!   the same file.
//! * Each family exports `MOUNTED: &[(&str, &str)]` — its declared (method,
//!   path) pairs — and a `router(state) -> Router` factory. `lattice-host`
//!   mounts the factories and asserts the union of `MOUNTED` has no duplicates
//!   *before* the router is built, so a double-mount is a named assertion
//!   rather than a panic in a constructor.
//! * Every guard answers `Result<T, axum::response::Response>`: the error *is*
//!   the rendered refusal, byte-for-byte the Python one.
//! * On-disk state is the Python state, in place. A live install upgrades
//!   without a conversion step.

// Every guard in this crate answers `Result<T, axum::response::Response>` —
// the error *is* the rendered refusal, byte-for-byte the Python one. That is
// `lattice-auth`'s convention (see its own crate-root allow) and boxing it
// here would only move the unboxing into every handler. Allowed once, at the
// root, rather than in the two dozen modules that already carry it.
#![allow(clippy::result_large_err)]

pub mod adminops;
pub mod governance;
pub mod knowledge;
pub mod modelops;
pub mod shell;
pub mod toolsurface;
pub mod workspaceos;

// ---------------------------------------------------------------------------
// Compatibility map — every `lattice_platform::…` path that existed before the
// v12.0.0 regrouping still resolves, spelled exactly as it was.
//
// `lattice-host` imports **all thirty** of these by name in one `use` (see
// `gateway/product.rs`), `lattice-chat` imports `admin`, and this crate's own
// integration tests under `tests/` name a dozen more. The regrouping moved
// *files*, not the public API: the re-exports below are the whole
// compatibility story, and they are also the honest list of what outside code
// depends on. Nothing may be dropped from it without a coordinated change in
// the crates named above.
//
// Inside `src/` the domain paths are used directly — `crate::adminops::admin`,
// not `crate::admin` — so a reader of any file sees which domain it is
// borrowing from. These aliases exist for callers outside the crate.
// ---------------------------------------------------------------------------

// workspaceos/ — the place a person works, and who may be in it.
pub use workspaceos::{
    features, invitations, permission_mode, permissions, project_sessions, realtime, workspace,
};
// governance/ — work the product does on its own, and the gate in front of it.
pub use governance::{automation, change_proposals, hooks, review_queue, workflow_designer};
// toolsurface/ — every capability the product can reach.
pub use toolsurface::{agent_registry, agents, computer_use, marketplace, mcp, plugins, tools};
// knowledge/ — how the graph's content crosses a trust boundary.
pub use knowledge::{network, network_boundary, portability, voice};
// modelops/ — which model runs here, and getting the machine ready for it.
pub use modelops::{models_catalog, setup};
// adminops/ — operating the install.
pub use adminops::{admin, funnel_metrics, security_dashboard};
// shell/ — the browser's first contact.
pub use shell::{static_ui, ui_redirects};

/// Placeholder from the v11.6.0 scaffold, kept because it is public API.
///
/// Every family module it was waiting for has landed; it is retained only so
/// that removing it is a deliberate, coordinated change rather than a side
/// effect of a regrouping.
pub fn crate_ready() -> bool {
    true
}
