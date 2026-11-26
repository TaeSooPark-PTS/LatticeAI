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

#[derive(Debug, Clone, Copy)]
pub enum PruneScope<'a> {
    Everything,
    User(&'a str),
    Watch {
        owner: Option<&'a str>,
        workspace_id: Option<&'a str>,
    },
}

impl PruneScope<'_> {
    fn covers(&self, conn: &Connection, node_id: &str) -> bool {
        if matches!(self, PruneScope::Everything) {
            return true;
        }
        let Ok((owner, workspace)) = conn.query_row(
            "SELECT COALESCE(json_extract(metadata_json, '$.owner'), ''), \
                    COALESCE(json_extract(metadata_json, '$.workspace_id'), '') \
             FROM nodes WHERE id = ?1",
            [node_id],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        ) else {
            return false;
        };
        match self {
            PruneScope::Everything => true,
            PruneScope::User(user) => owner == *user,
            PruneScope::Watch {
                owner: held,
                workspace_id,
            } => owner == held.unwrap_or_default() && workspace == workspace_id.unwrap_or_default(),
        }
    }
}

fn scoped_node_ids(conn: &Connection, files: &[String], scope: PruneScope<'_>) -> Vec<String> {
    let mut ids = node_ids_for_files(conn, files);
    ids.retain(|id| scope.covers(conn, id));
    ids
}

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

pub fn prune_deleted(
    graph: &GraphWriter,
    root: &Path,
    confirm: bool,
    scope: PruneScope<'_>,
) -> Result<Value, String> {
    let store = graph.store();
    let mut files = deleted_files(store, root);
    if !matches!(scope, PruneScope::Everything) {
        store
            .with_read_conn(|conn| {
                files.retain(|file| {
                    !scoped_node_ids(conn, std::slice::from_ref(file), scope).is_empty()
                });
                Ok(())
            })
            .map_err(|error| error.to_string())?;
    }
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
            let node_ids = scoped_node_ids(conn, &files, scope);
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
        .with_read_conn(|conn| Ok(scoped_node_ids(conn, &files, scope)))
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

pub async fn refresh_sidecar(
    seam: Option<&lattice_core::worker::WorkerSeamClient>,
    graph: Option<&GraphWriter>,
) {
    let Some(seam) = seam else {
        return;
    };
    let (model_id, dim) = graph
        .and_then(|g| g.current_index_contract().ok().flatten())
        .map(|contract| (contract.model_id, contract.dimension))
        .unwrap_or_else(|| {
            let model = lattice_core::LocalEmbeddingModel::from_env();
            (model.model_id().to_string(), model.dim())
        });
    let zeros = vec![0.0f32; dim];
    let body = json!({
        "embedding_model": model_id,
        "embedding_dim": dim as i64,
        "vector": zeros,
        "k": 1,
    });
    let _ = seam.post_json("/worker/vector/query", &body).await;
}

#[cfg(test)]
mod scope_tests {
    use std::sync::Arc;

    use super::*;
    use crate::worker::{NoteIngestor, NoteSubmission};

    #[tokio::test]
    async fn a_watch_prunes_only_the_records_of_its_own_owner_and_workspace() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("vault");
        std::fs::create_dir_all(&root).expect("vault");
        let store = Arc::new(Store::open(&dir.path().join("knowledge_graph.sqlite")).expect("db"));
        let graph = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let ingestor = NoteIngestor::new(graph.clone());
        let uri = root.join("gone.md").display().to_string();
        let mut ids = Vec::new();
        for (owner, workspace, text) in [
            ("a@x", None, "alpha text"),
            ("b@x", None, "beta text"),
            ("a@x", Some("team"), "team text"),
        ] {
            let note = NoteSubmission {
                title: "gone.md".into(),
                content: text.into(),
                source: Some(uri.clone()),
                metadata: Map::new(),
            };
            let receipt = ingestor
                .ingest_note(&note, Some(owner), workspace)
                .await
                .expect("note");
            ids.push(receipt.node_id);
        }
        let alive = || -> Vec<bool> {
            ids.iter()
                .map(|id| {
                    graph
                        .store()
                        .with_read_conn(|conn| {
                            Ok(conn.query_row(
                                "SELECT COUNT(*) FROM nodes WHERE id = ?1",
                                [id],
                                |row| row.get::<_, i64>(0),
                            )? > 0)
                        })
                        .expect("alive")
                })
                .collect()
        };
        let stranger = PruneScope::User("c@x");
        let report = prune_deleted(&graph, &root, true, stranger).expect("stranger");
        assert_eq!(report["files"], json!([]));
        assert_eq!(alive(), vec![true, true, true]);

        let watch = PruneScope::Watch {
            owner: Some("a@x"),
            workspace_id: None,
        };
        prune_deleted(&graph, &root, true, watch).expect("watch");
        assert_eq!(alive(), vec![false, true, true]);
    }
}
