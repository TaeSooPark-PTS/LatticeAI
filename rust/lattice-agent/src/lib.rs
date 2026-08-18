//! The agent safety kernel, natively (v11.5.0, plan §4).
//!
//! The product diagram gives four labels to the agent runtime — Tools, Process,
//! Sandbox, Permission — and this crate owns three of them. It answers two
//! questions and refuses to answer a third:
//!
//! * **May this tool call run?** [`kernel::permission::block_reason_for_tool`]
//!   and [`kernel::mode::effective_auto_approve`], over the same circuit
//!   breakers, tool sets and change classes as `latticeai.core.permission_mode`
//!   / `latticeai.core.agent_permission` / `latticeai.core.tool_governor`.
//! * **Is this command string safe to run?** [`tools::command::validate`], a
//!   1:1 port of `latticeai.tools.commands.run_command`'s validator — POSIX
//!   `shlex` splitting, the executable allowlist, the shell-operator ban,
//!   `find`/`rg` flag denials, and the absolute / `..` / symlink-escape path
//!   rules.
//! * **What should this tool *do*?** Since v11.6.0 (WP-W4) this crate answers
//!   that too, for the mutating half: [`tools`] executes the eighteen writing /
//!   actuating / executing handlers natively and writes the four document
//!   creators' bytes, so the loop's own effects never leave Rust. What stays
//!   with the Python worker is **compute** — inference, parsers, the document
//!   builders, and the twenty-five read-only handlers still reached over
//!   `POST /agent/tool`.
//! * **And when may it not run *yet*?** Since v11.6.0 (WP-P1c)
//!   [`kernel::proposals`] stages a `strict` mutation as a Review Center item
//!   here rather than asking the worker to: the classification, the unified
//!   diff ([`content::pydiff`]) and the review item are all computed in this
//!   process, and where the item is written is a port the Review Center's own
//!   store implements.
//!
//! Every decision is pinned by committed goldens under `rust/fixtures/agent/`,
//! produced from the **real** Python functions and now **frozen** —
//! `rust/fixtures/agent/FROZEN.md` records what each file is, what was removed
//! when the generator went away, and where the removed coverage moved to. The
//! policy tables themselves are **data**: this crate takes
//! [`kernel::policy::ToolPolicy`] values as input rather than hard-coding a
//! second copy that could drift.
//!
//! # The map (v12.0.0)
//!
//! Six groups, split first along **kernel vs surface** — what would still be
//! worth having with no HTTP server attached, versus the transport that carries
//! it. Each group's `mod.rs` states what belongs in it, what must never go in
//! it, and its invariants; `ARCHITECTURE.md` beside this file is the short
//! version for a first read.
//!
//! ```text
//!                       ┌──────────────────────────────────────────┐
//!    HTTP in  ────────► │ surface/   router · looproutes · runbody  │ ──────► HTTP out
//!                       │            worker (→ Python compute)     │
//!                       └────────────────────┬─────────────────────┘
//!                                            │ calls, never decides
//!                       ┌────────────────────▼─────────────────────┐
//!                       │ kernel/    agentloop/ (plan → execute →   │
//!                       │              verify → fallback/recovery)  │
//!                       │            policy · mode · permission ·   │
//!                       │            breaker · governor · proposals │
//!                       │            state · transcript · trace ·   │
//!                       │            plan · profile · runs          │
//!                       └───┬──────────────┬──────────────┬─────────┘
//!                           │ reads text   │ judges bytes │ performs effects
//!                    ┌──────▼──────┐ ┌─────▼───────┐ ┌────▼──────────────────┐
//!                    │ parse/      │ │ content/    │ │ tools/                │
//!                    │  action     │ │  sanitize/  │ │  host (dispatcher)    │
//!                    │  channel    │ │   extract   │ │  sandbox · command    │
//!                    │  inference  │ │   validate  │ │  exec · authorize     │
//!                    │  py{json,   │ │   repair    │ │  files · shell · vault│
//!                    │   literal,  │ │   salvage   │ │  desktop · render     │
//!                    │   shlex,str}│ │  pydiff     │ │  scaffold · local     │
//!                    └─────────────┘ └─────────────┘ └───────────────────────┘
//!                                            ▲
//!                                  prompts/  │  the words the model is given
//! ```
//!
//! Read it top-down: a request enters `surface`, the `kernel` decides, `parse`
//! tells it what the model said, `content` judges the bytes about to be
//! written, `tools` performs the effect. Arrows never point back up — the
//! kernel does not know it is behind a route.

pub mod content;
pub mod kernel;
pub mod parse;
pub mod prompts;
pub mod surface;
pub mod tools;

// ---------------------------------------------------------------------------
// Compatibility map — every `lattice_agent::…` path that existed before the
// v12.0.0 regrouping still resolves, spelled exactly as it was.
//
// `lattice-host`, `lattice-platform`, `lattice-chat` and `lattice-core` import
// these modules by name, and this crate's own integration tests under `tests/`
// do too. The regrouping moved *files*, not the public API: the re-exports
// below are the whole compatibility story, and they are also the honest list of
// what outside code depends on. Nothing may be dropped from it without a
// coordinated change in the crates named above.
// ---------------------------------------------------------------------------

// kernel/ — the loop and every decision that can refuse.
pub use kernel::{
    agentloop, breaker, governor, mode, permission, plan, policy, profile, proposals, runs, state,
    trace, transcript,
};
// parse/ — untrusted model text in, typed values out.
pub use parse::{action, channel, inference, pyjson, pyliteral, pyshlex, pystr};
// content/ — the bytes a run is about to write.
pub use content::{pydiff, sanitize};
// tools/ — `tools` itself is unmoved; these three joined it from the root.
pub use tools::{command, documents, exec, sandbox};
// surface/ — both edges of the HTTP boundary.
pub use surface::{looproutes, router, runbody, worker};

// The flat item re-exports the crate has always offered, unchanged.
pub use content::sanitize::{sanitize_write_content, SanitizeMeta};
pub use kernel::breaker::is_circuit_breaker;
pub use kernel::governor::{classify_tool_call, Classification, MUTATING_TOOL_INVENTORY};
pub use kernel::mode::{
    effective_auto_approve, mode_contract, normalize_mode, normalize_value, plan_requires_approval,
    should_stage_proposal, PermissionMode, DEFAULT_MODE,
};
pub use kernel::permission::{block_reason_for_tool, non_auto_plan_steps};
pub use kernel::policy::{PolicyTable, ToolPolicy};
pub use surface::looproutes::{loop_router, LoopConfig};
pub use surface::router::router;
pub use tools::command::{validate, Validated};
pub use tools::exec::{execute, Execution, NATIVE_EXECUTABLES, SAFE_EXECUTABLE_PATH};
pub use tools::sandbox::{ErrorKind, ToolError, Workspace, MAX_COMMAND_OUTPUT, MAX_FILE_BYTES};
pub use tools::{
    is_native, CallScope, HookSink, NativeCall, NativeTools, ToolConfig, ToolHost, MUTATING_TOOLS,
    RENDER_TOOLS,
};

/// Product version, kept in lockstep by `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
