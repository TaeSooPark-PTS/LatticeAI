//! One Door (v11.6.0) Wave 2.5 — the native knowledge-graph **write** engine.
//!
//! This is the keystone of the release's revised standard (plan §설계 결정 2,
//! as revised 2026-08-14): **Rust owns the entire write path.** The Python
//! worker becomes a pure compute worker that owns no state — it returns
//! vectors, parsed structure, extracted entities and rendered bytes, and
//! everything that *lands* lands here.
//!
//! ## What this is a port of
//!
//! `lattice_brain/graph/`'s write side, mirrored row for row:
//!
//! | Python | here |
//! |---|---|
//! | `store.py::_init_db`, `projection/v2_schema.py`, `schema.py::SCHEMA_SQL` | [`schema`] |
//! | `write_master.py` (`_upsert_node`/`_upsert_edge`/`_upsert_chunk`/`_upsert_vector_item`), `projection/v2_schema.py`'s per-row half | [`primitives`] |
//! | `ingest.py` (`ingest_source`/`ingest_document`/`ingest_message`/`ingest_event`/`_attach_source_node`) | [`ingest`] |
//! | `provenance.py` (`record_provenance`/`import_graph_data`/`export_graph_data`) | [`provenance`] |
//! | `projection/curation.py` (`curate`/promotions/`curate_noise`/`mark_superseded`) | [`curation`] |
//! | `retrieval_vector/{indexing,fingerprint}.py` | [`vectors`] |
//! | `documents.py::delete_document_tree`, `write_master.py::set_node_sensitivity`, `discovery.py`'s source writes, `discovery_index/cleanup.py` | [`documents`] |
//! | `curator.py`'s noise-plan half | [`curator`] |
//!
//! ## What this deliberately does **not** do
//!
//! It never calls a model and never parses a document. Chunk boundaries,
//! extracted concepts and triples, semantic (Task/Decision) items, a document's
//! parsed structure and the curator's topic overlay all arrive as data on the
//! request types in [`types`]. Embeddings are the one exception in shape but
//! not in principle: the deterministic hash embedder
//! ([`crate::embeddings::LocalEmbeddingModel`]) is arithmetic this crate
//! already owns bit-for-bit, so it runs inline exactly as Python's does; a
//! provider-backed embedder reaches the store through
//! [`GraphWriter::write_vectors`] instead.
//!
//! ## Concurrency — the invariant
//!
//! Every write goes through the WP-I1 writer pool
//! ([`crate::db::Store::with_write_txn`]), which is `BEGIN IMMEDIATE` on a
//! single write connection. **From Wave 3 on, Rust is the only writer of
//! `knowledge_graph.sqlite`.** The Python `/worker/graph/mutate` seam that
//! carried Wave 2 across the gap was retired with the worker's write door in
//! v11.6.0, and v11.7.0 removed the last source file that still named it, so
//! nothing outside this module may open that file for writing. SQLite has one
//! write lock per database, so a second writer buys nothing but `SQLITE_BUSY`
//! against ourselves — and, worse, a second copy of the rules above.

pub mod clock;
pub mod curator;
pub mod dump;
pub mod pyaux;
pub mod schema;
pub mod taxonomy;
pub mod types;

mod curation;
mod documents;
mod ingest;
mod primitives;
mod provenance;
mod vectors;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::db::{CoreError, Store};
use crate::embeddings::LocalEmbeddingModel;

pub use clock::{Clock, FrozenClock, SystemClock};

/// The write side of the Brain store.
///
/// Cheap to clone in the sense that matters: it holds an `Arc<Store>`, so two
/// writers share one write connection and therefore one write lock.
#[derive(Debug, Clone)]
pub struct GraphWriter {
    store: Arc<Store>,
    blob_dir: PathBuf,
    embedder: LocalEmbeddingModel,
    clock: Arc<dyn Clock>,
    /// Production `open()` stamps `"personal"` when a write has no workspace.
    /// The parity battery uses [`Self::with_parts`] (this stays `None`) so
    /// frozen goldens keep their legacy-null rows.
    default_workspace: Option<String>,
}

impl GraphWriter {
    /// Open a writer over `store`, bootstrapping the schema.
    ///
    /// Mirrors `KnowledgeGraphStore.__init__`: create the blob directory, run
    /// the DDL, stamp the versions. Idempotent against a database Python
    /// already built — nothing is migrated, because there is nothing to
    /// migrate.
    pub fn open(store: Arc<Store>, blob_dir: impl Into<PathBuf>) -> Result<Self, CoreError> {
        let mut writer = Self::with_parts(
            store,
            blob_dir,
            LocalEmbeddingModel::from_env(),
            Arc::new(SystemClock),
        )?;
        writer.default_workspace = Some(crate::read::DEFAULT_WORKSPACE_ID.to_string());
        Ok(writer)
    }

    /// Open a writer with an explicit embedder and clock.
    ///
    /// The parity replay uses this to pin the clock; nothing in the product
    /// does, which is the point of the seam.
    pub fn with_parts(
        store: Arc<Store>,
        blob_dir: impl Into<PathBuf>,
        embedder: LocalEmbeddingModel,
        clock: Arc<dyn Clock>,
    ) -> Result<Self, CoreError> {
        let blob_dir = blob_dir.into();
        std::fs::create_dir_all(&blob_dir).map_err(|err| {
            CoreError::Io(format!(
                "cannot create the blob directory {}: {err}",
                blob_dir.display()
            ))
        })?;
        let writer = Self {
            store,
            blob_dir,
            embedder,
            clock,
            default_workspace: None,
        };
        writer.bootstrap()?;
        Ok(writer)
    }

    /// Run the schema bootstrap on its own transaction.
    pub fn bootstrap(&self) -> Result<(), CoreError> {
        let now = self.clock.now_iso();
        self.store
            .with_write_txn(|txn| schema::bootstrap(txn, &now))
    }

    /// The store this writer writes through.
    pub fn store(&self) -> &Arc<Store> {
        &self.store
    }

    /// Where document blob sidecars are kept (`knowledge_graph_blobs/`).
    pub fn blob_dir(&self) -> &Path {
        &self.blob_dir
    }

    /// The embedder whose fingerprint the index carries.
    pub fn embedder(&self) -> &LocalEmbeddingModel {
        &self.embedder
    }

    /// The clock every stamp in the store comes from.
    pub fn clock(&self) -> &Arc<dyn Clock> {
        &self.clock
    }

    /// Fill in `"personal"` on the production writer when the request omitted a
    /// workspace. The parity constructor leaves this `None`, so goldens stay
    /// legacy-null.
    pub(crate) fn resolve_workspace(&self, requested: Option<&str>) -> Option<String> {
        requested
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .or_else(|| self.default_workspace.clone())
    }
}
