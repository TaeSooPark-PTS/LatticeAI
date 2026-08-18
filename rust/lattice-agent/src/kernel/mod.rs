//! **Kernel** — the loop that runs an agent, and every decision that can refuse.
//!
//! This is the half of the crate that would still be worth having with no HTTP
//! server attached. [`agentloop`] is the state machine (plan → execute →
//! verify, with fallback and rollback); everything beside it is either a
//! *decision* the machine consults ([`policy`], [`mode`], [`permission`],
//! [`breaker`], [`governor`], [`proposals`]) or *state* one run carries
//! ([`state`], [`transcript`], [`trace`], [`plan`], [`profile`], [`runs`]).
//!
//! ## What belongs here
//!
//! * A phase of the loop, or a gate a phase must pass.
//! * A rule that answers "may this run?" — and answers it from **values**
//!   (a [`policy::ToolPolicy`], a [`mode::PermissionMode`], a tool name), never
//!   from a network call, an environment probe or a clock.
//! * A measurement of the **model**, as opposed to a decision about a run:
//!   [`probe`] (v12.0.0) asks two fixed questions and caches what came back, so
//!   [`profile`] stops guessing a dial from a regex over the model id. It is
//!   the one module here that makes a network call, it is injected rather than
//!   assumed ([`agentloop::LoopDeps::probe`]), and every failure of it falls
//!   back to the pure prior.
//! * The typed record of what a run did: [`state::AgentRunContext`], the
//!   transcript shaping the critic is handed, the [`trace::LoopTrace`] stream,
//!   and the pause/resume snapshot in [`runs`].
//!
//! ## What must never go here
//!
//! * **An HTTP route, an axum type, or a request/response body.** Those live in
//!   [`crate::surface`]. The kernel is called by the surface and never the
//!   other way round; a kernel module that needs to know how a caller phrased
//!   something has been handed the wrong argument.
//! * **A tool implementation.** *Deciding* that `write_file` may run is kernel
//!   work; *writing the file* is [`crate::tools`]. The kernel names tools as
//!   strings and reads their policy; it never opens a file itself. (The one
//!   deliberate exception is [`agentloop::recovery`], which rolls the workspace
//!   back — a rollback that has to ask a possibly broken worker for permission
//!   is not a rollback, and the module's own doc says so.)
//! * **Text parsing.** Turning what a model said into a typed action is
//!   [`crate::parse`]; deciding what to do with the result is here.
//! * **A second copy of a policy table.** The tables are data owned by
//!   `latticeai.core.tool_registry` and arrive as [`policy::ToolPolicy`]
//!   values. A hard-coded transcription is a drift waiting to happen.
//!
//! ## Invariants
//!
//! 1. **Fail-closed is law.** Every gate's unknown case is a refusal. A
//!    verifier that cannot reach a verdict returns `NEEDS_REVIEW`, not "pass";
//!    a tool with no policy entry is treated as its most dangerous plausible
//!    class, not as read-only; an approval token that cannot be verified is
//!    rejected. If a change here makes an error path *permissive*, it is wrong
//!    however green the tests are.
//! 2. **Circuit breakers do not read the mode.** [`breaker`] exists as its own
//!    module so that "bypass skips approval prompts, it never unlocks a
//!    destructive tool" is enforced by the call graph, not by a comment.
//! 3. **Order is the contract.** The gate chain in [`agentloop::gates`], the
//!    priority chain in [`permission::block_reason_for_tool`] and the seven
//!    rules of [`plan::normalize_plan`] are ordered, the order is pinned by the
//!    frozen goldens under `rust/fixtures/agent/`, and reordering them is a
//!    behaviour change even when every individual rule is unchanged.
//! 4. **Decisions are pure.** Given the same context, policy table and mode,
//!    a gate returns the same verdict. The impure edges — the worker call, the
//!    proposal write, the run snapshot — are ports the caller injects.

pub mod agentloop;
pub mod breaker;
pub mod governor;
pub mod mode;
pub mod permission;
pub mod plan;
pub mod policy;
pub mod probe;
pub mod profile;
pub mod proposals;
pub mod runs;
pub mod state;
pub mod trace;
pub mod transcript;
