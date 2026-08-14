//! `ToolDispatchService.check_role` — the one authorization the seam owned.
//!
//! `POST /agent/tool` runs two checks the loop's permission kernel does not:
//! an **admin-only** gate over tools that reach outside the workspace or exec,
//! and a **capability** gate over the scoped knowledge tools. Both read the
//! caller's role, which is why they lived on the Python side — it is the
//! process that holds `users.json`.
//!
//! Making a tool native moves the execution but must not drop the check, so it
//! is ported here and takes the role as **input** (`ToolConfig::role`, wired by
//! the gateway from the authenticated session). The default is Python's own
//! default, `"user"`, so an unwired host is as restrictive as an unconfigured
//! `ToolDispatchService`, never more permissive.

use crate::policy::ToolPolicy;
use crate::sandbox::ToolError;

/// `ROLE_CAPABILITIES` (`latticeai.core.policy`). `owner` holds `all`.
const ROLE_CAPABILITIES: [(&str, &[&str]); 4] = [
    ("owner", &["all"]),
    (
        "admin",
        &[
            "admin:audit",
            "admin:policies",
            "admin:roles",
            "admin:security",
            "admin:users",
            "chat",
            "desktop:control",
            "files",
            "pipeline",
            "search",
            "workspace:manage",
            "workspace:members",
            "workspace:read",
            "workspace:write",
        ],
    ),
    (
        "member",
        &[
            "chat",
            "files",
            "pipeline",
            "search",
            "workspace:read",
            "workspace:write",
        ],
    ),
    ("viewer", &["chat", "search", "workspace:read"]),
];

/// `user` is the fallback role and shares `member`'s capabilities.
const DEFAULT_ROLE: &str = "user";

/// `normalize_role`: lowercased, and anything unknown is `user`.
pub fn normalize_role(role: &str) -> String {
    let lowered = role.trim().to_lowercase();
    let lowered = if lowered.is_empty() {
        DEFAULT_ROLE.to_string()
    } else {
        lowered
    };
    if lowered == DEFAULT_ROLE || ROLE_CAPABILITIES.iter().any(|(name, _)| *name == lowered) {
        lowered
    } else {
        DEFAULT_ROLE.to_string()
    }
}

/// `role_has_capability`.
pub fn role_has_capability(role: &str, capability: &str) -> bool {
    let role = normalize_role(role);
    // `user` is not a table row in this port because it is byte-identical to
    // `member`; Python spells both out and the equality is asserted below.
    let lookup = if role == DEFAULT_ROLE {
        "member"
    } else {
        &role
    };
    let Some((_, capabilities)) = ROLE_CAPABILITIES.iter().find(|(name, _)| *name == lookup) else {
        return false;
    };
    capabilities.contains(&"all") || capabilities.contains(&capability)
}

/// `ToolRegistry.admin_only_tools`: derived from the policy, never listed.
///
/// `{name for name, policy in governance.items() if policy["sandbox"] ==
/// "system" or policy["risk"] in {"exec", "destructive"}}` — a derivation, so a
/// new exec tool is admin-only the moment it is registered rather than the
/// moment somebody remembers to add it to a list.
pub fn is_admin_only(policy: &ToolPolicy) -> bool {
    policy.sandbox() == "system" || matches!(policy.risk(), "exec" | "destructive")
}

/// The two role denials, in Python's order and with Python's messages.
///
/// `check_role` reads `policy_for(tool, {})` — the **registry** entry, not the
/// argument-rewritten policy — so a blocked-prefix write is not turned into an
/// admin-only tool by the rewrite. Callers pass `PolicyTable::get(tool)`.
pub fn check_role(tool: &str, policy: &ToolPolicy, role: &str) -> Result<(), ToolError> {
    let admin_only = is_admin_only(policy);
    let capability = policy.capability.as_deref().unwrap_or("");
    if !admin_only && capability.is_empty() {
        return Ok(());
    }
    let role = normalize_role(role);
    if admin_only && !matches!(role.as_str(), "admin" | "owner") {
        return Err(ToolError::tool(format!("'{tool}' 툴은 관리자 전용입니다.")));
    }
    if !capability.is_empty() && !role_has_capability(&role, capability) {
        return Err(ToolError::tool(format!(
            "'{tool}' 툴에는 '{capability}' capability가 필요합니다."
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy(risk: &str, sandbox: &str, capability: Option<&str>) -> ToolPolicy {
        ToolPolicy {
            risk: risk.into(),
            sandbox: sandbox.into(),
            capability: capability.map(String::from),
            ..ToolPolicy::default()
        }
    }

    #[test]
    fn unknown_roles_and_case_collapse_onto_user() {
        assert_eq!(normalize_role("OWNER"), "owner");
        assert_eq!(normalize_role(" Admin "), "admin");
        assert_eq!(normalize_role(""), "user");
        assert_eq!(normalize_role("root"), "user");
        assert_eq!(normalize_role("user"), "user");
    }

    #[test]
    fn user_and_member_hold_the_same_capabilities() {
        for capability in [
            "workspace:read",
            "workspace:write",
            "chat",
            "search",
            "files",
            "pipeline",
        ] {
            assert!(role_has_capability("user", capability), "{capability}");
            assert!(role_has_capability("member", capability), "{capability}");
        }
        assert!(!role_has_capability("user", "desktop:control"));
        assert!(!role_has_capability("viewer", "workspace:write"));
        assert!(role_has_capability("admin", "desktop:control"));
        // `owner` holds the wildcard, so every capability answers true.
        assert!(role_has_capability("owner", "anything-at-all"));
    }

    #[test]
    fn admin_only_is_derived_from_sandbox_and_risk() {
        assert!(is_admin_only(&policy("read", "system", None)), "screenshot");
        assert!(is_admin_only(&policy("exec", "workspace", None)), "exec");
        assert!(is_admin_only(&policy("destructive", "workspace", None)));
        assert!(!is_admin_only(&policy("write", "workspace", None)));
        assert!(!is_admin_only(&policy("read", "home", None)), "local_read");
    }

    #[test]
    fn an_ordinary_write_needs_no_role_at_all() {
        for role in ["user", "viewer", "", "nonsense"] {
            check_role("write_file", &policy("write", "workspace", None), role)
                .expect("no gate applies");
        }
    }

    #[test]
    fn the_exec_tools_are_admin_only_with_pythons_message() {
        let exec = policy("exec", "workspace", None);
        let error = check_role("run_command", &exec, "user").expect_err("denied");
        assert_eq!(error.message, "'run_command' 툴은 관리자 전용입니다.");
        check_role("run_command", &exec, "admin").expect("admin runs it");
        check_role("run_command", &exec, "owner").expect("owner runs it");
    }

    #[test]
    fn a_capability_tool_names_the_capability_it_wanted() {
        let save = policy("write", "workspace", Some("workspace:write"));
        check_role("knowledge_save", &save, "user").expect("user may write knowledge");
        let error = check_role("knowledge_save", &save, "viewer").expect_err("denied");
        assert_eq!(
            error.message,
            "'knowledge_save' 툴에는 'workspace:write' capability가 필요합니다."
        );
    }

    #[test]
    fn admin_only_is_checked_before_the_capability() {
        // `computer_screenshot` is both (system sandbox + desktop:control); the
        // admin message is the one Python raises first.
        let both = policy("read", "system", Some("desktop:control"));
        assert_eq!(
            check_role("computer_status", &both, "viewer")
                .expect_err("denied")
                .message,
            "'computer_status' 툴은 관리자 전용입니다."
        );
        check_role("computer_status", &both, "admin").expect("admin holds both");
    }
}
