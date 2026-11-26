pub mod chunk_parents;
pub mod clock;
pub mod curator;
pub mod dump;
pub mod lifecycle;
pub mod pyaux;
pub mod schema;
pub mod taxonomy;
pub mod types;

mod curation;
mod documents;
mod ingest;
mod primitives;
mod projection_v2;
mod provenance;
mod revision;
mod vectors;
mod vectors_contract;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::db::{CoreError, Store};
use crate::embeddings::LocalEmbeddingModel;

pub use clock::{Clock, FrozenClock, SystemClock};
pub use lifecycle::{
    read_lifecycle_from_metadata, KnowledgeLifecycleMetadata, KnowledgeLifecycleState,
    KnowledgeLifecycleUpdate, LifecycleHistoryEntry,
};
pub use revision::{RevisionOutcome, RevisionReplace};
pub use types::IngestStatus;

#[derive(Debug, Clone)]
pub struct GraphWriter {
    store: Arc<Store>,
    blob_dir: PathBuf,
    embedder: LocalEmbeddingModel,
    clock: Arc<dyn Clock>,
    default_workspace: Option<String>,
    persist_bm25_sidecar: bool,
}

impl GraphWriter {
    pub fn open(store: Arc<Store>, blob_dir: impl Into<PathBuf>) -> Result<Self, CoreError> {
        let mut writer = Self::with_parts(
            store,
            blob_dir,
            LocalEmbeddingModel::from_env(),
            Arc::new(SystemClock),
        )?;
        writer.default_workspace = Some(crate::read::DEFAULT_WORKSPACE_ID.to_string());
        writer.persist_bm25_sidecar = true;
        writer.ensure_bm25_sidecar_schema()?;
        Ok(writer)
    }

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
            persist_bm25_sidecar: false,
        };
        writer.bootstrap()?;
        Ok(writer)
    }

    pub fn bootstrap(&self) -> Result<(), CoreError> {
        let now = self.clock.now_iso();
        self.store
            .with_write_txn(|txn| schema::bootstrap(txn, &now))
    }

    pub fn ensure_bm25_sidecar_schema(&self) -> Result<(), CoreError> {
        self.store
            .with_write_txn(|txn| crate::bm25_stats::ensure_bm25_node_stats_schema(txn))
    }

    pub(crate) fn should_persist_bm25_sidecar(&self, txn: &rusqlite::Transaction<'_>) -> bool {
        self.persist_bm25_sidecar || crate::bm25_stats::bm25_node_stats_exists(txn)
    }

    pub fn store(&self) -> &Arc<Store> {
        &self.store
    }

    pub fn blob_dir(&self) -> &Path {
        &self.blob_dir
    }

    pub fn embedder(&self) -> &LocalEmbeddingModel {
        &self.embedder
    }

    pub fn clock(&self) -> &Arc<dyn Clock> {
        &self.clock
    }

    pub(crate) fn resolve_workspace(&self, requested: Option<&str>) -> Option<String> {
        requested
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .or_else(|| self.default_workspace.clone())
    }
}
