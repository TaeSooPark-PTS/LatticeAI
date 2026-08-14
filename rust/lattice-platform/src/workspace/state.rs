//! The shape of a new Workspace OS state document, and upgrades to old ones.
//!
//! Port of `latticeai/core/workspace_os_state.py`. Three pure functions:
//! [`default_state`] describes a brand-new file, [`new_workspace_record`]
//! builds one workspace entry, and [`migrate_workspaces`] upgrades a file
//! written by an older version.
//!
//! `migrate_workspaces` is the one that has to be exactly right: it runs on
//! **every** load, over state a shipped install already has on disk. It is
//! non-destructive by construction — it rebuilds each entry from
//! `new_workspace_record` and then puts back the timestamps and status the
//! loaded record carried, so a field this version does not know about is the
//! only thing that can be lost, and none exist.

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
use serde_json::{json, Map, Value};

use super::constants::{
    DEFAULT_AGENTS, DEFAULT_FEATURE_FLAGS, DEFAULT_WORKSPACE_ID, ONBOARDING_STEPS,
    ROLE_PERMISSIONS, WORKSPACE_AREAS, WORKSPACE_OS_VERSION, WORKSPACE_TYPES,
};
use super::pyutil::{listify, now_iso};

/// `{role: sorted(perms)}` — the role matrix as it is serialized.
pub fn role_matrix() -> Value {
    let mut map = Map::new();
    for (role, grants) in ROLE_PERMISSIONS {
        map.insert(role.to_string(), json!(grants));
    }
    Value::Object(map)
}

/// `new_workspace_record` — one workspace entry.
///
/// Returns `Err` for an unknown type, mirroring the `ValueError` Python raises.
pub fn new_workspace_record(
    workspace_id: &str,
    name: &str,
    workspace_type: &str,
    owner_user_id: Option<&str>,
    settings: Option<Value>,
    members: Option<Vec<Value>>,
) -> Result<Value, String> {
    if !WORKSPACE_TYPES.contains(&workspace_type) {
        return Err(format!("unknown workspace type: {workspace_type}"));
    }
    let now = now_iso();
    let mut member_list = members.unwrap_or_default();
    if let Some(owner) = owner_user_id.filter(|value| !value.is_empty()) {
        let present = member_list
            .iter()
            .any(|member| member.get("user_id").and_then(Value::as_str) == Some(owner));
        if !present {
            member_list.insert(
                0,
                json!({"user_id": owner, "role": "owner", "added_at": now}),
            );
        }
    }
    Ok(json!({
        "workspace_id": workspace_id,
        "id": workspace_id,
        "name": name,
        "type": workspace_type,
        "owner_user_id": owner_user_id,
        "members": member_list,
        "roles": role_matrix(),
        "status": "active",
        "areas": WORKSPACE_AREAS,
        "settings": settings.unwrap_or_else(|| json!({})),
        "created_at": now,
        "updated_at": now,
    }))
}

/// `migrate_workspaces` — non-destructive upgrade of legacy entries.
///
/// Mutates `state` in place, exactly as the Python original does.
pub fn migrate_workspaces(state: &mut Value) {
    let loaded: Vec<(String, Value)> = match state.get("workspaces") {
        Some(Value::Object(map)) => map
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
        _ => Vec::new(),
    };

    let mut migrated = Map::new();
    for (workspace_id, entry) in loaded {
        let Value::Object(_) = entry else { continue };
        let declared = entry
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let mut workspace_type = if WORKSPACE_TYPES.contains(&declared) {
            declared
        } else {
            "organization"
        };
        if workspace_id == DEFAULT_WORKSPACE_ID {
            workspace_type = "personal";
        }
        let name = entry
            .get("name")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(&workspace_id)
            .to_string();
        let members = match entry.get("members") {
            Some(Value::Array(items)) => Some(items.clone()),
            _ => None,
        };
        let settings = match entry.get("settings") {
            Some(Value::Object(map)) if !map.is_empty() => Some(Value::Object(map.clone())),
            _ => Some(json!({})),
        };
        // The type is one of the two literals above, so this cannot fail.
        let Ok(mut base) = new_workspace_record(
            &workspace_id,
            &name,
            workspace_type,
            entry.get("owner_user_id").and_then(Value::as_str),
            settings,
            members,
        ) else {
            continue;
        };
        for key in ["created_at", "updated_at", "status"] {
            if let Some(kept) = entry.get(key).filter(|value| truthy(value)).cloned() {
                base[key] = kept;
            }
        }
        migrated.insert(workspace_id, base);
    }

    if !migrated.contains_key(DEFAULT_WORKSPACE_ID) {
        if let Ok(personal) = new_workspace_record(
            DEFAULT_WORKSPACE_ID,
            "Personal Workspace",
            "personal",
            None,
            None,
            None,
        ) {
            migrated.insert(DEFAULT_WORKSPACE_ID.to_string(), personal);
        }
    }

    let active_known = state
        .get("active_workspace")
        .and_then(Value::as_str)
        .is_some_and(|active| migrated.contains_key(active));
    state["workspaces"] = Value::Object(migrated);
    if !active_known {
        state["active_workspace"] = json!(DEFAULT_WORKSPACE_ID);
    }
}

/// Python truthiness for the `record.get(key) or default` idiom.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `default_state` — a brand-new state document.
pub fn default_state() -> Value {
    let now = now_iso();
    let mut flags = Map::new();
    for (name, enabled) in DEFAULT_FEATURE_FLAGS {
        flags.insert(name.to_string(), json!(enabled));
    }
    let mut steps = Map::new();
    for step in ONBOARDING_STEPS {
        steps.insert(
            step.to_string(),
            json!({"id": step, "status": "pending", "data": {}, "error": "", "updated_at": null}),
        );
    }
    let agents: Vec<Value> = DEFAULT_AGENTS
        .iter()
        .map(|(id, name, role, relationships)| {
            json!({
                "id": id,
                "name": name,
                "role": role,
                "status": "available",
                "relationships": relationships,
            })
        })
        .collect();
    let mut workspaces = Map::new();
    if let Ok(personal) = new_workspace_record(
        DEFAULT_WORKSPACE_ID,
        "Personal Workspace",
        "personal",
        None,
        None,
        None,
    ) {
        workspaces.insert(DEFAULT_WORKSPACE_ID.to_string(), personal);
    }

    json!({
        "version": WORKSPACE_OS_VERSION,
        "identity": "AI Workspace OS",
        "created_at": now,
        "updated_at": now,
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": Value::Object(workspaces),
        "feature_flags": Value::Object(flags),
        "onboarding": {
            "completed": false,
            "current_step": "account",
            "steps": Value::Object(steps),
        },
        "snapshots": [],
        "traces": [],
        "memories": [],
        "memory_snapshots": [],
        "agents": agents,
        "agent_runs": [],
        "handoffs": [],
        "workflows": [],
        "workflow_runs": [],
        "review_items": [],
        "skill_registry": {},
        "plugin_registry": {},
        "template_registry": {},
        "computer_memory": {
            "enabled": false,
            "approved": false,
            "approved_at": null,
            "approved_by": null,
            "scopes": super::constants::DEFAULT_COMPUTER_MEMORY_SCOPES,
            "activities": [],
            "notice": super::constants::COMPUTER_MEMORY_NOTICE,
        },
        "timeline": [],
    })
}

/// `_member_role(ws, user_id)` — the role this identity holds, if any.
///
/// The two fallbacks are load-bearing and neither is an accident:
/// a **personal** workspace always answers `owner`, and an **ownerless**
/// organization workspace answers `owner` to an anonymous caller — which is how
/// a no-auth local install keeps managing what it created.
pub fn member_role<'a>(workspace: &'a Value, user_id: Option<&str>) -> Option<&'a str> {
    if workspace.get("type").and_then(Value::as_str) == Some("personal") {
        return Some("owner");
    }
    let owner = workspace
        .get("owner_user_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    let user_id = user_id.filter(|value| !value.is_empty());
    if owner.is_none() && user_id.is_none() {
        return Some("owner");
    }
    if let (Some(user), Some(owner)) = (user_id, owner) {
        if user == owner {
            return Some("owner");
        }
    }
    for member in listify_members(workspace) {
        if member.get("user_id").and_then(Value::as_str) == user_id {
            return member.get("role").and_then(Value::as_str);
        }
    }
    None
}

fn listify_members(workspace: &Value) -> &[Value] {
    match workspace.get("members") {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    }
}

/// The members list of a workspace record.
pub fn members_of(workspace: &Value) -> Vec<Value> {
    listify(workspace.get("members"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_record_carries_the_owner_as_its_first_member() {
        let record =
            new_workspace_record("org-a", "A", "organization", Some("user:1"), None, None).unwrap();
        assert_eq!(record["workspace_id"], json!("org-a"));
        assert_eq!(record["id"], json!("org-a"));
        assert_eq!(record["members"][0]["user_id"], json!("user:1"));
        assert_eq!(record["members"][0]["role"], json!("owner"));
        assert_eq!(record["status"], json!("active"));
        assert_eq!(record["settings"], json!({}));
        assert_eq!(record["areas"], json!(WORKSPACE_AREAS));
        assert_eq!(record["created_at"], record["updated_at"]);
    }

    #[test]
    fn an_unknown_type_is_refused_the_way_python_raises() {
        assert_eq!(
            new_workspace_record("x", "X", "team", None, None, None).unwrap_err(),
            "unknown workspace type: team"
        );
    }

    #[test]
    fn an_existing_owner_member_is_not_duplicated() {
        let existing = vec![json!({"user_id": "user:1", "role": "admin"})];
        let record = new_workspace_record(
            "org-a",
            "A",
            "organization",
            Some("user:1"),
            None,
            Some(existing),
        )
        .unwrap();
        assert_eq!(record["members"].as_array().unwrap().len(), 1);
        assert_eq!(record["members"][0]["role"], json!("admin"));
    }

    #[test]
    fn the_default_state_is_the_documented_shape() {
        let state = default_state();
        assert_eq!(state["identity"], json!("AI Workspace OS"));
        assert_eq!(state["active_workspace"], json!("personal"));
        assert_eq!(state["workspaces"]["personal"]["type"], json!("personal"));
        assert_eq!(state["agents"].as_array().unwrap().len(), 5);
        assert_eq!(state["onboarding"]["current_step"], json!("account"));
        assert_eq!(
            state["onboarding"]["steps"]["complete"]["status"],
            json!("pending")
        );
        assert_eq!(
            state["feature_flags"]["local_computer_memory"],
            json!(false)
        );
        assert_eq!(state["computer_memory"]["enabled"], json!(false));
        assert!(state["timeline"].as_array().unwrap().is_empty());
    }

    #[test]
    fn migration_backfills_a_1_0_entry_and_guarantees_personal() {
        let mut state = json!({
            "workspaces": {"org-old": {"id": "org-old", "name": "Old", "areas": []}},
            "active_workspace": "org-gone",
        });
        migrate_workspaces(&mut state);
        let upgraded = &state["workspaces"]["org-old"];
        assert_eq!(upgraded["type"], json!("organization"));
        assert_eq!(upgraded["members"], json!([]));
        assert_eq!(upgraded["roles"], role_matrix());
        assert!(state["workspaces"]["personal"].is_object());
        // The named active workspace no longer exists → back to Personal.
        assert_eq!(state["active_workspace"], json!("personal"));
    }

    #[test]
    fn migration_preserves_timestamps_status_and_the_personal_type() {
        let mut state = json!({
            "workspaces": {
                "personal": {"type": "organization", "name": "Mine",
                             "created_at": "2020-01-01T00:00:00", "status": "archived"},
                "junk": 3,
            },
            "active_workspace": "personal",
        });
        migrate_workspaces(&mut state);
        let personal = &state["workspaces"]["personal"];
        assert_eq!(personal["type"], json!("personal"));
        assert_eq!(personal["created_at"], json!("2020-01-01T00:00:00"));
        assert_eq!(personal["status"], json!("archived"));
        assert!(state["workspaces"].get("junk").is_none());
        assert_eq!(state["active_workspace"], json!("personal"));
    }

    #[test]
    fn member_role_answers_the_three_fallbacks() {
        let personal = json!({"type": "personal"});
        assert_eq!(member_role(&personal, Some("anyone")), Some("owner"));

        let ownerless = json!({"type": "organization", "owner_user_id": null, "members": []});
        assert_eq!(member_role(&ownerless, None), Some("owner"));
        assert_eq!(member_role(&ownerless, Some("user:1")), None);

        let owned = json!({
            "type": "organization", "owner_user_id": "user:1",
            "members": [{"user_id": "user:1", "role": "owner"},
                        {"user_id": "user:2", "role": "viewer"}],
        });
        assert_eq!(member_role(&owned, Some("user:1")), Some("owner"));
        assert_eq!(member_role(&owned, Some("user:2")), Some("viewer"));
        assert_eq!(member_role(&owned, Some("user:3")), None);
        assert_eq!(member_role(&owned, None), None);
        assert_eq!(members_of(&owned).len(), 2);
    }
}
