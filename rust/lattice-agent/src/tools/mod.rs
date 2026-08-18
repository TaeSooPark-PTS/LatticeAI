//! **Tools** — what a tool does, and the ground it is allowed to do it on.
//!
//! The kernel decides *that* `write_file` may run; this group is what actually
//! happens next. [`host`] is the dispatcher ([`host::NativeTools`], the
//! eighteen mutating handlers plus the four document creators) and the rest is
//! the ground it stands on: [`sandbox`] resolves every path and owns the size
//! and output caps, [`command`] validates a command string and [`exec`] spawns
//! the validated one, [`authorize`] applies the role check, [`documents`] maps
//! a creator tool to the file it produces, [`args`] reads arguments with
//! Python's own error text.
//!
//! ## What belongs here
//!
//! * A handler that writes a file, writes the graph, actuates the OS or runs
//!   something — plus its refusals, ported message for message.
//! * A containment rule: a path resolution, a size cap, an output truncation,
//!   an executable allowlist.
//! * A helper the handlers share (argument coercion, `io_error`, `file_size`).
//! * **What a tool *is*, as the model is offered it** — [`catalog`], v12.0.0:
//!   one vocabulary for native tools, the host's MCP surface and installed
//!   skills, plus the port a host injects to provide the latter two. It sits
//!   beside [`host`] because it answers the same question ("what may run, and
//!   who runs it"), one level earlier.
//!
//! ## Where a new tool goes
//!
//! Pick the file by *what it touches* — [`files`] for workspace files,
//! [`vault`] for the brain/Obsidian vault, [`shell`] for subprocesses,
//! [`desktop`] for OS actuation, [`render`] for document creators,
//! [`scaffold`] for project templates, [`local`] for paths outside the
//! workspace — then add the name to [`host::MUTATING_TOOLS`] (or
//! [`host::RENDER_TOOLS`]) and the arm to the dispatcher in [`host`].
//! `is_native` follows from the tables; nothing else needs editing.
//!
//! ## What must never go here
//!
//! * **A permission decision.** By the time a handler runs, the kernel has
//!   already said yes. A tool that re-derives "is this allowed" either
//!   duplicates [`crate::kernel::permission`] or, worse, disagrees with it.
//!   The one check that *is* here is [`authorize::check_role`], because it is
//!   the seam check the kernel never made.
//! * **An HTTP route.** Handlers take a `Map<String, Value>` and return a
//!   value; the route that carried them is [`crate::surface`].
//! * **A read-only compute handler.** Those stay with the Python worker
//!   (`POST /agent/tool`). This group owns *effects*.
//! * **A raw `std::fs` path.** Every workspace path goes through
//!   [`sandbox::Workspace::resolve`], no exceptions — that one rule is what
//!   makes the sandbox a sandbox.
//!
//! ## Invariants
//!
//! 1. **Nothing escapes the workspace.** Absolute paths, `..` segments and
//!    symlink escapes are refused by [`sandbox`], and the loop's pre-write
//!    snapshot, the overwrite guard and the handlers all share that single
//!    resolution rule.
//! 2. **Validate, then spawn — never the reverse.** [`exec::execute`] accepts
//!    only a [`command::Validated`], so there is no path from a raw string to
//!    a subprocess.
//! 3. **The result is the Python result.** Key for key and message for
//!    message with the handler this ports, including the fact that a missing
//!    required argument reads as Python's `KeyError` repr. The transcript,
//!    the critic and the artifact list all parse those keys.
//! 4. **Every write passes [`crate::content::sanitize`] first.** A handler
//!    that writes bytes it validated itself has forked the funnel.

pub mod args;
pub mod authorize;
pub mod catalog;
pub mod command;
pub mod desktop;
pub mod documents;
pub mod exec;
pub mod files;
pub mod host;
pub mod local;
pub mod render;
pub mod sandbox;
pub mod scaffold;
pub mod shell;
pub mod vault;

// The dispatcher's own vocabulary reads as `tools::NativeTools`,
// `tools::ToolHost`, `tools::is_native` everywhere it is used — inside this
// crate and in `lattice-host` / `lattice-platform` — so it keeps that spelling
// while its 600 lines live in their own file.
pub use catalog::{
    ArgKind, ArgSpec, CatalogEntry, EntryKind, ToolCatalog, MCP_PREFIX, SKILL_PREFIX,
};
pub use host::{
    is_native, CallScope, HookSink, NativeCall, NativeTools, ToolConfig, ToolFuture, ToolHost,
    MUTATING_TOOLS, RENDER_TOOLS,
};
