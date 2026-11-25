use std::path::Path;

use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::internal::json_from_ordered;
use super::COMMUNITY_NOTICE;
use crate::knowledge::portability;

pub fn poc_overview() -> OrderedMap {
    let mut edition = OrderedMap::new();
    edition.insert("edition", json!("community"));
    edition.insert("is_enterprise", json!(false));
    let mut capabilities = OrderedMap::new();
    for cap in [
        "sso_advanced",
        "idp_provisioning",
        "scim",
        "rbac_abac_advanced",
        "tenant_isolation",
        "compliance_retention",
        "siem_export",
        "private_vpc",
        "air_gapped_deployment",
        "dlp_policy",
        "ediscovery",
        "admin_policy_packs",
        "graph_promotion_review",
    ] {
        capabilities.insert(cap, json!(false));
    }
    edition.insert("capabilities", json_from_ordered(&capabilities));
    edition.insert(
        "community_notice",
        json!("All listed capabilities are Enterprise-only extension points. The open-source Community edition ships none of them enabled; see docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md."),
    );

    let mut admin_policies = OrderedMap::new();
    admin_policies.insert("capability", json!("admin_policy_packs"));
    admin_policies.insert("enabled", json!(false));
    admin_policies.insert("enforced", json!(false));
    let mut effective = OrderedMap::new();
    effective.insert("base_roles", json!(["owner", "admin", "member", "viewer"]));
    effective.insert(
        "local_file_access",
        json!("approval-token gated (per path/user/action)"),
    );
    effective.insert("package_install", json!("admin-only with audit trail"));
    effective.insert("network_binding", json!("127.0.0.1 by default"));
    effective.insert("managed_policy_packs", json!([]));
    admin_policies.insert("effective_policy", json_from_ordered(&effective));
    admin_policies.insert("note", json!(COMMUNITY_NOTICE));

    let mut local_export = OrderedMap::new();
    local_export.insert("available", json!(true));
    local_export.insert("endpoint", json!("/admin/security/export"));
    local_export.insert("formats", json!(["json", "csv", "xlsx", "txt", "pdf"]));
    local_export.insert(
        "note",
        json!("Community local audit export is always available to admins."),
    );
    let mut siem_streaming = OrderedMap::new();
    siem_streaming.insert("capability", json!("siem_export"));
    siem_streaming.insert("enabled", json!(false));
    siem_streaming.insert("note", json!(COMMUNITY_NOTICE));
    let mut retention = OrderedMap::new();
    retention.insert("capability", json!("compliance_retention"));
    retention.insert("enabled", json!(false));
    retention.insert("note", json!(COMMUNITY_NOTICE));
    let mut audit_export = OrderedMap::new();
    audit_export.insert("local_export", json_from_ordered(&local_export));
    audit_export.insert("siem_streaming", json_from_ordered(&siem_streaming));
    audit_export.insert("compliance_retention", json_from_ordered(&retention));

    let mut org = OrderedMap::new();
    let mut baseline = OrderedMap::new();
    baseline.insert("workspaces", json!(["personal", "organization"]));
    baseline.insert("roles", json!(["owner", "admin", "member", "viewer"]));
    baseline.insert(
        "data_isolation",
        json!("single-tenant local storage (~/.ltcai)"),
    );
    org.insert("community_baseline", json_from_ordered(&baseline));
    let mut gov = OrderedMap::new();
    for cap in [
        "tenant_isolation",
        "rbac_abac_advanced",
        "scim",
        "idp_provisioning",
        "sso_advanced",
        "dlp_policy",
        "ediscovery",
        "private_vpc",
        "air_gapped_deployment",
    ] {
        gov.insert(cap, json!(false));
    }
    org.insert("governance_capabilities", json_from_ordered(&gov));
    org.insert("note", json!(COMMUNITY_NOTICE));

    let mut out = OrderedMap::new();
    out.insert("edition", json_from_ordered(&edition));
    out.insert("admin_policies", json_from_ordered(&admin_policies));
    out.insert("audit_export", json_from_ordered(&audit_export));
    out.insert("siem_export", json_from_ordered(&siem_export_stub()));
    out.insert("organization_settings", json_from_ordered(&org));
    out
}

pub fn siem_export_stub() -> OrderedMap {
    let mut record = OrderedMap::new();
    record.insert("ts", json!("1970-01-01T00:00:00Z"));
    record.insert("actor", json!("admin@example.com"));
    record.insert("act", json!("model_load"));
    record.insert("sev", json!("informational"));
    record.insert("kind", json!("audit_event"));
    record.insert("id", json!("evt_sample"));
    let mut envelope = OrderedMap::new();
    envelope.insert("format", json!("ltcai.siem.v1"));
    envelope.insert("encoding", json!("ndjson"));
    envelope.insert("vendor", json!("LatticeAI"));
    envelope.insert("product", json!("Workspace OS"));
    envelope.insert("records", json!([json_from_ordered(&record)]));
    let mut out = OrderedMap::new();
    out.insert("capability", json!("siem_export"));
    out.insert("enabled", json!(false));
    out.insert("streamed", json!(false));
    out.insert("destination", Value::Null);
    out.insert("preview_envelope", json_from_ordered(&envelope));
    out.insert("note", json!(COMMUNITY_NOTICE));
    out
}

pub fn default_product_hardening(
    data_dir: &Path,
    host: &str,
    port: u16,
    auth_required: bool,
) -> OrderedMap {
    default_product_hardening_probed(data_dir, host, port, auth_required, &which)
}

pub fn default_product_hardening_probed(
    data_dir: &Path,
    host: &str,
    port: u16,
    auth_required: bool,
    probe: &dyn Fn(&str) -> bool,
) -> OrderedMap {
    let env_flag = |key: &str| -> bool {
        std::env::var(key)
            .ok()
            .map(|v| {
                matches!(
                    v.trim().to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes" | "on"
                )
            })
            .unwrap_or(false)
    };
    let present = |keys: &[&str]| {
        keys.iter().any(|k| {
            std::env::var(k)
                .map(|v| !v.trim().is_empty())
                .unwrap_or(false)
        })
    };

    let mut startup = OrderedMap::new();
    startup.insert("local_only_default", json!(true));
    startup.insert("host", json!(host));
    startup.insert("port", json!(port));
    startup.insert("network_exposed", json!(false));
    startup.insert("auth_required", json!(auth_required));
    startup.insert(
        "cors_network_allowed",
        json!(env_flag("LATTICEAI_CORS_ALLOW_NETWORK")),
    );

    let mut updater = OrderedMap::new();
    updater.insert("enabled", json!(env_flag("LATTICEAI_ENABLE_UPDATES")));
    updater.insert(
        "limitation",
        json!("No external update checks run unless explicitly enabled by policy."),
    );
    let mut desktop = OrderedMap::new();
    desktop.insert("sidecar_lifecycle", json!("managed"));
    desktop.insert("restart_supported", json!(true));
    desktop.insert("shutdown_supported", json!(true));
    desktop.insert("updater", json_from_ordered(&updater));

    let mut first_run = OrderedMap::new();
    first_run.insert("data_dir", json!(data_dir.display().to_string()));
    first_run.insert("data_dir_exists", json!(data_dir.exists()));
    first_run.insert(
        "python_available",
        json!(probe("python3") || probe("python")),
    );
    first_run.insert("docker_available", json!(probe("docker")));
    first_run.insert("docker_required", json!(false));
    first_run.insert("postgres_required", json!(false));

    let integration = |enabled: bool, cred: bool, detail: &str| {
        let mut m = OrderedMap::new();
        m.insert("enabled", json!(enabled));
        m.insert("credential_present", json!(cred));
        m.insert("opt_in_required", json!(true));
        m.insert("automatic_egress", json!(enabled));
        m.insert("detail", json!(detail));
        json_from_ordered(&m)
    };
    let mut integrations = OrderedMap::new();
    integrations.insert(
        "telegram",
        integration(
            env_flag("LATTICEAI_ENABLE_TELEGRAM"),
            present(&["LATTICEAI_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"]),
            if env_flag("LATTICEAI_ENABLE_TELEGRAM") {
                "enabled by LATTICEAI_ENABLE_TELEGRAM"
            } else {
                "disabled; token presence alone does not start Telegram"
            },
        ),
    );
    integrations.insert(
        "brain_network",
        integration(
            env_flag("LATTICEAI_BRAIN_NETWORK_AUTO_PUSH"),
            false,
            "peer pushes are user/admin initiated; no automatic peer sync by default",
        ),
    );
    integrations.insert(
        "updates",
        integration(
            env_flag("LATTICEAI_ENABLE_UPDATES"),
            false,
            "desktop updater checks are disabled unless LATTICEAI_ENABLE_UPDATES is true",
        ),
    );
    integrations.insert(
        "model_downloads",
        integration(
            env_flag("LATTICEAI_ALLOW_MODEL_DOWNLOADS"),
            present(&["HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"]),
            "model downloads require an explicit load/autoload setting",
        ),
    );
    integrations.insert(
        "docker",
        integration(
            env_flag("LATTICEAI_DOCKER_AUTO_START"),
            false,
            "Docker setup requires explicit runtime consent; auto-start is disabled by default",
        ),
    );
    integrations.insert(
        "postgres",
        integration(
            false,
            false,
            "Postgres scale mode is used only when storage engine and DSN are explicitly configured",
        ),
    );
    integrations.insert(
        "external_connectors",
        integration(
            env_flag("LATTICEAI_ENABLE_EXTERNAL_CONNECTORS"),
            present(&[
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GITHUB_TOKEN",
                "SLACK_BOT_TOKEN",
                "DISCORD_BOT_TOKEN",
            ]),
            "connector credentials are inert until the connector is explicitly enabled and invoked",
        ),
    );
    let mut privacy = OrderedMap::new();
    privacy.insert("local_only_default", json!(true));
    privacy.insert("integrations", json_from_ordered(&integrations));

    let mut permissions = OrderedMap::new();
    permissions.insert("export_requires_admin", json!(true));
    permissions.insert("import_requires_admin", json!(true));
    permissions.insert("restore_requires_admin", json!(true));
    permissions.insert("destructive_restore_requires_confirmation", json!(true));
    permissions.insert("workspace_isolation_enforced", json!(true));
    permissions.insert("audit_log_visible_to_admin", json!(true));

    let mut failure = OrderedMap::new();
    failure.insert("archive_corruption", json!("fail_closed"));
    failure.insert("partial_archive", json!("fail_closed"));
    failure.insert("signature_mismatch", json!("fail_closed"));
    failure.insert("unsupported_version", json!("fail_closed"));
    failure.insert("missing_docker", json!("honest_unavailable"));
    failure.insert("missing_postgres", json!("honest_unavailable"));
    failure.insert("permission_denied", json!("honest_error"));

    let mut out = OrderedMap::new();
    out.insert("version", json!(env!("CARGO_PKG_VERSION")));
    out.insert("startup", json_from_ordered(&startup));
    out.insert("desktop", json_from_ordered(&desktop));
    out.insert("first_run", json_from_ordered(&first_run));
    out.insert("privacy", json_from_ordered(&privacy));
    let exports = data_dir.join(lattice_core::db::tables::state_files::WORKSPACE_EXPORTS);
    let backup = portability::backup_health_payload(&exports);
    let mut storage = OrderedMap::new();
    storage.insert("available", json!(true));
    storage.insert(
        "active",
        json!(portability::sqlite_capabilities(
            &data_dir.join("knowledge_graph.sqlite")
        )),
    );
    storage.insert("postgres", json!(portability::postgres_capabilities()));
    storage.insert("backup_health", json!(backup.clone()));
    out.insert("storage", json_from_ordered(&storage));
    out.insert("backup", json!(backup));
    let identity = crate::knowledge::network::DeviceIdentity::load_or_create(
        &data_dir.join(lattice_core::db::tables::state_files::DEVICE_IDENTITY),
    );
    out.insert("device_identity", json!(identity.describe()));
    out.insert("permissions", json_from_ordered(&permissions));
    out.insert("failure_policy", json_from_ordered(&failure));
    out
}

fn which(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths).any(|dir| {
                let p = dir.join(name);
                p.is_file()
            })
        })
        .unwrap_or(false)
}

const ORG_POLICIES_FILE: &str = "org_governance_policies.json";
const DEFAULT_TENANT_SCOPE: &str = "personal";
const SENSITIVE_AUDIT_KEYS: &[&str] = &[
    "passphrase",
    "password",
    "token",
    "secret",
    "api_key",
    "private_key",
    "encryption_key",
];

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct OrganizationGovernancePolicy {
    pub organization_id: String,
    pub enforce_tenant_isolation: bool,
    pub allow_cross_org_share: bool,
    pub require_review_for_shares: bool,
    pub allow_multimodal_capture: bool,
    pub allowed_modalities: Vec<String>,
    pub audit_retention_days: u32,
    pub updated_at: Option<String>,
}

impl OrganizationGovernancePolicy {
    pub fn default_for(org_id: &str) -> Self {
        Self {
            organization_id: normalize_tenant_scope(Some(org_id)).to_string(),
            enforce_tenant_isolation: true,
            allow_cross_org_share: false,
            require_review_for_shares: true,
            allow_multimodal_capture: true,
            allowed_modalities: vec!["speech".into(), "vision".into(), "image_retrieval".into()],
            audit_retention_days: 90,
            updated_at: None,
        }
    }

    pub fn permits_modality(&self, modality: &str) -> bool {
        self.allow_multimodal_capture
            && self
                .allowed_modalities
                .iter()
                .any(|m| m.eq_ignore_ascii_case(modality))
    }
}

pub fn normalize_tenant_scope(scope: Option<&str>) -> &str {
    match scope.map(str::trim) {
        Some(s) if !s.is_empty() => s,
        _ => DEFAULT_TENANT_SCOPE,
    }
}

pub fn load_org_governance_policy(data_dir: &Path, org_id: &str) -> OrganizationGovernancePolicy {
    let normalized = normalize_tenant_scope(Some(org_id));
    let path = data_dir.join(ORG_POLICIES_FILE);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return OrganizationGovernancePolicy::default_for(normalized);
    };
    let Ok(map) = serde_json::from_str::<serde_json::Map<String, Value>>(&text) else {
        return OrganizationGovernancePolicy::default_for(normalized);
    };
    map.get(normalized)
        .and_then(|v| serde_json::from_value::<OrganizationGovernancePolicy>(v.clone()).ok())
        .unwrap_or_else(|| OrganizationGovernancePolicy::default_for(normalized))
}

pub fn save_org_governance_policy(
    data_dir: &Path,
    org_id: &str,
    policy: &OrganizationGovernancePolicy,
    actor_email: Option<&str>,
) -> Result<OrganizationGovernancePolicy, String> {
    let normalized = normalize_tenant_scope(Some(org_id)).to_string();
    if policy.audit_retention_days == 0 {
        return Err("audit_retention_days must be at least 1.".into());
    }
    let mut stored = policy.clone();
    stored.organization_id = normalized.clone();
    stored.updated_at = Some(super::internal::now_iso());

    let path = data_dir.join(ORG_POLICIES_FILE);
    let mut all = std::fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Map<String, Value>>(&t).ok())
        .unwrap_or_default();
    let encoded = serde_json::to_value(&stored).map_err(|e| e.to_string())?;
    all.insert(normalized.clone(), encoded);
    let json_text = serde_json::to_string_pretty(&all).map_err(|e| e.to_string())?;
    lattice_auth::atomic::write_text(&path, &json_text);

    let mut audit = serde_json::Map::new();
    audit.insert("organization_id".into(), json!(normalized));
    audit.insert(
        "enforce_tenant_isolation".into(),
        json!(stored.enforce_tenant_isolation),
    );
    audit.insert(
        "allow_cross_org_share".into(),
        json!(stored.allow_cross_org_share),
    );
    audit.insert(
        "allow_multimodal_capture".into(),
        json!(stored.allow_multimodal_capture),
    );
    audit.insert(
        "audit_retention_days".into(),
        json!(stored.audit_retention_days),
    );
    emit_sanitized_governance_audit(
        data_dir,
        "org_policy_updated",
        actor_email,
        Some(&stored.organization_id),
        audit,
    );
    Ok(stored)
}

pub fn validate_tenant_scope(
    data_dir: &Path,
    actor_workspace: Option<&str>,
    target_workspace: Option<&str>,
    actor_email: Option<&str>,
    operation: &str,
) -> Result<(), String> {
    let actor_scope = normalize_tenant_scope(actor_workspace);
    let target_scope = normalize_tenant_scope(target_workspace);
    let policy = load_org_governance_policy(data_dir, actor_scope);
    if !policy.enforce_tenant_isolation || actor_scope == target_scope {
        return Ok(());
    }
    let mut details = serde_json::Map::new();
    details.insert("actor_workspace".into(), json!(actor_scope));
    details.insert("target_workspace".into(), json!(target_scope));
    details.insert("operation".into(), json!(operation));
    emit_sanitized_governance_audit(
        data_dir,
        "tenant_isolation_denied",
        actor_email,
        Some(actor_scope),
        details,
    );
    Err(format!(
        "Cross-tenant access denied for operation '{operation}': actor scope '{actor_scope}' cannot access '{target_scope}'."
    ))
}

pub fn sanitize_audit_payload(
    payload: &serde_json::Map<String, Value>,
) -> serde_json::Map<String, Value> {
    let mut out = serde_json::Map::new();
    for (k, v) in payload {
        let lower = k.to_ascii_lowercase();
        if SENSITIVE_AUDIT_KEYS.iter().any(|s| lower.contains(s)) {
            out.insert(k.clone(), json!("[REDACTED]"));
        } else {
            out.insert(k.clone(), v.clone());
        }
    }
    out
}

pub fn emit_sanitized_governance_audit(
    data_dir: &Path,
    event_type: &str,
    actor_email: Option<&str>,
    workspace_id: Option<&str>,
    details: serde_json::Map<String, Value>,
) {
    let mut sanitized = sanitize_audit_payload(&details);
    if let Some(actor) = actor_email.filter(|s| !s.is_empty()) {
        sanitized.insert("actor_email".into(), json!(actor));
    }
    if let Some(ws) = workspace_id.filter(|s| !s.is_empty()) {
        sanitized.insert("workspace_id".into(), json!(ws));
    }
    super::audit::append_audit_event(
        &super::audit::audit_log_path(data_dir),
        event_type,
        sanitized,
    );
}
