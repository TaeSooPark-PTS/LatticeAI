use crate::kernel::policy::ToolPolicy;
use crate::tools::sandbox::ToolError;

const DEFAULT_ROLE: &str = "user";

pub fn normalize_role(role: &str) -> String {
    let lowered = role.trim().to_lowercase();
    if lowered.is_empty() {
        return DEFAULT_ROLE.to_string();
    }
    lattice_auth::normalize_role(&lowered).to_string()
}

pub fn role_has_capability(role: &str, capability: &str) -> bool {
    lattice_auth::role_has_capability(&normalize_role(role), capability)
}

pub fn is_admin_only(policy: &ToolPolicy) -> bool {
    policy.sandbox() == "system" || matches!(policy.risk(), "exec" | "destructive")
}

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
