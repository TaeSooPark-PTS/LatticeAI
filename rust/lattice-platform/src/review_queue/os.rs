//! Workspace-OS document load/save for the review family.

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

use lattice_core::db::tables::state_files;
use serde_json::{json, Value};

use super::http::now_iso;
use super::{DEFAULT_WORKSPACE_ID, WORKSPACE_OS_VERSION};

// ── workspace OS persistence ──────────────────────────────────────────────

pub(crate) fn default_workspace_os() -> Value {
    let now = now_iso();
    json!({
        "version": WORKSPACE_OS_VERSION,
        "identity": "AI Workspace OS",
        "created_at": now,
        "updated_at": now,
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": {
            "personal": {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "id": DEFAULT_WORKSPACE_ID,
                "name": "Personal Workspace",
                "type": "personal",
                "owner_user_id": null,
                "members": [],
                "status": "active",
                "created_at": now,
                "updated_at": now
            }
        },
        "review_items": [],
        "workflows": [],
        "workflow_runs": [],
        "agent_runs": [],
        "timeline": []
    })
}

pub(crate) fn load_workspace_os(data_dir: &Path) -> Value {
    if let Some(from_sql) = load_sqlite_state(data_dir) {
        return merge_default(from_sql);
    }
    let path = data_dir.join(state_files::WORKSPACE_OS);
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if value.is_object() {
                return merge_default(value);
            }
        }
    }
    default_workspace_os()
}

pub(crate) fn merge_default(loaded: Value) -> Value {
    let mut base = default_workspace_os();
    if let (Some(base_obj), Some(loaded_obj)) = (base.as_object_mut(), loaded.as_object()) {
        for (key, value) in loaded_obj {
            base_obj.insert(key.clone(), value.clone());
        }
        base_obj.insert("version".into(), json!(WORKSPACE_OS_VERSION));
    }
    base
}

pub(crate) fn save_workspace_os(data_dir: &Path, state: &Value) {
    let mut to_write = state.clone();
    if let Some(obj) = to_write.as_object_mut() {
        obj.insert("version".into(), json!(WORKSPACE_OS_VERSION));
        obj.insert("updated_at".into(), json!(now_iso()));
    }
    let path = data_dir.join(state_files::WORKSPACE_OS);
    if let Ok(text) = serde_json::to_string_pretty(&to_write) {
        lattice_auth::atomic::write_text(&path, &format!("{text}\n"));
    }
    save_sqlite_state(data_dir, &to_write);
}

pub(crate) fn load_sqlite_state(data_dir: &Path) -> Option<Value> {
    let db = data_dir.join("knowledge_graph.sqlite");
    if !db.exists() {
        return None;
    }
    let conn = rusqlite::Connection::open(&db).ok()?;
    let text: String = conn
        .query_row(
            "SELECT state_json FROM workspace_os_state WHERE id='current'",
            [],
            |row| row.get(0),
        )
        .ok()?;
    serde_json::from_str(&text).ok()
}

pub(crate) fn save_sqlite_state(data_dir: &Path, state: &Value) {
    let db = data_dir.join("knowledge_graph.sqlite");
    let Ok(conn) = rusqlite::Connection::open(&db) else {
        return;
    };
    let _ = conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS workspace_os_state (
            id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );",
    );
    let Ok(payload) = serde_json::to_string(state) else {
        return;
    };
    let updated = state
        .get("updated_at")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let _ = conn.execute(
        "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) VALUES('current', ?1, ?2)",
        rusqlite::params![payload, updated],
    );
}

pub(crate) fn scoped(record: &Value, workspace_id: Option<&str>) -> bool {
    let Some(wanted) = workspace_id else {
        return true;
    };
    let stored = record
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    stored == wanted
}
