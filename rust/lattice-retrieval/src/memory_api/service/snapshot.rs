use std::path::Path;

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use crate::memory_api::kg;
use crate::memory_api::wsos;

pub struct Snapshot {
    pub state: Value,
    pub workspace_memories: Vec<Value>,
    pub owned_memories: Vec<Value>,
    pub project_memories: Vec<Value>,
    pub snapshots: Vec<Value>,
    pub conversations: Vec<Value>,
    pub scoped_conversations: Vec<Value>,
    pub stats: Option<OrderedMap>,
    pub index: Option<OrderedMap>,
    pub workspace_bytes: i64,
    pub graph_bytes: i64,
}

impl Snapshot {
    pub fn read(
        conn: &Connection,
        data_dir: &Path,
        graph_enabled: bool,
        user_email: &str,
        workspace_id: Option<&str>,
    ) -> Result<Self, CoreError> {
        let state = wsos::load_from(conn, data_dir);
        let user = Some(user_email).filter(|value| !value.is_empty());
        let personal = workspace_id.unwrap_or(wsos::DEFAULT_WORKSPACE_ID);
        let workspace_memories = wsos::list_memories(&state, user, None, Some(personal));
        let owned_memories = wsos::list_memories(&state, user, None, workspace_id);
        let project_memories = match workspace_id {
            None => wsos::list_memories(&state, None, None, None)
                .into_iter()
                .filter(|item| wsos::record_workspace(item) != wsos::DEFAULT_WORKSPACE_ID)
                .collect(),
            Some(workspace) => wsos::list_memories(&state, user, None, Some(workspace)),
        };
        let snapshots = wsos::list_memory_snapshots(&state, workspace_id, 200);
        let conversations = conversations(conn)?;
        let scoped_conversations = scoped_conversations(&conversations, user_email, workspace_id);
        let db_path = data_dir.join(lattice_core::DB_FILE_NAME);
        let db_display = db_path.display().to_string();
        let (stats, index) = if graph_enabled {
            (
                kg::stats(conn, &db_display, None).ok(),
                kg::index_status(conn, &db_display).ok(),
            )
        } else {
            (None, None)
        };
        Ok(Self {
            state,
            workspace_memories,
            owned_memories,
            project_memories,
            snapshots,
            conversations,
            scoped_conversations,
            stats,
            index,
            workspace_bytes: file_size(&data_dir.join("workspace_os.json")),
            graph_bytes: file_size(&db_path),
        })
    }
}

fn file_size(path: &Path) -> i64 {
    std::fs::metadata(path)
        .map(|meta| meta.len() as i64)
        .unwrap_or(0)
}

pub fn conversations(conn: &Connection) -> Result<Vec<Value>, CoreError> {
    let history =
        crate::history::history(conn, None, None, &crate::history::HistoryScope::owner())?;
    let mut order: Vec<String> = Vec::new();
    let mut grouped: std::collections::HashMap<String, Vec<Value>> =
        std::collections::HashMap::new();
    for item in history {
        let id = item
            .get("conversation_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("legacy-previous-history")
            .to_string();
        if !grouped.contains_key(&id) {
            order.push(id.clone());
        }
        grouped.entry(id).or_default().push(item);
    }
    Ok(order
        .into_iter()
        .map(|id| {
            let messages = grouped.remove(&id).unwrap_or_default();
            let mut record = OrderedMap::new();
            record.insert("id", Value::String(id));
            record.insert("messages", Value::Array(messages));
            serde_json::to_value(&record).unwrap_or(Value::Null)
        })
        .collect())
}

pub fn scoped_conversations(
    conversations: &[Value],
    user_email: &str,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    if user_email.is_empty() {
        return conversations.to_vec();
    }
    let target = workspace_id.unwrap_or(wsos::DEFAULT_WORKSPACE_ID);
    let mut scoped = Vec::new();
    for conversation in conversations {
        let Some(messages) = conversation.get("messages").and_then(Value::as_array) else {
            continue;
        };
        let kept: Vec<Value> = messages
            .iter()
            .filter(|message| {
                message.is_object()
                    && message.get("user_email").and_then(Value::as_str) == Some(user_email)
                    && message
                        .get("workspace_id")
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty())
                        .unwrap_or(wsos::DEFAULT_WORKSPACE_ID)
                        == target
            })
            .cloned()
            .collect();
        if kept.is_empty() {
            continue;
        }
        let mut record = conversation.clone();
        if let Some(object) = record.as_object_mut() {
            object.insert("messages".to_string(), Value::Array(kept));
        }
        scoped.push(record);
    }
    scoped
}

pub(crate) fn sum_counts(counts: Option<&Value>) -> Option<i64> {
    counts
        .and_then(Value::as_object)
        .map(|map| map.values().filter_map(Value::as_i64).sum())
}

pub fn brain_readiness(
    memory_count: i64,
    concept_count: Option<i64>,
    relationship_count: Option<i64>,
    healthy_sources: i64,
) -> OrderedMap {
    let concepts = concept_count.unwrap_or(0).max(0);
    let relationships = relationship_count.unwrap_or(0).max(0);
    let memories = memory_count.max(0);
    let healthy = healthy_sources.max(0);
    let mut score = (memories * 12 + concepts * 8 + relationships * 4 + healthy * 3).min(100);
    let (state, depth, title_key, action_key) = if memories < 1 && concepts < 1 {
        score = score.max(12);
        ("quiet", 2, "brain.readiness.quiet", "brain.readiness.start")
    } else if concepts < 3 || relationships < 2 {
        score = score.max(38);
        let depth = if concepts < 3 { 3 } else { 4 };
        (
            "forming",
            depth,
            "brain.readiness.forming",
            "brain.readiness.grow",
        )
    } else {
        score = score.max(72);
        ("alive", 5, "brain.readiness.alive", "brain.readiness.map")
    };
    let mut signals = OrderedMap::new();
    signals.insert("memory_count", Value::from(memories));
    signals.insert("concept_count", Value::from(concepts));
    signals.insert("relationship_count", Value::from(relationships));
    signals.insert("healthy_sources", Value::from(healthy));
    let mut out = OrderedMap::new();
    out.insert("score", Value::from(score));
    out.insert("state", Value::String(state.to_string()));
    out.insert("depth", Value::from(depth));
    out.insert("title_key", Value::String(title_key.to_string()));
    out.insert("action_key", Value::String(action_key.to_string()));
    out.insert("signals", json(&signals));
    out.insert("source", Value::String("memory_service".to_string()));
    out
}

pub(crate) fn json(map: &OrderedMap) -> Value {
    serde_json::to_value(map).unwrap_or(Value::Null)
}

pub(crate) fn source_row(
    id: &str,
    label: &str,
    count: Value,
    size_bytes: i64,
    health: &str,
    detail: &str,
    edges: Option<Value>,
) -> OrderedMap {
    let mut row = OrderedMap::new();
    row.insert("id", Value::String(id.to_string()));
    row.insert("type", Value::String(id.to_string()));
    row.insert("label", Value::String(label.to_string()));
    row.insert("count", count);
    row.insert("size_bytes", Value::from(size_bytes));
    row.insert("health", Value::String(health.to_string()));
    row.insert("detail", Value::String(detail.to_string()));
    if let Some(edges) = edges {
        row.insert("edges", edges);
    }
    row
}
