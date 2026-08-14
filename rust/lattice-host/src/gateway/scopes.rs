//! Workspace membership, as the retrieval routes want to ask about it.
//!
//! Two crates need the same fact — *which workspaces may this person see?* —
//! and they ask for it through two different traits, because neither crate
//! depends on the other:
//!
//! * `lattice_auth::WorkspaceResolver` resolves **one** requested scope into
//!   the one the caller is allowed to read or write, and
//!   `lattice_platform::workspace::WorkspaceService` implements it directly;
//! * `lattice_retrieval::search_api::AllowedScopes` asks for the **set**, which
//!   the search lanes intersect a row's `workspace_id` against.
//!
//! This adapter is the join. It exists in `lattice-host` rather than in either
//! crate because the gateway is the only place that knows both are talking
//! about the same registry — and because the alternative, passing `None`, is
//! the documented "no membership known" contract, under which every named
//! workspace passes ungated (WP-I2 §2). Scoping that never says no is not
//! scoping.

use std::collections::BTreeSet;
use std::sync::Arc;

use lattice_platform::workspace::WorkspaceService;
use lattice_retrieval::search_api::AllowedScopes;
use serde_json::Value;

/// `PLATFORM.allowed_scopes(user)` over the native workspace registry.
#[derive(Clone)]
pub struct WorkspaceScopes {
    service: Arc<WorkspaceService>,
}

impl WorkspaceScopes {
    /// Wrap the resolver the gateway already built.
    pub fn new(service: Arc<WorkspaceService>) -> Self {
        Self { service }
    }

    /// The ids in a `list_workspaces` answer.
    ///
    /// `list_workspaces` already applies membership — an organization workspace
    /// the caller does not belong to is not in the array — so this reads ids
    /// rather than re-deciding anything. A row without a usable `id` is skipped
    /// instead of being turned into an empty scope, which would match rows that
    /// carry no workspace at all.
    fn ids(listing: &Value) -> BTreeSet<String> {
        listing
            .as_array()
            .map(|rows| {
                rows.iter()
                    .filter_map(|row| row.get("id").and_then(Value::as_str))
                    .filter(|id| !id.is_empty())
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default()
    }
}

impl AllowedScopes for WorkspaceScopes {
    fn allowed_scopes(&self, user: &str) -> BTreeSet<String> {
        // The empty email is the trusted local owner (WP-I2 §2's rule the VS
        // Code extension depends on); `list_workspaces(None)` is the unfiltered
        // registry, which is the right answer for the machine's owner.
        let user = (!user.is_empty()).then_some(user);
        Self::ids(&self.service.list_workspaces(user))
    }
}

impl std::fmt::Debug for WorkspaceScopes {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkspaceScopes").finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn ids_are_read_and_unusable_rows_are_skipped() {
        let listing = json!([
            {"id": "personal", "type": "personal"},
            {"id": "", "type": "organization"},
            {"type": "organization"},
            {"id": "org-1", "type": "organization"},
        ]);
        assert_eq!(
            WorkspaceScopes::ids(&listing),
            BTreeSet::from(["personal".to_string(), "org-1".to_string()]),
            "an empty or missing id would otherwise become a scope that matches \
             rows carrying no workspace at all"
        );
    }

    #[test]
    fn a_shape_this_adapter_does_not_understand_is_an_empty_set() {
        for listing in [json!({}), json!(null), json!("personal")] {
            assert!(WorkspaceScopes::ids(&listing).is_empty(), "{listing}");
        }
    }
}
