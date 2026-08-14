//! `lattice-ingest` — watch, parse, chunk and hash, natively (v11.5.0 §2c).
//!
//! Phase 2c of `docs/v11.5.0_RUST_COMPLETE_PLAN.md`. Everything here is a 1:1
//! port of a named Python original, and where the two runtimes could
//! legitimately differ the Python behaviour is the contract:
//!
//! * [`chunk`] ports `lattice_brain/graph/_kg_common/text.py` — the four
//!   chunking strategies, their boundary arithmetic and their per-chunk
//!   provenance. **Every offset is a character offset**, because Python slices
//!   `str` by code points; a byte-sliced port would disagree on the first
//!   Korean sentence and panic on the first emoji.
//! * [`strategy`] ports `chunk_strategy_for` — filename/MIME → strategy, never
//!   raising, falling back to `plain`.
//! * [`hashes`] ports the chunk-id and content-hash conventions from
//!   `lattice_brain/graph/ingest.py` and `_kg_fsutil.py`.
//! * [`pages`] ports the PDF page-offset arithmetic and re-exports the
//!   citation locator lattice-core already owns.
//! * [`filters`] ports the folder-ingest filter chain from
//!   `lattice_brain/ingestion/folders.py` plus `.latticeignore` matching.
//! * [`pystr`] holds the handful of `str` behaviours the rest leans on
//!   (Python's whitespace set, `strip`, `Path.suffix`, `errors="ignore"`).
//! * [`watch`] ports the **polling** mtime-snapshot watcher from
//!   `latticeai/services/folder_watch.py` — not an OS watcher, because polling
//!   is what the product actually does and what a test can pin.
//!
//! ## What this crate deliberately does not do
//!
//! It never writes the knowledge graph. Detection, parsing, chunking and
//! duplicate judgement happen here; the graph write stays with the Python
//! worker, which is the single writer. [`worker::WorkerClient`] is the whole
//! delegation seam, and the HTTP surface in [`api`] is dry-run only.
//!
//! The parity proof lives in `tests/chunking_parity.rs`, against the goldens
//! `scripts/generate_chunking_parity_fixtures.py` writes and
//! `tests/unit/test_chunking_parity_contract.py` re-asserts from the Python side.

// The HTTP modules answer `Result<T, axum::response::Response>`: the error is
// the rendered refusal, as in `lattice-auth`. Allowed once at the root.
#![allow(clippy::result_large_err)]

pub mod api;
pub mod browser_api;
pub mod chunk;
pub mod filters;
pub mod hashes;
pub mod local_files_api;
pub mod pages;
pub mod pystr;
pub mod strategy;
pub mod watch;
pub mod worker;

pub use api::{router, IngestApiConfig, CHUNK_PATH, PLAN_PATH};
pub use chunk::{
    chunk_meta_fields, typed_chunks, Chunk, ChunkMeta, DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE,
};
pub use hashes::{
    chunk_id, content_id, file_content_hash, identity_hash, sha256_bytes, sha256_text,
    text_content_hash, vector_text_hash,
};
pub use pages::{citation_locator, page_for_offset, pdf_page_offsets};
pub use pystr::{is_py_space, py_strip, py_suffix};
pub use strategy::chunk_strategy_for;
pub use watch::{ScanDiff, WatchConfig, WatchScanner, MAX_FILES_PER_SCAN};
pub use worker::{NoteSubmission, WorkerClient, WorkerError};
