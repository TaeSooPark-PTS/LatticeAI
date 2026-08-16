//! `latticeai/api/garden.py` — the P-Reinforce markdown vault.
//!
//! Two routes. The vault is user-owned filesystem state (not a worker table),
//! so notes are written natively. Graph ingest is `GraphWriter::ingest_content`
//! plus the W5 `/worker/extract` + `/worker/embed` enrichment chain.

pub mod process;
pub mod vault;

pub use process::{router, MOUNTED};
