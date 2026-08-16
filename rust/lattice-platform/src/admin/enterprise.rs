//! Community-edition enterprise stubs and product hardening.

use std::path::Path;

use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::internal::json_from_ordered;
use super::COMMUNITY_NOTICE;

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

/// [`default_product_hardening`] with the executable probe injected.
///
/// `python_available` / `docker_available` are the only two fields in this
/// document that read the *machine* rather than the configuration, so they are
/// the only two a replay test cannot control: the Python oracle recorded them
/// on a developer laptop that had both installed, and a CI container without
/// Docker then fails the fixture over a host property rather than over
/// anything the handler does. Tests supply their own probe here; production
/// goes through [`default_product_hardening`], which passes [`which`].
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
    let backup = crate::portability::backup_health_payload(&exports);
    let mut storage = OrderedMap::new();
    storage.insert("available", json!(true));
    storage.insert(
        "active",
        json!(crate::portability::sqlite_capabilities(
            &data_dir.join("knowledge_graph.sqlite")
        )),
    );
    storage.insert(
        "postgres",
        json!(crate::portability::postgres_capabilities()),
    );
    storage.insert("backup_health", json!(backup.clone()));
    out.insert("storage", json_from_ordered(&storage));
    out.insert("backup", json!(backup));
    let identity = crate::network::DeviceIdentity::load_or_create(
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
