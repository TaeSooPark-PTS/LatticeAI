//! The six conversation-history routes.
//!
//! Reads are `lattice_retrieval::history` — the same lanes `/rust/history*`
//! already answers, proven against goldens — reshaped into the HTTP bodies
//! `api/chat_history.py` returns. Writes (`DELETE /history`,
//! `DELETE /history/conversations/{id}`) are native: `conversation_messages` is
//! a **RUST_PLATFORM** table in WP-I1's ownership map, so Rust is its writer.
//! The one write that is *not* native is the chat turn itself, which ends in a
//! graph write and goes over WP-I6's seam.
//!
//! ## Scope
//!
//! `ChatService.history_scope` is three lines and every one of them matters:
//!
//! ```text
//! scoped_user = user_email if require_auth else None
//! allowed     = allowed_workspaces_for(scoped_user) if require_auth and scoped_user else None
//! include_legacy_global = not require_auth
//! ```
//!
//! So a single-user loopback install (authentication off) reads **everything,
//! legacy rows included**, and a multi-user install reads one identity's rows
//! and no unattributed ones. Getting this backwards is how one user's history
//! ends up in another's prompt.
//!
//! ## Key order
//!
//! Bodies this module builds by hand use [`lattice_auth::OrderedMap`], so they
//! are byte-identical to Python's dict order. History *items* come out of
//! `lattice-retrieval` as `serde_json::Map`, which is a `BTreeMap` (the
//! workspace deliberately does not enable `preserve_order`), so their keys are
//! re-emitted in the store's declared column order here — the trailing keys a
//! row picked up from `metadata_json` stay sorted, which is what `/rust/history`
//! has always answered.

use std::collections::BTreeSet;

use axum::extract::{Path, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::messages::{self, LANGUAGE_HEADER};
use lattice_retrieval::history::{
    conversation_messages, conversation_title, group_conversations, history as read_history,
    search_history, HistoryScope,
};
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::state::ChatState;

/// The order `ConversationStore._row_to_item` inserts its keys in.
const ITEM_KEY_ORDER: [&str; 9] = [
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

/// `ChatService.search_history`'s default group cap.
const SEARCH_LIMIT: i64 = 30;

/// `conversations.clear_all`'s ceiling on `keep_last`.
const MAX_KEEP_LAST: i64 = 20;

/// Re-emit one history item in the store's declared column order.
fn ordered_item(item: &Value) -> Value {
    let Some(entries) = item.as_object() else {
        return item.clone();
    };
    let mut ordered = OrderedMap::new();
    for key in ITEM_KEY_ORDER {
        if let Some(value) = entries.get(key) {
            ordered.insert(key, value.clone());
        }
    }
    for (key, value) in entries {
        if !ITEM_KEY_ORDER.contains(&key.as_str()) {
            ordered.insert(key.clone(), value.clone());
        }
    }
    // Serialize through OrderedMap so keys stay in insertion order. Going via
    // `serde_json::Value` would sort them (`Map` is a BTreeMap).
    serde_json::from_str(&serde_json::to_string(&ordered).unwrap_or_else(|_| "null".into()))
        .unwrap_or_else(|_| item.clone())
}

fn ordered_items(items: &[Value]) -> Vec<Value> {
    items.iter().map(ordered_item).collect()
}

/// `ChatService.history_scope` — who this caller may read.
pub fn history_scope(state: &ChatState, current_user: &str) -> HistoryScope {
    let require_auth = state.auth.effective_require_auth();
    let scoped_user = if require_auth && !current_user.is_empty() {
        Some(current_user.to_string())
    } else {
        None
    };
    let allowed = scoped_user
        .as_deref()
        .and_then(|user| state.allowed_workspaces_for(user));
    HistoryScope {
        user_email: scoped_user,
        allowed_workspaces: allowed,
        include_legacy_global: !require_auth,
    }
}

/// `_scope_sql` for the **write** side.
///
/// `lattice_retrieval::history::HistoryScope` builds the identical clause for
/// its reads and keeps it private, so the delete statements build their own.
/// Both are the same twenty lines of `conversations._scope_sql`; if one moves,
/// the other has to, and a test below pins the branch that matters most (an
/// empty allowed set with no legacy opt-in deletes **nothing**).
fn scope_sql(scope: &HistoryScope) -> (String, Vec<String>) {
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<String> = Vec::new();
    if let Some(user_email) = scope.user_email.as_ref().filter(|value| !value.is_empty()) {
        clauses.push(
            if scope.include_legacy_global {
                "(user_email = ? OR user_email IS NULL OR user_email = '')"
            } else {
                "user_email = ?"
            }
            .to_string(),
        );
        params.push(user_email.clone());
    }
    if let Some(allowed) = scope.allowed_workspaces.as_ref() {
        let allowed: Vec<&String> = allowed.iter().filter(|item| !item.is_empty()).collect();
        if !allowed.is_empty() {
            let placeholders = vec!["?"; allowed.len()].join(",");
            clauses.push(if scope.include_legacy_global {
                format!(
                    "(workspace_id IN ({placeholders}) OR workspace_id IS NULL \
                     OR workspace_id = '')"
                )
            } else {
                format!("workspace_id IN ({placeholders})")
            });
            params.extend(allowed.into_iter().cloned());
        } else if scope.include_legacy_global {
            clauses.push("(workspace_id IS NULL OR workspace_id = '')".to_string());
        } else {
            clauses.push("1=0".to_string());
        }
    }
    (clauses.join(" AND "), params)
}

fn count_rows(conn: &Connection) -> i64 {
    conn.query_row("SELECT COUNT(*) FROM conversation_messages", [], |row| {
        row.get(0)
    })
    .unwrap_or(0)
}

fn as_sql(params: &[String]) -> Vec<&dyn rusqlite::ToSql> {
    params
        .iter()
        .map(|value| value as &dyn rusqlite::ToSql)
        .collect()
}

/// `ConversationStore.clear_all`.
pub fn clear_all(conn: &Connection, keep_last: i64, scope: &HistoryScope) -> Value {
    let keep_last = keep_last.clamp(0, MAX_KEEP_LAST);
    let total = count_rows(conn);
    let (scope_sql, params) = scope_sql(scope);
    let scope_where = if scope_sql.is_empty() {
        String::new()
    } else {
        format!(" WHERE {scope_sql}")
    };
    if keep_last > 0 {
        let sql = format!(
            "DELETE FROM conversation_messages
             WHERE id IN (SELECT id FROM conversation_messages{scope_where})
               AND id NOT IN (
                 SELECT id FROM conversation_messages{scope_where}
                 ORDER BY id DESC LIMIT ?)"
        );
        let mut bound: Vec<String> = params.clone();
        bound.extend(params.clone());
        bound.push(keep_last.to_string());
        let _ = conn.execute(&sql, as_sql(&bound).as_slice());
    } else {
        let sql = format!("DELETE FROM conversation_messages{scope_where}");
        let _ = conn.execute(&sql, as_sql(&params).as_slice());
    }
    let kept = count_rows(conn);
    let mut body = OrderedMap::new();
    body.insert("status", json!("cleared"));
    body.insert("removed", json!((total - kept).max(0)));
    body.insert("kept", json!(kept));
    serde_json::to_value(body).unwrap_or(Value::Null)
}

/// `ConversationStore.clear_conversation`.
pub fn clear_conversation(
    conn: &Connection,
    conversation_id: &str,
    started_at: Option<&str>,
    scope: &HistoryScope,
) -> Value {
    let total = count_rows(conn);
    let (scope_sql, params) = scope_sql(scope);
    let scoped = if scope_sql.is_empty() {
        String::new()
    } else {
        format!(" AND {scope_sql}")
    };
    if conversation_id == lattice_retrieval::history::LEGACY_CONVERSATION_ID {
        let sql =
            format!("DELETE FROM conversation_messages WHERE conversation_id IS NULL{scoped}");
        let _ = conn.execute(&sql, as_sql(&params).as_slice());
    } else {
        let sql = format!("DELETE FROM conversation_messages WHERE conversation_id = ?{scoped}");
        let mut bound = vec![conversation_id.to_string()];
        bound.extend(params.clone());
        let _ = conn.execute(&sql, as_sql(&bound).as_slice());
        if let Some(started_at) = started_at.filter(|value| !value.is_empty()) {
            let sql = format!(
                "DELETE FROM conversation_messages \
                 WHERE conversation_id IS NULL AND timestamp >= ?{scoped}"
            );
            let mut bound = vec![started_at.to_string()];
            bound.extend(params.clone());
            let _ = conn.execute(&sql, as_sql(&bound).as_slice());
        }
    }
    let kept = count_rows(conn);
    let mut body = OrderedMap::new();
    body.insert("status", json!("cleared"));
    body.insert("conversation_id", json!(conversation_id));
    body.insert("removed", json!((total - kept).max(0)));
    body.insert("kept", json!(kept));
    serde_json::to_value(body).unwrap_or(Value::Null)
}

impl ChatState {
    /// `_history_allowed_workspaces_for` — the readable set, or `None`.
    pub(crate) fn allowed_workspaces_for(&self, user_email: &str) -> Option<Vec<String>> {
        let resolver = self.workspace.as_ref()?;
        // `readable_workspaces` has no place on `WorkspaceResolver`, whose two
        // methods answer "may this scope be used?". The read scope for an
        // unspecified request is the same question, so it is asked that way; a
        // resolver that refuses answers with the empty set, which is Python's
        // `except: return set()` branch.
        match resolver.resolve_read_scope(None, Some(user_email)) {
            Ok(Some(workspace)) => Some(vec![workspace]),
            Ok(None) => None,
            Err(_) => Some(Vec::new()),
        }
    }

    /// Open the graph database read-only, when there is one.
    pub(crate) fn read_conn(&self) -> Option<Connection> {
        lattice_core::open_read_only(self.config.graph()?).ok()
    }

    /// Open the graph database for writing, when there is one.
    pub(crate) fn write_conn(&self) -> Option<Connection> {
        lattice_core::db::open_read_write(self.config.graph()?).ok()
    }
}

fn language_of(headers: &HeaderMap) -> &'static str {
    messages::resolve_language(
        headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok()),
        headers
            .get(axum::http::header::ACCEPT_LANGUAGE)
            .and_then(|v| v.to_str().ok()),
    )
}

/// One JSON body, rendered the way Starlette's `JSONResponse` renders it.
pub(crate) fn json_body(status: StatusCode, value: &Value) -> Response {
    lattice_auth::response::json_response(
        status,
        &serde_json::to_string(value).unwrap_or_else(|_| "null".into()),
        None,
    )
}

pub(crate) fn error_body(
    status: u16,
    id: &str,
    headers: &HeaderMap,
    args: &[(&str, &str)],
) -> Response {
    let error = messages::http_error(status, id, language_of(headers), args);
    let (status, body) = error.into_response_parts();
    json_body(
        StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        &body,
    )
}

/// FastAPI's 422 for a missing required query parameter.
fn missing_query(name: &str) -> Response {
    let mut entry = OrderedMap::new();
    entry.insert("type", json!("missing"));
    entry.insert("loc", json!(["query", name]));
    entry.insert("msg", json!("Field required"));
    entry.insert("input", Value::Null);
    let rendered = serde_json::to_string(&entry).unwrap_or_default();
    lattice_auth::response::json_response(
        StatusCode::UNPROCESSABLE_ENTITY,
        &format!("{{\"detail\":[{rendered}]}}"),
        None,
    )
}

/// One query parameter's first value, percent-decoded.
fn query_param(raw: Option<&str>, name: &str) -> Option<String> {
    let raw = raw?;
    raw.split('&')
        .filter_map(|pair| pair.split_once('=').or(Some((pair, ""))))
        .find(|(key, _)| percent_decode(key) == name)
        .map(|(_, value)| percent_decode(value))
}

/// `application/x-www-form-urlencoded` decoding, `+` included.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0usize;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    Err(_) => {
                        out.push(bytes[index]);
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn read_all(state: &ChatState, scope: &HistoryScope) -> Vec<Value> {
    let Some(conn) = state.read_conn() else {
        // `get_history` swallows its own failures and answers `[]`; a gateway
        // with no Brain file is the same situation.
        return Vec::new();
    };
    read_history(&conn, None, None, scope).unwrap_or_default()
}

/// `GET /history`.
pub async fn fetch_history(State(state): State<ChatState>, headers: HeaderMap) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = history_scope(&state, &identity.email);
    let items = ordered_items(&read_all(&state, &scope));
    json_body(StatusCode::OK, &Value::Array(items))
}

/// `GET /history/conversations`.
pub async fn fetch_conversations(State(state): State<ChatState>, headers: HeaderMap) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = history_scope(&state, &identity.email);
    let grouped = group_conversations(&read_all(&state, &scope));
    json_body(StatusCode::OK, &Value::Array(grouped))
}

/// `GET /history/conversations/{conversation_id:path}`.
pub async fn fetch_conversation(
    State(state): State<ChatState>,
    Path(conversation_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = history_scope(&state, &identity.email);
    let messages = conversation_messages(&read_all(&state, &scope), &conversation_id);
    if messages.is_empty() {
        return error_body(404, "chat.conversation_not_found", &headers, &[]);
    }
    let mut body = OrderedMap::new();
    body.insert("id", json!(conversation_id));
    body.insert("messages", Value::Array(ordered_items(&messages)));
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

/// `DELETE /history/conversations/{conversation_id:path}`.
pub async fn delete_conversation(
    State(state): State<ChatState>,
    Path(conversation_id): Path<String>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = history_scope(&state, &identity.email);
    let started_at = query_param(query.as_deref(), "started_at");
    let result = match state.write_conn() {
        Some(conn) => clear_conversation(&conn, &conversation_id, started_at.as_deref(), &scope),
        None => json!({
            "status": "cleared", "conversation_id": conversation_id,
            "removed": 0, "kept": 0,
        }),
    };
    state.audit(
        "conversation_delete",
        &json!({
            "user_email": identity.email,
            "conversation_id": conversation_id,
            "started_at": started_at,
            "removed": result.get("removed").cloned().unwrap_or(json!(0)),
            "kept": result.get("kept").cloned().unwrap_or(json!(0)),
        }),
    );
    json_body(StatusCode::OK, &result)
}

/// `DELETE /history?keep_last=`.
pub async fn delete_history(
    State(state): State<ChatState>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    // `keep_last: int = 0` — FastAPI's own 422 for a value it cannot coerce.
    let keep_last = match query_param(query.as_deref(), "keep_last") {
        Some(raw) => match raw.trim().parse::<i64>() {
            Ok(value) => value,
            Err(_) => {
                let mut entry = OrderedMap::new();
                entry.insert("type", json!("int_parsing"));
                entry.insert("loc", json!(["query", "keep_last"]));
                entry.insert(
                    "msg",
                    json!("Input should be a valid integer, unable to parse string as an integer"),
                );
                entry.insert("input", json!(raw));
                let rendered = serde_json::to_string(&entry).unwrap_or_default();
                return lattice_auth::response::json_response(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    &format!("{{\"detail\":[{rendered}]}}"),
                    None,
                );
            }
        },
        None => 0,
    };
    let scope = history_scope(&state, &identity.email);
    let result = match state.write_conn() {
        Some(conn) => clear_all(&conn, keep_last, &scope),
        None => json!({"status": "cleared", "removed": 0, "kept": 0}),
    };
    state.audit(
        "history_delete",
        &json!({
            "user_email": identity.email,
            "keep_last": keep_last,
            "removed": result.get("removed").cloned().unwrap_or(json!(0)),
            "kept": result.get("kept").cloned().unwrap_or(json!(0)),
        }),
    );
    json_body(StatusCode::OK, &result)
}

/// `GET /history/search?q=`.
pub async fn search(
    State(state): State<ChatState>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
) -> Response {
    // `q: str` is required and has no default, so its absence is a 422 that
    // never reaches the handler — and therefore never reaches `require_user`.
    let Some(needle) = query_param(query.as_deref(), "q") else {
        return missing_query("q");
    };
    let identity = match state.auth.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let scope = history_scope(&state, &identity.email);
    let rows = read_all(&state, &scope);
    let mut results: Vec<Value> = Vec::new();
    for group in search_history(&rows, &needle, SEARCH_LIMIT) {
        let mut ordered = OrderedMap::new();
        ordered.insert(
            "conversation_id",
            group.get("conversation_id").cloned().unwrap_or(Value::Null),
        );
        ordered.insert("title", group.get("title").cloned().unwrap_or(Value::Null));
        ordered.insert(
            "messages",
            Value::Array(ordered_items(
                group
                    .get("messages")
                    .and_then(Value::as_array)
                    .unwrap_or(&Vec::new()),
            )),
        );
        results.push(serde_json::to_value(ordered).unwrap_or(Value::Null));
    }
    let mut body = OrderedMap::new();
    body.insert("results", Value::Array(results));
    body.insert("query", json!(needle));
    json_body(
        StatusCode::OK,
        &serde_json::to_value(body).unwrap_or(Value::Null),
    )
}

/// `history_runtime.conversation_title`, re-exported for the pipeline.
pub fn title_of(item: &Value) -> String {
    conversation_title(item)
}

/// The workspace set a scope names, as the retrieval engines want it.
pub fn allowed_set(workspace_id: Option<&str>) -> Option<BTreeSet<String>> {
    workspace_id
        .filter(|id| !id.is_empty())
        .map(|id| [id.to_string()].into_iter().collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE conversation_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
               conversation_id TEXT, role TEXT, content TEXT, user_email TEXT,
               user_nickname TEXT, source TEXT, timestamp TEXT,
               metadata_json TEXT NOT NULL DEFAULT '{}', workspace_id TEXT,
               organization_id TEXT);
             INSERT INTO conversation_messages
               (conversation_id, role, content, user_email, timestamp, workspace_id) VALUES
               ('c1','user','one','a@x','2026-01-01','w1'),
               ('c1','assistant','two','a@x','2026-01-02','w1'),
               (NULL,'user','legacy',NULL,'2026-01-03',NULL),
               ('c2','user','three','b@x','2026-01-04','w2');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn items_are_re_emitted_in_the_stores_column_order() {
        let item = json!({
            "workspace_id": "w", "content": "c", "role": "user",
            "timestamp": "t", "trace": "extra",
        });
        let rendered = ordered_item(&item);
        assert_eq!(rendered["role"], "user");
        assert_eq!(rendered["content"], "c");
        assert_eq!(rendered["workspace_id"], "w");
        assert_eq!(rendered["trace"], "extra");
        assert_eq!(ordered_item(&json!("scalar")), json!("scalar"));
    }

    #[test]
    fn clearing_everything_reports_removed_and_kept() {
        let (_dir, conn) = store();
        let cleared = clear_all(&conn, 0, &HistoryScope::owner());
        assert_eq!(cleared["status"], "cleared");
        assert_eq!(cleared["removed"], 4);
        assert_eq!(cleared["kept"], 0);
    }

    #[test]
    fn keep_last_keeps_the_newest_rows_and_is_capped() {
        let (_dir, conn) = store();
        let cleared = clear_all(&conn, 2, &HistoryScope::owner());
        assert_eq!(cleared["removed"], 2);
        assert_eq!(cleared["kept"], 2);
        let (_dir, conn) = store();
        // 99 clamps to 20, which is more rows than exist: nothing goes.
        assert_eq!(clear_all(&conn, 99, &HistoryScope::owner())["removed"], 0);
        let (_dir, conn) = store();
        assert_eq!(clear_all(&conn, -5, &HistoryScope::owner())["removed"], 4);
    }

    #[test]
    fn a_scoped_clear_only_removes_what_the_caller_can_read() {
        let (_dir, conn) = store();
        let scope = HistoryScope {
            user_email: Some("a@x".into()),
            allowed_workspaces: None,
            include_legacy_global: false,
        };
        let cleared = clear_all(&conn, 0, &scope);
        assert_eq!(cleared["removed"], 2);
        assert_eq!(cleared["kept"], 2);
    }

    #[test]
    fn a_caller_with_no_readable_workspace_deletes_nothing() {
        let (_dir, conn) = store();
        let scope = HistoryScope {
            user_email: None,
            allowed_workspaces: Some(Vec::new()),
            include_legacy_global: false,
        };
        assert_eq!(clear_all(&conn, 0, &scope)["removed"], 0);
        assert_eq!(clear_conversation(&conn, "c1", None, &scope)["removed"], 0);
    }

    #[test]
    fn clearing_one_conversation_leaves_the_others() {
        let (_dir, conn) = store();
        let cleared = clear_conversation(&conn, "c1", None, &HistoryScope::owner());
        assert_eq!(cleared["conversation_id"], "c1");
        assert_eq!(cleared["removed"], 2);
        assert_eq!(cleared["kept"], 2);
    }

    #[test]
    fn the_legacy_bucket_and_started_at_target_unattributed_rows() {
        let (_dir, conn) = store();
        let cleared = clear_conversation(
            &conn,
            lattice_retrieval::history::LEGACY_CONVERSATION_ID,
            None,
            &HistoryScope::owner(),
        );
        assert_eq!(cleared["removed"], 1);
        let (_dir, conn) = store();
        // started_at sweeps the unattributed rows from that instant on.
        let cleared = clear_conversation(&conn, "c1", Some("2026-01-01"), &HistoryScope::owner());
        assert_eq!(cleared["removed"], 3);
        let (_dir, conn) = store();
        assert_eq!(
            clear_conversation(&conn, "c1", Some(""), &HistoryScope::owner())["removed"],
            2,
            "an empty started_at is no started_at"
        );
    }

    #[test]
    fn query_parameters_are_percent_decoded() {
        assert_eq!(
            query_param(Some("q=%ED%8E%98%EC%9D%B4%EC%A7%80"), "q").as_deref(),
            Some("페이지")
        );
        assert_eq!(query_param(Some("a=1&q=x"), "q").as_deref(), Some("x"));
        assert_eq!(query_param(Some("q"), "q").as_deref(), Some(""));
        assert_eq!(query_param(Some("a=1"), "q"), None);
        assert_eq!(query_param(None, "q"), None);
        assert_eq!(query_param(Some("q=a+b"), "q").as_deref(), Some("a b"));
        assert_eq!(query_param(Some("q=%zz"), "q").as_deref(), Some("%zz"));
        assert_eq!(query_param(Some("q=%f"), "q").as_deref(), Some("%f"));
    }

    #[test]
    fn a_missing_required_query_parameter_is_the_fastapi_422() {
        let refusal = missing_query("q");
        assert_eq!(refusal.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }

    #[test]
    fn allowed_set_names_one_workspace_or_none() {
        assert_eq!(allowed_set(None), None);
        assert_eq!(allowed_set(Some("")), None);
        assert_eq!(
            allowed_set(Some("w1")),
            Some(["w1".to_string()].into_iter().collect())
        );
        assert_eq!(title_of(&json!({"content": " a  b "})), "a b");
    }
}
