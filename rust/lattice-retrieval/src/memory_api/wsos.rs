//! The Workspace OS state these families read — and the two rows they delete.
//!
//! `latticeai/core/workspace_os.py` keeps one JSON document under
//! `workspace_os_state.id='current'` in `knowledge_graph.sqlite`, mirrored to
//! `<data_dir>/workspace_os.json`. That table is **RUST_PLATFORM**-owned
//! (WP-I1 §2), so the reads and the memory deletions here are native; nothing
//! in this module touches a worker-owned table.
//!
//! Only the keys the memory tiers need are modelled — `memories`,
//! `memory_snapshots`, `active_workspace` and `workspaces`. `load_state()`'s
//! deep merge with `default_state()` is reproduced by defaulting each of those
//! four readings, which is observationally identical for them and avoids a
//! second, drifting copy of a 180-line default document.
//!
//! **Handover note.** `WorkspaceOSStore` belongs to WP-R1 (`lattice-platform`).
//! When R1 lands a full port, this module should collapse into a call into it;
//! it exists because the memory tiers cannot be read without workspace state
//! and R1's `workspace.rs` was still a stub when this package was written.

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
use std::path::Path;
use std::sync::Arc;

use lattice_auth::WorkspaceResolver;
use lattice_core::db::Store;
use lattice_core::CoreError;
use serde_json::{Map, Value};

/// `workspace_os_constants.DEFAULT_WORKSPACE_ID`.
pub const DEFAULT_WORKSPACE_ID: &str = "personal";

/// `ROLE_PERMISSIONS` — the community defaults, verbatim.
fn role_permissions(role: &str) -> &'static [&'static str] {
    match role {
        "owner" | "admin" => &["read", "write", "manage_members", "manage_workspace"],
        "member" => &["read", "write"],
        "viewer" => &["read"],
        _ => &[],
    }
}

/// The whole state document, as `load_state()` would have returned it.
pub fn load(store: &Arc<Store>, data_dir: &Path) -> Value {
    store
        .with_read_conn(|conn| Ok(load_from(conn, data_dir)))
        .unwrap_or_else(|_| Value::Object(Map::new()))
}

/// [`load`] on a connection the caller already holds.
///
/// The memory manager reads workspace state *and* the graph in the same answer;
/// taking a second connection out of the pool for the second half is how a
/// four-reader pool turns into a stall under load.
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
    // `_import_json_state_once`: a store with no row yet still has the mirror
    // file from an install that predates the SQLite move. Read-only here — the
    // Python loader re-saves, and a read path that writes would be a surprise.
    from_sqlite
        .or_else(|| {
            std::fs::read_to_string(data_dir.join("workspace_os.json"))
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok())
                .filter(Value::is_object)
        })
        .unwrap_or_else(|| Value::Object(Map::new()))
}

/// `state["memories"]`, in the order they were written.
pub fn memories(state: &Value) -> Vec<Value> {
    listify(state.get("memories"))
}

/// `state["memory_snapshots"]`, in the order they were written.
pub fn memory_snapshots(state: &Value) -> Vec<Value> {
    listify(state.get("memory_snapshots"))
}

/// `_listify` — a non-list reads as empty rather than raising.
fn listify(value: Option<&Value>) -> Vec<Value> {
    match value {
        Some(Value::Array(items)) => items.clone(),
        _ => Vec::new(),
    }
}

/// `_active_workspace_id` — the named workspace must still exist.
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

/// One workspace record, if the state names it.
///
/// `migrate_workspaces` guarantees the Personal workspace exists on every
/// load, so an absent `personal` entry answers the record that migration would
/// have created rather than "no such workspace" — otherwise the gate would
/// refuse every read on a store whose state document has not been written yet.
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

/// Every workspace id the state knows, plus the guaranteed Personal one.
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

/// `_member_role` — a personal workspace always grants its local user `owner`.
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

/// `WorkspaceOSStore.has_permission` — an unknown workspace grants nothing.
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

/// `_record_workspace` — a legacy record with no workspace is Personal's.
pub fn record_workspace(record: &Value) -> String {
    record
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_WORKSPACE_ID)
        .to_string()
}

/// `_scoped` — an empty scope means "every record", not "no records".
pub fn scoped(records: Vec<Value>, workspace_id: Option<&str>) -> Vec<Value> {
    let Some(target) = workspace_id.filter(|value| !value.is_empty()) else {
        return records;
    };
    records
        .into_iter()
        .filter(|record| record_workspace(record) == target)
        .collect()
}

/// `WorkspaceMemory.list_memories` — scoped, filtered, newest first.
pub fn list_memories(
    state: &Value,
    user_email: Option<&str>,
    kind: Option<&str>,
    workspace_id: Option<&str>,
) -> Vec<Value> {
    let mut items = scoped(memories(state), workspace_id);
    if let Some(email) = user_email.filter(|value| !value.is_empty()) {
        // `item.get("user_email") in {None, user_email}`: a record written
        // before accounts existed stays visible to whoever asks.
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

/// `WorkspaceOSStore.search_memories` — a substring scan over the same list.
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

/// `" ".join(item.get("tags") or [])` — non-string tags stringify as Python's
/// `str.join` would refuse, so only strings participate.
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

/// `WorkspaceOSStore.list_memory_snapshots` — the last `limit`, newest first.
pub fn list_memory_snapshots(state: &Value, workspace_id: Option<&str>, limit: i64) -> Vec<Value> {
    let mut items = scoped(memory_snapshots(state), workspace_id);
    let cap = limit.clamp(1, 200) as usize;
    if items.len() > cap {
        items = items.split_off(items.len() - cap);
    }
    items.reverse();
    items
}

/// The owner of `workspace_os.json` + the `workspace_os_state` row.
///
/// This crate cannot name `lattice_platform::workspace::WorkspaceOsStore` —
/// the dependency runs the other way — so it declares the port and the host
/// installs the implementation, exactly as `lattice-agent` declares
/// `ProposalStore` for the Review Center. Without one installed the writes
/// below stay what they were: correct on their own, unaware of anybody else's.
pub trait StateWriter: Send + Sync {
    /// Load, apply `body`, save — under the owner's write lock.
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String>;

    /// Append one timeline event through the owner.
    ///
    /// Needed because this crate writes **review items** (synthesis and
    /// Self-Model proposals), and every other writer of `review_items` records
    /// a `review_item_created` / `review_item_updated` event after the save.
    /// Recording it here by hand would append to `timeline` without the
    /// owner's 10,000-entry cap or its realtime echo, so the port carries it
    /// instead: the implementation is
    /// `WorkspaceOsStore::record_timeline_event`, one function.
    fn record_event(
        &self,
        area: &str,
        event_type: &str,
        payload: Value,
        workspace_id: Option<&str>,
    ) -> Result<(), String>;
}

/// `review_queue::REVIEW_TIMELINE_AREA`.
///
/// The three constants below are `lattice_platform::review_queue`'s, spelled
/// again because the dependency runs the other way. `lattice-host`'s
/// `the_review_event_vocabulary_is_one_vocabulary` asserts the two spellings
/// are equal — it is the one crate that can see both.
pub const REVIEW_TIMELINE_AREA: &str = "review";
/// `review_queue::REVIEW_ITEM_CREATED_EVENT`.
pub const REVIEW_ITEM_CREATED_EVENT: &str = "review_item_created";
/// `review_queue::REVIEW_ITEM_UPDATED_EVENT`.
pub const REVIEW_ITEM_UPDATED_EVENT: &str = "review_item_updated";

/// Record one review-queue timeline event, after the item was saved.
///
/// The payload is built key-for-key the way
/// `review_queue::GovernanceState::record_review_event` builds it, in the same
/// insertion order, so a reader cannot tell which crate wrote the row —
/// `action` is the Review Center's vocabulary (`create` / `approve` / …).
///
/// Without an installed writer this is a no-op rather than a second, unbounded
/// appender: a standalone `lattice-retrieval` has no timeline reader either,
/// and `timeline`'s cap belongs to the document's owner.
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

/// Name the process's state writer. The first call wins; later ones are
/// refused (`false`) rather than swapping the document under a live route.
pub fn install_state_writer(writer: Arc<dyn StateWriter>) -> bool {
    STATE_WRITER.set(writer).is_ok()
}

/// The installed writer, if the host wired one.
pub fn state_writer() -> Option<&'static Arc<dyn StateWriter>> {
    STATE_WRITER.get()
}

/// Load, apply, save — atomically when a writer is installed.
///
/// This is the shape every caller should reach for: it holds the owner's lock
/// across the read and the write, so a concurrent review-item append or
/// workspace mutation cannot be erased by a stale snapshot. `save_state`
/// remains for callers that already hold a whole document.
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

/// `WorkspaceOSStore.delete_memory` — remove one row, mirror, and say so.
///
/// Platform state, so this is a native write (WP-I1 §2). The timeline event
/// `record_timeline_event("memory", "memory_deleted", …)` is **not** written:
/// the timeline is WP-R1's surface and a second writer appending to it from
/// here would fight R1's own port. Named in the wiring note as a gap.
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

/// `save_state` — the SQLite row is the source of truth, the JSON its mirror.
///
/// The caller loaded and mutated a whole document, so the read-modify-write
/// window is theirs; what this can still guarantee is that the write itself
/// goes through the one owner (same lock, same bytes, same version stamp).
/// Callers that can express their change as a closure should use
/// [`mutate_state`] instead, which closes the window too.
pub fn save_state(store: &Arc<Store>, data_dir: &Path, state: &Value) -> Result<(), CoreError> {
    if let Some(writer) = state_writer() {
        let replacement = state.clone();
        return writer
            .mutate(&mut |current: &mut Value| *current = replacement.clone())
            .map_err(|error| CoreError::Runtime(format!("workspace state write failed: {error}")));
    }
    write_through(store, data_dir, state)
}

/// The unowned write: the SQLite row, then the JSON mirror.
///
/// Only reached when no [`StateWriter`] is installed — a standalone
/// `lattice-retrieval` process, and the tests that exercise this module.
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

/// `WorkspaceService.resolve_{read,write}_scope`, as the auth crate's trait.
pub struct Resolver {
    state: Value,
}

impl Resolver {
    /// Read the state once; both directions answer off the same snapshot.
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
        // A *named* stranger never bypasses membership, even in no-auth local
        // mode: only the ownerless case keeps the legacy owner fallback.
        assert!(resolver.resolve_read_scope(Some("org"), None).is_err());
    }
}
