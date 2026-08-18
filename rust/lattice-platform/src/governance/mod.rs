//! **Governance** — work the product proposes or performs on its own, and the
//! human gate in front of it.
//!
//! Three modules make work happen without anyone asking: [`automation`] mines
//! questions and offers an automation to install, [`workflow_designer`] holds
//! the definitions and executes them, [`hooks`] fires one when an event says
//! so. Two modules stand in front of that work: [`change_proposals`] stages a
//! file change with its base SHA and its unified diff, and [`review_queue`] is
//! the Review Center that a person actually approves or rejects in.
//!
//! They are one domain because neither half means anything alone. A proposal
//! nothing can produce is dead code; automation with no gate is the thing
//! v9.6.0 was built to stop. The call graph says the same: every module here
//! reaches [`review_queue`], and [`review_queue`] reaches back into
//! [`change_proposals`] to apply an approved item.
//!
//! ## What belongs here
//!
//! * A trigger — an event, a schedule, a mined suggestion — and the definition
//!   of the work it starts.
//! * A node type, an executor step, or a workflow recipe.
//! * A review item: its shape, its status transitions, and what approving it
//!   does.
//! * The staging of a change that is *not yet* applied, and the conflict check
//!   that decides whether it still may be.
//!
//! ## What must never go here
//!
//! * **A permission decision.** Whether the agent was allowed to attempt the
//!   change is `lattice_agent::kernel::permission`; this domain records what it
//!   attempted and asks a person. A gate here that re-derives "was this
//!   allowed" has forked the kernel.
//! * **A second store over `workspace_os.json`.** [`review_queue`]'s
//!   [`review_queue::GovernanceState`] is a facade over the workspace family's
//!   single [`crate::workspaceos::workspace::store::WorkspaceOsStore`]. Before
//!   v11.7.0 it kept its own cached copy, and workspace-side writes landing
//!   between its load and its save were silently erased.
//! * **The write itself, done directly.** Applying an approved proposal goes
//!   through [`lattice_agent::sandbox::Workspace`] — the same sandbox the
//!   agent's own native `write_file` resolves through — so a proposal can never
//!   reach a path the agent could not have written in the first place.
//! * **A page shell.** `GET /workflows` is a 308 owned by
//!   [`crate::shell::ui_redirects`].
//!
//! ## Invariants
//!
//! 1. **Nothing applies without a base-SHA check.** An approval whose base no
//!    longer matches is a 409, not a merge and not a silent overwrite. That is
//!    the v9.9.1 conflict protection, and it is the only thing standing between
//!    a stale review tab and a lost edit.
//! 2. **Fail-closed on an unreachable verdict.** A verifier that cannot decide
//!    yields `NEEDS_REVIEW`, never "approved". An item whose class cannot be
//!    determined is treated as its most dangerous plausible class.
//! 3. **Every state change is an event.** [`review_queue::REVIEW_ITEM_CREATED_EVENT`]
//!    and [`review_queue::REVIEW_ITEM_UPDATED_EVENT`] land on
//!    [`review_queue::REVIEW_TIMELINE_AREA`], and `lattice-host` asserts those
//!    three names from outside the crate. Renaming one is a cross-crate change.
//! 4. **The item shape is shared, not copied.** `lattice_agent::kernel::proposals`
//!    builds review items byte-compatible with the ones here so a staged
//!    mutation looks identical whether the loop or a route created it. A field
//!    added on one side and not the other is drift a test will not see.

pub mod automation;
pub mod change_proposals;
pub mod hooks;
pub mod review_queue;
pub mod workflow_designer;
