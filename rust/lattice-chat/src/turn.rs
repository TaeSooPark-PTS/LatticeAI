use std::path::PathBuf;

use lattice_auth::OrderedMap;
use lattice_core::graph_write::types::{
    ChunkPiece, ExtractReply, IngestMessageRequest, IngestionRecord, SuppliedVector,
};
use lattice_core::graph_write::GraphWriter;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::intents::HistoryMeta;
use crate::redact::redact_for_role;
use crate::state::ChatState;

const PIPELINE_NAME: &str = "unified-ingestion";
const SOURCE_TYPE: &str = "chat_message";
const EXTRACT_PATH: &str = "/worker/extract";
const HASH_FIELDS: [&str; 6] = [
    "role",
    "content",
    "timestamp",
    "user_email",
    "conversation_id",
    "source",
];

#[derive(Debug, Clone, PartialEq)]
pub struct RecordedTurn {
    pub stored: bool,
    pub item: Option<Value>,
    pub ingested: Option<Value>,
}

impl RecordedTurn {
    pub fn content(&self) -> Option<&str> {
        self.item
            .as_ref()
            .and_then(|item| item.get("content"))
            .and_then(Value::as_str)
    }
}

pub fn message_hash(item: &Value) -> String {
    let basis: Vec<String> = HASH_FIELDS
        .iter()
        .map(|key| match item.get(*key) {
            Some(Value::String(text)) => text.clone(),
            Some(Value::Null) | None => String::new(),
            Some(other) => other.to_string(),
        })
        .collect();
    let digest = Sha256::digest(basis.join("|").as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

const CONVERSATION_DDL: &str = "
    CREATE TABLE IF NOT EXISTS conversation_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      message_hash TEXT NOT NULL UNIQUE,
      conversation_id TEXT,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      user_email TEXT,
      user_nickname TEXT,
      source TEXT,
      timestamp TEXT NOT NULL,
      metadata_json TEXT NOT NULL DEFAULT '{}',
      workspace_id TEXT,
      organization_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_conv_messages_conv
      ON conversation_messages(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_conv_messages_time
      ON conversation_messages(timestamp);
    CREATE INDEX IF NOT EXISTS idx_conv_messages_user
      ON conversation_messages(user_email);
    CREATE INDEX IF NOT EXISTS idx_conv_messages_workspace
      ON conversation_messages(workspace_id);
";

fn build_item(role: &str, message: &str, timestamp: &str, meta: &HistoryMeta<'_>) -> Value {
    let mut item = OrderedMap::new();
    item.insert("role", json!(role));
    item.insert("content", json!(message));
    item.insert("timestamp", json!(timestamp));
    for (key, value) in [
        ("user_email", meta.email),
        ("user_nickname", meta.nickname),
        ("source", meta.source),
        ("conversation_id", meta.conversation_id),
        ("workspace_id", meta.workspace_id),
    ] {
        if let Some(value) = value.filter(|text| !text.is_empty()) {
            item.insert(key, json!(value));
        }
    }
    serde_json::from_str(&serde_json::to_string(&item).unwrap_or_else(|_| "null".into()))
        .unwrap_or(Value::Null)
}

fn audit_payload(role: &str, message: &str, meta: &HistoryMeta<'_>, sensitive: &Value) -> Value {
    fn optional(value: Option<&str>) -> Value {
        value.map(|text| json!(text)).unwrap_or(Value::Null)
    }
    let mut payload = OrderedMap::new();
    payload.insert("role", json!(role));
    payload.insert("user_email", optional(meta.email));
    payload.insert("user_nickname", optional(meta.nickname));
    payload.insert("source", optional(meta.source));
    payload.insert("conversation_id", optional(meta.conversation_id));
    payload.insert("workspace_id", optional(meta.workspace_id));
    payload.insert(
        "content_preview",
        sensitive.get("preview").cloned().unwrap_or(Value::Null),
    );
    payload.insert("content_chars", json!(message.chars().count()));
    payload.insert(
        "sensitivity",
        sensitive.get("sensitivity").cloned().unwrap_or(Value::Null),
    );
    payload.insert(
        "sensitive_labels",
        sensitive
            .get("labels")
            .cloned()
            .filter(|labels| !labels.is_null())
            .unwrap_or_else(|| json!([])),
    );
    serde_json::from_str(&serde_json::to_string(&payload).unwrap_or_else(|_| "null".into()))
        .unwrap_or(Value::Null)
}

fn store_item(conn: &Connection, item: &Value) -> Result<(), rusqlite::Error> {
    conn.execute_batch(CONVERSATION_DDL)?;
    const KNOWN: [&str; 9] = [
        "role",
        "content",
        "timestamp",
        "user_email",
        "user_nickname",
        "source",
        "conversation_id",
        "workspace_id",
        "organization_id",
    ];
    let mut extra = OrderedMap::new();
    if let Some(entries) = item.as_object() {
        for (key, value) in entries {
            if !KNOWN.contains(&key.as_str()) {
                extra.insert(key.clone(), value.clone());
            }
        }
    }
    let metadata_json = if extra.is_empty() {
        "{}".to_string()
    } else {
        serde_json::to_string(&extra).unwrap_or_else(|_| "{}".into())
    };
    let text = |key: &str| -> Option<String> {
        item.get(key)
            .and_then(Value::as_str)
            .map(|value| value.to_string())
    };
    conn.execute(
        "INSERT OR IGNORE INTO conversation_messages
           (message_hash, conversation_id, role, content, user_email,
            user_nickname, source, timestamp, metadata_json, workspace_id,
            organization_id)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
        rusqlite::params![
            message_hash(item),
            text("conversation_id"),
            text("role").unwrap_or_else(|| "user".into()),
            text("content").unwrap_or_default(),
            text("user_email"),
            text("user_nickname"),
            text("source"),
            text("timestamp").unwrap_or_default(),
            metadata_json,
            text("workspace_id").or_else(|| Some("personal".into())),
            text("organization_id"),
        ],
    )?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn ingestion_result(
    node_id: &str,
    title: &str,
    embedded: bool,
    indexing_status: &str,
    provenance_id: Option<&str>,
    detail: Option<&str>,
) -> Value {
    let mut result = OrderedMap::new();
    result.insert("status", json!("ok"));
    result.insert("source_type", json!(SOURCE_TYPE));
    result.insert("node_id", json!(node_id));
    result.insert("source_node_id", Value::Null);
    result.insert("content_hash", Value::Null);
    result.insert("title", json!(title));
    result.insert("chunk_ids", json!([]));
    result.insert("chunk_count", json!(0));
    result.insert("duplicate", json!(false));
    result.insert("embedded", json!(embedded));
    result.insert("indexing_status", json!(indexing_status));
    result.insert(
        "provenance_id",
        provenance_id.map(|id| json!(id)).unwrap_or(Value::Null),
    );
    result.insert(
        "detail",
        detail.map(|text| json!(text)).unwrap_or(Value::Null),
    );
    serde_json::from_str(&serde_json::to_string(&result).unwrap_or_else(|_| "null".into()))
        .unwrap_or(Value::Null)
}

fn ingest_metadata(role: &str, meta: &HistoryMeta<'_>, item: &Value) -> Map<String, Value> {
    let mut metadata = Map::new();
    metadata.insert("role".into(), json!(role));
    metadata.insert(
        "user_nickname".into(),
        meta.nickname.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    metadata.insert(
        "source".into(),
        meta.source.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    metadata.insert("raw".into(), item.clone());
    metadata
}

fn node_is_embedded(graph: &GraphWriter, node_id: &str) -> bool {
    graph
        .store()
        .with_read_conn(|conn| {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM vector_embeddings \
                     WHERE item_id=? AND item_type='node'",
                    rusqlite::params![node_id],
                    |row| row.get(0),
                )
                .unwrap_or(0);
            Ok(count > 0)
        })
        .unwrap_or(false)
}

struct Persist {
    message: String,
    item: Value,
    audit_event: Value,
    audit_path: Option<PathBuf>,
    conversation_db: Option<PathBuf>,
    graph: Option<GraphWriter>,
    ingest: Option<IngestMessageRequest>,
    ingest_metadata: Map<String, Value>,
    captured_at: String,
    owner: Option<String>,
    workspace_id: Option<String>,
    vector_identity: VectorIdentity,
}

#[derive(Debug, Clone, PartialEq)]
pub enum VectorIdentity {
    Agrees,
    Diverges(String),
    NotAsked,
}

impl Persist {
    fn run(self) -> RecordedTurn {
        if let Some(path) = self.audit_path.as_ref() {
            if let Some(payload) = self.audit_event.as_object() {
                lattice_platform::adminops::admin::append_audit_event(
                    path,
                    "chat_message",
                    payload.clone(),
                );
            }
        }

        let stored = match self.conversation_db.as_ref() {
            Some(path) => match lattice_core::db::open_read_write(path) {
                Ok(conn) => store_item(&conn, &self.item).is_ok(),
                Err(_) => false,
            },
            None => false,
        };

        let ingested = match (self.graph.as_ref(), self.ingest.as_ref()) {
            (Some(graph), Some(request)) => self.grow(graph, request),
            _ => None,
        };

        RecordedTurn {
            stored,
            item: stored.then(|| self.item.clone()),
            ingested,
        }
    }

    fn grow(&self, graph: &GraphWriter, request: &IngestMessageRequest) -> Option<Value> {
        let outcome = graph.ingest_message(request).ok()?;
        let node_id = outcome.node_id.clone();
        let title: String = self.message.chars().take(80).collect();

        let (indexing_status, vector_detail) = match &self.vector_identity {
            VectorIdentity::Agrees => {
                let outcome = graph.write_vectors(&node_id);
                match outcome.status.as_str() {
                    "failed" => (
                        "pending".to_string(),
                        Some(format!(
                            "vector index sync failed: {}",
                            outcome.detail.unwrap_or_else(|| "unknown error".into())
                        )),
                    ),
                    _ => ("indexed".to_string(), None),
                }
            }
            VectorIdentity::Diverges(reason) => ("pending".to_string(), Some(reason.clone())),
            VectorIdentity::NotAsked => (
                "pending".to_string(),
                Some("vector index identity unknown: no embed seam was asked".into()),
            ),
        };
        let embedded = node_is_embedded(graph, &node_id);

        let receipt = graph.record_ingestion(&IngestionRecord {
            node_id: node_id.clone(),
            source_type: SOURCE_TYPE.into(),
            pipeline: PIPELINE_NAME.into(),
            source_uri: None,
            content_hash: None,
            title: Some(title.clone()),
            owner: self.owner.clone(),
            workspace_id: self.workspace_id.clone(),
            captured_at: Some(self.captured_at.clone()),
            modified_at: None,
            embedded,
            linked: false,
            duplicate: false,
            agent_used: None,
            chunk_count: 0,
            permissions: Map::new(),
            metadata: self.ingest_metadata.clone(),
        });
        let (provenance_id, provenance_detail) = match receipt {
            Ok(receipt) => (Some(receipt.id), None),
            Err(error) => (None, Some(format!("provenance capture failed: {error}"))),
        };
        let detail: Vec<String> = [provenance_detail, vector_detail]
            .into_iter()
            .flatten()
            .collect();
        Some(ingestion_result(
            &node_id,
            &title,
            embedded,
            &indexing_status,
            provenance_id.as_deref(),
            (!detail.is_empty()).then(|| detail.join("; ")).as_deref(),
        ))
    }
}

async fn vector_identity(
    state: &ChatState,
    text: &str,
) -> (VectorIdentity, Option<SuppliedVector>) {
    let Some(graph) = state.graph.as_ref() else {
        return (VectorIdentity::NotAsked, None);
    };
    let Some(worker) = state.worker.as_ref() else {
        return (
            VectorIdentity::Diverges("no worker to confirm the embedder".into()),
            None,
        );
    };
    let embedder = graph.embedder().clone();
    let reply = match worker.embed(&[text.to_string()], "passage").await {
        Ok(reply) => reply,
        Err(error) => {
            return (
                VectorIdentity::Diverges(format!("embed seam unavailable: {error}")),
                None,
            )
        }
    };
    let Some(values) = reply.vectors.first().cloned() else {
        return (
            VectorIdentity::Diverges("the embed seam returned no vector".into()),
            None,
        );
    };
    let supplied = SuppliedVector {
        values: values.clone(),
        model_id: reply.model_id.clone(),
        dim: reply.dim,
    };
    if reply.model_id != embedder.model_id() || reply.dim != embedder.dim() {
        return (
            VectorIdentity::Diverges(format!(
                "embedder mismatch: worker {} ({}) vs native {} ({})",
                reply.model_id,
                reply.dim,
                embedder.model_id(),
                embedder.dim()
            )),
            Some(supplied),
        );
    }
    if embedder.encode(&values) != embedder.encode(&embedder.embed(text)) {
        return (
            VectorIdentity::Diverges("embedder mismatch: same id, different vector".into()),
            Some(supplied),
        );
    }
    (VectorIdentity::Agrees, Some(supplied))
}

fn plain_chunk_pieces(text: &str) -> Vec<ChunkPiece> {
    let cleaned: Vec<char> = text
        .trim_matches(lattice_core::pytext::is_py_space)
        .chars()
        .collect();
    let mut pieces = Vec::new();
    let mut start = 0usize;
    while start < cleaned.len() {
        let end = cleaned.len().min(start + 1200);
        let mut fields = Map::new();
        fields.insert("strategy".into(), json!("plain"));
        fields.insert("start_char".into(), json!(start));
        pieces.push(ChunkPiece {
            text: cleaned[start..end].iter().collect(),
            fields,
            embedding: None,
        });
        if end >= cleaned.len() {
            break;
        }
        start = end.saturating_sub(160);
    }
    pieces
}

async fn supply_chunk_vectors(state: &ChatState, mut chunks: Vec<ChunkPiece>) -> Vec<ChunkPiece> {
    let (Some(worker), Some(_graph)) = (state.worker.as_ref(), state.graph.as_ref()) else {
        return chunks;
    };
    let texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
    let Ok(reply) = worker.embed(&texts, "passage").await else {
        return chunks;
    };
    if reply.model_id.trim().is_empty() || reply.dim == 0 {
        return chunks;
    }
    for (piece, values) in chunks.iter_mut().zip(reply.vectors) {
        if values.len() == reply.dim {
            piece.embedding = Some(SuppliedVector {
                values,
                model_id: reply.model_id.clone(),
                dim: reply.dim,
            });
        }
    }
    chunks
}

async fn extract_for_turn(state: &ChatState, text: &str) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(worker) = state.worker.as_ref() else {
        return ExtractReply::default();
    };
    let body = json!({"text": text, "kind": "message"});
    match worker
        .client()
        .post_idempotent_compute(EXTRACT_PATH, &body)
        .await
    {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

pub async fn write_chat_turn(
    state: &ChatState,
    role: &str,
    message: &str,
    meta: &HistoryMeta<'_>,
) -> RecordedTurn {
    let message = redact_for_role(role, message);
    let item = build_item(role, &message, &naive_local_iso(), meta);

    let sensitive = serde_json::to_value(
        lattice_platform::adminops::admin::classify_sensitive_message(&item, 0),
    )
    .unwrap_or(Value::Null);
    let audit_event = audit_payload(role, &message, meta, &sensitive);

    let graph = state.graph.clone();
    let extracted = if graph.is_some() {
        extract_for_turn(state, &message).await
    } else {
        ExtractReply::default()
    };
    let (vector_identity, supplied_vector) = if graph.is_some() {
        vector_identity(state, &message).await
    } else {
        (VectorIdentity::NotAsked, None)
    };
    let mut chunks = if graph.is_some() {
        plain_chunk_pieces(&message)
    } else {
        Vec::new()
    };
    if supplied_vector.is_some() && !chunks.is_empty() {
        chunks = supply_chunk_vectors(state, chunks).await;
    }
    let ingest = graph.as_ref().map(|_| IngestMessageRequest {
        role: role.to_string(),
        content: message.clone(),
        user_email: meta.email.map(str::to_string),
        user_nickname: meta.nickname.map(str::to_string),
        source: Some(
            meta.source
                .filter(|value| !value.is_empty())
                .unwrap_or(SOURCE_TYPE)
                .to_string(),
        ),
        conversation_id: meta.conversation_id.map(str::to_string),
        workspace_id: meta.workspace_id.map(str::to_string),
        raw: item.as_object().cloned(),
        chunks,
        concepts: extracted.concepts,
        triples: extracted.triples,
        semantic: extracted.semantic,
        embedding: supplied_vector.clone(),
    });

    let persist = Persist {
        ingest_metadata: ingest_metadata(role, meta, &item),
        message,
        item,
        audit_event,
        audit_path: state.audit_log_path(),
        conversation_db: Some(state.conversation_db()),
        graph,
        ingest,
        captured_at: utc_now_iso(),
        owner: meta.email.map(str::to_string),
        workspace_id: meta.workspace_id.map(str::to_string),
        vector_identity,
    };
    tokio::task::spawn_blocking(move || persist.run())
        .await
        .unwrap_or(RecordedTurn {
            stored: false,
            item: None,
            ingested: None,
        })
}

pub fn naive_local_iso() -> String {
    let now = lattice_retrieval::routes::naive_local_now();
    let seconds = now.floor();
    let micros = ((now - seconds) * 1_000_000.0) as i64;
    let stamp = civil_iso(seconds as i64);
    if micros <= 0 {
        stamp
    } else {
        format!("{stamp}.{micros:06}")
    }
}

fn utc_now_iso() -> String {
    let seconds = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0);
    format!("{}+00:00", civil_iso(seconds as i64))
}

fn civil_iso(total: i64) -> String {
    let days = total.div_euclid(86_400);
    let tod = total.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = year + i64::from(month <= 2);
    let (hour, minute, second) = (tod / 3600, (tod % 3600) / 60, tod % 60);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}")
}

#[cfg(test)]
#[path = "turn_tests.rs"]
mod tests;
