use std::collections::BTreeSet;
use std::sync::Arc;

use lattice_platform::workspaceos::workspace::WorkspaceService;
use lattice_retrieval::search_api::AllowedScopes;
use serde_json::Value;

#[derive(Clone)]
pub struct WorkspaceScopes {
    service: Arc<WorkspaceService>,
}

impl WorkspaceScopes {
    pub fn new(service: Arc<WorkspaceService>) -> Self {
        Self { service }
    }

    fn ids(listing: &Value) -> BTreeSet<String> {
        listing
            .get("workspaces")
            .unwrap_or(listing)
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
    fn the_service_listing_object_yields_its_workspace_ids() {
        let listing = json!({
            "active_workspace": "personal",
            "workspaces": [
                {"workspace_id": "personal", "id": "personal", "type": "personal"},
                {"workspace_id": "org-1", "id": "org-1", "type": "organization"},
            ],
            "roles": ["owner"],
        });
        assert_eq!(
            WorkspaceScopes::ids(&listing),
            BTreeSet::from(["personal".to_string(), "org-1".to_string()]),
            "list_workspaces answers an object, and reading it as an array left every \
             signed-in search with no workspace at all"
        );
        assert!(WorkspaceScopes::ids(&json!({"workspaces": {}})).is_empty());
    }

    #[test]
    fn a_shape_this_adapter_does_not_understand_is_an_empty_set() {
        for listing in [json!({}), json!(null), json!("personal")] {
            assert!(WorkspaceScopes::ids(&listing).is_empty(), "{listing}");
        }
    }
}
