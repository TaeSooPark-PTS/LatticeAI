//! Consent prune of folder-ingest orphans: deleted files' graph, not the disk.
//!
//! Folder ingest *counts* vanished files and leaves their nodes. The watch
//! poller calls this door with ``confirm=true`` so a vanished watched file
//! leaves the graph. The HTTP prune route is still the explicit consent
//! path for a one-shot folder. Only `delete_document_tree` (both-direction
//! edges); `GraphWriter::delete_node` is the 11.7 trap and is never used.
//! `GraphWriter::delete_node` is the 11.7 trap — it leaves `PART_OF` dangling
//! — and is never used here.

use std::collections::HashSet;
use std::path::Path;

use lattice_core::db::Store;
use lattice_core::graph_write::types::IngestionRecord;
use lattice_core::graph_write::GraphWriter;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::fingerprint;
use crate::hashes::sha256_text;
use crate::watch::{walk_folder, WatchConfig};

/// What a prune would (or did) remove.
#[derive(Debug, Clone, Default)]
pub struct PrunePlan {
    pub files: Vec<String>,
    pub nodes: i64,
    pub edges: i64,
    pub chunks: i64,
    pub vectors: i64,
}

impl PrunePlan {
    pub fn to_json(&self) -> Value {
        json!({
            "nodes": self.nodes,
            "edges": self.edges,
            "chunks": self.chunks,
            "vectors": self.vectors,
        })
    }
}

/// Source files under `root` that provenance still records and the walk no
/// longer sees.
pub fn deleted_files(store: &Store, root: &Path) -> Vec<String> {
    let present: HashSet<String> = match walk_folder(root, &WatchConfig::default()) {
        Ok(files) => files
            .into_iter()
            .map(|file| file.path.display().to_string())
            .collect(),
        Err(_) => HashSet::new(),
    };
    fingerprint::missing_under_root(store, root, &present)
}

/// Node ids provenance attached to these source URIs.
pub fn node_ids_for_files(conn: &Connection, files: &[String]) -> Vec<String> {
    let mut ids = Vec::new();
    let mut seen = HashSet::new();
    for uri in files {
        let mut stmt = match conn.prepare(
            "SELECT DISTINCT node_id FROM ingestion_provenance \
             WHERE source_uri = ? AND node_id IS NOT NULL AND node_id <> ''",
        ) {
            Ok(stmt) => stmt,
            Err(_) => continue,
        };
        let rows = stmt.query_map(rusqlite::params![uri], |row| row.get::<_, String>(0));
        let Ok(rows) = rows else {
            continue;
        };
        for row in rows.flatten() {
            if seen.insert(row.clone()) {
                ids.push(row);
            }
        }
    }
    ids
}

fn child_ids(conn: &Connection, node_id: &str) -> Vec<String> {
    let mut stmt = match conn
        .prepare("SELECT id FROM nodes WHERE json_extract(metadata_json, '$.source_node') = ?")
    {
        Ok(stmt) => stmt,
        Err(_) => return Vec::new(),
    };
    stmt.query_map(rusqlite::params![node_id], |row| row.get::<_, String>(0))
        .ok()
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default()
}

fn count_in(conn: &Connection, sql: &str, ids: &[String]) -> i64 {
    if ids.is_empty() {
        return 0;
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let rendered = sql.replace("{ids}", &placeholders);
    let params: Vec<&dyn rusqlite::ToSql> =
        ids.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
    conn.query_row(&rendered, params.as_slice(), |row| row.get::<_, i64>(0))
        .unwrap_or(0)
}

/// Count the subgraph `delete_document_tree` would remove for these nodes.
pub fn preview_subgraph(conn: &Connection, roots: &[String]) -> PrunePlan {
    let mut remove: Vec<String> = Vec::new();
    let mut seen = HashSet::new();
    for root in roots {
        if seen.insert(root.clone()) {
            remove.push(root.clone());
        }
        for child in child_ids(conn, root) {
            if seen.insert(child.clone()) {
                remove.push(child);
            }
        }
    }
    let nodes = remove.len() as i64;
    let chunks = count_in(
        conn,
        "SELECT COUNT(*) FROM chunks WHERE source_node IN ({ids})",
        &remove,
    );
    let edges = if remove.is_empty() {
        0
    } else {
        let placeholders = vec!["?"; remove.len()].join(",");
        let sql = format!(
            "SELECT COUNT(*) FROM edges WHERE from_node IN ({placeholders}) \
             OR to_node IN ({placeholders})"
        );
        let params: Vec<&dyn rusqlite::ToSql> =
            remove.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
        let doubled: Vec<&dyn rusqlite::ToSql> =
            params.iter().chain(params.iter()).copied().collect();
        conn.query_row(&sql, doubled.as_slice(), |row| row.get::<_, i64>(0))
            .unwrap_or(0)
    };
    let vectors = if remove.is_empty() {
        0
    } else {
        let placeholders = vec!["?"; remove.len()].join(",");
        let sql = format!(
            "SELECT COUNT(*) FROM vector_embeddings \
             WHERE item_id IN ({placeholders}) OR source_node IN ({placeholders})"
        );
        let params: Vec<&dyn rusqlite::ToSql> =
            remove.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
        let doubled: Vec<&dyn rusqlite::ToSql> =
            params.iter().chain(params.iter()).copied().collect();
        conn.query_row(&sql, doubled.as_slice(), |row| row.get::<_, i64>(0))
            .unwrap_or(0)
    };
    PrunePlan {
        files: Vec::new(),
        nodes,
        edges,
        chunks,
        vectors,
    }
}

/// Plan (and optionally apply) a prune of deleted files under `root`.
pub fn prune_deleted(graph: &GraphWriter, root: &Path, confirm: bool) -> Result<Value, String> {
    let store = graph.store();
    let files = deleted_files(store, root);
    if files.is_empty() {
        return Ok(json!({
            "status": "ok",
            "confirm": confirm,
            "dry_run": !confirm,
            "files": [],
            "would_remove": { "nodes": 0, "edges": 0, "chunks": 0, "vectors": 0 },
            "removed": { "nodes": 0, "edges": 0, "chunks": 0, "vectors": 0 },
        }));
    }
    let plan = store
        .with_read_conn(|conn| {
            let node_ids = node_ids_for_files(conn, &files);
            let mut preview = preview_subgraph(conn, &node_ids);
            preview.files = files.clone();
            Ok(preview)
        })
        .map_err(|error| error.to_string())?;

    if !confirm {
        return Ok(json!({
            "status": "preview",
            "confirm": false,
            "dry_run": true,
            "files": plan.files,
            "would_remove": plan.to_json(),
            "removed": { "nodes": 0, "edges": 0, "chunks": 0, "vectors": 0 },
        }));
    }

    let roots = store
        .with_read_conn(|conn| Ok(node_ids_for_files(conn, &files)))
        .map_err(|error| error.to_string())?;
    let mut removed_nodes = 0i64;
    for node_id in &roots {
        match graph.delete_document_tree(node_id) {
            Ok(receipt) => {
                removed_nodes += receipt
                    .get("removed_nodes")
                    .and_then(Value::as_i64)
                    .unwrap_or(0);
            }
            Err(error) => return Err(error.to_string()),
        }
    }

    let mut metadata = Map::new();
    metadata.insert("prune_deleted".into(), json!(true));
    metadata.insert("files".into(), json!(plan.files));
    metadata.insert("would_remove".into(), plan.to_json());
    metadata.insert("removed_nodes".into(), json!(removed_nodes));
    let _ = graph.record_ingestion(&IngestionRecord {
        node_id: format!("prune:{}", sha256_text(&root.display().to_string())),
        source_type: "prune".into(),
        pipeline: "folder-prune".into(),
        source_uri: Some(root.display().to_string()),
        title: Some(format!("pruned {} deleted file(s)", plan.files.len())),
        metadata,
        ..Default::default()
    });

    let dangling = store
        .with_read_conn(|conn| Ok(dangling_edge_count(conn)))
        .unwrap_or(0);
    Ok(json!({
        "status": "ok",
        "confirm": true,
        "dry_run": false,
        "files": plan.files,
        "would_remove": plan.to_json(),
        "removed": {
            "nodes": removed_nodes,
            "edges": plan.edges,
            "chunks": plan.chunks,
            "vectors": plan.vectors,
        },
        "dangling_edges": dangling,
    }))
}

/// After a confirmed prune: no edge may point at a missing node.
pub fn dangling_edge_count(conn: &Connection) -> i64 {
    conn.query_row(
        "SELECT COUNT(*) FROM edges e \
         WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.from_node) \
            OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.to_node)",
        [],
        |row| row.get(0),
    )
    .unwrap_or(0)
}

/// Nudge the HNSW sidecar so new vectors from this ingest are learned.
pub async fn refresh_sidecar(seam: Option<&lattice_core::worker::WorkerSeamClient>) {
    let Some(seam) = seam else {
        return;
    };
    let model = lattice_core::LocalEmbeddingModel::from_env();
    let zeros = vec![0.0f32; model.dim()];
    let body = json!({
        "embedding_model": model.model_id(),
        "embedding_dim": model.dim() as i64,
        "vector": zeros,
        "k": 1,
    });
    let _ = seam.post_json("/worker/vector/query", &body).await;
}
