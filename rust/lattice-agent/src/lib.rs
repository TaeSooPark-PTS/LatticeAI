//! The agent safety kernel, natively (v11.5.0, plan §4).
//!
//! The product diagram gives four labels to the agent runtime — Tools, Process,
//! Sandbox, Permission — and this crate owns three of them. It answers two
//! questions and refuses to answer a third:
//!
//! * **May this tool call run?** [`permission::block_reason_for_tool`] and
//!   [`mode::effective_auto_approve`], over the same circuit breakers, tool sets
//!   and change classes as `latticeai.core.permission_mode` /
//!   `latticeai.core.agent_permission` / `latticeai.core.tool_governor`.
//! * **Is this command string safe to run?** [`command::validate`], a 1:1 port
//!   of `latticeai.tools.commands.run_command`'s validator — POSIX `shlex`
//!   splitting, the executable allowlist, the shell-operator ban, `find`/`rg`
//!   flag denials, and the absolute / `..` / symlink-escape path rules.
//! * **What should this tool *do*?** Not this crate's question. Only validated,
//!   read-only, allow-listed commands are executed ([`exec`]); every mutating
//!   tool, `git`, `build_project` and `deploy_project` get a verdict and
//!   nothing else. Inference and mutation belong to the Python worker.
//!
//! Every decision is pinned by committed goldens under `rust/fixtures/agent/`,
//! produced from the **real** Python functions by
//! `scripts/generate_agent_parity_fixtures.py` and re-asserted from the Python
//! side by `tests/unit/test_agent_kernel_parity_contract.py`. The policy tables
//! themselves are **data**: the registry lives in Python, and this crate takes
//! [`policy::ToolPolicy`] values as input rather than hard-coding a second copy
//! that could drift.

pub mod action;
pub mod agentloop;
pub mod breaker;
pub mod command;
pub mod documents;
pub mod exec;
pub mod governor;
pub mod inference;
pub mod looproutes;
pub mod mode;
pub mod permission;
pub mod plan;
pub mod policy;
pub mod profile;
pub mod pyjson;
pub mod pyliteral;
pub mod pyshlex;
pub mod pystr;
pub mod router;
pub mod runbody;
pub mod runs;
pub mod sandbox;
pub mod state;
pub mod trace;
pub mod transcript;
pub mod worker;

pub use breaker::is_circuit_breaker;
pub use command::{validate, Validated};
pub use exec::{execute, Execution, NATIVE_EXECUTABLES, SAFE_EXECUTABLE_PATH};
pub use governor::{classify_tool_call, Classification, MUTATING_TOOL_INVENTORY};
pub use looproutes::{loop_router, LoopConfig};
pub use mode::{
    effective_auto_approve, mode_contract, normalize_mode, normalize_value, plan_requires_approval,
    should_stage_proposal, PermissionMode, DEFAULT_MODE,
};
pub use permission::{block_reason_for_tool, non_auto_plan_steps};
pub use policy::{PolicyTable, ToolPolicy};
pub use router::router;
pub use sandbox::{ErrorKind, ToolError, Workspace, MAX_COMMAND_OUTPUT, MAX_FILE_BYTES};

/// Product version, kept in lockstep by `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
