use std::path::Path;
use std::sync::Arc;

use lattice_auth::WorkspaceResolver;
use lattice_core::db::Store;
use lattice_core::CoreError;
use serde_json::{Map, Value};

pub const DEFAULT_WORKSPACE_ID: &str = "personal";

fn role_permissions(role: &str) -> &'static [&'static str] {
    match role {
        "owner" | "admin" => &["read", "write", "manage_members", "manage_workspace"],
        "member" => &["read", "write"],
        "viewer" => &["read"],
        _ => &[],
    }
}

pub fn load(store: &Arc<Store>, data_dir: &Path) -> Value {
    store
        .with_read_conn(|conn| Ok(load_from(conn, data_dir)))
        .unwrap_or_else(|_| Value::Object(Map::new()))
}

pub fn load_from(conn: &rusqlite::Connection, data_dir: &Path) -> Value {
    let from_sqlite = conn
        .query_row(
            "SELECT state_json FROM workspace_os_state WHERE id='current'",
            [],
            |row| row.get::<_, Option<String>>(0),
        )
        .ok()
        .flatten()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .filter(Value::is_object);
    from_sqlite
        .or_else(|| {
            std::fs::read_to_string(data_dir.join("workspace_os.json"))
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok())
                .filter(Value::is_object)
        })
        .unwrap_or_else(|| Value::Object(Map::new()))
}

pub fn memories(state: &Value) -> Vec<Value> {
    listify(state.get("memories"))
}

pub fn memory_snapshots(state: &Value) -> Vec<Value> {
    listify(state.get("memory_snapshots"))
}

fn listify(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items.clone(),
        _ => Vec::new(),
    }
}

pub fn active_workspace(state: &Value) -> String {
    let active = state
        .get("active_workspace")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    if workspace(state, active).is_some() {
        return active.to_string();
    }
    DEFAULT_WORKSPACE_ID.to_string()
}

pub fn workspace(state: &Value, workspace_id: &str) -> Option<Value> {
    let found = state
        .get("workspaces")
        .and_then(Value::as_object)
        .and_then(|map| map.get(workspace_id))
        .filter(|value| value.is_object())
        .cloned();
    match found {
        Some(record) => Some(record),
        None if workspace_id == DEFAULT_WORKSPACE_ID => {
            Some(serde_json::json!({"workspace_id": DEFAULT_WORKSPACE_ID, "type": "personal"}))
        }
        None => None,
    }
}

pub fn workspace_ids(state: &Value) -> Vec<String> {
    let mut ids: Vec<String> = state
        .get("workspaces")
        .and_then(Value::as_object)
        .map(|map| map.keys().cloned().collect())
        .unwrap_or_default();
    if !ids.iter().any(|id| id == DEFAULT_WORKSPACE_ID) {
        ids.push(DEFAULT_WORKSPACE_ID.to_string());
    }
    ids
}

pub fn member_role(workspace: &Value, user_id: Option<&str>) -> Option<String> {
    if workspace.get("type").and_then(Value::as_str) == Some("personal") {
        return Some("owner".to_string());
    }
    let owner = workspace
        .get("owner_user_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    if owner.is_none() && user_id.is_none() {
        return Some("owner".to_string());
    }
    if let (Some(user), Some(owner)) = (user_id, owner) {
        if user == owner {
            return Some("owner".to_string());
        }
    }
    for member in listify(workspace.get("members")) {
        if member.get("user_id").and_then(Value::as_str) == user_id {
            return member
                .get("role")
                .and_then(Value::as_str)
                .map(str::to_string);
        }
    }
    None
}

pub fn has_permission(
    state: &Value,
    workspace_id: &str,
    user_id: Option<&str>,
    permission: &str,
) -> bool {
    let Some(record) = workspace(state, workspace_id) else {
        return false;
    };
    let Some(role) = member_role(&record, user_id) else {
        return false;
    };
    role_permissions(&role).contains(&permission)
}

pub fn record_workspace(record: &Value) -> String {
    record
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID)
        .to_string()
}

pub fn scoped(records: Vec<Value>, workspace_id: Option<&str>) -> Vec<Value> {
    let Some(target) = workspace_id.filter(|value| !value.is_empty()) else {
        return records;
    };
    records
        .into_iter()
        .filter(|record| record_workspace(record) == target)
        .collect()
}

pub fn list_memories(
    state: &Value,
    user_email: Option<&str>,
    kind: Option<&str>,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    let mut items = scoped(memories(state), workspace_id);
    if let Some(email) = user_email.filter(|value| !value.is_empty()) {
        items.retain(|item| match item.get("user_email") {
            None | Some(Value::Null) => true,
            Some(Value::String(owner)) => owner == email,
            Some(_) => false,
        });
    }
    if let Some(kind) = kind {
        items.retain(|item| item.get("kind").and_then(Value::as_str) == Some(kind));
    }
    items.reverse();
    items
}

pub fn search_memories(
    state: &Value,
    query: &str,
    user_email: Option<&str>,
    limit: i64,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    let needle = query.to_lowercase();
    let needle = needle.trim();
    let mut items = list_memories(state, user_email, None, workspace_id);
    if !needle.is_empty() {
        items.retain(|item| {
            let content = item
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            let tags = tag_text(item).to_lowercase();
            let kind = item
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_lowercase();
            content.contains(needle) || tags.contains(needle) || kind.contains(needle)
        });
    }
    let cap = limit.clamp(1, 100) as usize;
    items.truncate(cap);
    items
}

pub fn tag_text(item: &Value) -> String {
    match item.get("tags") {
        Some(Value::Array(tags)) => tags
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(" "),
        _ => String::new(),
    }
}

pub fn list_memory_snapshots(state: &Value, workspace_id: Option<&str>, limit: i64) -> Vec<Value> {
    let mut items = scoped(memory_snapshots(state), workspace_id);
    let cap = limit.clamp(1, 200) as usize;
    if items.len() > cap {
        items = items.split_off(items.len() - cap);
    }
    items.reverse();
    items
}

pub trait StateWriter: Send + Sync {
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String>;

    fn record_event(
        &self,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) -> Result<(), String>;
}

pub const REVIEW_TIMELINE_AREA: &str = "review";
pub const REVIEW_ITEM_CREATED_EVENT: &str = "review_item_created";
pub const REVIEW_ITEM_UPDATED_EVENT: &str = "review_item_updated";

pub fn review_snooze_expiry_secs(until: &str) -> Option<f64> {
    let parsed = crate::chronicle_api::pytime::parse_iso(until)?;
    let naive = parsed.naive;
    let days = lattice_core::pytext::days_from_civil(naive.year as i64, naive.month, naive.day);
    let local = days as f64 * 86_400.0
        + naive.hour as f64 * 3_600.0
        + naive.minute as f64 * 60.0
        + naive.second as f64;
    Some(local - parsed.offset_secs.unwrap_or(0) as f64)
}

pub fn review_effective_status(item: &Value, now_utc_secs: f64) -> String {
    let status = item
        .get("status")
        .and_then(Value::as_str)
        .filter(|status| !status.is_empty())
        .unwrap_or("pending");
    if status != "snoozed" {
        return status.to_string();
    }
    let expiry = item
        .get("snoozed_until")
        .and_then(Value::as_str)
        .and_then(review_snooze_expiry_secs);
    match expiry {
        Some(until) if until <= now_utc_secs => "pending".into(),
        _ => "snoozed".into(),
    }
}

pub fn record_review_event(item: &Value, action: &str) {
    let Some(writer) = state_writer() else {
        return;
    };
    let workspace = item
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID)
        .to_string();
    let event_type = if action == "create" {
        REVIEW_ITEM_CREATED_EVENT
    } else {
        REVIEW_ITEM_UPDATED_EVENT
    };
    let payload = serde_json::json!({
        "item_id": item.get("id").cloned().unwrap_or(Value::Null),
        "action": action,
        "status": item.get("status").cloned().unwrap_or(Value::Null),
        "source": item.get("source").cloned().unwrap_or(Value::Null),
        "workspace_id": workspace,
    });
    let _ = writer.record_event(
        REVIEW_TIMELINE_AREA,
        event_type,
        payload,
        Some(workspace.as_str()),
    );
}

static STATE_WRITER: std::sync::OnceLock<Arc<dyn StateWriter>> = std::sync::OnceLock::new();

pub fn install_state_writer(writer: Arc<dyn StateWriter>) -> bool {
    STATE_WRITER.set(writer).is_ok()
}

pub fn state_writer() -> Option<&'static Arc<dyn StateWriter>> {
    STATE_WRITER.get()
}

pub fn mutate_state(
    store: &Arc<Store>,
    data_dir: &Path,
    mut body: impl FnMut(&mut Value),
) -> Result<(), CoreError> {
    if let Some(writer) = state_writer() {
        return writer
            .mutate(&mut body)
            .map_err(|error| CoreError::Runtime(format!("workspace state write failed: {error}")));
    }
    let mut state = load(store, data_dir);
    body(&mut state);
    write_through(store, data_dir, &state)
}

pub fn delete_memory(
    store: &Arc<Store>,
    data_dir: &Path,
    memory_id: &str,
) -> Result<bool, CoreError> {
    let mut removed = false;
    mutate_state(store, data_dir, |state| {
        let rows = memories(state);
        if !rows
            .iter()
            .any(|row| row.get("id").and_then(Value::as_str) == Some(memory_id))
        {
            return;
        }
        let kept: Vec<Value> = rows
            .into_iter()
            .filter(|row| row.get("id").and_then(Value::as_str) != Some(memory_id))
            .collect();
        if let Some(object) = state.as_object_mut() {
            object.insert("memories".to_string(), Value::Array(kept));
        }
        removed = true;
    })?;
    Ok(removed)
}

pub fn save_state(store: &Arc<Store>, data_dir: &Path, state: &Value) -> Result<(), CoreError> {
    if let Some(writer) = state_writer() {
        let replacement = state.clone();
        return writer
            .mutate(&mut |current: &mut Value| *current = replacement.clone())
            .map_err(|error| CoreError::Runtime(format!("workspace state write failed: {error}")));
    }
    write_through(store, data_dir, state)
}

fn write_through(store: &Arc<Store>, data_dir: &Path, state: &Value) -> Result<(), CoreError> {
    let payload = serde_json::to_string(state).map_err(|error| {
        CoreError::Runtime(format!("workspace state is not serialisable: {error}"))
    })?;
    let updated_at = state
        .get("updated_at")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    store.with_write_txn(|txn| {
        txn.execute(
            "CREATE TABLE IF NOT EXISTS workspace_os_state (\
             id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
            [],
        )?;
        txn.execute(
            "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) VALUES('current', ?, ?)",
            rusqlite::params![payload, updated_at],
        )?;
        Ok(())
    })?;
    lattice_auth::atomic::write_text(&data_dir.join("workspace_os.json"), &payload);
    Ok(())
}

pub struct Resolver {
    state: Value,
}

impl Resolver {
    pub fn new(store: &Arc<Store>, data_dir: &Path) -> Self {
        Self {
            state: load(store, data_dir),
        }
    }

    fn resolve(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
        permission: &str,
    ) -> Result<Option<String>, String> {
        let workspace_id = requested
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| active_workspace(&self.state));
        if has_permission(&self.state, &workspace_id, user, permission) {
            return Ok(Some(workspace_id));
        }
        Err(format!(
            "'{}' lacks '{permission}' on workspace '{workspace_id}'",
            user.unwrap_or("anonymous")
        ))
    }
}

impl WorkspaceResolver for Resolver {
    fn resolve_read_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String> {
        self.resolve(requested, user, "read")
    }

    fn resolve_write_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String> {
        self.resolve(requested, user, "write")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> Value {
        serde_json::json!({
            "active_workspace": "personal",
            "workspaces": {
                "personal": {"workspace_id": "personal", "type": "personal"},
                "org": {"workspace_id": "org", "type": "organization",
                        "owner_user_id": "user:1",
                        "members": [{"user_id": "user:2", "role": "viewer"}]},
            },
            "memories": [
                {"id": "m1", "kind": "decisions", "content": "alpha fusion stays",
                 "tags": ["ranking"], "user_email": "owner@x", "workspace_id": "personal"},
                {"id": "m2", "kind": "short_term", "content": "회의록 정리",
                 "user_email": null, "workspace_id": "personal"},
                {"id": "m3", "kind": "decisions", "content": "org note",
                 "user_email": "other@x", "workspace_id": "org"},
            ],
            "memory_snapshots": [
                {"id": "s1", "workspace_id": "personal"},
                {"id": "s2", "workspace_id": "personal"},
            ],
        })
    }

    #[test]
    fn the_memory_list_is_scoped_filtered_and_newest_first() {
        let state = state();
        let rows = list_memories(&state, Some("owner@x"), None, Some("personal"));
        let ids: Vec<&str> = rows.iter().filter_map(|r| r["id"].as_str()).collect();
        assert_eq!(
            ids,
            vec!["m2", "m1"],
            "reversed, and the null owner is kept"
        );
        let decisions = list_memories(&state, None, Some("decisions"), None);
        assert_eq!(decisions.len(), 2);
        assert!(list_memories(&state, Some("owner@x"), None, Some("org")).is_empty());
    }

    #[test]
    fn search_matches_content_tags_or_kind_and_caps_the_answer() {
        let state = state();
        assert_eq!(
            search_memories(&state, "RANKING", None, 20, Some("personal")).len(),
            1,
            "tags are searched case-insensitively"
        );
        assert_eq!(search_memories(&state, "회의록", None, 20, None).len(), 1);
        assert_eq!(
            search_memories(&state, "decisions", None, 20, None).len(),
            2
        );
        assert_eq!(search_memories(&state, "", None, 20, None).len(), 3);
        assert_eq!(
            search_memories(&state, "", None, 0, None).len(),
            1,
            "limit clamps to 1"
        );
    }

    #[test]
    fn snapshots_take_the_tail_and_reverse_it() {
        let state = state();
        let rows = list_memory_snapshots(&state, Some("personal"), 1);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["id"], "s2");
        assert_eq!(list_memory_snapshots(&state, None, 50).len(), 2);
    }

    #[test]
    fn permissions_follow_the_role_table() {
        let state = state();
        assert!(has_permission(&state, "personal", None, "write"));
        assert!(has_permission(
            &state,
            "personal",
            Some("anyone@x"),
            "manage_workspace"
        ));
        assert!(has_permission(&state, "org", Some("user:1"), "write"));
        assert!(has_permission(&state, "org", Some("user:2"), "read"));
        assert!(!has_permission(&state, "org", Some("user:2"), "write"));
        assert!(!has_permission(&state, "org", Some("user:3"), "read"));
        assert!(!has_permission(&state, "nope", None, "read"));
        assert_eq!(active_workspace(&state), "personal");
        assert_eq!(
            active_workspace(&serde_json::json!({"active_workspace": "gone"})),
            "personal",
            "an active workspace that no longer exists falls back"
        );
        assert!(workspace_ids(&state).contains(&"org".to_string()));
        assert!(workspace_ids(&Value::Null).contains(&"personal".to_string()));
    }

    #[test]
    fn an_ownerless_organization_answers_owner_to_the_anonymous_local_user() {
        let ws = serde_json::json!({"workspace_id": "o", "type": "organization"});
        assert_eq!(member_role(&ws, None).as_deref(), Some("owner"));
        assert_eq!(member_role(&ws, Some("user:9")), None);
        assert_eq!(record_workspace(&serde_json::json!({})), "personal");
        assert_eq!(tag_text(&serde_json::json!({"tags": ["a", 1, "b"]})), "a b");
    }

    #[test]
    fn the_resolver_answers_the_active_workspace_when_nothing_is_named() {
        let resolver = Resolver { state: state() };
        assert_eq!(
            resolver
                .resolve_read_scope(None, Some("owner@x"))
                .expect("personal"),
            Some("personal".to_string())
        );
        assert_eq!(
            resolver
                .resolve_write_scope(Some("org"), Some("user:1"))
                .expect("owner"),
            Some("org".to_string())
        );
        let denied = resolver
            .resolve_write_scope(Some("org"), Some("user:2"))
            .unwrap_err();
        assert_eq!(denied, "'user:2' lacks 'write' on workspace 'org'");
        assert!(resolver.resolve_read_scope(Some("org"), None).is_err());
    }
}
