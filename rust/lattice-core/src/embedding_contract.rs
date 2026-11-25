use serde::{Deserialize, Serialize};

use crate::db::CoreError;

pub const DEFAULT_INDEX_VERSION: u32 = 1;
pub const NORMALIZATION_L2: &str = "l2";
pub const DEFAULT_EMBEDDING_MODE: &str = "raw";

fn default_normalization() -> String {
    NORMALIZATION_L2.to_string()
}

fn default_embedding_mode() -> String {
    DEFAULT_EMBEDDING_MODE.to_string()
}

fn default_index_version() -> u32 {
    DEFAULT_INDEX_VERSION
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EmbeddingIndexContract {
    pub provider: String,
    pub model_id: String,
    #[serde(default)]
    pub model_revision: Option<String>,
    pub dimension: usize,
    #[serde(default = "default_normalization")]
    pub normalization: String,
    #[serde(default = "default_embedding_mode")]
    pub passage_mode: String,
    #[serde(default = "default_embedding_mode")]
    pub query_mode: String,
    #[serde(default = "default_index_version")]
    pub index_version: u32,
    #[serde(default)]
    pub grade: String,
    #[serde(default)]
    pub semantic: bool,
}

impl EmbeddingIndexContract {
    pub fn is_hash_model_id(model_id: &str) -> bool {
        let lower = model_id.trim().to_ascii_lowercase();
        lower.is_empty()
            || lower == "hash"
            || lower.starts_with("lattice-local-hash")
            || lower.starts_with("lattice-fallback-")
    }

    pub fn infer_provider(model_id: &str) -> String {
        if Self::is_hash_model_id(model_id) {
            return "hash".to_string();
        }
        if let Some((prefix, _)) = model_id.split_once(':') {
            let trimmed = prefix.trim().to_ascii_lowercase();
            if !trimmed.is_empty() {
                return trimmed;
            }
        }
        "semantic".to_string()
    }

    pub fn infer_modes(model_id: &str) -> (String, String) {
        let lower = model_id.to_ascii_lowercase();
        if lower.contains("embeddinggemma") {
            (
                "embeddinggemma_passage".to_string(),
                "embeddinggemma_query".to_string(),
            )
        } else if lower.contains("e5") {
            ("e5_passage".to_string(), "e5_query".to_string())
        } else {
            (
                DEFAULT_EMBEDDING_MODE.to_string(),
                DEFAULT_EMBEDDING_MODE.to_string(),
            )
        }
    }

    pub fn from_hash(model_id: impl Into<String>, dimension: usize) -> Self {
        let model_id = model_id.into();
        let revision = if model_id.starts_with("lattice-fallback-subword-v2") {
            Some("v2".to_string())
        } else {
            Some("v1".to_string())
        };
        Self {
            provider: "hash".to_string(),
            model_id,
            model_revision: revision,
            dimension,
            normalization: NORMALIZATION_L2.to_string(),
            passage_mode: DEFAULT_EMBEDDING_MODE.to_string(),
            query_mode: DEFAULT_EMBEDDING_MODE.to_string(),
            index_version: DEFAULT_INDEX_VERSION,
            grade: "fallback".to_string(),
            semantic: false,
        }
    }

    pub fn from_semantic(
        provider: impl Into<String>,
        model_id: impl Into<String>,
        dimension: usize,
    ) -> Self {
        let provider = provider.into();
        let model_id = model_id.into();
        let (passage_mode, query_mode) = Self::infer_modes(&model_id);
        let hash = provider.eq_ignore_ascii_case("hash") || Self::is_hash_model_id(&model_id);
        Self {
            provider: if provider.trim().is_empty() {
                Self::infer_provider(&model_id)
            } else {
                provider
            },
            model_id,
            model_revision: None,
            dimension,
            normalization: NORMALIZATION_L2.to_string(),
            passage_mode,
            query_mode,
            index_version: DEFAULT_INDEX_VERSION,
            grade: if hash {
                "fallback".to_string()
            } else {
                "production".to_string()
            },
            semantic: !hash,
        }
    }

    pub fn from_parts(
        provider: Option<&str>,
        model_id: impl Into<String>,
        dimension: usize,
    ) -> Self {
        let model_id = model_id.into();
        let inferred_provider = provider
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| Self::infer_provider(&model_id));
        if inferred_provider.eq_ignore_ascii_case("hash") || Self::is_hash_model_id(&model_id) {
            Self::from_hash(model_id, dimension)
        } else {
            Self::from_semantic(inferred_provider, model_id, dimension)
        }
    }

    pub fn is_hash_fallback(&self) -> bool {
        self.provider.eq_ignore_ascii_case("hash")
            || self.grade.eq_ignore_ascii_case("fallback")
            || !self.semantic
            || Self::is_hash_model_id(&self.model_id)
    }

    pub fn is_semantic(&self) -> bool {
        self.semantic && !self.is_hash_fallback()
    }

    pub fn validate_vector(&self, vector: &[f64]) -> Result<(), CoreError> {
        if self.dimension == 0 {
            return Err(CoreError::InvalidRequest(
                "embedding contract dimension must be positive".to_string(),
            ));
        }
        if vector.len() != self.dimension {
            return Err(CoreError::InvalidRequest(format!(
                "embedding dimension mismatch for {}: expected {}, got {}",
                self.model_id,
                self.dimension,
                vector.len()
            )));
        }
        if vector.iter().any(|v| !v.is_finite()) {
            return Err(CoreError::InvalidRequest(format!(
                "embedding vector for {} contains non-finite values",
                self.model_id
            )));
        }
        Ok(())
    }

    pub fn is_compatible_with(&self, other: &Self) -> bool {
        if self.model_id != other.model_id || self.dimension != other.dimension {
            return false;
        }
        if self.normalization != other.normalization || self.index_version != other.index_version {
            return false;
        }
        if let (Some(left), Some(right)) = (&self.model_revision, &other.model_revision) {
            if left != right {
                return false;
            }
        }
        true
    }

    pub fn requires_reindex(&self, existing: &Self) -> bool {
        !self.is_compatible_with(existing)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::LocalEmbeddingModel;

    #[test]
    fn embedding_index_contract_distinguishes_semantic_from_hash_and_validates_dims() {
        let hash = LocalEmbeddingModel::new(384).contract();
        assert!(hash.is_hash_fallback());
        assert!(!hash.is_semantic());
        assert_eq!(hash.provider, "hash");
        assert_eq!(hash.dimension, 384);
        assert_eq!(hash.index_version, DEFAULT_INDEX_VERSION);
        assert!(hash.validate_vector(&vec![0.0; 384]).is_ok());
        assert!(hash.validate_vector(&vec![0.0; 768]).is_err());

        let gemma = EmbeddingIndexContract::from_semantic(
            "huggingface",
            "huggingface:google/embeddinggemma-2:768",
            768,
        );
        assert!(gemma.is_semantic());
        assert!(!gemma.is_hash_fallback());
        assert_eq!(gemma.passage_mode, "embeddinggemma_passage");
        assert_eq!(gemma.query_mode, "embeddinggemma_query");
        assert!(gemma.requires_reindex(&hash));
        assert!(gemma.is_compatible_with(&gemma));
    }
}
