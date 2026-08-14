//! Workspace OS vocabulary: types, roles, permissions, areas, defaults.
//!
//! Port of `latticeai/core/workspace_os_constants.py`. Everything here is a
//! name the behaviour in the sibling modules is written against, so it is
//! stated once and never re-spelled.
//!
//! `WORKSPACE_OS_VERSION` is deliberately the **crate** version rather than a
//! literal: Python keeps `"11.5.2"` in lockstep with the product version
//! through `scripts/bump_version.py`, and `rust/Cargo.toml`'s
//! `workspace.package.version` is bumped by the same script. Reading it from
//! `CARGO_PKG_VERSION` means a bump cannot leave the two disagreeing.

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
/// `WORKSPACE_OS_VERSION` — stamped into every state document and snapshot.
pub const WORKSPACE_OS_VERSION: &str = env!("CARGO_PKG_VERSION");

/// The two kinds of workspace. Single-user `personal`, shared `organization`.
pub const WORKSPACE_TYPES: [&str; 2] = ["personal", "organization"];

/// The workspace every install starts with and falls back to.
pub const DEFAULT_WORKSPACE_ID: &str = "personal";

/// Role hierarchy for organization workspaces, in Python's declared order.
pub const WORKSPACE_ROLES: [&str; 4] = ["owner", "admin", "member", "viewer"];

/// The capability-style permissions roles are checked against.
pub const WORKSPACE_PERMISSIONS: [&str; 4] =
    ["read", "write", "manage_members", "manage_workspace"];

/// `ROLE_PERMISSIONS` — the permissions each role holds.
///
/// Python stores a `set` per role and every reader either tests membership or
/// emits `sorted(perms)`; the arrays below are already in sorted order, which
/// is what `list_workspaces` and `new_workspace_record` serialize.
pub const ROLE_PERMISSIONS: [(&str, &[&str]); 4] = [
    (
        "owner",
        &["manage_members", "manage_workspace", "read", "write"],
    ),
    (
        "admin",
        &["manage_members", "manage_workspace", "read", "write"],
    ),
    ("member", &["read", "write"]),
    ("viewer", &["read"]),
];

/// Whether `role` holds `permission`. Unknown role ⇒ `false`.
pub fn role_grants(role: &str, permission: &str) -> bool {
    ROLE_PERMISSIONS
        .iter()
        .find(|(name, _)| *name == role)
        .is_some_and(|(_, grants)| grants.contains(&permission))
}

/// The navigation areas a workspace exposes.
pub const WORKSPACE_AREAS: [&str; 9] = [
    "graph",
    "snapshot",
    "memory",
    "agent",
    "workflow",
    "plugins",
    "skills",
    "marketplace",
    "timeline",
];

/// The named first-run steps, in order. `current_step` advances through this.
pub const ONBOARDING_STEPS: [&str; 9] = [
    "account",
    "admin",
    "hardware",
    "model_recommendation",
    "model_install",
    "model_connection",
    "folder_connection",
    "first_question",
    "complete",
];

/// Statuses one onboarding step may hold (`workspace_onboarding.py`).
pub const ONBOARDING_STATUSES: [&str; 5] = ["pending", "running", "complete", "failed", "skipped"];

/// The memory kinds `upsert_memory` accepts.
pub const MEMORY_KINDS: [&str; 7] = [
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
];

/// Timeline event types that are additionally emitted as execution events.
pub const EXECUTION_EVENT_TYPES: [&str; 15] = [
    "agent_started",
    "handoff_created",
    "handoff_accepted",
    "handoff_completed",
    "review_requested",
    "review_approved",
    "review_rejected",
    "retry_requested",
    "workflow_started",
    "workflow_completed",
    "plugin_started",
    "plugin_completed",
    "execution_failed",
    "execution_cancelled",
    "execution_interrupted",
];

/// The five agents a fresh state document ships with, in Python's order.
pub const DEFAULT_AGENTS: [(&str, &str, &str, &[&str]); 5] = [
    (
        "agent:planner",
        "Planner",
        "Breaks workspace goals into executable plans.",
        &["agent:executor", "agent:reviewer"],
    ),
    (
        "agent:executor",
        "Executor",
        "Runs approved tool and code workflows.",
        &["agent:planner", "agent:reviewer"],
    ),
    (
        "agent:reviewer",
        "Reviewer",
        "Checks outputs, tests, and regressions.",
        &["agent:executor", "agent:release"],
    ),
    (
        "agent:researcher",
        "Researcher",
        "Finds and curates relevant workspace knowledge.",
        &["agent:planner"],
    ),
    (
        "agent:release",
        "Release Agent",
        "Coordinates versioning, packaging, and release checks.",
        &["agent:reviewer"],
    ),
];

/// `feature_flags` as a brand-new state document carries them, in order.
pub const DEFAULT_FEATURE_FLAGS: [(&str, bool); 22] = [
    ("workspace_os", true),
    ("graph_trace", true),
    ("snapshots", true),
    ("personal_memory", true),
    ("multi_agent_graph", true),
    ("workflow_graph", true),
    ("skill_marketplace", true),
    ("local_computer_memory", false),
    ("organization_workspaces", true),
    ("enterprise_seam", true),
    ("plugin_sdk", true),
    ("workflow_designer", true),
    ("multi_agent_runtime", true),
    ("realtime_collaboration", true),
    ("agent_handoff", true),
    ("agent_context_packets", true),
    ("review_retry_loops", true),
    ("timeline_replay", true),
    ("agent_memory", true),
    ("agent_planning", true),
    ("marketplace_foundation", true),
    ("realtime_execution_observability", true),
];

/// Folders Local Computer Memory offers to watch when none are named.
pub const DEFAULT_COMPUTER_MEMORY_SCOPES: [&str; 3] = ["Downloads", "Documents", "Repositories"];

/// The consent notice a fresh `computer_memory` block carries.
pub const COMPUTER_MEMORY_NOTICE: &str =
    "Local Computer Memory is OFF by default and requires explicit approval.";

/// Graph and installed skills are machine-global, not per-workspace.
/// `WorkspaceService.SHARED_GLOBAL_AREAS`.
pub const SHARED_GLOBAL_AREAS: [&str; 2] = ["graph", "skills"];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_role_table_answers_the_four_permissions() {
        for permission in WORKSPACE_PERMISSIONS {
            assert!(role_grants("owner", permission));
            assert!(role_grants("admin", permission));
        }
        assert!(role_grants("member", "read"));
        assert!(role_grants("member", "write"));
        assert!(!role_grants("member", "manage_members"));
        assert!(role_grants("viewer", "read"));
        assert!(!role_grants("viewer", "write"));
        assert!(!role_grants("stranger", "read"));
    }

    #[test]
    fn every_role_in_the_table_is_a_declared_role() {
        for (role, _) in ROLE_PERMISSIONS {
            assert!(WORKSPACE_ROLES.contains(&role), "{role}");
        }
        assert_eq!(ROLE_PERMISSIONS.len(), WORKSPACE_ROLES.len());
    }

    #[test]
    fn the_permission_lists_are_sorted_the_way_python_emits_them() {
        for (_, grants) in ROLE_PERMISSIONS {
            let mut sorted = grants.to_vec();
            sorted.sort_unstable();
            assert_eq!(grants.to_vec(), sorted);
        }
    }
}
