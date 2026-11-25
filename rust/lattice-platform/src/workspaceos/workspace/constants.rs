pub const WORKSPACE_OS_VERSION: &str = env!("CARGO_PKG_VERSION");

pub const WORKSPACE_TYPES: [&str; 2] = ["personal", "organization"];

pub const DEFAULT_WORKSPACE_ID: &str = "personal";

pub const WORKSPACE_ROLES: [&str; 4] = ["owner", "admin", "member", "viewer"];

pub const WORKSPACE_PERMISSIONS: [&str; 4] =
    ["read", "write", "manage_members", "manage_workspace"];

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

pub fn role_grants(role: &str, permission: &str) -> bool {
    ROLE_PERMISSIONS
        .iter()
        .find(|(name, _)| *name == role)
        .is_some_and(|(_, grants)| grants.contains(&permission))
}

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

pub const ONBOARDING_STATUSES: [&str; 5] = ["pending", "running", "complete", "failed", "skipped"];

pub const MEMORY_KINDS: [&str; 7] = [
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
];

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

pub const DEFAULT_COMPUTER_MEMORY_SCOPES: [&str; 3] = ["Downloads", "Documents", "Repositories"];

pub const COMPUTER_MEMORY_NOTICE: &str =
    "Local Computer Memory is OFF by default and requires explicit approval.";

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
