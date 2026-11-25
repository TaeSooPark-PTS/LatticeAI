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
pub mod safe_io;
pub mod sandbox;
pub mod scaffold;
pub mod shell;
pub mod vault;
pub mod web;

pub use catalog::{
    ArgKind, ArgSpec, CatalogEntry, EntryKind, ToolCatalog, MCP_PREFIX, SKILL_PREFIX,
};
pub use host::{
    is_native, CallScope, HookSink, NativeCall, NativeTools, ToolConfig, ToolFuture, ToolHost,
    MUTATING_TOOLS, RENDER_TOOLS, WEB_AND_INSPECT_TOOLS,
};
