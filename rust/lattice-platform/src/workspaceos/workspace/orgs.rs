//! Organization workspaces: the registry, membership, roles, and activation.
//!
//! Port of the organization half of `WorkspaceOSStore` plus
//! `core/workspace_permissions.py`. Two rules here are load-bearing and are
//! easy to "fix" into a security hole:
//!
//! * **An unknown workspace id answers 403, not 404.** `get_workspace` and
//!   `workspace_summary` check read permission *before* they look the record
//!   up, and an unknown id has no members, so it can never grant read. A
//!   404/403 split would let anyone enumerate which organizations exist on this
//!   install. The routes therefore carry no not-found arm at all — the service
//!   cannot reach one.
//! * **An ownerless organization grants `owner` to an anonymous caller**
//!   ([`super::state::member_role`]). That is the no-auth local install
//!   managing what it created, and tightening it locks a desktop user out of
//!   their own workspace.

use serde_json::{json, Map, Value};

use super::constants::{role_grants, DEFAULT_WORKSPACE_ID, ROLE_PERMISSIONS, WORKSPACE_ROLES};
use super::pyutil::{listify, now_iso, safe_slug};
use super::state::{member_role, new_workspace_record, role_matrix};
use super::store::{StoreError, WorkspaceOsStore};

/// `_workspace_public` — the projection every workspace route answers with.
pub fn workspace_public(workspace: &Value, user_id: Option<&str>) -> Value {
    let identifier = workspace
        .get("workspace_id")
        .filter(|value| !value.is_null())
        .or_else(|| workspace.get("id"))
        .cloned()
        .unwrap_or(Value::Null);
    let members = listify(workspace.get("members"));
    let user_id = user_id.filter(|value| !value.is_empty());
    let your_role = match user_id {
        Some(user) => member_role(workspace, Some(user)).map_or(Value::Null, |role| json!(role)),
        None => {
            if workspace.get("type").and_then(Value::as_str) == Some("personal") {
                json!("owner")
            } else {
                Value::Null
            }
        }
    };
    json!({
        "workspace_id": identifier,
        "id": identifier,
        "name": workspace.get("name").cloned().unwrap_or(Value::Null),
        "type": workspace.get("type").cloned().unwrap_or(Value::Null),
        "owner_user_id": workspace.get("owner_user_id").cloned().unwrap_or(Value::Null),
        "status": workspace.get("status").cloned().unwrap_or_else(|| json!("active")),
        "member_count": members.len(),
        "members": members,
        "settings": object_or_empty(workspace.get("settings")),
        "created_at": workspace.get("created_at").cloned().unwrap_or(Value::Null),
        "updated_at": workspace.get("updated_at").cloned().unwrap_or(Value::Null),
        "your_role": your_role,
    })
}

fn object_or_empty(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Object(map)) => Value::Object(map.clone()),
        _ => Value::Object(Map::new()),
    }
}

/// The `{role: [permission, …]}` matrix, as `list_workspaces` serializes it.
pub fn permission_matrix() -> Value {
    role_matrix()
}

/// One workspace record out of a loaded state document.
fn workspace_of<'a>(state: &'a Value, workspace_id: &str) -> Option<&'a Value> {
    state
        .get("workspaces")
        .and_then(Value::as_object)
        .and_then(|map| map.get(workspace_id))
}

/// `_load_org` — the record, refusing anything that is not an organization.
fn load_org<'a>(state: &'a mut Value, workspace_id: &str) -> Result<&'a mut Value, StoreError> {
    let is_org = workspace_of(state, workspace_id)
        .map(|workspace| workspace.get("type").and_then(Value::as_str) == Some("organization"));
    match is_org {
        None => Err(StoreError::NotFound(workspace_id.to_string())),
        Some(false) => Err(StoreError::Value(
            "operation only valid for organization workspaces".into(),
        )),
        Some(true) => state
            .get_mut("workspaces")
            .and_then(Value::as_object_mut)
            .and_then(|map| map.get_mut(workspace_id))
            .ok_or_else(|| StoreError::NotFound(workspace_id.to_string())),
    }
}

/// `require_permission` — the message is the one Python's `PermissionError`
/// carries, and it reaches the client as the 403 detail.
pub fn require_permission(
    workspace: &Value,
    actor: Option<&str>,
    permission: &str,
) -> Result<(), StoreError> {
    let role = member_role(workspace, actor);
    let granted = role.is_some_and(|role| role_grants(role, permission));
    if granted {
        return Ok(());
    }
    Err(StoreError::Permission(format!(
        "'{}' lacks '{permission}' on workspace '{}'",
        actor
            .filter(|value| !value.is_empty())
            .unwrap_or("anonymous"),
        workspace
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    )))
}

/// `get_member_role` — refuses an unknown workspace, as Python does.
pub fn get_member_role(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: Option<&str>,
) -> Result<Option<String>, StoreError> {
    let state = store.load_state();
    let workspace = workspace_of(&state, workspace_id)
        .ok_or_else(|| StoreError::NotFound(workspace_id.to_string()))?;
    Ok(member_role(workspace, user_id).map(str::to_string))
}

/// `has_permission` — an unknown workspace is simply `false`.
pub fn has_permission(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: Option<&str>,
    permission: &str,
) -> bool {
    has_permission_in(&store.load_state(), workspace_id, user_id, permission)
}

/// The same question, answered against an already-loaded document.
pub fn has_permission_in(
    state: &Value,
    workspace_id: &str,
    user_id: Option<&str>,
    permission: &str,
) -> bool {
    workspace_of(state, workspace_id)
        .and_then(|workspace| member_role(workspace, user_id))
        .is_some_and(|role| role_grants(role, permission))
}

/// `list_workspaces` — the registry, filtered to what this identity may see.
pub fn list_workspaces(store: &WorkspaceOsStore, user_id: Option<&str>) -> Value {
    let state = store.load_state();
    let user_id = user_id.filter(|value| !value.is_empty());
    let mut items: Vec<Value> = Vec::new();
    if let Some(map) = state.get("workspaces").and_then(Value::as_object) {
        for workspace in map.values() {
            let hidden = user_id.is_some()
                && workspace.get("type").and_then(Value::as_str) == Some("organization")
                && member_role(workspace, user_id).is_none();
            if hidden {
                continue;
            }
            items.push(workspace_public(workspace, user_id));
        }
    }
    // `sorted(key=(type != "personal", created_at or ""))`: Personal first,
    // then oldest organization first. Python's sort is stable and so is this.
    items.sort_by(|left, right| {
        let key = |item: &Value| {
            (
                item.get("type").and_then(Value::as_str) != Some("personal"),
                item.get("created_at")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            )
        };
        key(left).cmp(&key(right))
    });
    json!({
        "active_workspace": WorkspaceOsStore::active_workspace_id(&state),
        "workspaces": items,
        "roles": WORKSPACE_ROLES,
        "permissions": permission_matrix(),
    })
}

/// `get_workspace` — the record, or not-found. Permission is the caller's job.
pub fn get_workspace(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: Option<&str>,
) -> Result<Value, StoreError> {
    let state = store.load_state();
    let workspace = workspace_of(&state, workspace_id)
        .ok_or_else(|| StoreError::NotFound(workspace_id.to_string()))?;
    Ok(workspace_public(workspace, user_id))
}

/// `workspace_summary` — the public record plus per-area counts.
pub fn workspace_summary(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: Option<&str>,
) -> Result<Value, StoreError> {
    let state = store.load_state();
    let workspace = workspace_of(&state, workspace_id)
        .ok_or_else(|| StoreError::NotFound(workspace_id.to_string()))?;
    let mut public = workspace_public(workspace, user_id);
    let scoped_len =
        |key: &str| WorkspaceOsStore::scoped(listify(state.get(key)), Some(workspace_id)).len();
    public["counts"] = json!({
        "snapshots": scoped_len("snapshots"),
        "memories": scoped_len("memories"),
        "memory_snapshots": scoped_len("memory_snapshots"),
        "agent_runs": scoped_len("agent_runs"),
        "handoffs": scoped_len("handoffs"),
        "workflows": scoped_len("workflows"),
        "workflow_runs": scoped_len("workflow_runs"),
        "traces": scoped_len("traces"),
        "timeline": scoped_len("timeline"),
    });
    Ok(public)
}

/// `create_organization_workspace` — the id is a slug, de-duplicated by suffix.
pub fn create_organization_workspace(
    store: &WorkspaceOsStore,
    name: &str,
    owner_user_id: Option<&str>,
    settings: Option<Value>,
) -> Result<Value, StoreError> {
    if name.trim().is_empty() {
        return Err(StoreError::Value("workspace name is required".into()));
    }
    let outcome = store.mutate(|state| {
        let existing: Vec<String> = state
            .get("workspaces")
            .and_then(Value::as_object)
            .map(|map| map.keys().cloned().collect())
            .unwrap_or_default();
        let base = safe_slug(&format!("org-{name}"));
        let mut workspace_id = base.clone();
        let mut suffix = 2;
        while existing.contains(&workspace_id) {
            workspace_id = format!("{base}-{suffix}");
            suffix += 1;
        }
        let record = new_workspace_record(
            &workspace_id,
            name.trim(),
            "organization",
            owner_user_id,
            settings.clone(),
            None,
        )
        .map_err(StoreError::Value)?;
        if let Some(map) = state.get_mut("workspaces").and_then(Value::as_object_mut) {
            map.insert(workspace_id.clone(), record.clone());
        }
        Ok((workspace_id, record))
    })?;
    let (workspace_id, record) = outcome;
    store.record_timeline_event(
        "workspace",
        "workspace_created",
        json!({"workspace_id": workspace_id, "type": "organization"}),
        None,
    );
    Ok(workspace_public(&record, owner_user_id))
}

/// `update_workspace` — rename and/or merge settings.
pub fn update_workspace(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    name: Option<&str>,
    settings: Option<&Value>,
    actor: Option<&str>,
) -> Result<Value, StoreError> {
    let record = store.mutate(|state| {
        let workspace = load_org(state, workspace_id)?;
        require_permission(workspace, actor, "manage_workspace")?;
        if let Some(name) = name.filter(|value| !value.trim().is_empty()) {
            workspace["name"] = json!(name.trim());
        }
        if let Some(Value::Object(incoming)) = settings {
            let mut merged = match workspace.get("settings") {
                Some(Value::Object(current)) => current.clone(),
                _ => Map::new(),
            };
            for (key, value) in incoming {
                merged.insert(key.clone(), value.clone());
            }
            workspace["settings"] = Value::Object(merged);
        } else if let Some(other) = settings.filter(|value| !value.is_null()) {
            workspace["settings"] = other.clone();
        }
        workspace["updated_at"] = json!(now_iso());
        Ok(workspace.clone())
    })?;
    store.record_timeline_event(
        "workspace",
        "workspace_updated",
        json!({"workspace_id": workspace_id}),
        None,
    );
    Ok(workspace_public(&record, actor))
}

/// `archive_workspace` — soft archive. Data is never deleted.
pub fn archive_workspace(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    actor: Option<&str>,
) -> Result<Value, StoreError> {
    let record = store.mutate(|state| {
        let workspace = load_org(state, workspace_id)?;
        require_permission(workspace, actor, "manage_workspace")?;
        workspace["status"] = json!("archived");
        workspace["updated_at"] = json!(now_iso());
        let archived = workspace.clone();
        if state.get("active_workspace").and_then(Value::as_str) == Some(workspace_id) {
            state["active_workspace"] = json!(DEFAULT_WORKSPACE_ID);
        }
        Ok(archived)
    })?;
    store.record_timeline_event(
        "workspace",
        "workspace_archived",
        json!({"workspace_id": workspace_id}),
        None,
    );
    Ok(workspace_public(&record, actor))
}

/// `add_member` — insert or re-role one identity.
pub fn add_member(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: &str,
    role: &str,
    actor: Option<&str>,
) -> Result<Value, StoreError> {
    if !WORKSPACE_ROLES.contains(&role) {
        return Err(StoreError::Value(format!("unknown role: {role}")));
    }
    if user_id.trim().is_empty() {
        return Err(StoreError::Value("user_id is required".into()));
    }
    let record = store.mutate(|state| {
        let workspace = load_org(state, workspace_id)?;
        require_permission(workspace, actor, "manage_members")?;
        let mut members = listify(workspace.get("members"));
        match members
            .iter_mut()
            .find(|member| member.get("user_id").and_then(Value::as_str) == Some(user_id))
        {
            Some(existing) => {
                existing["role"] = json!(role);
                existing["updated_at"] = json!(now_iso());
            }
            None => members.push(json!({
                "user_id": user_id, "role": role, "added_at": now_iso(),
            })),
        }
        workspace["members"] = Value::Array(members);
        workspace["updated_at"] = json!(now_iso());
        Ok(workspace.clone())
    })?;
    store.record_timeline_event(
        "workspace",
        "member_added",
        json!({"workspace_id": workspace_id, "user_id": user_id, "role": role}),
        None,
    );
    Ok(workspace_public(&record, actor))
}

/// `update_member_role` — the owner cannot be demoted.
pub fn update_member_role(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: &str,
    role: &str,
    actor: Option<&str>,
) -> Result<Value, StoreError> {
    if !WORKSPACE_ROLES.contains(&role) {
        return Err(StoreError::Value(format!("unknown role: {role}")));
    }
    let record = store.mutate(|state| {
        let workspace = load_org(state, workspace_id)?;
        require_permission(workspace, actor, "manage_members")?;
        if workspace.get("owner_user_id").and_then(Value::as_str) == Some(user_id)
            && role != "owner"
        {
            return Err(StoreError::Value(
                "cannot demote the workspace owner".into(),
            ));
        }
        let mut members = listify(workspace.get("members"));
        let member = members
            .iter_mut()
            .find(|member| member.get("user_id").and_then(Value::as_str) == Some(user_id))
            .ok_or_else(|| StoreError::NotFound(user_id.to_string()))?;
        member["role"] = json!(role);
        member["updated_at"] = json!(now_iso());
        workspace["members"] = Value::Array(members);
        workspace["updated_at"] = json!(now_iso());
        Ok(workspace.clone())
    })?;
    store.record_timeline_event(
        "workspace",
        "member_role_updated",
        json!({"workspace_id": workspace_id, "user_id": user_id, "role": role}),
        None,
    );
    Ok(workspace_public(&record, actor))
}

/// `remove_member` — the owner cannot be removed.
pub fn remove_member(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: &str,
    actor: Option<&str>,
) -> Result<Value, StoreError> {
    let record = store.mutate(|state| {
        let workspace = load_org(state, workspace_id)?;
        require_permission(workspace, actor, "manage_members")?;
        if workspace.get("owner_user_id").and_then(Value::as_str) == Some(user_id) {
            return Err(StoreError::Value(
                "cannot remove the workspace owner".into(),
            ));
        }
        let members = listify(workspace.get("members"));
        let kept: Vec<Value> = members
            .iter()
            .filter(|member| member.get("user_id").and_then(Value::as_str) != Some(user_id))
            .cloned()
            .collect();
        if kept.len() == members.len() {
            return Err(StoreError::NotFound(user_id.to_string()));
        }
        workspace["members"] = Value::Array(kept);
        workspace["updated_at"] = json!(now_iso());
        Ok(workspace.clone())
    })?;
    store.record_timeline_event(
        "workspace",
        "member_removed",
        json!({"workspace_id": workspace_id, "user_id": user_id}),
        None,
    );
    Ok(workspace_public(&record, actor))
}

/// `set_active_workspace` — membership is enforced for organizations.
pub fn set_active_workspace(
    store: &WorkspaceOsStore,
    workspace_id: &str,
    user_id: Option<&str>,
) -> Result<Value, StoreError> {
    let record = store.mutate(|state| {
        let workspace = workspace_of(state, workspace_id)
            .ok_or_else(|| StoreError::NotFound(workspace_id.to_string()))?
            .clone();
        if workspace.get("type").and_then(Value::as_str) == Some("organization")
            && member_role(&workspace, user_id).is_none()
        {
            return Err(StoreError::Permission(format!(
                "'{}' is not a member of '{workspace_id}'",
                user_id
                    .filter(|value| !value.is_empty())
                    .unwrap_or("anonymous"),
            )));
        }
        state["active_workspace"] = json!(workspace_id);
        Ok(workspace)
    })?;
    store.record_timeline_event(
        "workspace",
        "workspace_activated",
        json!({"workspace_id": workspace_id}),
        None,
    );
    Ok(workspace_public(&record, user_id))
}

/// Every permission a role holds, for callers rendering the matrix.
pub fn role_permissions() -> &'static [(&'static str, &'static [&'static str])] {
    &ROLE_PERMISSIONS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    #[test]
    fn a_created_organization_is_slugged_and_deduplicated() {
        let (_dir, store) = store();
        let first =
            create_organization_workspace(&store, "Fixture Team", Some("user:1"), None).unwrap();
        assert_eq!(first["workspace_id"], json!("org-Fixture-Team"));
        assert_eq!(first["your_role"], json!("owner"));
        assert_eq!(first["member_count"], json!(1));
        let second =
            create_organization_workspace(&store, "Fixture Team", Some("user:1"), None).unwrap();
        assert_eq!(second["workspace_id"], json!("org-Fixture-Team-2"));
        assert_eq!(
            create_organization_workspace(&store, "   ", None, None).unwrap_err(),
            StoreError::Value("workspace name is required".into())
        );
    }

    #[test]
    fn the_registry_hides_organizations_a_stranger_is_not_in() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        let owner_view = list_workspaces(&store, Some("user:1"));
        assert_eq!(owner_view["workspaces"].as_array().unwrap().len(), 2);
        // Personal sorts first.
        assert_eq!(owner_view["workspaces"][0]["type"], json!("personal"));
        let stranger = list_workspaces(&store, Some("user:9"));
        assert_eq!(stranger["workspaces"].as_array().unwrap().len(), 1);
        // No identity at all sees every workspace, with no role in the orgs.
        let anonymous = list_workspaces(&store, None);
        assert_eq!(anonymous["workspaces"].as_array().unwrap().len(), 2);
        assert_eq!(anonymous["workspaces"][1]["your_role"], Value::Null);
        assert_eq!(anonymous["roles"], json!(WORKSPACE_ROLES));
    }

    #[test]
    fn membership_is_added_re_roled_and_removed() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        let id = "org-Team";
        let added = add_member(&store, id, "user:2", "member", Some("user:1")).unwrap();
        assert_eq!(added["member_count"], json!(2));
        let re_added = add_member(&store, id, "user:2", "viewer", Some("user:1")).unwrap();
        assert_eq!(re_added["member_count"], json!(2));
        assert_eq!(re_added["members"][1]["role"], json!("viewer"));

        assert_eq!(
            update_member_role(&store, id, "user:2", "admin", Some("user:1")).unwrap()["members"]
                [1]["role"],
            json!("admin")
        );
        assert_eq!(
            update_member_role(&store, id, "user:1", "member", Some("user:1")).unwrap_err(),
            StoreError::Value("cannot demote the workspace owner".into())
        );
        assert_eq!(
            update_member_role(&store, id, "user:ghost", "member", Some("user:1")).unwrap_err(),
            StoreError::NotFound("user:ghost".into())
        );
        assert_eq!(
            remove_member(&store, id, "user:1", Some("user:1")).unwrap_err(),
            StoreError::Value("cannot remove the workspace owner".into())
        );
        assert_eq!(
            remove_member(&store, id, "user:2", Some("user:1")).unwrap()["member_count"],
            json!(1)
        );
        assert_eq!(
            remove_member(&store, id, "user:2", Some("user:1")).unwrap_err(),
            StoreError::NotFound("user:2".into())
        );
    }

    #[test]
    fn an_unknown_role_and_an_empty_user_id_are_refused_before_anything_loads() {
        let (_dir, store) = store();
        assert_eq!(
            add_member(&store, "org-x", "user:2", "boss", Some("user:1")).unwrap_err(),
            StoreError::Value("unknown role: boss".into())
        );
        assert_eq!(
            add_member(&store, "org-x", "  ", "member", Some("user:1")).unwrap_err(),
            StoreError::Value("user_id is required".into())
        );
        assert_eq!(
            update_member_role(&store, "org-x", "u", "boss", None).unwrap_err(),
            StoreError::Value("unknown role: boss".into())
        );
    }

    #[test]
    fn a_non_member_cannot_manage_and_an_unknown_workspace_is_not_found() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        add_member(&store, "org-Team", "user:2", "member", Some("user:1")).unwrap();
        let refusal =
            add_member(&store, "org-Team", "user:3", "member", Some("user:2")).unwrap_err();
        assert!(matches!(refusal, StoreError::Permission(_)));
        assert_eq!(
            refusal.to_string(),
            "'user:2' lacks 'manage_members' on workspace 'org-Team'"
        );
        assert_eq!(
            update_workspace(&store, "org-nope", Some("x"), None, Some("user:1")).unwrap_err(),
            StoreError::NotFound("org-nope".into())
        );
        // Personal is not an organization, so the org routes refuse it.
        assert_eq!(
            archive_workspace(&store, "personal", Some("user:1")).unwrap_err(),
            StoreError::Value("operation only valid for organization workspaces".into())
        );
    }

    #[test]
    fn update_merges_settings_and_archive_falls_back_to_personal() {
        let (_dir, store) = store();
        create_organization_workspace(
            &store,
            "Team",
            Some("user:1"),
            Some(json!({"tier": "team", "keep": 1})),
        )
        .unwrap();
        let updated = update_workspace(
            &store,
            "org-Team",
            Some("  Renamed  "),
            Some(&json!({"tier": "pro"})),
            Some("user:1"),
        )
        .unwrap();
        assert_eq!(updated["name"], json!("Renamed"));
        assert_eq!(updated["settings"], json!({"tier": "pro", "keep": 1}));

        set_active_workspace(&store, "org-Team", Some("user:1")).unwrap();
        assert_eq!(
            list_workspaces(&store, Some("user:1"))["active_workspace"],
            json!("org-Team")
        );
        let archived = archive_workspace(&store, "org-Team", Some("user:1")).unwrap();
        assert_eq!(archived["status"], json!("archived"));
        assert_eq!(
            list_workspaces(&store, Some("user:1"))["active_workspace"],
            json!("personal")
        );
    }

    #[test]
    fn activation_requires_membership_and_a_known_workspace() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        assert_eq!(
            set_active_workspace(&store, "org-nope", Some("user:1")).unwrap_err(),
            StoreError::NotFound("org-nope".into())
        );
        let refusal = set_active_workspace(&store, "org-Team", Some("user:9")).unwrap_err();
        assert_eq!(
            refusal,
            StoreError::Permission("'user:9' is not a member of 'org-Team'".into())
        );
        assert_eq!(
            set_active_workspace(&store, "personal", Some("user:9")).unwrap()["type"],
            json!("personal")
        );
    }

    #[test]
    fn the_summary_counts_only_this_workspaces_records() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        store
            .mutate(|state| {
                state["memories"] = json!([
                    {"id": "a", "workspace_id": "org-Team"},
                    {"id": "b", "workspace_id": "personal"},
                    {"id": "c"},
                ]);
                Ok(())
            })
            .unwrap();
        let summary = workspace_summary(&store, "org-Team", Some("user:1")).unwrap();
        assert_eq!(summary["counts"]["memories"], json!(1));
        assert_eq!(summary["counts"]["timeline"], json!(0));
        let personal = workspace_summary(&store, "personal", Some("user:1")).unwrap();
        assert_eq!(personal["counts"]["memories"], json!(2));
        assert_eq!(
            workspace_summary(&store, "org-nope", None).unwrap_err(),
            StoreError::NotFound("org-nope".into())
        );
    }

    #[test]
    fn permission_questions_answer_without_raising() {
        let (_dir, store) = store();
        create_organization_workspace(&store, "Team", Some("user:1"), None).unwrap();
        assert!(has_permission(&store, "org-Team", Some("user:1"), "write"));
        assert!(!has_permission(&store, "org-Team", Some("user:9"), "read"));
        assert!(!has_permission(&store, "org-nope", Some("user:1"), "read"));
        assert!(has_permission(&store, "personal", Some("anyone"), "write"));
        assert_eq!(
            get_member_role(&store, "org-Team", Some("user:1")).unwrap(),
            Some("owner".into())
        );
        assert_eq!(
            get_member_role(&store, "org-nope", None).unwrap_err(),
            StoreError::NotFound("org-nope".into())
        );
        assert_eq!(role_permissions().len(), 4);
        assert_eq!(
            get_workspace(&store, "personal", None).unwrap()["your_role"],
            json!("owner")
        );
    }
}
