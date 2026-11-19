//! Port of the durable conversation-history reads
//! (`lattice_brain/conversations.py` + `latticeai/runtime/history_runtime.py` +
//! `latticeai/services/chat_service.py` + `latticeai/api/chat_helpers.py`).
//!
//! Conversations live in the same SQLite file as the graph, in a table the
//! Python side owns; everything here is a `SELECT` and a re-shape.
//!
//! Three details decide the answers and are all easy to get subtly wrong:
//!
//! * `_scope_sql` treats a *legacy* row (NULL or empty user/workspace) as
//!   visible whenever the compatibility opt-in is on — which is the **default**
//!   here, the opposite of the graph layer's. An empty allowed set with the
//!   opt-in off is the one case that produces `1=0`: a caller who may read
//!   nothing gets nothing, not everything.
//! * `_row_to_item` copies the optional columns only when they are *truthy*, so
//!   an empty-string `source` disappears rather than arriving as `""`, and then
//!   merges `metadata_json` **flat** over the item — a metadata key named
//!   `role` would overwrite the role.
//! * `search_history` returns the **last** `limit` groups in insertion order,
//!   not the best or the most recent. That is what the product does today, so
//!   it is what this returns.

use std::collections::HashMap;

use lattice_core::pytext::{clean_text, safe_loads, truncate_chars};
use lattice_core::read::column_json;
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{Map, Value};

/// The bucket ungrouped (pre-conversation-id) messages are gathered into.
pub const LEGACY_CONVERSATION_ID: &str = "legacy-previous-history";
/// Its fixed title. Not derived from content — these messages predate the idea
/// of a conversation, so naming them after their first line would invent one.
pub const LEGACY_CONVERSATION_TITLE: &str = "이전 대화 기록";
/// The placeholder a conversation carries until a user message names it.
pub const UNTITLED_CONVERSATION: &str = "새 대화";

const MESSAGE_COLUMNS: &str = "conversation_id, role, content, user_email, user_nickname, \
                               source, timestamp, metadata_json, workspace_id, organization_id";

/// The optional columns `_row_to_item` copies across only when truthy.
const OPTIONAL_COLUMNS: [&str; 6] = [
    "user_email",
    "user_nickname",
    "source",
    "conversation_id",
    "workspace_id",
    "organization_id",
];

/// Who is asking, and how much legacy data they may see.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HistoryScope {
    /// Restrict to one identity's messages.
    pub user_email: Option<String>,
    /// `None` is no workspace filter at all; `Some(vec![])` is a caller who may
    /// read no workspace.
    pub allowed_workspaces: Option<Vec<String>>,
    /// Whether unattributed (NULL / empty) rows are visible. **Defaults to
    /// true** in the Python original.
    pub include_legacy_global: bool,
}

impl HistoryScope {
    /// The scope a loopback owner gets: everything, legacy rows included.
    pub fn owner() -> Self {
        Self {
            user_email: None,
            allowed_workspaces: None,
            include_legacy_global: true,
        }
    }

    /// `ConversationStore._scope_sql` — `(sql, params)`, empty when unscoped.
    fn to_sql(&self) -> (String, Vec<String>) {
        let mut clauses: Vec<String> = Vec::new();
        let mut params: Vec<String> = Vec::new();
        if let Some(user_email) = self.user_email.as_ref().filter(|value| !value.is_empty()) {
            clauses.push(
                if self.include_legacy_global {
                    "(user_email = ? OR user_email IS NULL OR user_email = '')"
                } else {
                    "user_email = ?"
                }
                .to_string(),
            );
            params.push(user_email.clone());
        }
        if let Some(allowed) = self.allowed_workspaces.as_ref() {
            let allowed: Vec<&String> = allowed.iter().filter(|item| !item.is_empty()).collect();
            if !allowed.is_empty() {
                let placeholders = vec!["?"; allowed.len()].join(",");
                // NULL/blank is default/personal visibility. A named tenant
                // stays strict unless the caller opted into the legacy pool.
                let sees_null = self.include_legacy_global
                    || allowed
                        .iter()
                        .any(|id| *id == lattice_core::DEFAULT_WORKSPACE_ID);
                clauses.push(if sees_null {
                    format!(
                        "(workspace_id IN ({placeholders}) OR workspace_id IS NULL \
                         OR workspace_id = '')"
                    )
                } else {
                    format!("workspace_id IN ({placeholders})")
                });
                params.extend(allowed.into_iter().cloned());
            } else if self.include_legacy_global {
                clauses.push("(workspace_id IS NULL OR workspace_id = '')".to_string());
            } else {
                // A caller with no readable workspace and no legacy opt-in reads
                // nothing — never "everything, because the filter was empty".
                clauses.push("1=0".to_string());
            }
        }
        (clauses.join(" AND "), params)
    }
}

/// `ConversationStore._row_to_item`.
fn row_to_item(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    let mut item = Map::new();
    for key in ["role", "content", "timestamp"] {
        item.insert(key.into(), column_json(row, key)?);
    }
    for key in OPTIONAL_COLUMNS {
        let value = column_json(row, key)?;
        if crate::shape::truthy(&value) {
            item.insert(key.into(), value);
        }
    }
    let metadata: Option<String> = row.get("metadata_json")?;
    // Flat merge, and it wins: this is `item.update(extra)`.
    for (key, value) in safe_loads(metadata.as_deref()) {
        item.insert(key, value);
    }
    Ok(Value::Object(item))
}

/// `ConversationStore.history` — chronological items, unbounded by default.
///
/// `conversation_id` is three-valued: `None` reads every conversation, an empty
/// string reads the rows that have none (`conversation_id IS NULL`), and a name
/// reads that one.
pub fn history(
    conn: &Connection,
    conversation_id: Option<&str>,
    limit: Option<i64>,
    scope: &HistoryScope,
) -> Result<Vec<Value>, CoreError> {
    let mut sql = format!("SELECT {MESSAGE_COLUMNS} FROM conversation_messages");
    let mut where_clauses: Vec<String> = Vec::new();
    let mut params: Vec<Value> = Vec::new();
    if let Some(conversation_id) = conversation_id {
        if conversation_id.is_empty() {
            where_clauses.push("conversation_id IS ?".to_string());
            params.push(Value::Null);
        } else {
            where_clauses.push("conversation_id = ?".to_string());
            params.push(Value::String(conversation_id.to_string()));
        }
    }
    let (scope_sql, scope_params) = scope.to_sql();
    if !scope_sql.is_empty() {
        where_clauses.push(scope_sql);
        params.extend(scope_params.into_iter().map(Value::String));
    }
    if !where_clauses.is_empty() {
        sql.push_str(&format!(" WHERE {}", where_clauses.join(" AND ")));
    }
    sql.push_str(" ORDER BY id ASC");
    if let Some(limit) = limit {
        sql.push_str(" LIMIT ?");
        params.push(Value::from(limit.max(1)));
    }

    let mut statement = match conn.prepare(&sql) {
        Ok(statement) => statement,
        Err(err) => {
            let error = CoreError::from(err);
            if error.is_missing_table("conversation_messages") {
                return Ok(Vec::new());
            }
            return Err(error);
        }
    };
    let bound: Vec<Box<dyn rusqlite::ToSql>> = params
        .iter()
        .map(|value| -> Box<dyn rusqlite::ToSql> {
            match value {
                Value::Null => Box::new(Option::<String>::None),
                Value::Number(number) => Box::new(number.as_i64().unwrap_or(0)),
                other => Box::new(crate::shape::py_str(other)),
            }
        })
        .collect();
    let refs: Vec<&dyn rusqlite::ToSql> = bound.iter().map(|value| value.as_ref()).collect();
    let rows = match statement.query_map(refs.as_slice(), row_to_item) {
        Ok(rows) => rows,
        Err(err) => {
            let error = CoreError::from(err);
            if error.is_missing_table("conversation_messages") {
                return Ok(Vec::new());
            }
            return Err(error);
        }
    };
    Ok(rows.filter_map(Result::ok).collect())
}

/// `history_runtime.conversation_title` — the first 48 characters, or a placeholder.
pub fn conversation_title(item: &Value) -> String {
    let content = item
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let collapsed = truncate_chars(&clean_text(content), 48);
    if collapsed.is_empty() {
        UNTITLED_CONVERSATION.to_string()
    } else {
        collapsed
    }
}

fn text_of(item: &Value, key: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// `history_runtime.group_history_conversations`.
pub fn group_conversations(history: &[Value]) -> Vec<Value> {
    struct Group {
        id: String,
        title: String,
        created_at: Value,
        updated_at: Value,
        message_count: i64,
        last_message: String,
        source: Value,
    }
    let mut order: Vec<Group> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for item in history {
        let raw = text_of(item, "conversation_id");
        let conv_id = if raw.is_empty() {
            LEGACY_CONVERSATION_ID.to_string()
        } else {
            raw
        };
        let position = match index.get(&conv_id) {
            Some(position) => *position,
            None => {
                order.push(Group {
                    title: if conv_id == LEGACY_CONVERSATION_ID {
                        LEGACY_CONVERSATION_TITLE.to_string()
                    } else {
                        conversation_title(item)
                    },
                    id: conv_id.clone(),
                    created_at: item.get("timestamp").cloned().unwrap_or(Value::Null),
                    updated_at: item.get("timestamp").cloned().unwrap_or(Value::Null),
                    message_count: 0,
                    last_message: String::new(),
                    source: item.get("source").cloned().unwrap_or(Value::Null),
                });
                let position = order.len() - 1;
                index.insert(conv_id.clone(), position);
                position
            }
        };
        let group = &mut order[position];
        group.message_count += 1;
        if let Some(stamp) = item.get("timestamp").filter(|v| crate::shape::truthy(v)) {
            group.updated_at = stamp.clone();
        }
        group.last_message = conversation_title(item);
        let untitled = group.title.is_empty() || group.title == UNTITLED_CONVERSATION;
        if conv_id != LEGACY_CONVERSATION_ID && text_of(item, "role") == "user" && untitled {
            group.title = conversation_title(item);
        }
    }
    // Stable, newest first, with a missing timestamp sorting as "".
    let mut grouped: Vec<Group> = order;
    grouped.sort_by(|a, b| {
        let key = |group: &Group| {
            group
                .updated_at
                .as_str()
                .map(str::to_string)
                .unwrap_or_default()
        };
        key(b).cmp(&key(a))
    });
    grouped
        .into_iter()
        .map(|group| {
            let mut item = Map::new();
            item.insert("id".into(), Value::String(group.id));
            item.insert("title".into(), Value::String(group.title));
            item.insert("created_at".into(), group.created_at);
            item.insert("updated_at".into(), group.updated_at);
            item.insert("message_count".into(), Value::from(group.message_count));
            item.insert("last_message".into(), Value::String(group.last_message));
            item.insert("source".into(), group.source);
            Value::Object(item)
        })
        .collect()
}

/// `history_runtime.get_conversation_messages` — load everything, then filter.
///
/// Deliberately not pushed into SQL: the scoped read is what decides visibility,
/// and filtering afterwards keeps one code path between "the whole history" and
/// "this conversation" instead of two that can disagree.
pub fn conversation_messages(history: &[Value], conversation_id: &str) -> Vec<Value> {
    history
        .iter()
        .filter(|item| {
            let id = text_of(item, "conversation_id");
            if conversation_id == LEGACY_CONVERSATION_ID {
                id.is_empty()
            } else {
                id == conversation_id
            }
        })
        .cloned()
        .collect()
}

/// `ChatService.search_history` — case-insensitive substring, grouped, last N.
pub fn search_history(history: &[Value], query: &str, limit: i64) -> Vec<Value> {
    let needle = query.trim().to_lowercase();
    if needle.is_empty() {
        return Vec::new();
    }
    let mut order: Vec<(String, String, Vec<Value>)> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for item in history {
        let content = text_of(item, "content").to_lowercase();
        if !content.contains(&needle) {
            continue;
        }
        let raw = text_of(item, "conversation_id");
        // Note: "legacy", not LEGACY_CONVERSATION_ID. The two buckets are spelled
        // differently in the product, and a port that unified them would change
        // what the search results say.
        let conversation_id = if raw.is_empty() {
            "legacy".to_string()
        } else {
            raw
        };
        let position = match index.get(&conversation_id) {
            Some(position) => *position,
            None => {
                order.push((
                    conversation_id.clone(),
                    conversation_title(item),
                    Vec::new(),
                ));
                let position = order.len() - 1;
                index.insert(conversation_id, position);
                position
            }
        };
        order[position].2.push(item.clone());
    }
    let keep = limit.max(1) as usize;
    let start = order.len().saturating_sub(keep);
    order
        .into_iter()
        .skip(start)
        .map(|(conversation_id, title, messages)| {
            let mut group = Map::new();
            group.insert("conversation_id".into(), Value::String(conversation_id));
            group.insert("title".into(), Value::String(title));
            group.insert("messages".into(), Value::Array(messages));
            Value::Object(group)
        })
        .collect()
}

/// `chat_helpers.pair_user_history` — one user's messages and the replies to them.
///
/// A bare `role == "assistant"` filter would leak every other user's replies
/// into this user's prompt, so an assistant row is admitted only when it follows
/// one of this user's messages and is not explicitly owned by someone else.
pub fn pair_user_history(history: &[Value], user_email: &str) -> Vec<Value> {
    let mut paired = Vec::new();
    let mut include_next_assistant = false;
    for item in history {
        if text_of(item, "role") == "assistant" {
            if include_next_assistant {
                let owner = text_of(item, "user_email");
                if !owner.is_empty() && owner != user_email {
                    continue;
                }
                paired.push(item.clone());
                include_next_assistant = false;
            }
        } else if text_of(item, "user_email") == user_email {
            paired.push(item.clone());
            include_next_assistant = true;
        } else {
            include_next_assistant = false;
        }
    }
    paired
}

/// What `recent_chat_context` narrows the history down to.
#[derive(Debug, Clone)]
pub struct RecentChatOptions {
    /// How many trailing messages to keep.
    pub limit: i64,
    /// Keep assistant replies that say an image is missing.
    pub include_image_missing_replies: bool,
    /// The identity whose exchange this is.
    pub user_email: Option<String>,
    /// One conversation, or every one.
    pub conversation_id: Option<String>,
    /// A workspace, where the sentinel `personal` means "no workspace".
    pub workspace_id: Option<String>,
}

impl Default for RecentChatOptions {
    fn default() -> Self {
        Self {
            limit: 10,
            include_image_missing_replies: true,
            user_email: None,
            conversation_id: None,
            workspace_id: None,
        }
    }
}

/// The scope `build_recent_chat_context` reads its history with.
///
/// An identified caller never sees legacy-global rows: they may belong to
/// somebody else, and an authenticated model prompt is the last place to guess.
/// An anonymous (single-user, loopback) caller reads everything.
pub fn recent_chat_scope(user_email: Option<&str>, workspace_id: Option<&str>) -> HistoryScope {
    match user_email.filter(|value| !value.is_empty()) {
        Some(user_email) => HistoryScope {
            user_email: Some(user_email.to_string()),
            allowed_workspaces: workspace_id.map(|value| vec![value.to_string()]),
            include_legacy_global: false,
        },
        None => HistoryScope::owner(),
    }
}

/// `chat_helpers.build_recent_chat_context` — read, then format.
pub fn recent_chat(conn: &Connection, options: &RecentChatOptions) -> Result<String, CoreError> {
    let scope = recent_chat_scope(
        options.user_email.as_deref(),
        options.workspace_id.as_deref(),
    );
    let rows = history(conn, None, None, &scope)?;
    Ok(recent_chat_context(&rows, options))
}

/// Where Python's `history[-limit:]` starts.
///
/// Not `len - limit`. `-0 == 0`, so a **zero** limit slices from the front and
/// keeps the whole transcript rather than none of it, and a negative limit
/// becomes a positive start that drops rows off the front. This looks like a
/// bug on both sides and is neither: it is what the live `/chat` prompt builder
/// does, so the port has to do it too or the two runtimes disagree exactly
/// where a caller passes an unvalidated limit through.
fn python_tail_start(len: usize, limit: i64) -> usize {
    if limit > 0 {
        len.saturating_sub(limit as usize)
    } else {
        ((-limit) as usize).min(len)
    }
}

/// `chat_helpers.build_recent_chat_context`'s narrowing and formatting half.
pub fn recent_chat_context(history: &[Value], options: &RecentChatOptions) -> String {
    let mut rows: Vec<Value> = history.to_vec();
    if let Some(workspace_id) = options.workspace_id.as_ref() {
        // `str(item.get("workspace_id") or "personal")`: a row with no workspace
        // belongs to the literal workspace named "personal".
        rows.retain(|item| {
            let raw = text_of(item, "workspace_id");
            let effective = if raw.is_empty() {
                "personal"
            } else {
                raw.as_str()
            };
            effective == workspace_id
        });
    }
    if let Some(conversation_id) = options
        .conversation_id
        .as_ref()
        .filter(|value| !value.is_empty())
    {
        rows.retain(|item| &text_of(item, "conversation_id") == conversation_id);
    }
    if let Some(user_email) = options.user_email.as_ref().filter(|v| !v.is_empty()) {
        rows = pair_user_history(&rows, user_email);
    }
    let start = python_tail_start(rows.len(), options.limit);
    let mut lines: Vec<String> = Vec::new();
    for item in rows.iter().skip(start) {
        let role = item
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("user")
            .to_string();
        let content = item.get("content").and_then(Value::as_str).unwrap_or("");
        if !options.include_image_missing_replies
            && role == "assistant"
            && content.contains("이미지")
            && ["업로드", "제공", "올려"]
                .iter()
                .any(|w| content.contains(w))
        {
            continue;
        }
        let source = text_of(item, "source");
        let label = if source.is_empty() {
            role
        } else {
            format!("{role} ({source})")
        };
        lines.push(format!("{label}: {content}"));
    }
    lines.join("\n")
}

#[cfg(test)]
#[path = "history_tests.rs"]
mod tests;
