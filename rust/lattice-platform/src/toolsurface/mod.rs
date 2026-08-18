//! **Tool surface** — every capability the product can reach, and the catalogs
//! that describe them.
//!
//! [`mcp`] is the hub: the MCP registry, the JSON-RPC server, the skills and
//! plugin-directory surface, and the shared HTTP vocabulary (`require_user`,
//! `parse_json_object`, `detail`, `json_status`) that the rest of the domain
//! calls a route "well-formed" with. Hanging off it: [`tools`] is the direct
//! tool surface a client can invoke by name, [`plugins`] the plugin SDK,
//! [`marketplace`] the local template catalog, [`agents`] the multi-agent
//! runtime plus the single-agent HTTP loop, [`agent_registry`] the roster of
//! agents that runtime can start, and [`computer_use`] the desktop bridge.
//! `generated_catalogs.rs` beside this file is the committed catalog data
//! [`mcp`] and [`marketplace`] serve; it is `include!`d, not a module.
//!
//! Read the domain as one sentence: **what can be called, what it is called,
//! and who is allowed to call it.**
//!
//! ## Where a new tool goes
//!
//! Pick the file by what the tool *touches* — `tools/fs.rs` for workspace
//! files, `tools/shell.rs` for subprocesses, `tools/knowledge.rs` for the
//! graph, `tools/downloads.rs` for artifacts, `tools/meta.rs` for
//! self-description — then add its governance class to [`tools::governance_for`],
//! which `lattice-host` reads from outside the crate. A tool that runs *inside
//! an agent turn* is not added here at all: its handler belongs to
//! `lattice_agent::tools`, and this domain only exposes it over HTTP.
//!
//! ## What belongs here
//!
//! * A capability the product can invoke, and its HTTP route.
//! * A catalog entry, a connector definition, or a template.
//! * The registry that answers "does this name exist, and what does it cost to
//!   run it".
//!
//! ## What must never go here
//!
//! * **A second copy of a tool's behaviour.** Filesystem writes go through
//!   [`lattice_agent::sandbox::Workspace`] so that the three writers — the
//!   agent's `write_file`, this domain's `/tools/write_file`, and an approved
//!   change proposal — share one path policy. A handler that opens a file
//!   itself has forked the sandbox.
//! * **A permission *policy*.** [`tools::governance_for`] reports a tool's
//!   class; deciding what that class permits is `lattice_agent::kernel`.
//! * **Workspace state.** Which workspace a call is scoped to comes from
//!   [`crate::workspaceos`] and is passed in.
//! * **A route whose real work is inference.** Model compute stays with the
//!   Python worker over the seam; this domain carries the request there and the
//!   answer back.
//!
//! ## Invariants
//!
//! 1. **One HTTP vocabulary.** Every module here builds its refusals from
//!    [`mcp`]'s helpers, so a client that can parse a 400 from `/tools/*` can
//!    parse one from `/plugins/*`. A module that hand-rolls its own error body
//!    has made the surface inconsistent in a way no test asserts.
//! 2. **Traversal denials are the Python bodies, verbatim.** The i18n
//!    `ToolError` shapes the fixtures pin are the contract; a "clearer" message
//!    is a breaking change to every client that matches on them.
//! 3. **`GET /plugins/sdk` is this domain's, not the redirect table's.** It
//!    carries its own `require_user`, which the shell's redirect map does not —
//!    which is why `lattice-host` lists it in `REDIRECTS_OWNED_ELSEWHERE`.
//!    Mounting both would panic the router at startup.
//! 4. **The catalogs are data.** `generated_catalogs.rs` is committed content,
//!    not logic. A remote catalog fetch is optional, cached, and may never fail
//!    a request — the offline cache is the answer of record.

pub mod agent_registry;
pub mod agents;
pub mod computer_use;
pub mod marketplace;
pub mod mcp;
pub mod plugins;
pub mod tools;
