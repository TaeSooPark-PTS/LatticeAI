//! Glue types that bind chat's optional sinks to `lattice-platform`.
//!
//! Chat never names the Review Center or the audit file: it takes
//! [`lattice_chat::ReviewSink`] / [`lattice_chat::EgressAudit`] /
//! [`lattice_chat::AuditSink`]. This module is the one place the host
//! implements those traits over the platform APIs that already own the
//! documents.

use std::path::PathBuf;
use std::sync::Arc;

use lattice_agent::proposals::{NewReviewItem, ProposalStore};
use lattice_chat::{AuditSink, EgressAudit, ReviewSink};
use lattice_platform::admin::{append_audit_event, audit_log_path};
use serde_json::{json, Value};

/// Stage a cloud KG expansion through the Review Center's own store.
pub struct PlatformReview {
    store: Arc<dyn ProposalStore>,
}

impl PlatformReview {
    /// Bind the product's [`lattice_platform::review_queue::GovernanceState`].
    pub fn new(store: Arc<dyn ProposalStore>) -> Self {
        Self { store }
    }
}

impl ReviewSink for PlatformReview {
    fn create(&self, item: &Value) -> Result<String, String> {
        let title = item
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("Cloud KG expansion")
            .to_string();
        let summary = item
            .get("summary")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let source = item
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("change_proposal")
            .to_string();
        let kind = item
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("kg_cloud_expansion")
            .to_string();
        let payload = item.get("payload").cloned().unwrap_or_else(|| json!({}));
        let provenance = item.get("provenance").cloned().unwrap_or_else(|| json!({}));
        let user_email = item
            .get("user_email")
            .and_then(Value::as_str)
            .map(str::to_string);
        let workspace_id = item
            .get("workspace_id")
            .and_then(Value::as_str)
            .map(str::to_string);
        let stored = self.store.create(&NewReviewItem {
            title,
            summary,
            source,
            kind,
            payload,
            provenance,
            user_email,
            workspace_id,
        })?;
        stored
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| "review item had no id".into())
    }
}

/// Append a cloud-egress record to `audit_log.json`.
pub struct PlatformEgress {
    path: PathBuf,
}

impl PlatformEgress {
    /// Point at the audit file under `data_dir`.
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            path: audit_log_path(&data_dir.into()),
        }
    }
}

impl EgressAudit for PlatformEgress {
    fn record(&self, event: &Value) {
        let mut payload = event.as_object().cloned().unwrap_or_default();
        let event_type = payload
            .remove("event")
            .and_then(|value| value.as_str().map(str::to_string))
            .unwrap_or_else(|| "cloud_egress".into());
        append_audit_event(&self.path, &event_type, payload);
    }
}

/// The same audit file, for chat's named events (`chat_message`, …).
pub struct PlatformAudit {
    path: PathBuf,
}

impl PlatformAudit {
    /// Point at the audit file under `data_dir`.
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            path: audit_log_path(&data_dir.into()),
        }
    }
}

impl AuditSink for PlatformAudit {
    fn append(&self, event: &str, fields: &Value) {
        let payload = fields.as_object().cloned().unwrap_or_default();
        append_audit_event(&self.path, event, payload);
    }
}
