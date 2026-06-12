from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401
from .documents import KnowledgeGraphDocumentsMixin
from .discovery import KnowledgeGraphDiscoveryMixin
from .ingest import KnowledgeGraphIngestMixin
from .projection import KnowledgeGraphProjectionMixin
from .provenance import KnowledgeGraphProvenanceMixin
from .retrieval import KnowledgeGraphRetrievalMixin
from .write_master import KnowledgeGraphWriteMixin


class KnowledgeGraphStore(
    KnowledgeGraphProjectionMixin,
    KnowledgeGraphWriteMixin,
    KnowledgeGraphDiscoveryMixin,
    KnowledgeGraphIngestMixin,
    KnowledgeGraphProvenanceMixin,
    KnowledgeGraphDocumentsMixin,
    KnowledgeGraphRetrievalMixin,
):
    def __init__(self, db_path: Path, blob_dir: Path, embedder: Any = None):
        self.db_path = Path(db_path)
        self.blob_dir = Path(blob_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        # The embedder is swappable behind a fixed interface
        # (model_id/dim/embed/encode/decode/similarity). Defaults to the
        # deterministic, offline hash model so the store works with no config;
        # server_app injects a provider-backed embedder from Config.
        self._embedding_model = (
            embedder if embedder is not None else LocalEmbeddingModel()
        )
        self._init_db()
        # Read graph queries from the v2 projection (kgv2_* views) when available.
        # Toggle off (e.g. in tests) to compare against the legacy tables.
        self._read_from_v2 = KGStoreV2 is not None and _READ_FROM_V2_DEFAULT

    def _read_tables(self) -> tuple:
        """Return (nodes_table, edges_table) for read queries.

        Same read code runs against the legacy tables or the v2 reconstruction
        views, so the two paths are equivalent by construction.
        """
        if self._read_from_v2:
            return ("kgv2_nodes", "kgv2_edges")
        return ("nodes", "edges")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            db_format = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if db_format > _KG_DB_FORMAT_VERSION:
                raise RuntimeError(
                    f"Knowledge Graph DB format {db_format} is newer than this build "
                    f"({_KG_DB_FORMAT_VERSION}); restore a pre-upgrade backup or upgrade Lattice AI."
                )
            conn.executescript(
                """
                    CREATE TABLE IF NOT EXISTS graph_meta (
                      key TEXT PRIMARY KEY,
                      value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS nodes (
                      id TEXT PRIMARY KEY,
                      type TEXT NOT NULL,
                      title TEXT NOT NULL,
                      summary TEXT,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                      raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS edges (
                      id TEXT PRIMARY KEY,
                      from_node TEXT NOT NULL,
                      to_node TEXT NOT NULL,
                      type TEXT NOT NULL,
                      weight REAL NOT NULL DEFAULT 1.0,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                      created_at TEXT NOT NULL,
                      UNIQUE(from_node, to_node, type),
                      FOREIGN KEY(from_node) REFERENCES nodes(id) ON DELETE CASCADE,
                      FOREIGN KEY(to_node) REFERENCES nodes(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS chunks (
                      id TEXT PRIMARY KEY,
                      source_node TEXT NOT NULL,
                      text TEXT NOT NULL,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                      created_at TEXT NOT NULL,
                      FOREIGN KEY(source_node) REFERENCES nodes(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS knowledge_sources (
                      id TEXT PRIMARY KEY,
                      root_path TEXT NOT NULL UNIQUE,
                      os_type TEXT NOT NULL,
                      drive_id TEXT,
                      label TEXT,
                      status TEXT NOT NULL,
                      include_ocr INTEGER NOT NULL DEFAULT 0,
                      watch_enabled INTEGER NOT NULL DEFAULT 0,
                      consent_json TEXT NOT NULL CHECK (json_valid(consent_json)),
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      last_scanned_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS local_file_index (
                      id TEXT PRIMARY KEY,
                      source_id TEXT NOT NULL,
                      os_type TEXT NOT NULL,
                      drive_id TEXT,
                      root_path TEXT NOT NULL,
                      file_path TEXT NOT NULL,
                      relative_path TEXT NOT NULL,
                      file_name TEXT NOT NULL,
                      extension TEXT NOT NULL,
                      size_bytes INTEGER,
                      modified_at TEXT,
                      sha256 TEXT,
                      last_scanned_at TEXT,
                      last_indexed_at TEXT,
                      parser_type TEXT,
                      status TEXT NOT NULL,
                      error_message TEXT,
                      graph_node_id TEXT,
                      deleted INTEGER NOT NULL DEFAULT 0,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                      UNIQUE(source_id, relative_path),
                      FOREIGN KEY(source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS vector_embeddings (
                      item_id TEXT PRIMARY KEY,
                      item_type TEXT NOT NULL,
                      source_node TEXT NOT NULL,
                      text_hash TEXT NOT NULL,
                      embedding BLOB NOT NULL,
                      embedding_dim INTEGER NOT NULL,
                      embedding_model TEXT NOT NULL,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                      indexed_at TEXT NOT NULL,
                      FOREIGN KEY(source_node) REFERENCES nodes(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS vector_index_operations (
                      id TEXT PRIMARY KEY,
                      operation TEXT NOT NULL,
                      status TEXT NOT NULL,
                      requested_at TEXT NOT NULL,
                      started_at TEXT,
                      completed_at TEXT,
                      items_total INTEGER NOT NULL DEFAULT 0,
                      items_indexed INTEGER NOT NULL DEFAULT 0,
                      items_skipped INTEGER NOT NULL DEFAULT 0,
                      error_message TEXT,
                      metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json))
                    );
                    -- v3.6.0 Knowledge Graph First: per-ingestion provenance trail.
                    -- Append-only audit of where every graph node came from, when it
                    -- was captured, how it was processed, and whether it was embedded /
                    -- linked / used by an agent. get_provenance() returns the latest row.
                    CREATE TABLE IF NOT EXISTS ingestion_provenance (
                      id TEXT PRIMARY KEY,
                      node_id TEXT NOT NULL,
                      source_type TEXT NOT NULL,
                      source_uri TEXT,
                      content_hash TEXT,
                      title TEXT,
                      pipeline TEXT NOT NULL,
                      owner TEXT,
                      workspace_id TEXT,
                      captured_at TEXT,
                      modified_at TEXT,
                      embedded INTEGER NOT NULL DEFAULT 0,
                      linked INTEGER NOT NULL DEFAULT 0,
                      duplicate INTEGER NOT NULL DEFAULT 0,
                      agent_used TEXT,
                      chunk_count INTEGER NOT NULL DEFAULT 0,
                      permissions_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(permissions_json)),
                      metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
                      created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
                    CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);
                    CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node);
                    CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_node);
                    CREATE INDEX IF NOT EXISTS idx_knowledge_sources_root ON knowledge_sources(root_path);
                    CREATE INDEX IF NOT EXISTS idx_local_file_index_source ON local_file_index(source_id);
                    CREATE INDEX IF NOT EXISTS idx_local_file_index_status ON local_file_index(status);
                    CREATE INDEX IF NOT EXISTS idx_local_file_index_graph_node ON local_file_index(graph_node_id);
                    CREATE INDEX IF NOT EXISTS idx_vector_embeddings_type ON vector_embeddings(item_type);
                    CREATE INDEX IF NOT EXISTS idx_vector_embeddings_source ON vector_embeddings(source_node);
                    CREATE INDEX IF NOT EXISTS idx_vector_embeddings_model ON vector_embeddings(embedding_model);
                    CREATE INDEX IF NOT EXISTS idx_vector_index_operations_requested ON vector_index_operations(requested_at);
                    CREATE INDEX IF NOT EXISTS idx_provenance_node ON ingestion_provenance(node_id);
                    CREATE INDEX IF NOT EXISTS idx_provenance_source_type ON ingestion_provenance(source_type);
                    CREATE INDEX IF NOT EXISTS idx_provenance_hash ON ingestion_provenance(content_hash);
                    CREATE INDEX IF NOT EXISTS idx_provenance_created ON ingestion_provenance(created_at);
                    """
            )
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(GRAPH_SCHEMA_VERSION)),
            )
        self._init_v2_schema()
        self._init_fts()


__all__ = ["KnowledgeGraphStore"]
