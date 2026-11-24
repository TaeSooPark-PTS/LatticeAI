//! Multimodal (Vision, Speech, Image Retrieval) platform provider contracts
//! and policy enforcement gates.
//!
//! Model inference and embedding generation stay inside the Python worker seam;
//! `lattice-platform` enforces MIME/size bounds, embedding dimensionality
//! alignment, local-only network boundaries, organization governance policies,
//! and sanitized audit trail emission before any capture or retrieval runs.

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::adminops::admin::enterprise::{
    emit_sanitized_governance_audit, load_org_governance_policy, normalize_tenant_scope,
};
use crate::knowledge::network_boundary::{BoundaryStore, PolicyStore};

pub const MAX_VISION_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_SPEECH_BYTES: usize = 50 * 1024 * 1024;
pub const MAX_IMAGE_RETRIEVAL_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_VIDEO_BYTES: usize = 250 * 1024 * 1024;
pub const MAX_MULTIMODAL_EMBED_DIM: usize = 4096;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Modality {
    Vision,
    Speech,
    ImageRetrieval,
    Video,
}

impl Modality {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Vision => "vision",
            Self::Speech => "speech",
            Self::ImageRetrieval => "image_retrieval",
            Self::Video => "video",
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ProviderContract {
    pub provider_id: &'static str,
    pub modality: Modality,
    pub worker_endpoint: &'static str,
    pub allowed_mime_types: &'static [&'static str],
    pub max_payload_bytes: usize,
    pub requires_local_worker: bool,
    pub default_embedding_dim: Option<usize>,
}

pub fn default_provider_contracts() -> [ProviderContract; 4] {
    [
        ProviderContract {
            provider_id: "worker.vlm.vision",
            modality: Modality::Vision,
            worker_endpoint: "/worker/parse",
            allowed_mime_types: &["image/png", "image/jpeg", "image/webp", "image/gif"],
            max_payload_bytes: MAX_VISION_BYTES,
            requires_local_worker: true,
            default_embedding_dim: None,
        },
        ProviderContract {
            provider_id: "worker.asr.speech",
            modality: Modality::Speech,
            worker_endpoint: "/worker/asr",
            allowed_mime_types: &[
                "audio/wav",
                "audio/x-wav",
                "audio/mpeg",
                "audio/mp3",
                "audio/ogg",
                "audio/webm",
                "audio/mp4",
                "audio/flac",
            ],
            max_payload_bytes: MAX_SPEECH_BYTES,
            requires_local_worker: true,
            default_embedding_dim: None,
        },
        ProviderContract {
            provider_id: "worker.clip.image_retrieval",
            modality: Modality::ImageRetrieval,
            worker_endpoint: "/worker/embed",
            allowed_mime_types: &["image/png", "image/jpeg", "image/webp"],
            max_payload_bytes: MAX_IMAGE_RETRIEVAL_BYTES,
            requires_local_worker: true,
            default_embedding_dim: Some(512),
        },
        ProviderContract {
            provider_id: "worker.video.indexer",
            modality: Modality::Video,
            worker_endpoint: "/worker/parse",
            allowed_mime_types: &["video/mp4", "video/webm", "video/quicktime", "video/x-matroska"],
            max_payload_bytes: MAX_VIDEO_BYTES,
            requires_local_worker: true,
            default_embedding_dim: Some(512),
        },
    ]
}

pub fn contract_for(modality: Modality) -> ProviderContract {
    default_provider_contracts()
        .into_iter()
        .find(|c| c.modality == modality)
        .expect("all Modality variants have a contract")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MultimodalContractRequest {
    pub modality: Modality,
    pub mime_type: String,
    pub payload_bytes: usize,
    pub user_email: Option<String>,
    pub workspace_id: Option<String>,
    pub cloud_fallback: bool,
    pub expected_embedding_dim: Option<usize>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MultimodalDispatchPlan {
    pub provider_id: String,
    pub worker_endpoint: String,
    pub modality: Modality,
    pub workspace_id: String,
    pub local_only_enforced: bool,
    pub policy_allowed: bool,
    pub embedding_dim: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MultimodalViolation {
    pub code: &'static str,
    pub message: String,
}

impl std::fmt::Display for MultimodalViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

/// Validate a multimodal provider invocation against MIME/size contracts,
/// `PolicyStore::allow_multimodal`, `BoundaryStore` local-only guarantees,
/// `OrganizationGovernancePolicy`, and optional embedding dimensions.
pub fn validate_multimodal_contract(
    data_dir: &Path,
    req: &MultimodalContractRequest,
    require_hybrid_multimodal_opt_in: bool,
    emit_audit: bool,
) -> Result<MultimodalDispatchPlan, MultimodalViolation> {
    let contract = contract_for(req.modality);
    let workspace = normalize_tenant_scope(req.workspace_id.as_deref()).to_string();

    let reject = |code: &'static str,
                  message: String|
     -> Result<MultimodalDispatchPlan, MultimodalViolation> {
        if emit_audit {
            let mut details = Map::new();
            details.insert("modality".into(), json!(req.modality.as_str()));
            details.insert("mime_type".into(), json!(req.mime_type));
            details.insert("payload_bytes".into(), json!(req.payload_bytes));
            details.insert("violation_code".into(), json!(code));
            details.insert("reason".into(), json!(message));
            emit_sanitized_governance_audit(
                data_dir,
                "multimodal_contract_denied",
                req.user_email.as_deref(),
                Some(&workspace),
                details,
            );
        }
        Err(MultimodalViolation { code, message })
    };

    let mime_norm = req.mime_type.trim().to_ascii_lowercase();
    if !contract
        .allowed_mime_types
        .iter()
        .any(|allowed| *allowed == mime_norm)
    {
        return reject(
            "unsupported_mime_type",
            format!(
                "MIME type '{}' is not allowed for modality '{}'.",
                req.mime_type,
                req.modality.as_str()
            ),
        );
    }

    if req.payload_bytes == 0 || req.payload_bytes > contract.max_payload_bytes {
        return reject(
            "payload_size_out_of_bounds",
            format!(
                "Payload size {} bytes is outside [1, {}] for modality '{}'.",
                req.payload_bytes,
                contract.max_payload_bytes,
                req.modality.as_str()
            ),
        );
    }

    if req.modality == Modality::ImageRetrieval {
        if let Some(dim) = req.expected_embedding_dim {
            if dim == 0 || dim > MAX_MULTIMODAL_EMBED_DIM {
                return reject(
                    "invalid_embedding_dimension",
                    format!(
                        "Image retrieval embedding_dim {dim} must be in 1..={MAX_MULTIMODAL_EMBED_DIM}."
                    ),
                );
            }
        }
    }

    let org_policy = load_org_governance_policy(data_dir, &workspace);
    if !org_policy.permits_modality(req.modality.as_str()) {
        return reject(
            "org_policy_denied",
            format!(
                "Organization '{}' policy prohibits multimodal '{}' capture.",
                org_policy.organization_id,
                req.modality.as_str()
            ),
        );
    }

    let boundary = BoundaryStore::open(data_dir.join("network_boundary.json"));
    let mode = boundary.resolve(req.user_email.as_deref(), Some(&workspace));
    let local_only_enforced = mode == "local_only";
    if req.cloud_fallback && local_only_enforced {
        return reject(
            "network_boundary_local_only",
            format!(
                "Cloud multimodal fallback blocked by local_only network boundary for workspace '{workspace}'."
            ),
        );
    }

    let policy_store = PolicyStore::open(data_dir.join("hybrid_policy.json"));
    let hybrid = policy_store.resolve(req.user_email.as_deref(), Some(&workspace));
    let policy_allowed = hybrid
        .get("allow_multimodal")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if require_hybrid_multimodal_opt_in && !policy_allowed {
        return reject(
            "hybrid_policy_multimodal_disabled",
            "Multimodal extraction requires hybrid_policy.allow_multimodal=true.".into(),
        );
    }

    if emit_audit {
        let mut details = Map::new();
        details.insert("modality".into(), json!(req.modality.as_str()));
        details.insert("provider_id".into(), json!(contract.provider_id));
        details.insert("mime_type".into(), json!(mime_norm));
        details.insert("payload_bytes".into(), json!(req.payload_bytes));
        details.insert("local_only_enforced".into(), json!(local_only_enforced));
        emit_sanitized_governance_audit(
            data_dir,
            "multimodal_contract_checked",
            req.user_email.as_deref(),
            Some(&workspace),
            details,
        );
    }

    Ok(MultimodalDispatchPlan {
        provider_id: contract.provider_id.into(),
        worker_endpoint: contract.worker_endpoint.into(),
        modality: req.modality,
        workspace_id: workspace,
        local_only_enforced,
        policy_allowed,
        embedding_dim: req
            .expected_embedding_dim
            .or(contract.default_embedding_dim),
    })
}

/// Convenience helper validating incoming audio payloads against the Speech contract.
pub fn validate_speech_audio_payload(
    data_dir: &Path,
    mime_type: &str,
    audio_bytes: usize,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<MultimodalDispatchPlan, MultimodalViolation> {
    let req = MultimodalContractRequest {
        modality: Modality::Speech,
        mime_type: mime_type.into(),
        payload_bytes: audio_bytes,
        user_email: user_email.map(str::to_string),
        workspace_id: workspace_id.map(str::to_string),
        cloud_fallback: false,
        expected_embedding_dim: None,
    };
    validate_multimodal_contract(data_dir, &req, false, true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adminops::admin::enterprise::{
        save_org_governance_policy, OrganizationGovernancePolicy,
    };

    #[test]
    fn multimodal_contracts_validate_mime_size_boundary_and_org_policy() {
        let dir = tempfile::tempdir().unwrap();
        let data = dir.path();

        // Unsupported MIME fails cleanly.
        let bad_mime = validate_multimodal_contract(
            data,
            &MultimodalContractRequest {
                modality: Modality::Vision,
                mime_type: "application/x-msdownload".into(),
                payload_bytes: 1024,
                user_email: Some("owner@example.com".into()),
                workspace_id: Some("acme".into()),
                cloud_fallback: false,
                expected_embedding_dim: None,
            },
            false,
            false,
        );
        assert_eq!(bad_mime.unwrap_err().code, "unsupported_mime_type");

        // Cloud fallback blocked under default local_only network boundary.
        let cloud_blocked = validate_multimodal_contract(
            data,
            &MultimodalContractRequest {
                modality: Modality::ImageRetrieval,
                mime_type: "image/png".into(),
                payload_bytes: 2048,
                user_email: Some("owner@example.com".into()),
                workspace_id: Some("acme".into()),
                cloud_fallback: true,
                expected_embedding_dim: Some(512),
            },
            false,
            true,
        );
        assert_eq!(
            cloud_blocked.unwrap_err().code,
            "network_boundary_local_only"
        );

        // Hybrid opt-in gating works when require_hybrid_multimodal_opt_in = true.
        let hybrid_blocked = validate_multimodal_contract(
            data,
            &MultimodalContractRequest {
                modality: Modality::Vision,
                mime_type: "image/webp".into(),
                payload_bytes: 2048,
                user_email: Some("owner@example.com".into()),
                workspace_id: Some("acme".into()),
                cloud_fallback: false,
                expected_embedding_dim: None,
            },
            true,
            false,
        );
        assert_eq!(
            hybrid_blocked.unwrap_err().code,
            "hybrid_policy_multimodal_disabled"
        );

        // Enabling hybrid allow_multimodal allows local worker dispatch.
        PolicyStore::open(data.join("hybrid_policy.json")).set_policy(
            serde_json::json!({"allow_multimodal": true})
                .as_object()
                .unwrap(),
            Some("owner@example.com"),
            Some("acme"),
        );
        let plan = validate_multimodal_contract(
            data,
            &MultimodalContractRequest {
                modality: Modality::ImageRetrieval,
                mime_type: "image/png".into(),
                payload_bytes: 4096,
                user_email: Some("owner@example.com".into()),
                workspace_id: Some("acme".into()),
                cloud_fallback: false,
                expected_embedding_dim: Some(512),
            },
            true,
            true,
        )
        .expect("allowed after opt-in");
        assert_eq!(plan.worker_endpoint, "/worker/embed");
        assert_eq!(plan.embedding_dim, Some(512));

        // Org policy can restrict modalities.
        let mut policy = OrganizationGovernancePolicy::default_for("acme");
        policy.allowed_modalities = vec!["speech".into()];
        save_org_governance_policy(data, "acme", &policy, Some("owner@example.com")).unwrap();
        let org_denied = validate_multimodal_contract(
            data,
            &MultimodalContractRequest {
                modality: Modality::Vision,
                mime_type: "image/png".into(),
                payload_bytes: 1024,
                user_email: Some("owner@example.com".into()),
                workspace_id: Some("acme".into()),
                cloud_fallback: false,
                expected_embedding_dim: None,
            },
            true,
            true,
        );
        assert_eq!(org_denied.unwrap_err().code, "org_policy_denied");
    }
}
