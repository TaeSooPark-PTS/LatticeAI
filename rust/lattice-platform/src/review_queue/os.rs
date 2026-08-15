//! Workspace-scoping helper for the review family.
//!
//! This module used to carry a *second* implementation of workspace-OS
//! load/save — its own default document, a shallow merge, a `"11.5.2"` version
//! literal and its own SQLite mirror — beside
//! [`crate::workspace::WorkspaceOsStore`]. Two writers over one file is
//! last-writer-wins, so v11.7.0 deleted this half:
//! [`crate::review_queue::GovernanceState`] now goes through the store. What is
//! left is the one pure function that half owned.

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
