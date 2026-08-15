//! One chat turn, persisted natively: **redact → audit → store → ingest**.
//!
//! Port of `latticeai/runtime/history_writer.py::write_chat_turn`, which is the
//! only place in the product that decides what a chat message looks like after
//! redaction and what the audit log records about it. Until WP-W3a that chain
//! ran in the Python worker and chat reached it over
//! `POST /worker/chat/record-turn`; now every step runs here and that seam has
//! no caller left (W2's retirement precondition 1 and 2).
//!
//! The order is the contract, and it is the Python module's own words:
//!
//! 1. **Redact first** — everything downstream (the audit preview, the durable
//!    store, the knowledge graph) sees the redacted text, never the original.
//!    [`crate::redact::redact_for_role`] is that step, byte-parity proved
//!    against the live Python redactor by `tests/redact_parity.rs`.
//! 2. **Audit before storing** — the row carries a masked preview and the
//!    sensitivity verdict, so a message that should not have been sent is
//!    visible in the log even if the store write later fails.
//! 3. **Store** — `conversation_messages`, the unbounded episodic memory, in
//!    the same SQLite file as the graph. Field-for-field
//!    `lattice_brain.conversations.ConversationStore.append`, `message_hash`
//!    included, so a natively written turn is indistinguishable from one Python
//!    wrote and the `lattice-retrieval` history lanes read it unchanged
//!    (`tests/turn_chain.rs` proves the round trip through `GET /history`).
//! 4. **Ingest is best-effort** — graph growth may fail; the conversation store
//!    may not. A skipped or failed ingest is `ingested: null`, which is exactly
//!    what the seam reported.
//!
//! Nothing here raises: `write_chat_turn` swallows its own failures by design,
//! because a chat reply must not be lost to a logging bug. What this module adds
//! over the Python function is the [`RecordedTurn`] receipt — the same one the
//! seam's `_TurnRecorder` produced — so a caller can see whether the row landed.
//!
//! ## What is *not* native, and why (the honest half)
//!
//! `ingest_message`'s concept / triple / semantic-item extraction is **LLM-first**
//! (`lattice_brain/graph/_kg_common/extraction.py::_extract_concepts` asks a
//! model before falling back to rules), so it is model compute and W1
//! deliberately left it out of `lattice_core::graph_write`: those fields arrive
//! *as data* on the request. WP-W5's `POST /worker/extract` is that compute.
//! This chain calls it once per ingested turn and hands the reply's
//! `concepts` / `triples` / `semantic` to [`GraphWriter::ingest_message`].
//! Graph-off and empty text skip the call (Python never extracts those
//! either — there is no ingest). A failed extract is best-effort: the turn
//! still lands and the three fields stay empty, which is the same
//! `ingested: null`-or-empty outcome Python reports when the compute cannot
//! run. The Message/AIResponse/Chat/Person nodes and their edges land either
//! way.

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

/// `IngestionPipeline(pipeline_name="unified-ingestion")`.
const PIPELINE_NAME: &str = "unified-ingestion";
/// `IngestionItem(source_type="chat_message", …)`.
const SOURCE_TYPE: &str = "chat_message";
/// `POST /worker/extract` — W5's LLM-first concept / triple / semantic seam.
const EXTRACT_PATH: &str = "/worker/extract";
/// `_message_hash`'s field order — the hash is the store's UNIQUE key, so this
/// list is load-bearing rather than cosmetic.
const HASH_FIELDS: [&str; 6] = [
    "role",
    "content",
    "timestamp",
    "user_email",
    "conversation_id",
    "source",
];

/// What one recorded turn produced.
///
/// Named for the receipt `POST /worker/chat/record-turn` used to answer with, and
/// carrying the same three fields, because the whole point of W3a is that
/// nothing downstream can tell the difference.
#[derive(Debug, Clone, PartialEq)]
pub struct RecordedTurn {
    /// Whether the durable store accepted the row.
    pub stored: bool,
    /// The row as stored — **redacted**. `None` when the store refused.
    pub item: Option<Value>,
    /// The ingestion receipt, or `None` when the graph is off or ingest failed.
    pub ingested: Option<Value>,
}

impl RecordedTurn {
    /// The stored (redacted) text, when there is one.
    pub fn content(&self) -> Option<&str> {
        self.item
            .as_ref()
            .and_then(|item| item.get("content"))
            .and_then(Value::as_str)
    }
}

/// `_message_hash(item)` — sha256 over six fields joined by `|`.
///
/// `str(item.get(key) or "")` for each, so a missing key and an empty string
/// hash the same, which is what makes a Python-written row and a Rust-written
/// one collide on `INSERT OR IGNORE` instead of duplicating.
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

/// `ConversationStore._init_db`'s DDL, `IF NOT EXISTS` throughout.
///
/// A fresh machine has no `conversation_messages` table until something writes
/// the first turn — in Python that is `ConversationStore.__init__`, which runs
/// whether or not the graph is enabled. The column list and the three indexes
/// are transcribed so a store this creates is the store Python would have.
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

/// The item `write_chat_turn` builds, in its key order.
///
/// `role`/`content`/`timestamp` always; the five attribution keys only when
/// truthy — Python's `if value:`, so an empty string is as absent as `None`.
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
    // Through OrderedMap so the keys keep insertion order; `serde_json::Map` is
    // a BTreeMap and would sort them.
    serde_json::from_str(&serde_json::to_string(&item).unwrap_or_else(|_| "null".into()))
        .unwrap_or(Value::Null)
}

/// The audit payload `write_chat_turn` passes to `append_audit_event`.
///
/// Ten keys, always all ten: Python hands them as keyword arguments, so a `None`
/// lands in the row as `null` rather than being omitted. `content_chars` is
/// `len(message)` — **characters**, since Python measures a `str`.
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

/// `ConversationStore.append` — one `INSERT OR IGNORE`, same eleven columns.
///
/// `metadata_json` holds whatever keys the item carries beyond the nine the
/// table has columns for; a turn from this chain never has any, so it is `{}`,
/// but the branch is kept because the store's contract has it.
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
            text("workspace_id"),
            text("organization_id"),
        ],
    )?;
    Ok(())
}

/// `IngestionResult.as_dict()` for a chat turn.
///
/// Thirteen keys in Python's order. The three additive v9.8 keys
/// (`extraction_quality`, `warnings`, `quality_gate`) are omitted because the
/// pipeline never populates them for `chat_message` — `_assess_item_quality` and
/// `_observe_quality_gate` both return `None` for `CHAT_SOURCE_TYPES`.
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

/// The metadata `write_chat_turn` hangs on the `IngestionItem`.
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

/// `node_is_embedded(node_id)` — is there a vector row for this node?
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

/// The blocking half of the chain: audit, store, ingest, vector sync.
///
/// Split out so the whole of it runs on `spawn_blocking` — an audit append, a
/// SQLite insert and (with a graph) an embedding pass are three things that do
/// not belong on the event loop (v10.9.0).
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

/// What `POST /worker/embed` said about the index this turn's vector joins.
///
/// The seam owns the embedding provider, so it — not a native guess — is the
/// authority on which `(model_id, dim)` vectors are filed under (W2 §1, §4).
///
/// W5's supplied-vector door lets `ingest_message` file the worker's vector on
/// the Message / AIResponse node. The identity check still decides whether
/// `write_vectors` (native hash, incremental) may run: agreement licenses it;
/// a mismatch leaves other nodes as backlog rather than overwriting the
/// provider row.
#[derive(Debug, Clone, PartialEq)]
pub enum VectorIdentity {
    /// The seam embedded the text and the native model reproduced it bit for bit.
    Agrees,
    /// The seam answered, and it is not the model this process writes with.
    Diverges(String),
    /// The graph is off, so nothing was asked and nothing is written.
    NotAsked,
}

impl Persist {
    fn run(self) -> RecordedTurn {
        // 2. Audit carries a masked preview and the verdict, never the body.
        if let Some(path) = self.audit_path.as_ref() {
            if let Some(payload) = self.audit_event.as_object() {
                lattice_platform::admin::append_audit_event(path, "chat_message", payload.clone());
            }
        }

        // 3. Durable episodic memory.
        let stored = match self.conversation_db.as_ref() {
            Some(path) => match lattice_core::db::open_read_write(path) {
                Ok(conn) => store_item(&conn, &self.item).is_ok(),
                Err(_) => false,
            },
            None => false,
        };

        // 4. Best-effort Brain growth through the graph write engine.
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

    /// `IngestionPipeline.ingest` for a `chat_message`, minus what needs a model.
    fn grow(&self, graph: &GraphWriter, request: &IngestMessageRequest) -> Option<Value> {
        let outcome = graph.ingest_message(request).ok()?;
        let node_id = outcome.node_id.clone();
        // `result.setdefault("title", item.title or text[:80])` — the raw text,
        // not the cleaned title the node carries.
        let title: String = self.message.chars().take(80).collect();

        // `_sync_vector_index`: run the incremental sync, and report a failure as
        // backlog rather than as a failed ingest. The identity verdict gates it —
        // re-embedding under a model the provider does not use only adds rows
        // nobody will match.
        //
        // Stated deviation: Python also *queues* the node on `vector_jobs`
        // (`_pending_detail` → "; queued for background embedding"). That table
        // is one of the two W1 left with a Python door, so nothing is queued here
        // and the detail says only why it is pending.
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

        // Provenance must never turn an already-persisted ingest into a failure.
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

/// Ask `/worker/embed` for this text and check the answer against the model
/// this process would file the vector under.
///
/// W2 precondition 4 in one function: "a native re-derived hash embedder is the
/// failure mode to avoid — `similarity()` raises on a width mismatch, so a
/// divergence silently kills vector search." Rather than trust that the two
/// embedders agree, the seam is asked to embed the turn's text as a `passage`
/// and its vector is compared to the native one **after both are encoded to the
/// f32 the index stores**. Agreement is the licence to write natively.
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

/// Plain-window chunks (1200 / 160). lattice-chat cannot depend on lattice-ingest.
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

/// Batch-embed chunk texts. Seam / model disagreement → no supplied vectors.
async fn supply_chunk_vectors(state: &ChatState, mut chunks: Vec<ChunkPiece>) -> Vec<ChunkPiece> {
    let (Some(worker), Some(graph)) = (state.worker.as_ref(), state.graph.as_ref()) else {
        return chunks;
    };
    let texts: Vec<String> = chunks.iter().map(|piece| piece.text.clone()).collect();
    let Ok(reply) = worker.embed(&texts, "passage").await else {
        return chunks;
    };
    if reply.model_id != graph.embedder().model_id() || reply.dim != graph.embedder().dim() {
        return chunks;
    }
    for (piece, values) in chunks.iter_mut().zip(reply.vectors) {
        if !values.is_empty() {
            piece.embedding = Some(SuppliedVector {
                values,
                model_id: reply.model_id.clone(),
                dim: reply.dim,
            });
        }
    }
    chunks
}

/// Ask `/worker/extract` for this turn's concept subgraph.
///
/// Skipped when there is no text (Python's extractors return empty for that
/// input, so the HTTP call would only buy a round trip). A missing worker or
/// a failed call leaves the three fields empty — the ingest still runs.
async fn extract_for_turn(state: &ChatState, text: &str) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(worker) = state.worker.as_ref() else {
        return ExtractReply::default();
    };
    let body = json!({"text": text, "kind": "message"});
    match worker.client().post_json(EXTRACT_PATH, &body).await {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

/// `write_chat_turn(role, message, …)` — the whole chain, natively.
///
/// Never raises and never panics: every step swallows its own failure, exactly
/// as the Python function does, because a reply must not be lost to a bookkeeping
/// bug. The [`RecordedTurn`] says what actually happened.
pub async fn write_chat_turn(
    state: &ChatState,
    role: &str,
    message: &str,
    meta: &HistoryMeta<'_>,
) -> RecordedTurn {
    // 1. Redact before anything else sees the text.
    let message = redact_for_role(role, message);
    let item = build_item(role, &message, &naive_local_iso(), meta);

    let sensitive = serde_json::to_value(lattice_platform::admin::classify_sensitive_message(
        &item, 0,
    ))
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
    if matches!(vector_identity, VectorIdentity::Agrees) && !chunks.is_empty() {
        chunks = supply_chunk_vectors(state, chunks).await;
    }
    let ingest = graph.as_ref().map(|_| IngestMessageRequest {
        role: role.to_string(),
        content: message.clone(),
        user_email: meta.email.map(str::to_string),
        user_nickname: meta.nickname.map(str::to_string),
        // `meta.get("source") or source_type` — an unattributed turn is filed
        // under the pipeline's own name, not under nothing.
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

/// `datetime.now().isoformat()` — naive **local** time, microseconds included.
///
/// Naive local because that is what every stamp in `conversation_messages` was
/// written with; a UTC stamp on a machine at UTC+9 would sort nine hours wrong
/// against the rows already there. The conversion goes through
/// `lattice_retrieval::routes::naive_local_now`, which is this workspace's one
/// `localtime_r(3)` route, rather than a fourth copy of it.
///
/// Stated deviation: the fraction comes back through an `f64` of seconds, so the
/// microseconds are accurate to about a quarter of one. `isoformat()` omits the
/// fraction entirely when it is zero, and so does this.
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

/// `datetime.now(timezone.utc).isoformat()` — offset-aware, second resolution.
fn utc_now_iso() -> String {
    let seconds = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0);
    format!("{}+00:00", civil_iso(seconds as i64))
}

/// `YYYY-MM-DDTHH:MM:SS` for a count of seconds since the epoch.
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
mod tests {
    use super::*;

    fn meta<'a>() -> HistoryMeta<'a> {
        HistoryMeta {
            email: Some("owner@example.com"),
            nickname: Some("owner"),
            source: Some("web"),
            conversation_id: Some("c1"),
            workspace_id: Some("personal"),
        }
    }

    #[test]
    fn the_item_keeps_pythons_key_order_and_drops_empty_attribution() {
        let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
        assert_eq!(
            serde_json::to_string(&item).unwrap(),
            "{\"role\":\"user\",\"content\":\"hi\",\"timestamp\":\"2026-08-14T12:00:00\",\
             \"user_email\":\"owner@example.com\",\"user_nickname\":\"owner\",\
             \"source\":\"web\",\"conversation_id\":\"c1\",\"workspace_id\":\"personal\"}"
        );
        let bare = build_item(
            "assistant",
            "yo",
            "t",
            &HistoryMeta {
                email: None,
                nickname: Some(""),
                source: None,
                conversation_id: None,
                workspace_id: None,
            },
        );
        assert_eq!(
            serde_json::to_string(&bare).unwrap(),
            "{\"role\":\"assistant\",\"content\":\"yo\",\"timestamp\":\"t\"}",
            "an empty nickname is as absent as a missing one"
        );
    }

    #[test]
    fn the_message_hash_is_pythons_six_field_digest() {
        let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
        // sha256("user|hi|2026-08-14T12:00:00|owner@example.com|c1|web")
        let expected: String = {
            let basis = "user|hi|2026-08-14T12:00:00|owner@example.com|c1|web";
            Sha256::digest(basis.as_bytes())
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect()
        };
        assert_eq!(message_hash(&item), expected);
        // A missing key hashes as an empty field, not as "null".
        let bare = json!({"role": "user", "content": "hi", "timestamp": "t"});
        assert_eq!(
            message_hash(&bare),
            Sha256::digest("user|hi|t|||".as_bytes())
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>()
        );
        // A non-string field stringifies rather than vanishing.
        assert_ne!(
            message_hash(&json!({"role": 1, "content": "hi", "timestamp": "t"})),
            message_hash(&bare)
        );
    }

    #[test]
    fn the_audit_payload_has_all_ten_keys_even_when_empty() {
        let sensitive = json!({"preview": "hi", "sensitivity": "none", "labels": []});
        let payload = audit_payload("user", "안녕", &meta(), &sensitive);
        let keys: Vec<&String> = payload.as_object().unwrap().keys().collect();
        assert_eq!(
            keys,
            [
                "role",
                "user_email",
                "user_nickname",
                "source",
                "conversation_id",
                "workspace_id",
                "content_preview",
                "content_chars",
                "sensitivity",
                "sensitive_labels",
            ]
            .iter()
            .collect::<Vec<_>>()
        );
        assert_eq!(payload["content_chars"], 2, "characters, not UTF-8 bytes");
        let empty = audit_payload(
            "assistant",
            "",
            &HistoryMeta {
                email: None,
                nickname: None,
                source: None,
                conversation_id: None,
                workspace_id: None,
            },
            &json!({}),
        );
        assert_eq!(empty["user_email"], Value::Null);
        assert_eq!(empty["sensitive_labels"], json!([]));
        assert_eq!(empty["content_preview"], Value::Null);
    }

    #[test]
    fn a_turn_stores_and_reads_back_through_the_same_columns() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        let item = build_item("user", "hi", "2026-08-14T12:00:00", &meta());
        store_item(&conn, &item).unwrap();
        // Idempotent: the same turn twice is one row (`INSERT OR IGNORE` on the
        // message hash), which is what makes a retry safe.
        store_item(&conn, &item).unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM conversation_messages", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);
        let (content, metadata, workspace): (String, String, String) = conn
            .query_row(
                "SELECT content, metadata_json, workspace_id FROM conversation_messages",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(content, "hi");
        assert_eq!(metadata, "{}");
        assert_eq!(workspace, "personal");
    }

    #[test]
    fn unknown_item_keys_land_in_metadata_json() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        let item = json!({
            "role": "user", "content": "hi", "timestamp": "t", "trace_id": "abc",
        });
        store_item(&conn, &item).unwrap();
        let metadata: String = conn
            .query_row(
                "SELECT metadata_json FROM conversation_messages",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(metadata, "{\"trace_id\":\"abc\"}");
    }

    #[test]
    fn the_ingestion_receipt_is_the_pipelines_thirteen_keys() {
        let receipt = ingestion_result("message:abc", "hi", true, "indexed", Some("prov-1"), None);
        let keys: Vec<&String> = receipt.as_object().unwrap().keys().collect();
        assert_eq!(keys.len(), 13);
        assert_eq!(keys[0], "status");
        assert_eq!(receipt["source_type"], "chat_message");
        assert_eq!(receipt["chunk_count"], 0);
        assert_eq!(receipt["duplicate"], false);
        assert_eq!(receipt["provenance_id"], "prov-1");
        assert_eq!(receipt["detail"], Value::Null);
        assert!(
            receipt.get("extraction_quality").is_none(),
            "chat turns carry no quality annotation, so the key is absent"
        );
        let degraded = ingestion_result("m", "t", false, "pending", None, Some("boom"));
        assert_eq!(degraded["provenance_id"], Value::Null);
        assert_eq!(degraded["detail"], "boom");
    }

    #[test]
    fn the_ingest_metadata_carries_the_raw_item() {
        let item = build_item("user", "hi", "t", &meta());
        let metadata = ingest_metadata("user", &meta(), &item);
        assert_eq!(metadata["role"], "user");
        assert_eq!(metadata["user_nickname"], "owner");
        assert_eq!(metadata["source"], "web");
        assert_eq!(metadata["raw"]["content"], "hi");
    }

    #[test]
    fn a_receipt_reports_the_stored_text() {
        let turn = RecordedTurn {
            stored: true,
            item: Some(json!({"role": "user", "content": "[redacted]"})),
            ingested: None,
        };
        assert_eq!(turn.content(), Some("[redacted]"));
        let refused = RecordedTurn {
            stored: false,
            item: None,
            ingested: None,
        };
        assert_eq!(refused.content(), None);
        assert_ne!(turn, refused);
        assert!(format!("{refused:?}").contains("stored"));
    }

    #[test]
    fn the_timestamps_are_iso_and_the_utc_one_says_so() {
        let local = naive_local_iso();
        assert_eq!(&local[4..5], "-");
        assert_eq!(&local[10..11], "T");
        assert!(local.len() >= 19, "{local}");
        let utc = utc_now_iso();
        assert!(utc.ends_with("+00:00"), "{utc}");
        assert_eq!(civil_iso(0), "1970-01-01T00:00:00");
        // `datetime.fromtimestamp(1_786_000_000, timezone.utc).isoformat()`.
        assert_eq!(civil_iso(1_786_000_000), "2026-08-06T07:06:40");
        assert_eq!(civil_iso(-86_400), "1969-12-31T00:00:00");
    }

    #[test]
    fn a_divergent_embedder_is_named_rather_than_written_over() {
        let identity = VectorIdentity::Diverges("worker x vs native y".into());
        assert!(format!("{identity:?}").contains("worker x"));
        assert_ne!(identity, VectorIdentity::Agrees);
        assert_ne!(VectorIdentity::NotAsked, VectorIdentity::Agrees);
        assert_eq!(VectorIdentity::Agrees.clone(), VectorIdentity::Agrees);
    }
}
