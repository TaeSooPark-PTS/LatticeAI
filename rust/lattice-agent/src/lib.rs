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
pub mod seam;
pub mod surface;
pub mod tools;

/// Product version, kept in lockstep by `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
