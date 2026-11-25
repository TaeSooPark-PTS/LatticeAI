use axum::http::{header, HeaderMap};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map};

use crate::enterprise::{constant_time_eq, is_valid_tenant_id, tenant_of_user};
use crate::policy::normalize_role;
use crate::state::AuthState;
use crate::users::{ensure_user_identity, normalize_email};

#[derive(Debug, Clone)]
pub struct ScimConfig {
    pub enabled: bool,
    pub bearer_token: String,
    pub tenant_id: Option<String>,
    pub allowed_domains: Vec<String>,
    pub default_role: String,
    pub allow_admin_provisioning: bool,
}

impl Default for ScimConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            bearer_token: String::new(),
            tenant_id: None,
            allowed_domains: Vec::new(),
            default_role: "user".into(),
            allow_admin_provisioning: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScimUserProvision {
    pub user_name: String,
    pub email: String,
    pub display_name: String,
    pub active: bool,
    pub role: Option<String>,
    pub tenant_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScimProvisionResult {
    pub email: String,
    pub active: bool,
    pub role: String,
    pub tenant_id: Option<String>,
    pub revoked_sessions: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScimPolicyError {
    Disabled,
    Unauthorized,
    InvalidPayload(String),
    DomainNotAllowed(String),
    CrossTenantViolation,
    RoleEscalationDenied(String),
}

impl std::fmt::Display for ScimPolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ScimPolicyError::Disabled => write!(formatter, "SCIM provisioning is disabled"),
            ScimPolicyError::Unauthorized => write!(formatter, "invalid SCIM bearer token"),
            ScimPolicyError::InvalidPayload(msg) => write!(formatter, "{msg}"),
            ScimPolicyError::DomainNotAllowed(domain) => {
                write!(formatter, "SCIM email domain '{domain}' is not allowed")
            }
            ScimPolicyError::CrossTenantViolation => {
                write!(formatter, "SCIM request crosses tenant boundary")
            }
            ScimPolicyError::RoleEscalationDenied(role) => {
                write!(formatter, "SCIM may not assign role '{role}'")
            }
        }
    }
}

pub fn authorize_scim_request(
    config: &ScimConfig,
    headers: &HeaderMap,
) -> Result<(), ScimPolicyError> {
    if !config.enabled || config.bearer_token.trim().is_empty() {
        return Err(ScimPolicyError::Disabled);
    }
    let Some(raw) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
    else {
        return Err(ScimPolicyError::Unauthorized);
    };
    let Some(token) = raw
        .strip_prefix("Bearer ")
        .or_else(|| raw.strip_prefix("bearer "))
        .map(str::trim)
    else {
        return Err(ScimPolicyError::Unauthorized);
    };
    if !constant_time_eq(token.as_bytes(), config.bearer_token.trim().as_bytes()) {
        return Err(ScimPolicyError::Unauthorized);
    }
    Ok(())
}

pub fn apply_scim_user(
    state: &AuthState,
    config: &ScimConfig,
    payload: &ScimUserProvision,
) -> Result<ScimProvisionResult, ScimPolicyError> {
    if !config.enabled {
        return Err(ScimPolicyError::Disabled);
    }
    let email = normalize_email(if payload.email.trim().is_empty() {
        &payload.user_name
    } else {
        &payload.email
    });
    let Some((_, domain)) = email.split_once('@') else {
        return Err(ScimPolicyError::InvalidPayload(
            "SCIM user must supply a valid email".into(),
        ));
    };
    if domain.is_empty() || domain.contains(char::is_whitespace) {
        return Err(ScimPolicyError::InvalidPayload(
            "SCIM user email domain is invalid".into(),
        ));
    }
    if !config.allowed_domains.is_empty()
        && !config
            .allowed_domains
            .iter()
            .any(|allowed| allowed.trim().eq_ignore_ascii_case(domain))
    {
        return Err(ScimPolicyError::DomainNotAllowed(domain.to_string()));
    }

    let bound_tenant = config
        .tenant_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());
    let req_tenant = payload
        .tenant_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());
    if let Some(req) = req_tenant {
        if !is_valid_tenant_id(req) {
            return Err(ScimPolicyError::InvalidPayload(
                "invalid SCIM tenant_id".into(),
            ));
        }
        if let Some(bound) = bound_tenant {
            if bound != req {
                return Err(ScimPolicyError::CrossTenantViolation);
            }
        }
    }
    let effective_tenant = bound_tenant.or(req_tenant).map(str::to_string);

    let requested_role = payload.role.as_deref().unwrap_or(&config.default_role);
    let role = normalize_role(requested_role).to_string();
    if role == "owner" || (role == "admin" && !config.allow_admin_provisioning) {
        return Err(ScimPolicyError::RoleEscalationDenied(role));
    }

    let mut users = state.users().load();
    let mut record = if let Some(existing) = users.get(&email).cloned() {
        if let (Some(existing_tenant), Some(eff_tenant)) =
            (tenant_of_user(&existing), effective_tenant.as_deref())
        {
            if existing_tenant != eff_tenant {
                state.record_audit(
                    "auth.scim.denied",
                    json!({
                        "email": email,
                        "reason": "cross_tenant_conflict",
                        "existing_tenant": existing_tenant,
                        "requested_tenant": eff_tenant,
                    }),
                );
                return Err(ScimPolicyError::CrossTenantViolation);
            }
        }
        existing
    } else {
        Map::new()
    };

    record.insert("role".into(), json!(role));
    record.insert("disabled".into(), json!(!payload.active));
    if !payload.display_name.trim().is_empty() {
        record.insert("name".into(), json!(payload.display_name.trim()));
    }
    if let Some(ref tenant) = effective_tenant {
        record.insert("tenant_id".into(), json!(tenant));
    }
    ensure_user_identity(&email, &mut record);
    users.insert(&email, record);
    state.users().save(&users);

    let revoked_sessions = if !payload.active {
        state.sessions().revoke_for_email(&email)
    } else {
        0
    };

    let event = if payload.active {
        "auth.scim.provisioned"
    } else {
        "auth.scim.deprovisioned"
    };
    state.record_audit(
        event,
        json!({
            "email": email,
            "role": role,
            "tenant_id": effective_tenant,
            "active": payload.active,
            "revoked_sessions": revoked_sessions,
        }),
    );

    Ok(ScimProvisionResult {
        email,
        active: payload.active,
        role,
        tenant_id: effective_tenant,
        revoked_sessions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::Clock;
    use crate::config::AuthConfig;
    use crate::enterprise::MemoryAuditSink;
    use std::collections::HashMap;
    use std::sync::Arc;

    fn test_state(dir: &std::path::Path) -> (Arc<AuthState>, Arc<MemoryAuditSink>) {
        let mut env = HashMap::new();
        env.insert("LATTICEAI_REQUIRE_AUTH".into(), "true".into());
        env.insert(
            "LATTICEAI_DATA_DIR".into(),
            dir.to_string_lossy().into_owned(),
        );
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = dir.to_path_buf();
        let state = AuthState::with_clock(config, Clock::frozen(1_000.0));
        let sink = Arc::new(MemoryAuditSink::new());
        state.set_audit_sink(sink.clone());
        (state, sink)
    }

    #[test]
    fn scim_provisioning_revokes_sessions_and_isolates_tenants() {
        let dir = tempfile::tempdir().expect("tempdir");
        let (state, sink) = test_state(dir.path());
        let config = ScimConfig {
            enabled: true,
            bearer_token: "scim-secret-token".into(),
            tenant_id: Some("acme".into()),
            allowed_domains: vec!["acme.test".into()],
            default_role: "user".into(),
            allow_admin_provisioning: false,
        };

        let mut bad_headers = HeaderMap::new();
        bad_headers.insert(header::AUTHORIZATION, "Bearer wrong".parse().unwrap());
        assert_eq!(
            authorize_scim_request(&config, &bad_headers),
            Err(ScimPolicyError::Unauthorized)
        );
        let mut good_headers = HeaderMap::new();
        good_headers.insert(
            header::AUTHORIZATION,
            "Bearer scim-secret-token".parse().unwrap(),
        );
        assert!(authorize_scim_request(&config, &good_headers).is_ok());

        let escalated = ScimUserProvision {
            user_name: "alice@acme.test".into(),
            email: "alice@acme.test".into(),
            display_name: "Alice".into(),
            active: true,
            role: Some("admin".into()),
            tenant_id: Some("acme".into()),
        };
        assert!(matches!(
            apply_scim_user(&state, &config, &escalated),
            Err(ScimPolicyError::RoleEscalationDenied(_))
        ));

        let valid = ScimUserProvision {
            role: Some("user".into()),
            ..escalated.clone()
        };
        let res = apply_scim_user(&state, &config, &valid).expect("provision");
        assert!(res.active);
        let tok = state
            .sessions()
            .create("user:alice", Some("alice@acme.test"));
        assert_eq!(
            state.sessions().get_email(&tok).as_deref(),
            Some("alice@acme.test")
        );

        let other_tenant_cfg = ScimConfig {
            tenant_id: Some("globex".into()),
            allowed_domains: vec![],
            ..config.clone()
        };
        assert_eq!(
            apply_scim_user(
                &state,
                &other_tenant_cfg,
                &ScimUserProvision {
                    tenant_id: Some("globex".into()),
                    active: false,
                    ..valid.clone()
                }
            ),
            Err(ScimPolicyError::CrossTenantViolation)
        );

        let deprov = apply_scim_user(
            &state,
            &config,
            &ScimUserProvision {
                active: false,
                ..valid
            },
        )
        .expect("deprovision");
        assert!(!deprov.active);
        assert_eq!(deprov.revoked_sessions, 1);
        assert_eq!(state.sessions().get_email(&tok), None);
        assert!(sink
            .events()
            .iter()
            .any(|(ev, _)| ev == "auth.scim.deprovisioned"));
    }
}
