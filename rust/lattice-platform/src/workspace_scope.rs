//! The shared workspace-scope resolver, re-exported for the integrator.
//!
//! Python's `latticeai/api/workspace_scope.py` has no routes of its own —
//! eight routers call it. I2 already ported the HTTP selector
//! (`requested_workspace` / `resolve_workspace_scope`). Membership lives
//! here: [`crate::workspace::WorkspaceService`] implements
//! [`lattice_auth::WorkspaceResolver`] over the native registry, so a
//! named workspace is gated on `read`/`write` instead of passing through.

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
pub use crate::workspace::{WorkspaceService, WorkspaceState};
pub use lattice_auth::{
    requested_workspace, resolve_workspace_scope, workspace_scope_from_request, ScopeMode,
    WorkspaceResolver, WORKSPACE_HEADER, WORKSPACE_PARAM,
};
