//! Workspace OS and organization workspaces, native (v11.6.0, WP-R1).
//!
//! Port of `latticeai/api/workspace.py` (54 routes here; its two page shells
//! live with [`crate::ui_redirects`]) over `latticeai/core/workspace_os.py` and
//! the twelve manager modules that class composes. It is the largest platform
//! family in the product and the one that owns the most state, so it is split
//! by the same seams the Python side is split by — one module per branch of the
//! state document — rather than kept as one file.
//!
//! ## What is native and what is delegated
//!
//! Per WP-I1's ownership map, the **product** side of Workspace OS state is
//! Rust's: `workspace_os.json`, the `workspace_os_state` row in
//! `knowledge_graph.sqlite`, the workspace registry, the timeline, and the
//! `workspace_snapshots/` + `workspace_exports/` directories. All of that is
//! read and written here, in place, in the formats Python wrote — a live
//! install migrates without a conversion step.
//!
//! Everything that **writes the knowledge graph** is delegated over the worker
//! seam ([`deps::GraphSeam`]), because the graph has one writer. Five of this
//! family's routes reach it: memory upsert, agent-run recording, workflow
//! creation, the VS Code bridge, and computer-activity recording all call
//! `ingest_event`; snapshot restore calls `import_graph_data`; the three
//! indexing controls call `set_local_source_watch` / `remove_local_source`.
//! Graph *reads* (stats, the node/edge window, local sources, neighbours) are
//! another family's surface and arrive through the same dependency object.
//!
//! ## Structure
//!
//! | module | Python original |
//! |---|---|
//! | [`constants`] | `core/workspace_os_constants.py` |
//! | [`pyutil`] | `core/workspace_os_utils.py` + `lattice_brain.utils` |
//! | [`state`] | `core/workspace_os_state.py` |
//! | [`store`] | `WorkspaceOSStore`'s persistence half |
//! | [`orgs`] | the org/membership half + `core/workspace_permissions.py` |
//! | [`onboarding`] | `core/workspace_onboarding.py` |
//! | [`memories`] | `core/workspace_memory.py` |
//! | [`snapshots`] | `core/workspace_snapshots.py` |
//! | [`runs`] | the agent/workflow slice of `core/workspace_runs.py` |
//! | [`skills`] | `core/workspace_skills.py` |
//! | [`computer`] | `core/workspace_computer_memory.py` + the VS Code bridge |
//! | [`indexing`] | `core/workspace_indexing.py` |
//! | [`relationships`] | `core/workspace_relationships.py` |
//! | [`timeline`] | `core/workspace_timeline.py` |
//! | [`redact`] | `core/security.py::redact_secret_text` |
//! | [`deps`] | `services/app_context.py`'s workspace slice |
//! | [`routes`] | the router factory and every handler |
//!
//! Splitting is not a preference here: `scripts/check_max_file_lines.mjs` caps
//! every `*.rs` at 1,000 lines, and this family is ~3,500 lines of behaviour.
//! `src/workspace.rs` stays the module root the crate's `lib.rs` declares, so
//! no shared file changed to make room for it.

pub mod computer;
pub mod constants;
pub mod deps;
pub mod handlers_core;
pub mod handlers_more;
pub mod http;
pub mod indexing;
pub mod memories;
pub mod onboarding;
pub mod orgs;
pub mod pyutil;
pub mod redact;
pub mod relationships;
pub mod reqbody;
pub mod routes;
pub mod runs;
pub mod service;
pub mod skills;
pub mod snapshots;
pub mod state;
pub mod store;
pub mod timeline;

pub use deps::{GraphReads, GraphSeam, IngestEvent, WorkspaceDeps, WorkspaceProviders};
pub use routes::{router, WorkspaceState, MOUNTED};
pub use service::WorkspaceService;
pub use store::{StoreError, WorkspaceOsStore};
