#![allow(clippy::result_large_err)]

pub mod adminops;
pub mod governance;
pub(crate) mod http_reply;
pub mod knowledge;
pub mod modelops;
pub mod shell;
pub mod toolsurface;
pub mod workspaceos;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
