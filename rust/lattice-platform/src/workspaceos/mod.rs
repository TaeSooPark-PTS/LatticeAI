//! **Workspace OS** — the place a person works, who may be in it, and what is
//! happening in it right now.
//!
//! [`workspace`] is the family itself: the workspace registry, the org and
//! membership half, memories, snapshots, skills, the timeline, indexing
//! controls and the run log — ~3,500 lines over one state document
//! (`workspace_os.json` plus the `workspace_os_state` row in
//! `knowledge_graph.sqlite`). Around it sit the four questions a workspace has
//! to answer about *people*: who may join ([`invitations`]), what a caller may
//! touch right now ([`permissions`]), how much the agent may do unasked
//! ([`permission_mode`]), and which parts of the product are switched on
//! ([`features`]). [`project_sessions`] is the working context a run is scoped
//! to, and [`realtime`] is presence and the activity feed — the same place,
//! observed live.
//!
//! ## What belongs here
//!
//! * A route over the workspace state document, or over one of its branches.
//! * A rule about **membership or scope**: who is in this workspace, what their
//!   role permits, which workspace a request is acting inside.
//! * A dial a *person* sets that changes what the product may do to them —
//!   the autonomy mode, a feature toggle, a per-path approval.
//! * Presence, the activity feed, and the session a run belongs to.
//!
//! ## What must never go here
//!
//! * **A second store over `workspace_os.json`.** [`workspace::store::WorkspaceOsStore`]
//!   is the only writer, and [`crate::governance::review_queue`] is a facade over
//!   the *same* `Arc`, not a second copy. Two stores over one document is
//!   last-writer-wins, and that is exactly the bug v11.7.0 closed.
//! * **A graph write.** Every knowledge-graph mutation this family needs goes
//!   out through [`workspace::deps::GraphSeam`] — the graph has one writer and
//!   it is not here. Reads arrive through the same dependency object.
//! * **The page shells.** `GET /workspace` and `GET /graph` are 308s into the
//!   SPA's hash router and belong to [`crate::shell::ui_redirects`], even though
//!   `workspace.py` declares them. A family that mounts its own shell and the
//!   redirect table that also mounts it will panic the router at startup.
//! * **A decision about a *tool*.** Whether `write_file` may run is
//!   `lattice_agent::kernel`; this domain says who the caller is and what scope
//!   they are in, and hands that over.
//!
//! ## Invariants
//!
//! 1. **One writer, one lock, one version stamp.** The host clones a single
//!    [`workspace::store::WorkspaceOsStore`] `Arc` into every family that
//!    touches the document. A module that constructs its own is wrong even if
//!    its tests pass, because the failure it causes is another family's write
//!    disappearing.
//! 2. **State migrates in place.** Everything here reads and writes the exact
//!    files and shapes the Python original wrote. A live install upgrades
//!    without a conversion step, so a format change is a product decision, not
//!    an implementation detail.
//! 3. **Scope is carried, never inferred.** A handler resolves the workspace
//!    from the request (header, path, or the caller's default) and passes it
//!    down; deriving it a second time deeper in the call stack is how v1.1.0's
//!    scoping holes were made.
//! 4. **The autonomy dial's write is atomic.** [`permission_mode`] replaces
//!    `permission_mode.json` through `<path>.tmp` → rename at mode 0600, and
//!    resolves the previous value from the already-loaded document rather than
//!    re-entering its own lock. Both rules are v9.9.8 bug fixes: a torn write
//!    lost a scope, and the re-entry deadlocked.

pub mod features;
pub mod invitations;
pub mod permission_mode;
pub mod permissions;
pub mod project_sessions;
pub mod realtime;
pub mod workspace;
