//! Admin console family — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/admin.py` plus the audit-log surface that file
//! already owned (`latticeai/core/audit.py`). Other families (security
//! dashboard, feature toggles) call [`append_audit_event`] / [`load_audit_log`]
//! rather than growing a second writer.
//!
//! Storage is the same files Python uses, resolved through
//! `lattice_core::db::tables::state_files` and written atomically.

use std::path::PathBuf;
use std::sync::Arc;

use axum::routing::{get, patch};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use serde_json::{json, Value};

pub(crate) mod audit;
pub(crate) mod config;
pub(crate) mod enterprise;
pub(crate) mod handlers;
pub(crate) mod http;
pub(crate) mod internal;
pub(crate) mod redact;

pub use audit::{
    append_audit_event, audit_log_path, build_admin_audit_report, build_sensitivity_report,
    classify_sensitive_message, load_audit_log,
};
pub use config::{
    default_sso_redirect, default_vpc_config, load_chat_history, load_sso_config, load_vpc_config,
    matches_workspace_scope, public_sso_config, save_sso_config, save_vpc_config,
};
pub use enterprise::{
    default_product_hardening, default_product_hardening_probed, poc_overview, siem_export_stub,
};
pub use http::{
    detail_status, json_ok, json_ok_value, json_status, language_from, message_error,
    workspace_from_headers,
};
pub use internal::{json_from_ordered, now_iso, today_str, tz_name};
pub use redact::{redact_secret_text, redact_secrets, redact_structure};

use handlers::{
    admin_audit, admin_delete_user, admin_enterprise, admin_enterprise_siem, admin_health_summary,
    admin_invite_link, admin_log_retention, admin_policies, admin_product_hardening, admin_roles,
    admin_sensitivity, admin_sso, admin_stats, admin_summary, admin_update_sso, admin_update_user,
    admin_update_vpc, admin_users, vpc_status,
};

/// Mounted (method, path) pairs — axum 0.7 spelling. Greedy `{email:path}`
/// is `/*email`.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/admin/audit"),
    ("GET", "/admin/enterprise"),
    ("GET", "/admin/enterprise/siem-export"),
    ("GET", "/admin/health-summary"),
    ("GET", "/admin/invite-link"),
    ("GET", "/admin/log-retention"),
    ("GET", "/admin/policies"),
    ("GET", "/admin/product-hardening"),
    ("GET", "/admin/roles"),
    ("GET", "/admin/sso"),
    ("PATCH", "/admin/sso"),
    ("GET", "/admin/stats"),
    ("GET", "/admin/summary"),
    ("GET", "/admin/users"),
    ("DELETE", "/admin/users/*email"),
    ("PATCH", "/admin/users/*email"),
    ("PATCH", "/admin/vpc"),
    ("GET", "/admin/sensitivity"),
    ("GET", "/vpc/status"),
];

/// Community edition notice, verbatim from `enterprise_admin.COMMUNITY_NOTICE`.
pub const COMMUNITY_NOTICE: &str =
    "Community edition: this is an Enterprise extension point and is not \
enforced. Local-first behaviour is always available. See \
docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md.";

// ── router state ─────────────────────────────────────────────────────────────

/// What the admin family needs from the host.
#[derive(Clone)]
pub struct AdminState {
    pub auth: Arc<AuthState>,
    pub data_dir: PathBuf,
    pub invite_code: String,
    pub invite_gate_enabled: bool,
    pub default_port: u16,
    pub enable_graph: bool,
    pub graph_stats: Arc<dyn Fn() -> Result<Value, String> + Send + Sync>,
    pub hardening: Option<Arc<dyn Fn() -> OrderedMap + Send + Sync>>,
}

impl AdminState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        let default_port = auth.config().port;
        let invite_gate_enabled = auth.config().invite_gate_enabled;
        let invite_code = std::env::var("LATTICEAI_INVITE_CODE").unwrap_or_default();
        let hardening_dir = data_dir.clone();
        let host = auth.config().host.clone();
        let require_auth = auth.config().require_auth;
        Self {
            auth,
            data_dir,
            invite_code,
            invite_gate_enabled,
            default_port,
            enable_graph: true,
            graph_stats: Arc::new(|| Ok(json!({"total_nodes": 0, "total_edges": 0}))),
            hardening: Some(Arc::new(move || {
                default_product_hardening(&hardening_dir, &host, default_port, require_auth)
            })),
        }
    }
}

impl axum::extract::FromRef<AdminState> for Arc<AuthState> {
    fn from_ref(s: &AdminState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: AdminState) -> Router {
    Router::new()
        .route("/admin/summary", get(admin_summary))
        .route("/admin/health-summary", get(admin_health_summary))
        .route("/admin/stats", get(admin_stats))
        .route("/admin/users", get(admin_users))
        .route("/admin/sensitivity", get(admin_sensitivity))
        .route("/admin/audit", get(admin_audit))
        .route("/admin/roles", get(admin_roles))
        .route("/admin/policies", get(admin_policies))
        .route("/admin/log-retention", get(admin_log_retention))
        .route("/admin/product-hardening", get(admin_product_hardening))
        .route("/vpc/status", get(vpc_status))
        .route("/admin/vpc", patch(admin_update_vpc))
        .route(
            "/admin/users/*email",
            patch(admin_update_user).delete(admin_delete_user),
        )
        .route("/admin/invite-link", get(admin_invite_link))
        .route("/admin/sso", get(admin_sso).patch(admin_update_sso))
        .route("/admin/enterprise", get(admin_enterprise))
        .route("/admin/enterprise/siem-export", get(admin_enterprise_siem))
        .with_state(state)
}

/// Union of every R2 family's mounted routes (OpenAPI contract test).
pub fn family_mounted() -> Vec<(&'static str, &'static str)> {
    let mut out = MOUNTED.to_vec();
    out.extend_from_slice(crate::adminops::security_dashboard::MOUNTED);
    out.extend_from_slice(crate::workspaceos::features::MOUNTED);
    out.extend_from_slice(crate::adminops::funnel_metrics::MOUNTED);
    out.extend_from_slice(crate::modelops::setup::MOUNTED);
    out
}
