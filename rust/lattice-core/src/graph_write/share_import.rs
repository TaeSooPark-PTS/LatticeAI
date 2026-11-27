use std::collections::HashSet;

use rusqlite::{params, Transaction};
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::viewer_scope::{CONVERSATION_NODE_TYPES, CONVERSATION_SOURCE_PREFIXES};

use super::provenance::{array_of, insert_provenance_row, optional_text, text_of, ImportOutcome};
use super::taxonomy::node_type_from_legacy;
use super::types::{EdgeSpec, NodeSpec, RebuildRequest};
use super::GraphWriter;

pub const SHAREABLE_NODE_TYPES: &[&str] = &[
    "FILE",
    "DOCUMENT",
    "CHUNK",
    "CODE_SYMBOL",
    "CONCEPT",
    "MODEL",
    "TOOL",
    "PROJECT",
    "CODE_FILE",
    "SPREADSHEET",
    "SLIDE_DECK",
    "IMAGE",
    "IMAGE_TEXT",
    "SLIDE",
    "PAGE",
    "SHEET",
    "SECTION",
    "TOPIC",
    "FEATURE",
    "TASK",
    "DECISION",
    "ERROR",
    "EVENT",
    "SOURCE",
    "REPOSITORY",
    "MEETING",
    "ORGANIZATION",
    "WORKFLOW",
    "AUDIO",
    "VIDEO",
];

pub const UNSHAREABLE_NODE_LABELS: &[&str] = &[
    "apikey",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
];

pub const LOCAL_ONLY_FLAGS: &[&str] = &["do_not_share", "local_only", "private", "sensitive"];

pub const STRIPPED_OWNERSHIP_KEYS: &[&str] = &[
    "owner",
    "owner_id",
    "user_email",
    "user_id",
    "workspace_id",
    "visibility",
    "superseded_by",
    "superseded_at",
    "supersedes",
    "valid_to",
    "lifecycle_status",
    "conversation_id",
];

#[derive(Debug, Clone)]
pub struct SharedSubgraphImport {
    pub data: Map<String, Value>,
    pub workspace_id: String,
    pub owner: Option<String>,
    pub origin: String,
}

#[derive(Debug, Clone)]
pub struct SharedImportOutcome {
    pub outcome: ImportOutcome,
    pub workspace_id: String,
    pub skipped_existing: usize,
    pub skipped_unsafe: usize,
    pub ignored_knowledge_sources: usize,
}

impl SharedImportOutcome {
    pub fn to_json(&self) -> Value {
        let mut value = self.outcome.to_json();
        if let Some(map) = value.as_object_mut() {
            map.insert("workspace_id".into(), json!(self.workspace_id));
            map.insert("skipped_existing".into(), json!(self.skipped_existing));
            map.insert("skipped_unsafe".into(), json!(self.skipped_unsafe));
            map.insert(
                "ignored_knowledge_sources".into(),
                json!(self.ignored_knowledge_sources),
            );
        }
        value
    }
}

pub fn is_shareable_node(node_type: &str, metadata: &Map<String, Value>) -> bool {
    let label = node_type.trim().to_ascii_lowercase();
    if label.is_empty()
        || UNSHAREABLE_NODE_LABELS.contains(&label.as_str())
        || CONVERSATION_NODE_TYPES
            .iter()
            .any(|kind| kind.eq_ignore_ascii_case(&label))
    {
        return false;
    }
    if !SHAREABLE_NODE_TYPES.contains(&node_type_from_legacy(node_type).as_str()) {
        return false;
    }
    if LOCAL_ONLY_FLAGS
        .iter()
        .any(|flag| metadata.get(*flag).and_then(Value::as_bool) == Some(true))
    {
        return false;
    }
    !metadata
        .get("source_node")
        .and_then(Value::as_str)
        .map(str::to_ascii_lowercase)
        .is_some_and(|source| {
            CONVERSATION_SOURCE_PREFIXES
                .iter()
                .any(|prefix| source.starts_with(prefix))
        })
}

fn stripped(mut fields: Map<String, Value>) -> Map<String, Value> {
    for key in STRIPPED_OWNERSHIP_KEYS {
        fields.remove(*key);
    }
    fields
}

fn sanitized(fields: Map<String, Value>, request: &SharedSubgraphImport) -> Map<String, Value> {
    let mut fields = stripped(fields);
    fields.insert("workspace_id".into(), json!(request.workspace_id));
    if let Some(owner) = request.owner.as_deref() {
        fields.insert("owner".into(), json!(owner));
    }
    fields.insert("shared_from".into(), json!(request.origin));
    fields
}

fn exists(txn: &Transaction<'_>, sql: &str, id: &str) -> Result<bool, CoreError> {
    Ok(txn.query_row(sql, params![id], |row| row.get::<_, i64>(0))? != 0)
}

fn node_taken(txn: &Transaction<'_>, id: &str) -> Result<bool, CoreError> {
    Ok(
        exists(txn, "SELECT EXISTS(SELECT 1 FROM nodes WHERE id = ?1)", id)?
            || exists(
                txn,
                "SELECT EXISTS(SELECT 1 FROM nodes_v2 WHERE id = ?1)",
                id,
            )?,
    )
}

fn object_rows(data: &Map<String, Value>, key: &str) -> Vec<Map<String, Value>> {
    array_of(data, key)
        .into_iter()
        .filter_map(|row| match row {
            Value::Object(map) => Some(map),
            _ => None,
        })
        .collect()
}

fn loads(row: &Map<String, Value>, key: &str) -> Map<String, Value> {
    crate::pytext::safe_loads(row.get(key).and_then(Value::as_str))
}

struct Tally {
    accepted: HashSet<String>,
    nodes: usize,
    edges: usize,
    chunks: usize,
    parents: usize,
    provenance: usize,
    skipped_existing: usize,
    skipped_unsafe: usize,
}

impl GraphWriter {
    pub fn import_shared_subgraph(
        &self,
        request: &SharedSubgraphImport,
    ) -> Result<SharedImportOutcome, CoreError> {
        let workspace = request.workspace_id.trim();
        if workspace.is_empty() {
            return Err(CoreError::InvalidRequest(
                "A shared subgraph needs the accepting workspace.".into(),
            ));
        }
        let mut tally = Tally {
            accepted: HashSet::new(),
            nodes: 0,
            edges: 0,
            chunks: 0,
            parents: 0,
            provenance: 0,
            skipped_existing: 0,
            skipped_unsafe: 0,
        };
        let now = self.clock.now_iso();
        self.store.with_write_txn(|txn| {
            self.share_nodes(txn, request, &mut tally)?;
            self.share_parents(txn, request, &mut tally)?;
            self.share_chunks(txn, request, &mut tally)?;
            self.share_edges(txn, request, &mut tally)?;
            share_provenance(txn, request, &mut tally, &now)
        })?;
        let index = self.rebuild_vector_index(&RebuildRequest {
            full: false,
            include_nodes: true,
            include_chunks: true,
        })?;
        Ok(SharedImportOutcome {
            outcome: ImportOutcome {
                mode: "share".into(),
                nodes: tally.nodes,
                edges: tally.edges,
                chunks: tally.chunks,
                knowledge_sources: 0,
                provenance: tally.provenance,
                chunk_parents: tally.parents,
                dry_run: false,
                imported: true,
                index: Some(index),
            },
            workspace_id: workspace.to_string(),
            skipped_existing: tally.skipped_existing,
            skipped_unsafe: tally.skipped_unsafe,
            ignored_knowledge_sources: array_of(&request.data, "knowledge_sources").len(),
        })
    }

    fn share_nodes(
        &self,
        txn: &Transaction<'_>,
        request: &SharedSubgraphImport,
        tally: &mut Tally,
    ) -> Result<(), CoreError> {
        for node in object_rows(&request.data, "nodes") {
            let id = text_of(&node, "id");
            let node_type = text_of(&node, "type");
            let metadata = loads(&node, "metadata_json");
            if id.trim().is_empty() || !is_shareable_node(&node_type, &metadata) {
                tally.skipped_unsafe += 1;
                continue;
            }
            if tally.accepted.contains(&id) || node_taken(txn, &id)? {
                tally.skipped_existing += 1;
                continue;
            }
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: id.clone(),
                    node_type,
                    title: optional_text(&node, "title").unwrap_or_default(),
                    summary: optional_text(&node, "summary").unwrap_or_default(),
                    metadata: sanitized(metadata, request),
                    raw: stripped(loads(&node, "raw_json")),
                    owner: request.owner.clone(),
                    workspace_id: Some(request.workspace_id.clone()),
                    visibility: None,
                },
            )?;
            tally.accepted.insert(id);
            tally.nodes += 1;
        }
        Ok(())
    }

    fn share_parents(
        &self,
        txn: &Transaction<'_>,
        request: &SharedSubgraphImport,
        tally: &mut Tally,
    ) -> Result<(), CoreError> {
        let table = super::chunk_parents::chunk_parents_exist(txn);
        let mut kept = Vec::new();
        for parent in object_rows(&request.data, "chunk_parents") {
            let id = text_of(&parent, "id");
            if !tally.accepted.contains(&text_of(&parent, "source_node"))
                || (table
                    && exists(
                        txn,
                        "SELECT EXISTS(SELECT 1 FROM chunk_parents WHERE id = ?1)",
                        &id,
                    )?)
            {
                continue;
            }
            kept.push(Value::Object(parent));
        }
        tally.parents = super::chunk_parents::import_chunk_parents(txn, &kept)?;
        Ok(())
    }

    fn share_chunks(
        &self,
        txn: &Transaction<'_>,
        request: &SharedSubgraphImport,
        tally: &mut Tally,
    ) -> Result<(), CoreError> {
        for chunk in object_rows(&request.data, "chunks") {
            let id = text_of(&chunk, "id");
            let source = text_of(&chunk, "source_node");
            let own_node = tally.accepted.contains(&id);
            if id.trim().is_empty()
                || !tally.accepted.contains(&source)
                || exists(
                    txn,
                    "SELECT EXISTS(SELECT 1 FROM chunks WHERE id = ?1)",
                    &id,
                )?
                || (!own_node && node_taken(txn, &id)?)
            {
                continue;
            }
            let mut row = chunk.clone();
            let metadata = sanitized(loads(&chunk, "metadata_json"), request);
            row.insert(
                "metadata_json".into(),
                json!(Value::Object(metadata.clone()).to_string()),
            );
            self.restore_missing_chunk_node(
                txn,
                &row,
                request.owner.as_deref(),
                Some(&request.workspace_id),
            )?;
            self.upsert_chunk(
                txn,
                &id,
                &source,
                &optional_text(&chunk, "text").unwrap_or_default(),
                &metadata,
            )?;
            tally.accepted.insert(id);
            tally.chunks += 1;
        }
        Ok(())
    }

    fn share_edges(
        &self,
        txn: &Transaction<'_>,
        request: &SharedSubgraphImport,
        tally: &mut Tally,
    ) -> Result<(), CoreError> {
        for edge in object_rows(&request.data, "edges") {
            let from_node = text_of(&edge, "from_node");
            let to_node = text_of(&edge, "to_node");
            if !tally.accepted.contains(&from_node) || !tally.accepted.contains(&to_node) {
                continue;
            }
            let metadata = stripped(loads(&edge, "metadata_json"));
            let legacy_label = metadata
                .get("legacy_label")
                .and_then(Value::as_str)
                .filter(|label| !label.is_empty())
                .map(str::to_string)
                .or_else(|| optional_text(&edge, "type").filter(|value| !value.is_empty()));
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node,
                    to_node,
                    edge_type: text_of(&edge, "type"),
                    weight: edge
                        .get("weight")
                        .and_then(Value::as_f64)
                        .filter(|value| *value != 0.0)
                        .unwrap_or(1.0),
                    metadata,
                    legacy_label,
                },
            )?;
            tally.edges += 1;
        }
        Ok(())
    }
}

fn share_provenance(
    txn: &Transaction<'_>,
    request: &SharedSubgraphImport,
    tally: &mut Tally,
    now: &str,
) -> Result<(), CoreError> {
    for record in object_rows(&request.data, "provenance") {
        let id = text_of(&record, "id");
        if id.trim().is_empty()
            || !tally.accepted.contains(&text_of(&record, "node_id"))
            || exists(
                txn,
                "SELECT EXISTS(SELECT 1 FROM ingestion_provenance WHERE id = ?1)",
                &id,
            )?
        {
            continue;
        }
        let mut row = record.clone();
        row.insert("owner".into(), json!(request.owner));
        row.insert("workspace_id".into(), json!(request.workspace_id));
        row.insert("permissions_json".into(), json!("{}"));
        row.insert(
            "metadata_json".into(),
            json!(Value::Object(sanitized(loads(&record, "metadata_json"), request)).to_string()),
        );
        insert_provenance_row(txn, &row, now)?;
        tally.provenance += 1;
    }
    Ok(())
}
