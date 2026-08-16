//! Workspace-scoping helper for the review family.
//!
//! This module used to carry a *second* implementation of workspace-OS
//! load/save — its own default document, a shallow merge, a `"11.5.2"` version
//! literal and its own SQLite mirror — beside
//! [`crate::workspace::WorkspaceOsStore`]. Two writers over one file is
//! last-writer-wins, so v11.7.0 deleted this half:
//! [`crate::review_queue::GovernanceState`] now goes through the store. What is
//! left is the one pure function that half owned.

use serde_json::Value;

use super::DEFAULT_WORKSPACE_ID;

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
