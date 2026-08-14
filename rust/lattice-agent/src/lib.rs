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
//! * **What should this tool *do*?** Since v11.6.0 (WP-W4) this crate answers
//!   that too, for the mutating half: [`tools`] executes the eighteen writing /
//!   actuating / executing handlers natively and writes the four document
//!   creators' bytes, so the loop's own effects never leave Rust. What stays
//!   with the Python worker is **compute** — inference, parsers, the document
//!   builders, and the twenty-five read-only handlers still reached over
//!   `POST /agent/tool`.
//! * **And when may it not run *yet*?** Since v11.6.0 (WP-P1c) [`proposals`]
//!   stages a `strict` mutation as a Review Center item here rather than asking
//!   the worker to: the classification, the unified diff ([`pydiff`]) and the
//!   review item are all computed in this process, and where the item is
//!   written is a port the Review Center's own store implements.
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
pub mod proposals;
pub mod pydiff;
pub mod pyjson;
pub mod pyliteral;
pub mod pyshlex;
pub mod pystr;
pub mod router;
pub mod runbody;
pub mod runs;
pub mod sandbox;
pub mod state;
pub mod tools;
pub mod trace;
pub mod transcript;
pub mod worker;

/// Membership in a **sorted** `&[&str]` table.
///
/// Every tool-name set in this crate is a sorted literal copied from the Python
/// constant it mirrors, so the lookup is a binary search rather than a scan —
/// and because the sortedness is what makes it correct, there is exactly one
/// place that assumes it. `governor` and `mode` each had their own copy.
pub(crate) fn in_set(set: &[&str], name: &str) -> bool {
    set.binary_search(&name).is_ok()
}

/// The 422-shaped 400 both routers answer for a body they cannot read.
///
/// One body for `/rust/agent/{preflight,exec}` and `/rust/agent/{run,resume}`:
/// a client that parses one parses the other.
pub(crate) fn bad_request(detail: impl Into<String>) -> axum::response::Response {
    use axum::response::IntoResponse;
    (
        axum::http::StatusCode::BAD_REQUEST,
        axum::Json(serde_json::json!({
            "error": "invalid_request",
            "detail": detail.into(),
        })),
    )
        .into_response()
}

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
pub use tools::{
    is_native, CallScope, HookSink, NativeCall, NativeTools, ToolConfig, ToolHost, MUTATING_TOOLS,
    RENDER_TOOLS,
};

/// Product version, kept in lockstep by `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
