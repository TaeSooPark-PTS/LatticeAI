//! Schema bootstrap — the DDL `KnowledgeGraphStore.__init__` emits, verbatim.
//!
//! Three sources, run in Python's order:
//!
//! 1. `graph/store.py::_init_db` — the legacy tables + indexes, then the
//!    `graph_meta.schema_version` stamp;
//! 2. `graph/projection/v2_schema.py::_init_v2_schema` — drop the `kgv2_*`
//!    views, drop a stale projection, `schema.py::SCHEMA_SQL`, recreate the
//!    views, backfill, normalize `legacy_type`, stamp four `kg_meta` keys and
//!    `PRAGMA user_version`;
//! 3. `graph/projection/v2_schema.py::_init_fts` — the trigram FTS index, its
//!    three triggers on `nodes`, and a one-time backfill.
//!
//! The DDL text is copied character for character rather than rewritten. That
//! is the point: an existing Python-created database must open and be written
//! with **no migration**, and a fresh Rust-only install must produce a schema a
//! Python build would accept. `CREATE TABLE IF NOT EXISTS` only agrees on the
//! first of those if the two texts agree.
//!
//! One thing deliberately **not** created: `storage_meta`. It belongs to
//! `SQLiteEngine.initialize()`, which `KnowledgeGraphStore` never calls — a
//! real Brain built by Python has no such table, and inventing one here would
//! be the port adding state the original does not have.

use rusqlite::{Connection, Transaction};

use crate::db::CoreError;

/// `_kg_constants._KG_DB_FORMAT_VERSION`.
pub const KG_DB_FORMAT_VERSION: i64 = 4;
/// `_kg_constants._KG_DB_FORMAT_KEY`.
pub const KG_DB_FORMAT_KEY: &str = "db_format_version";
/// `_kg_constants._V2_WRITE_MASTER_KEY`.
pub const V2_WRITE_MASTER_KEY: &str = "v2_write_mastered_at";
/// `_kg_constants._PROJECTION_VERSION`.
pub const PROJECTION_VERSION: i64 = 4;
/// `_kg_constants.GRAPH_SCHEMA_VERSION`.
pub const GRAPH_SCHEMA_VERSION: i64 = 1;
/// `schema.KG_SCHEMA_V2_VERSION`.
pub const KG_SCHEMA_V2_VERSION: i64 = 2;
/// `schema.EMBED_DIM`'s environment variable.
pub const EMBED_DIM_ENV: &str = "LATTICEAI_EMBED_DIM";
/// `schema.EMBED_DIM`'s default.
pub const DEFAULT_EMBED_DIM: i64 = 1024;

/// `schema.EMBED_DIM` — `int(os.getenv("LATTICEAI_EMBED_DIM", "1024"))`.
///
/// Stated deviation: Python raises at import on a malformed value and takes the
/// process with it. A library cannot do that to its host, so an unparseable
/// value falls back to the documented default.
pub fn embed_dim() -> i64 {
    std::env::var(EMBED_DIM_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<i64>().ok())
        .unwrap_or(DEFAULT_EMBED_DIM)
}

/// `store.py::_init_db`'s script.
const LEGACY_SCHEMA_SQL: &str = r#"
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
                    CREATE INDEX IF NOT EXISTS idx_vector_embeddings_model_dim_indexed
                      ON vector_embeddings(embedding_model, embedding_dim, indexed_at);
                    CREATE INDEX IF NOT EXISTS idx_vector_index_operations_requested ON vector_index_operations(requested_at);
                    CREATE INDEX IF NOT EXISTS idx_provenance_node ON ingestion_provenance(node_id);
                    CREATE INDEX IF NOT EXISTS idx_provenance_source_type ON ingestion_provenance(source_type);
                    CREATE INDEX IF NOT EXISTS idx_provenance_hash ON ingestion_provenance(content_hash);
                    CREATE INDEX IF NOT EXISTS idx_provenance_created ON ingestion_provenance(created_at);
                    "#;

/// `schema.SCHEMA_SQL` — the normalized v2 tables.
const V2_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS kg_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes_v2 (
  id               TEXT PRIMARY KEY,
  type             TEXT NOT NULL,
  legacy_type      TEXT,
  label            TEXT NOT NULL,
  summary          TEXT,
  attrs            TEXT NOT NULL DEFAULT '{}',
  embedding        BLOB,
  owner_id         TEXT,
  -- NULL workspace_id = legacy-global (pre-scoping rows, readable machine-wide).
  workspace_id     TEXT,
  -- 'legacy' marks rows that predate scoping — the 'private' default must not
  -- silently privatize previously machine-shared data (design-review ruling).
  visibility       TEXT NOT NULL DEFAULT 'private',
  -- Revision chain: a node replaced by a newer one points at its successor.
  superseded_by    TEXT,
  -- Bitemporal validity (v11.1.0). NULL is the convention, not '':
  --   valid_from IS NULL  → the row has been valid since created_at
  --   valid_to   IS NULL  → the row is still valid now
  -- An empty string would read as "valid since the beginning of time" in a
  -- string comparison, which is why the columns are nullable with no default.
  valid_from       TEXT,
  valid_to         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  style            TEXT,
  tone             TEXT,
  importance_score REAL NOT NULL DEFAULT 0.0,
  last_used        TEXT
);

CREATE TABLE IF NOT EXISTS edges_v2 (
  id           TEXT PRIMARY KEY,
  source       TEXT NOT NULL,
  target       TEXT NOT NULL,
  type         TEXT NOT NULL,
  legacy_type  TEXT NOT NULL DEFAULT '',
  weight       REAL NOT NULL DEFAULT 1.0,
  confidence   REAL NOT NULL DEFAULT 1.0,
  evidence     TEXT NOT NULL DEFAULT '[]',
  metadata     TEXT NOT NULL DEFAULT '{}',
  created_by   TEXT NOT NULL DEFAULT 'user',
  created_at   TEXT NOT NULL,
  -- Bitemporal validity + revision chain (v11.1.0), same NULL convention as
  -- nodes_v2: NULL valid_from falls back to created_at, NULL valid_to means
  -- "still holds". Relationships go stale exactly like facts do.
  valid_from   TEXT,
  valid_to     TEXT,
  superseded_by TEXT,
  -- Edge identity (v4): the normalized type AND the raw legacy type.
  -- Migrated rows keep their legacy_type discriminator, so two distinct
  -- legacy strings between one pair (e.g. "mentions" / "관련됨") stay
  -- distinct even though both normalize to MENTIONS. Native canonical
  -- writes carry legacy_type='' so their identity is effectively
  -- (source, target, type) — two canonical types between the same pair
  -- (e.g. MENTIONS + CONTAINS) never collide. The pre-v4
  -- UNIQUE(source, target, legacy_type) would have silently merged them.
  UNIQUE(source, target, type, legacy_type),
  FOREIGN KEY(source) REFERENCES nodes_v2(id) ON DELETE CASCADE,
  FOREIGN KEY(target) REFERENCES nodes_v2(id) ON DELETE CASCADE
);

-- Temporal dimension (v4): every repeated observation of a relationship is
-- recorded — edges_v2's UNIQUE identity + weight=max would otherwise erase
-- when something was learned, how often, and whether it still holds.
CREATE TABLE IF NOT EXISTS edge_occurrences (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_id     TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  weight      REAL NOT NULL DEFAULT 1.0,
  source      TEXT,
  FOREIGN KEY(edge_id) REFERENCES edges_v2(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_edge ON edge_occurrences(edge_id);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_time ON edge_occurrences(observed_at);

CREATE INDEX IF NOT EXISTS idx_nodes_v2_type     ON nodes_v2(type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_legacy   ON nodes_v2(legacy_type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_owner    ON nodes_v2(owner_id);
CREATE INDEX IF NOT EXISTS idx_edges_v2_source   ON edges_v2(source);
CREATE INDEX IF NOT EXISTS idx_edges_v2_target   ON edges_v2(target);
CREATE INDEX IF NOT EXISTS idx_edges_v2_type     ON edges_v2(type);
CREATE INDEX IF NOT EXISTS idx_edges_v2_legacy   ON edges_v2(legacy_type);
"#;

/// `v2_schema._V2_VIEWS_SQL` — the reconstruction views.
const V2_VIEWS_SQL: &str = r#"
        CREATE VIEW IF NOT EXISTS kgv2_nodes AS
          SELECT id,
                 COALESCE(NULLIF(legacy_type, ''), type) AS type,
                 label AS title,
                 summary,
                 attrs AS metadata_json,
                 created_at, updated_at,
                 valid_from, valid_to, superseded_by
          FROM nodes_v2;
        CREATE VIEW IF NOT EXISTS kgv2_edges AS
          SELECT id, source AS from_node, target AS to_node,
                 COALESCE(NULLIF(legacy_type, ''), type) AS type,
                 weight,
                 metadata AS metadata_json,
                 created_at,
                 valid_from, valid_to, superseded_by
          FROM edges_v2;
        "#;

/// `v2_schema._FTS_SQL` — the trigram index and the three triggers on `nodes`.
const FTS_SQL: &str = r#"
        CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
          node_id UNINDEXED, title, summary, metadata, tokenize='trigram'
        );
        CREATE TRIGGER IF NOT EXISTS node_fts_ai AFTER INSERT ON nodes BEGIN
          INSERT INTO node_fts(node_id, title, summary, metadata)
          VALUES (new.id, new.title, COALESCE(new.summary, ''), new.metadata_json);
        END;
        CREATE TRIGGER IF NOT EXISTS node_fts_au AFTER UPDATE ON nodes BEGIN
          DELETE FROM node_fts WHERE node_id = old.id;
          INSERT INTO node_fts(node_id, title, summary, metadata)
          VALUES (new.id, new.title, COALESCE(new.summary, ''), new.metadata_json);
        END;
        CREATE TRIGGER IF NOT EXISTS node_fts_ad AFTER DELETE ON nodes BEGIN
          DELETE FROM node_fts WHERE node_id = old.id;
        END;
        "#;

/// The columns `KGStoreV2._V2_EXPECTED_COLUMNS` says each v2 table must have.
const EXPECTED_NODES_V2: [&str; 19] = [
    "id",
    "type",
    "legacy_type",
    "label",
    "summary",
    "attrs",
    "embedding",
    "owner_id",
    "workspace_id",
    "visibility",
    "superseded_by",
    "valid_from",
    "valid_to",
    "created_at",
    "updated_at",
    "style",
    "tone",
    "importance_score",
    "last_used",
];
const EXPECTED_EDGES_V2: [&str; 14] = [
    "id",
    "source",
    "target",
    "type",
    "legacy_type",
    "weight",
    "confidence",
    "evidence",
    "metadata",
    "created_by",
    "created_at",
    "valid_from",
    "valid_to",
    "superseded_by",
];

/// `KGStoreV2._V2_ADDABLE_COLUMNS` — columns that heal in place.
const ADDABLE_NODES_V2: [(&str, &str); 4] = [
    ("superseded_by", "TEXT"),
    ("valid_from", "TEXT"),
    ("valid_to", "TEXT"),
    ("workspace_id", "TEXT"),
];
const ADDABLE_EDGES_V2: [(&str, &str); 3] = [
    ("superseded_by", "TEXT"),
    ("valid_from", "TEXT"),
    ("valid_to", "TEXT"),
];

/// Bring `conn` up to the current schema, exactly as Python's constructor does.
///
/// Idempotent, and a no-op against a database Python already initialised: the
/// projection version and the `db_format_version` stamp are already current, so
/// nothing is dropped and nothing is rebuilt.
pub fn bootstrap(txn: &Transaction<'_>, now: &str) -> Result<(), CoreError> {
    guard_db_format(txn)?;
    txn.execute_batch(LEGACY_SCHEMA_SQL)?;
    txn.execute(
        "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
        rusqlite::params!["schema_version", GRAPH_SCHEMA_VERSION.to_string()],
    )?;
    init_v2_schema(txn, now)?;
    init_fts(txn)?;
    Ok(())
}

/// `store.py::_init_db`'s refusal to open a database from a newer build.
fn guard_db_format(txn: &Transaction<'_>) -> Result<(), CoreError> {
    let db_format: i64 = txn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if db_format > KG_DB_FORMAT_VERSION {
        return Err(CoreError::InvalidRequest(format!(
            "Knowledge Graph DB format {db_format} is newer than this build \
             ({KG_DB_FORMAT_VERSION}); restore a pre-upgrade backup or upgrade Lattice AI."
        )));
    }
    Ok(())
}

/// `v2_schema::_init_v2_schema`, in its order.
///
/// Stated deviation: Python wraps the whole thing in `try/except` and logs a
/// warning, leaving `_v2_projection_available = False`. That degradation is
/// unreachable for a writer — every `_upsert_node` projects with `strict=True`
/// and would raise on the next write anyway — so a failure here is returned as
/// an error rather than swallowed into a store that cannot accept a write.
fn init_v2_schema(txn: &Transaction<'_>, now: &str) -> Result<(), CoreError> {
    let stale = projection_version(txn)? != PROJECTION_VERSION;
    txn.execute_batch("DROP VIEW IF EXISTS kgv2_edges; DROP VIEW IF EXISTS kgv2_nodes;")?;
    if stale {
        txn.execute_batch("DROP TABLE IF EXISTS edges_v2; DROP TABLE IF EXISTS nodes_v2;")?;
    }
    init_v2_tables(txn)?;
    txn.execute_batch(V2_VIEWS_SQL)?;
    backfill_v2(txn, stale)?;
    normalize_v2_legacy_types(txn)?;
    txn.execute(
        "INSERT OR REPLACE INTO kg_meta(key, value) VALUES ('projection_version', ?)",
        rusqlite::params![PROJECTION_VERSION.to_string()],
    )?;
    txn.execute(
        "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
        rusqlite::params![KG_DB_FORMAT_KEY, KG_DB_FORMAT_VERSION.to_string()],
    )?;
    txn.execute(
        "INSERT OR REPLACE INTO kg_meta(key, value) \
         VALUES (?, COALESCE((SELECT value FROM kg_meta WHERE key=?), ?))",
        rusqlite::params![V2_WRITE_MASTER_KEY, V2_WRITE_MASTER_KEY, now],
    )?;
    txn.execute_batch(&format!("PRAGMA user_version={KG_DB_FORMAT_VERSION}"))?;
    Ok(())
}

/// `KGStoreV2._init_schema_on`.
fn init_v2_tables(txn: &Transaction<'_>) -> Result<(), CoreError> {
    drop_stale_empty_v2_tables(txn)?;
    rebuild_edges_identity(txn)?;
    txn.execute_batch(V2_SCHEMA_SQL)?;
    txn.execute(
        "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
        rusqlite::params!["schema_version", KG_SCHEMA_V2_VERSION.to_string()],
    )?;
    txn.execute(
        "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
        rusqlite::params!["embed_dim", embed_dim().to_string()],
    )?;
    Ok(())
}

/// `KGStoreV2._drop_stale_empty_v2_tables` — heal additively, drop only if empty.
fn drop_stale_empty_v2_tables(txn: &Transaction<'_>) -> Result<(), CoreError> {
    for table in ["edges_v2", "nodes_v2"] {
        if !object_exists(txn, "table", table)? {
            continue;
        }
        let columns = table_columns(txn, table)?;
        let expected: &[&str] = if table == "nodes_v2" {
            &EXPECTED_NODES_V2
        } else {
            &EXPECTED_EDGES_V2
        };
        let addable: &[(&str, &str)] = if table == "nodes_v2" {
            &ADDABLE_NODES_V2
        } else {
            &ADDABLE_EDGES_V2
        };
        let mut missing: Vec<&str> = expected
            .iter()
            .copied()
            .filter(|name| !columns.iter().any(|have| have == name))
            .collect();
        if missing.is_empty() {
            continue;
        }
        // `for col in sorted(missing & set(addable))`
        let mut healable: Vec<(&str, &str)> = addable
            .iter()
            .copied()
            .filter(|(name, _)| missing.contains(name))
            .collect();
        healable.sort_by(|left, right| left.0.cmp(right.0));
        for (name, kind) in healable {
            txn.execute_batch(&format!("ALTER TABLE {table} ADD COLUMN {name} {kind}"))?;
        }
        missing.retain(|name| !addable.iter().any(|(addable_name, _)| addable_name == name));
        if missing.is_empty() {
            continue;
        }
        let count: i64 = txn.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
            row.get(0)
        })?;
        if count == 0 {
            txn.execute_batch(&format!("DROP TABLE {table}"))?;
        }
        // A table that is both stale and populated is left untouched, exactly
        // as Python leaves it (it logs a warning and requires a manual
        // migration); dropping a populated table is not a port's decision.
    }
    Ok(())
}

/// `KGStoreV2._rebuild_edges_identity` — the pre-v4 UNIQUE constraint migration.
fn rebuild_edges_identity(txn: &Transaction<'_>) -> Result<(), CoreError> {
    let sql: Option<String> = txn
        .query_row(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges_v2'",
            [],
            |row| row.get(0),
        )
        .ok();
    let Some(sql) = sql else { return Ok(()) };
    if sql.contains("UNIQUE(source, target, type, legacy_type)") {
        return Ok(());
    }
    txn.execute_batch("ALTER TABLE edges_v2 RENAME TO edges_v2_old")?;
    let start = V2_SCHEMA_SQL
        .find("CREATE TABLE IF NOT EXISTS edges_v2")
        .expect("the edges_v2 DDL is in V2_SCHEMA_SQL");
    let end = V2_SCHEMA_SQL[start..]
        .find(");")
        .expect("the edges_v2 DDL is terminated")
        + start
        + 2;
    txn.execute_batch(V2_SCHEMA_SQL[start..end].trim_end_matches(';'))?;
    txn.execute_batch(
        "INSERT INTO edges_v2 (id, source, target, type, legacy_type, weight, \
                               confidence, evidence, metadata, created_by, created_at) \
         SELECT id, source, target, type, legacy_type, weight, \
                confidence, evidence, metadata, created_by, created_at \
         FROM edges_v2_old",
    )?;
    txn.execute_batch("DROP TABLE edges_v2_old")?;
    Ok(())
}

/// `v2_schema::_projection_version` — a fresh database is version 0.
fn projection_version(txn: &Transaction<'_>) -> Result<i64, CoreError> {
    if !object_exists(txn, "table", "kg_meta")? {
        return Ok(0);
    }
    let stored: Option<String> = txn
        .query_row(
            "SELECT value FROM kg_meta WHERE key='projection_version'",
            [],
            |row| row.get(0),
        )
        .ok();
    Ok(stored.and_then(|raw| raw.parse::<i64>().ok()).unwrap_or(0))
}

/// `(id, type, title, summary, metadata_json, created_at, updated_at)`.
type LegacyNodeRow = (
    String,
    String,
    String,
    Option<String>,
    String,
    String,
    String,
);

/// `v2_schema::_backfill_v2_on` — project the authoritative legacy rows into v2.
fn backfill_v2(txn: &Transaction<'_>, force: bool) -> Result<(), CoreError> {
    let legacy_nodes: i64 = txn.query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))?;
    if legacy_nodes == 0 {
        return Ok(());
    }
    let v2_nodes: i64 = txn.query_row("SELECT COUNT(*) FROM nodes_v2", [], |row| row.get(0))?;
    if v2_nodes > 0 && !force {
        return Ok(());
    }
    txn.execute_batch("DELETE FROM edges_v2; DELETE FROM nodes_v2;")?;
    let nodes: Vec<LegacyNodeRow> = {
        let mut statement = txn.prepare(
            "SELECT id, type, title, summary, metadata_json, created_at, updated_at FROM nodes",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get(5)?,
                row.get(6)?,
            ))
        })?;
        rows.collect::<Result<Vec<_>, _>>()?
    };
    for (id, node_type, title, summary, metadata_json, created_at, updated_at) in nodes {
        super::primitives::project_node_v2(
            txn,
            &super::primitives::NodeProjection {
                node_id: &id,
                node_type: &node_type,
                title: &title,
                summary: summary.as_deref(),
                metadata_json: Some(&metadata_json),
                created_at: Some(&created_at),
                updated_at: &updated_at,
                owner: None,
                workspace_id: None,
                visibility: None,
            },
        )?;
    }
    let edges: Vec<(String, String, String, String, f64, String, String)> = {
        let mut statement = txn.prepare(
            "SELECT id, from_node, to_node, type, weight, metadata_json, created_at FROM edges",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get::<_, Option<f64>>(4)?.unwrap_or(1.0),
                row.get(5)?,
                row.get(6)?,
            ))
        })?;
        rows.collect::<Result<Vec<_>, _>>()?
    };
    for (id, from_node, to_node, edge_type, weight, metadata_json, created_at) in edges {
        super::primitives::project_edge_v2(
            txn,
            &super::primitives::EdgeProjection {
                from_node: &from_node,
                to_node: &to_node,
                edge_type: &edge_type,
                weight,
                metadata_json: Some(&metadata_json),
                edge_id: Some(&id),
                created_at: Some(&created_at),
                legacy_type: None,
            },
        )?;
    }
    Ok(())
}

/// `v2_schema::_normalize_v2_legacy_types` — the pre-11.2.0 convention fix.
fn normalize_v2_legacy_types(txn: &Transaction<'_>) -> Result<(), CoreError> {
    txn.execute_batch("UPDATE OR IGNORE edges_v2 SET legacy_type='' WHERE legacy_type = type")?;
    txn.execute_batch("UPDATE nodes_v2 SET legacy_type=NULL WHERE legacy_type = ''")?;
    Ok(())
}

/// `v2_schema::_init_fts` — the trigram index plus its one-time backfill.
///
/// Python degrades when FTS5/trigram is missing from the SQLite build and keeps
/// LIKE search authoritative. `rusqlite`'s bundled build always has it (there is
/// a `tests/fts5_probe.rs` in this crate that fails loudly otherwise), so the
/// degrade is kept for shape rather than expected to fire.
fn init_fts(txn: &Transaction<'_>) -> Result<bool, CoreError> {
    if txn.execute_batch(FTS_SQL).is_err() {
        return Ok(false);
    }
    let count: i64 = txn.query_row("SELECT count(*) FROM node_fts", [], |row| row.get(0))?;
    if count == 0 {
        txn.execute_batch(
            "INSERT INTO node_fts(node_id, title, summary, metadata) \
             SELECT id, title, COALESCE(summary, ''), metadata_json FROM nodes",
        )?;
    }
    Ok(true)
}

fn object_exists(txn: &Transaction<'_>, kind: &str, name: &str) -> Result<bool, CoreError> {
    let found: Option<i64> = txn
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
            rusqlite::params![kind, name],
            |row| row.get(0),
        )
        .ok();
    Ok(found.is_some())
}

fn table_columns(txn: &Transaction<'_>, table: &str) -> Result<Vec<String>, CoreError> {
    let mut statement = txn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// `provenance.schema_versions()` — what an exporter stamps and an importer checks.
pub fn schema_versions() -> serde_json::Value {
    let mut map = serde_json::Map::new();
    map.insert("graph_schema_version".into(), GRAPH_SCHEMA_VERSION.into());
    map.insert("db_format_version".into(), KG_DB_FORMAT_VERSION.into());
    map.insert("kg_v2_schema_version".into(), KG_SCHEMA_V2_VERSION.into());
    map.insert("projection_version".into(), PROJECTION_VERSION.into());
    map.insert("embed_dim".into(), embed_dim().into());
    serde_json::Value::Object(map)
}

/// `provenance.backup_database` — `VACUUM INTO` after a full WAL checkpoint.
///
/// Takes a plain connection rather than a transaction because `VACUUM` cannot
/// run inside one — the same reason Python reaches past its own `_connect()`
/// helper for this single call.
pub fn backup_database(conn: &Connection, destination: &std::path::Path) -> Result<(), CoreError> {
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent).map_err(|err| {
            CoreError::Io(format!(
                "cannot create the backup directory {}: {err}",
                parent.display()
            ))
        })?;
    }
    if destination.exists() {
        std::fs::remove_file(destination).map_err(|err| {
            CoreError::Io(format!(
                "cannot replace the backup target {}: {err}",
                destination.display()
            ))
        })?;
    }
    conn.execute_batch("PRAGMA wal_checkpoint(FULL)")?;
    conn.execute(
        "VACUUM INTO ?",
        rusqlite::params![destination.to_string_lossy()],
    )?;
    Ok(())
}
