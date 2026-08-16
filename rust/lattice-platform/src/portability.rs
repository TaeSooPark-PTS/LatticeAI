//! Knowledge-graph portability — native port of `latticeai/api/portability.py`.
//!
//! File operations (list / download / validate / backup ZIP / dry-run
//! inspect) are native. So is the import write: `import_graph_data` runs on
//! [`lattice_core::graph_write::GraphWriter`]. Encrypted-archive
//! success with a live passphrase is a documented gap (nonce bytes).

use std::path::PathBuf;
use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;
use lattice_auth::AuthState;
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;

use crate::network::DeviceIdentity;

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/brain/storage"),
    ("POST", "/api/brain/storage/migrate-postgres"),
    ("POST", "/api/brain/storage/postgres/docker"),
    ("POST", "/api/knowledge-graph/archive"),
    ("POST", "/api/knowledge-graph/archive/import"),
    ("POST", "/api/knowledge-graph/archive/inspect"),
    ("POST", "/api/knowledge-graph/archive/restore"),
    ("POST", "/api/knowledge-graph/archive/verify"),
    ("POST", "/api/knowledge-graph/backup"),
    ("GET", "/api/knowledge-graph/backup-health"),
    ("POST", "/api/knowledge-graph/export"),
    ("POST", "/api/knowledge-graph/export-file"),
    ("POST", "/api/knowledge-graph/import"),
    ("GET", "/api/knowledge-graph/portability"),
    ("GET", "/api/knowledge-graph/provenance"),
    ("POST", "/api/knowledge-graph/restore"),
    ("GET", "/api/knowledge-graph/share"),
    ("POST", "/api/knowledge-graph/share/archive"),
    ("POST", "/api/knowledge-graph/share/export"),
    ("POST", "/api/knowledge-graph/share/import"),
    (
        "POST",
        "/api/knowledge-graph/share/proposals/:item_id/accept",
    ),
    ("GET", "/api/knowledge-graph/share/recipient-key"),
];

pub(crate) const FORMAT: &str = "latticeai.kg.export";
pub(crate) const FORMAT_VERSION: u64 = 1;
pub(crate) const GRAPH_SCHEMA_VERSION: u64 = 1;
pub(crate) const DB_FORMAT_VERSION: u64 = 4;
pub(crate) const KG_V2_SCHEMA_VERSION: u64 = 2;
pub(crate) const PROJECTION_VERSION: u64 = 4;
pub(crate) const BRAIN_NETWORK_ENV: &str = "LATTICEAI_BRAIN_NETWORK";
pub(crate) const SUBGRAPH_FORMAT: &str = "latticeai.kg.subgraph";
pub(crate) const SEALED_BOX_ALGORITHM: &str = "x25519-hkdf-sha256-aes256gcm";
pub(crate) const BRAIN_NETWORK_DISABLED_EN: &str = "Brain Network sharing is off. It is opt-in by design: set LATTICEAI_BRAIN_NETWORK=1 to enable selective subgraph export and receipt.";

/// Router state.
#[derive(Clone)]
pub struct PortabilityState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
    pub identity: Arc<DeviceIdentity>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
}

impl PortabilityState {
    pub fn new(auth: Arc<AuthState>, config: RuntimeConfig) -> Self {
        let identity = Arc::new(DeviceIdentity::load_or_create(
            &config.state_file(state_files::DEVICE_IDENTITY),
        ));
        Self {
            auth,
            config: Arc::new(config),
            identity,
            graph: None,
        }
    }

    fn exports_dir(&self) -> PathBuf {
        self.config.state_file(state_files::WORKSPACE_EXPORTS)
    }

    fn graph_available(&self) -> bool {
        std::env::var("LATTICEAI_ENABLE_GRAPH")
            .map(|v| v != "0" && !v.is_empty())
            .unwrap_or(true)
    }
}

mod archive;
mod graph;
mod postgres;
mod status;

use archive::{
    archive_import, archive_inspect, archive_restore, archive_verify, encrypted_archive,
    share_accept, share_archive, share_export, share_import, share_recipient_key, share_status,
};
use graph::{backup_graph, export_graph, export_graph_file, import_graph, restore_graph};
use postgres::{migrate_postgres, postgres_docker};
use status::{backup_health, brain_storage, portability_status, provenance};
pub(crate) use status::{backup_health_payload, postgres_capabilities, sqlite_capabilities};

/// Build the portability router.
pub fn router(state: PortabilityState) -> Router {
    Router::new()
        .route("/api/knowledge-graph/portability", get(portability_status))
        .route("/api/brain/storage", get(brain_storage))
        .route("/api/knowledge-graph/backup-health", get(backup_health))
        .route("/api/knowledge-graph/provenance", get(provenance))
        .route("/api/knowledge-graph/export", post(export_graph))
        .route("/api/knowledge-graph/export-file", post(export_graph_file))
        .route("/api/knowledge-graph/import", post(import_graph))
        .route("/api/knowledge-graph/backup", post(backup_graph))
        .route("/api/knowledge-graph/restore", post(restore_graph))
        .route("/api/knowledge-graph/archive", post(encrypted_archive))
        .route(
            "/api/knowledge-graph/archive/inspect",
            post(archive_inspect),
        )
        .route("/api/knowledge-graph/archive/verify", post(archive_verify))
        .route("/api/knowledge-graph/archive/import", post(archive_import))
        .route(
            "/api/knowledge-graph/archive/restore",
            post(archive_restore),
        )
        .route("/api/knowledge-graph/share", get(share_status))
        .route("/api/knowledge-graph/share/export", post(share_export))
        .route(
            "/api/knowledge-graph/share/recipient-key",
            get(share_recipient_key),
        )
        .route("/api/knowledge-graph/share/archive", post(share_archive))
        .route("/api/knowledge-graph/share/import", post(share_import))
        .route(
            "/api/knowledge-graph/share/proposals/:item_id/accept",
            post(share_accept),
        )
        .route("/api/brain/storage/postgres/docker", post(postgres_docker))
        .route(
            "/api/brain/storage/migrate-postgres",
            post(migrate_postgres),
        )
        .with_state(state)
}
