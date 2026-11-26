#![allow(clippy::result_large_err)]

pub mod api;
pub mod browser_api;
pub mod chunk;
pub mod chunk_pieces;
pub mod document_structure;
mod document_structure_parsers;
pub mod filters;
pub mod fingerprint;
pub mod hashes;
pub mod incremental_sync;
pub mod incremental_watch;
pub mod local_files_api;
pub mod pages;
pub mod pystr;
pub mod sections;
pub mod strategy;
pub mod watch;
pub mod worker;

pub use api::{router, IngestApiConfig, CHUNK_PATH, PLAN_PATH};
pub use chunk::{
    chunk_meta_fields, typed_chunks, Chunk, ChunkMeta, DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE,
};
pub use chunk_pieces::{chunk_set_for, chunk_set_with, embedding_inputs, ChunkSet};
pub use document_structure::{
    extract_document_structure, persist_structure_and_multimodal_overlay, DocumentStructurePlan,
    OcrVisionObservation, OcrVisionProvider,
};
pub use hashes::{
    chunk_id, content_id, file_content_hash, identity_hash, sha256_bytes, sha256_text,
    text_content_hash, vector_text_hash,
};
pub use incremental_sync::{
    reconcile_deleted_uris, sync_document_incremental, IncrementalSyncReceipt,
    IncrementalSyncRequest,
};
pub use incremental_watch::{
    DebouncedWatchConfig, DebouncedWatchEngine, DetectedMove, RawFsChange, WatchBatch,
};
pub use pages::{citation_locator, page_for_offset, pdf_page_offsets};
pub use pystr::{is_py_space, py_strip, py_suffix};
pub use sections::{build_overlay, section_id, SectionOverlay};
pub use strategy::chunk_strategy_for;
pub use watch::{ScanDiff, WatchConfig, WatchScanner, MAX_FILES_PER_SCAN};
pub use worker::{NoteIngestError, NoteIngestor, NoteReceipt, NoteSubmission, INGEST_INFLIGHT};
