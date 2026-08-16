//! `latticeai/api/evidence_actions.py` — citations → ready-to-send prompts.
//!
//! Read-only. Node lookup is `kg::get_node`; nothing is written.

pub mod json;
pub mod routes;
pub mod service;

pub use routes::{router, MOUNTED};
