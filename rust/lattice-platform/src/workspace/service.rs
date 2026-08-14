//! Permission-aware façade over the workspace registry.
//!
//! Port of `latticeai/services/workspace_service.py`. This is also the
//! [`lattice_auth::WorkspaceResolver`] the rest of the product hands to
//! `resolve_workspace_scope` so scoping guards stop being a pass-through.

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
use std::sync::Arc;

use lattice_auth::WorkspaceResolver;
use serde_json::{json, Value};

use super::constants::SHARED_GLOBAL_AREAS;
use super::memories;
use super::orgs;
use super::store::{StoreError, WorkspaceOsStore};

/// How an email (or already-stable `user:…` id) becomes the id membership
/// records carry.
pub type IdentityFn = Arc<dyn Fn(Option<&str>) -> Option<String> + Send + Sync>;

/// Permission-aware façade over [`WorkspaceOsStore`].
#[derive(Clone)]
pub struct WorkspaceService {
    store: Arc<WorkspaceOsStore>,
    resolve_user: IdentityFn,
}

impl WorkspaceService {
    /// Wrap a store. `resolve_user` is `Users::user_id_for_email`.
    pub fn new(store: Arc<WorkspaceOsStore>, resolve_user: IdentityFn) -> Self {
        Self {
            store,
            resolve_user,
        }
    }

    /// The store this façade writes.
    pub fn store(&self) -> &WorkspaceOsStore {
        &self.store
    }

    /// `WorkspaceService._identity`.
    pub fn identity(&self, user_id: Option<&str>) -> Option<String> {
        let user_id = user_id.filter(|value| !value.is_empty())?;
        if user_id.starts_with("user:") {
            return Some(user_id.to_string());
        }
        (self.resolve_user)(Some(user_id))
    }

    fn ensure_permission(
        &self,
        workspace_id: &str,
        user_id: Option<&str>,
        permission: &str,
    ) -> Result<(), StoreError> {
        let resolved = self.identity(user_id);
        if orgs::has_permission(&self.store, workspace_id, resolved.as_deref(), permission) {
            return Ok(());
        }
        Err(StoreError::Permission(format!(
            "'{}' lacks '{permission}' on workspace '{workspace_id}'",
            user_id
                .filter(|value| !value.is_empty())
                .unwrap_or("anonymous"),
        )))
    }

    /// `resolve_read_scope` — `None` falls back to the active workspace.
    pub fn resolve_read(
        &self,
        requested: Option<&str>,
        user_id: Option<&str>,
    ) -> Result<String, StoreError> {
        let workspace_id = requested
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| WorkspaceOsStore::active_workspace_id(&self.store.load_state()));
        self.ensure_permission(&workspace_id, user_id, "read")?;
        Ok(workspace_id)
    }

    /// `resolve_write_scope`.
    pub fn resolve_write(
        &self,
        requested: Option<&str>,
        user_id: Option<&str>,
    ) -> Result<String, StoreError> {
        let workspace_id = requested
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| WorkspaceOsStore::active_workspace_id(&self.store.load_state()));
        self.ensure_permission(&workspace_id, user_id, "write")?;
        Ok(workspace_id)
    }

    /// `authorize_record_read` — a record without `workspace_id` stays public.
    pub fn authorize_record_read(
        &self,
        record: &Value,
        user_id: Option<&str>,
    ) -> Result<(), StoreError> {
        let workspace_id = record
            .get("workspace_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        if let Some(workspace_id) = workspace_id {
            self.ensure_permission(workspace_id, user_id, "read")?;
        }
        Ok(())
    }

    /// `authorize_memory_delete`.
    pub fn authorize_memory_delete(
        &self,
        record: &Value,
        user_id: Option<&str>,
    ) -> Result<(), StoreError> {
        let resolved = self.identity(user_id);
        memories::authorize_delete(&self.store, record, user_id, resolved.as_deref())
    }

    /// `summary` — store summary plus the membership-filtered registry.
    pub fn summary(&self, user_id: Option<&str>) -> Value {
        let mut data = self.store.summary();
        data["workspace_registry"] =
            orgs::list_workspaces(&self.store, self.identity(user_id).as_deref());
        data["shared_global_areas"] = json!(SHARED_GLOBAL_AREAS);
        data
    }

    /// `list_workspaces`.
    pub fn list_workspaces(&self, user_id: Option<&str>) -> Value {
        orgs::list_workspaces(&self.store, self.identity(user_id).as_deref())
    }

    /// `get_workspace` — permission is checked *before* the lookup.
    pub fn get_workspace(
        &self,
        workspace_id: &str,
        user_id: Option<&str>,
    ) -> Result<Value, StoreError> {
        self.ensure_permission(workspace_id, user_id, "read")?;
        orgs::get_workspace(&self.store, workspace_id, self.identity(user_id).as_deref())
    }

    /// `workspace_summary`.
    pub fn workspace_summary(
        &self,
        workspace_id: &str,
        user_id: Option<&str>,
    ) -> Result<Value, StoreError> {
        self.ensure_permission(workspace_id, user_id, "read")?;
        orgs::workspace_summary(&self.store, workspace_id, self.identity(user_id).as_deref())
    }

    /// `create_organization_workspace`.
    pub fn create_organization_workspace(
        &self,
        name: &str,
        owner_user_id: Option<&str>,
        settings: Option<Value>,
    ) -> Result<Value, StoreError> {
        orgs::create_organization_workspace(
            &self.store,
            name,
            self.identity(owner_user_id).as_deref(),
            settings,
        )
    }

    /// `update_workspace`.
    pub fn update_workspace(
        &self,
        workspace_id: &str,
        name: Option<&str>,
        settings: Option<&Value>,
        actor: Option<&str>,
    ) -> Result<Value, StoreError> {
        orgs::update_workspace(
            &self.store,
            workspace_id,
            name,
            settings,
            self.identity(actor).as_deref(),
        )
    }

    /// `archive_workspace`.
    pub fn archive_workspace(
        &self,
        workspace_id: &str,
        actor: Option<&str>,
    ) -> Result<Value, StoreError> {
        orgs::archive_workspace(&self.store, workspace_id, self.identity(actor).as_deref())
    }

    /// `add_member`.
    pub fn add_member(
        &self,
        workspace_id: &str,
        user_id: &str,
        role: &str,
        actor: Option<&str>,
    ) -> Result<Value, StoreError> {
        let member = self
            .identity(Some(user_id))
            .unwrap_or_else(|| user_id.to_string());
        orgs::add_member(
            &self.store,
            workspace_id,
            &member,
            role,
            self.identity(actor).as_deref(),
        )
    }

    /// `update_member_role`.
    pub fn update_member_role(
        &self,
        workspace_id: &str,
        user_id: &str,
        role: &str,
        actor: Option<&str>,
    ) -> Result<Value, StoreError> {
        let member = self
            .identity(Some(user_id))
            .unwrap_or_else(|| user_id.to_string());
        orgs::update_member_role(
            &self.store,
            workspace_id,
            &member,
            role,
            self.identity(actor).as_deref(),
        )
    }

    /// `remove_member`.
    pub fn remove_member(
        &self,
        workspace_id: &str,
        user_id: &str,
        actor: Option<&str>,
    ) -> Result<Value, StoreError> {
        let member = self
            .identity(Some(user_id))
            .unwrap_or_else(|| user_id.to_string());
        orgs::remove_member(
            &self.store,
            workspace_id,
            &member,
            self.identity(actor).as_deref(),
        )
    }

    /// `set_active_workspace`.
    pub fn set_active_workspace(
        &self,
        workspace_id: &str,
        user_id: Option<&str>,
    ) -> Result<Value, StoreError> {
        orgs::set_active_workspace(&self.store, workspace_id, self.identity(user_id).as_deref())
    }
}

impl WorkspaceResolver for WorkspaceService {
    fn resolve_read_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String> {
        self.resolve_read(requested, user)
            .map(Some)
            .map_err(|error| error.to_string())
    }

    fn resolve_write_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String> {
        self.resolve_write(requested, user)
            .map(Some)
            .map_err(|error| error.to_string())
    }
}
