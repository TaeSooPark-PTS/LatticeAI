pub mod chunk_parents;
pub mod clock;
pub mod curator;
pub mod dump;
pub mod frontmatter;
pub mod identity;
pub mod lifecycle;
pub mod pyaux;
pub mod schema;
pub mod share_import;
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

use std::fmt;
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
pub struct BrainIngestEvent {
    pub node_id: String,
    pub title: String,
    pub source_type: String,
    pub workspace_id: String,
    pub owner: Option<String>,
    pub duplicate: bool,
}

pub type IngestListener = Arc<dyn Fn(&BrainIngestEvent) + Send + Sync>;

#[derive(Clone)]
struct ListenerHandle(IngestListener);

impl fmt::Debug for ListenerHandle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("IngestListener(..)")
    }
}

#[derive(Debug, Clone)]
pub struct GraphWriter {
    store: Arc<Store>,
    blob_dir: PathBuf,
    embedder: LocalEmbeddingModel,
    clock: Arc<dyn Clock>,
    default_workspace: Option<String>,
    persist_bm25_sidecar: bool,
    ingest_listener: Option<ListenerHandle>,
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
            ingest_listener: None,
        };
        writer.bootstrap()?;
        Ok(writer)
    }

    pub fn with_ingest_listener(mut self, listener: IngestListener) -> Self {
        self.ingest_listener = Some(ListenerHandle(listener));
        self
    }

    pub(crate) fn notify_ingest(&self, event: BrainIngestEvent) {
        if let Some(listener) = &self.ingest_listener {
            (listener.0)(&event);
        }
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
