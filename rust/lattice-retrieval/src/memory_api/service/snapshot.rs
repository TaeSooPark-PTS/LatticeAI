//! Snapshot of every memory-tier reading a request needs.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::collections::BTreeSet;
use std::path::Path;

use lattice_auth::OrderedMap;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use crate::memory_api::kg;
use crate::memory_api::shared::BrainState;
use crate::memory_api::wsos;

/// Everything the memory surfaces read, taken once.
pub struct Snapshot {
    /// The Workspace OS document.
    pub state: Value,
    /// `_workspace_memories(user, workspace_id or "personal")` — the reading
    /// `manager`, `inspect("workspace")` and the brief all take.
    pub workspace_memories: Vec<Value>,
    /// `_workspace_memories(user, workspace_id)` — **without** the `"personal"`
    /// default. `prune` and `compact` use this one, and the difference is the
    /// ownership guard: defaulting an unscoped delete to Personal would let a
    /// caller prune a workspace they never named.
    pub owned_memories: Vec<Value>,
    /// `manager`'s project tier, already branched on `workspace_id is None`.
    pub project_memories: Vec<Value>,
    /// `_snapshots(workspace_id)`.
    pub snapshots: Vec<Value>,
    /// `_conversations()` — every conversation, ungrouped by scope.
    pub conversations: Vec<Value>,
    /// `_scoped_conversations(user, workspace_id)`.
    pub scoped_conversations: Vec<Value>,
    /// `_kg_stats()`, or `None` when the graph is off or unreadable.
    pub stats: Option<OrderedMap>,
    /// `_kg_index()`, same rule.
    pub index: Option<OrderedMap>,
    /// `_file_size(data_dir/workspace_os.json)`.
    pub workspace_bytes: i64,
    /// `_file_size(data_dir/knowledge_graph.sqlite)` — also the conversation
    /// store's own `size_bytes()`, because it is the same file.
    pub graph_bytes: i64,
}

impl Snapshot {
    /// Take every reading one request needs, on one connection.
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
        // `_kg_stats` / `_kg_index` degrade to None rather than raising: both
        // tiers are optional and report themselves `unavailable` upstream.
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

/// `_conversations()` over the durable store: group `history()` by id.
///
/// `dict.setdefault` keeps first-seen order, and a row whose `conversation_id`
/// column is NULL is filed under the legacy bucket — the same id
/// `history::group_conversations` uses, so the two views agree.
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

/// `_scoped_conversations` — an anonymous caller sees every conversation.
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

/// `MemoryManagerMixin._brain_readiness`.
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
