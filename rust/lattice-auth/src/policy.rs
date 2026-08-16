//! Role → capability, ported from `latticeai/core/policy.py`.
//!
//! `owner` holds the wildcard `all` and is what the trusted local owner is
//! projected as; every other role is an explicit set. An unknown role is
//! `user`, never a denial — the store may hold a role this build predates and
//! the safe reading of that is "an ordinary account", not "locked out".

/// Every role the community policy names, with its capabilities.
const ROLE_CAPABILITIES: &[(&str, &[&str])] = &[
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
    (
        "user",
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

/// `normalize_role`: lowercase, and anything unrecognised becomes `user`.
pub fn normalize_role(role: &str) -> &'static str {
    let lowered = if role.is_empty() {
        "user".to_string()
    } else {
        role.to_lowercase()
    };
    ROLE_CAPABILITIES
        .iter()
        .map(|(name, _)| *name)
        .find(|name| *name == lowered)
        .unwrap_or("user")
}

/// `capabilities_for_role`, already sorted (the table above is stored sorted,
/// which is what Python's `sorted(caps)` returns).
pub fn capabilities_for_role(role: &str) -> &'static [&'static str] {
    let normalized = normalize_role(role);
    ROLE_CAPABILITIES
        .iter()
        .find(|(name, _)| *name == normalized)
        .map(|(_, caps)| *caps)
        .unwrap_or(&[])
}

/// `role_has_capability`: the wildcard `all` satisfies everything.
pub fn role_has_capability(role: &str, capability: &str) -> bool {
    let caps = capabilities_for_role(role);
    caps.contains(&"all") || caps.contains(&capability)
}

/// The composable check every guard reads. `Err` carries the message Python's
/// `PermissionError` carries, for callers that surface it.
pub fn check_role(role: &str, capability: &str) -> Result<(), String> {
    if role_has_capability(role, capability) {
        Ok(())
    } else {
        Err(format!(
            "role '{}' lacks capability '{}'",
            normalize_role(role),
            capability
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_roles_become_user() {
        assert_eq!(normalize_role("ADMIN"), "admin");
        assert_eq!(normalize_role("wizard"), "user");
        assert_eq!(normalize_role(""), "user");
        assert_eq!(normalize_role("Owner"), "owner");
    }

    #[test]
    fn the_owner_wildcard_covers_everything() {
        assert!(role_has_capability("owner", "admin:users"));
        assert!(role_has_capability("owner", "anything-at-all"));
        assert!(check_role("owner", "admin:users").is_ok());
    }

    #[test]
    fn the_admin_capability_separates_admin_from_user() {
        assert!(role_has_capability("admin", "admin:users"));
        assert!(!role_has_capability("user", "admin:users"));
        assert!(!role_has_capability("viewer", "workspace:write"));
        assert!(role_has_capability("member", "workspace:write"));
        assert_eq!(
            check_role("viewer", "admin:users").unwrap_err(),
            "role 'viewer' lacks capability 'admin:users'"
        );
    }

    #[test]
    fn capabilities_come_back_sorted() {
        for (_, caps) in ROLE_CAPABILITIES {
            let mut sorted = caps.to_vec();
            sorted.sort_unstable();
            assert_eq!(&sorted[..], *caps);
        }
        assert_eq!(capabilities_for_role("viewer").len(), 3);
    }

    #[test]
    fn user_is_member_under_another_name() {
        // Python spells both rows out and they are identical. `lattice-agent`'s
        // tool authorizer folds an unknown role onto `user` and looks it up
        // here, so the day these two diverge is the day that fold changes
        // meaning — which is a decision, not a typo.
        assert_eq!(
            capabilities_for_role("user"),
            capabilities_for_role("member")
        );
    }
}
