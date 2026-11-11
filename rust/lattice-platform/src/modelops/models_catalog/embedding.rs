//! Embedding model recommendations and catalog hints for hardware-aware retrieval.
//!
//! Separated from `probe.rs` to keep individual files focused and under line limits.

use super::probe::{CatalogHint, HostProbe};
use serde_json::{json, Value};

/// Catalog hints for Google EmbeddingGemma 2 / 300M models.
pub(crate) const EMBEDDING_CATALOG_HINTS: &[(&str, CatalogHint)] = &[
    (
        "google/embeddinggemma-2",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 4.0,
            recommended_ram_gb: 8.0,
            recommended_default: true,
            display_priority: 70,
        },
    ),
    (
        "mlx-community/embeddinggemma-2-4bit",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 4.0,
            recommended_ram_gb: 8.0,
            recommended_default: true,
            display_priority: 71,
        },
    ),
    (
        "google/embeddinggemma-300m",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 2.0,
            recommended_ram_gb: 4.0,
            recommended_default: false,
            display_priority: 72,
        },
    ),
    (
        "mlx-community/embeddinggemma-300m-4bit",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 2.0,
            recommended_ram_gb: 4.0,
            recommended_default: false,
            display_priority: 73,
        },
    ),
];

pub(crate) fn embedding_catalog_hints(id: &str) -> Option<&'static CatalogHint> {
    EMBEDDING_CATALOG_HINTS
        .iter()
        .find(|(name, _)| *name == id)
        .map(|(_, hint)| hint)
}

/// Build a PC-tailored embedding model recommendation (`google/embeddinggemma-2`
/// or `google/embeddinggemma-300m` on constrained RAM < 8 GB) accounting for
/// host RAM and Apple Silicon unified memory vs CPU/GPU.
pub fn recommend_embedding_for_host(probe: &HostProbe, engine: &str) -> Value {
    let effective_ram_gb = if probe.ram_bytes.is_none() || probe.ram_gb == 0 {
        16.0
    } else {
        probe.ram_gb as f64
    };
    let use_mlx = probe.apple_silicon && matches!(engine, "local_mlx" | "mlx" | "");
    if effective_ram_gb >= 8.0 {
        let footprint_gb = if use_mlx { 0.5 } else { 1.5 };
        let load_id = if use_mlx {
            "mlx-community/embeddinggemma-2-4bit"
        } else {
            "google/embeddinggemma-2"
        };
        let rec_engine = if use_mlx { "local_mlx" } else { "huggingface" };
        let reason = if probe.apple_silicon {
            format!(
                "Apple Silicon ({:.0}GB RAM) 맞춤 4-bit MLX / Hugging Face 시맨틱 임베딩 모델 (Google EmbeddingGemma 2, 768차원)",
                effective_ram_gb
            )
        } else {
            format!(
                "{:.0}GB RAM PC 맞춤 Hugging Face 시맨틱 임베딩 모델 (Google EmbeddingGemma 2, 768차원)",
                effective_ram_gb
            )
        };
        json!({
            "id": "google/embeddinggemma-2",
            "model_id": "google/embeddinggemma-2",
            "hf_repo_id": "google/embeddinggemma-2",
            "mlx_repo_id": "mlx-community/embeddinggemma-2-4bit",
            "recommended_load_id": load_id,
            "recommended_engine": rec_engine,
            "provider": if use_mlx { "mlx" } else { "huggingface" },
            "profile_id": if use_mlx { "mlx:embeddinggemma-2" } else { "local:embeddinggemma-2" },
            "name": "Google EmbeddingGemma 2",
            "display_name": "Google EmbeddingGemma 2",
            "family": "EmbeddingGemma",
            "category": "embedding",
            "role": "embedding",
            "task": "embedding",
            "tag": "local-embedding",
            "modality": "text",
            "dimensions": 768,
            "size": if use_mlx { "0.4GB" } else { "1.5GB" },
            "download_size": if use_mlx { "0.4GB" } else { "1.5GB" },
            "download_size_gb": if use_mlx { 0.42 } else { 1.48 },
            "memory_footprint_gb": footprint_gb,
            "effective_llm_ram_gb": (effective_ram_gb - footprint_gb).max(0.0),
            "status": "recommended",
            "load_status": "ready",
            "supported": true,
            "recommended_default": true,
            "display_priority": 70,
            "hardware": {
                "min_ram_gb": 4.0,
                "recommended_ram_gb": 8.0,
                "apple_silicon_pref": true
            },
            "reason": reason
        })
    } else {
        let footprint_gb = 0.3;
        let load_id = if use_mlx {
            "mlx-community/embeddinggemma-300m-4bit"
        } else {
            "google/embeddinggemma-300m"
        };
        let rec_engine = if use_mlx { "local_mlx" } else { "huggingface" };
        json!({
            "id": "google/embeddinggemma-300m",
            "model_id": "google/embeddinggemma-300m",
            "hf_repo_id": "google/embeddinggemma-300m",
            "mlx_repo_id": "mlx-community/embeddinggemma-300m-4bit",
            "recommended_load_id": load_id,
            "recommended_engine": rec_engine,
            "provider": if use_mlx { "mlx" } else { "huggingface" },
            "profile_id": "local:embeddinggemma-300m",
            "name": "Google EmbeddingGemma 300M",
            "display_name": "Google EmbeddingGemma 300M",
            "family": "EmbeddingGemma",
            "category": "embedding",
            "role": "embedding",
            "task": "embedding",
            "tag": "local-embedding",
            "modality": "text",
            "dimensions": 768,
            "size": if use_mlx { "0.3GB" } else { "0.6GB" },
            "download_size": if use_mlx { "0.3GB" } else { "0.6GB" },
            "download_size_gb": if use_mlx { 0.30 } else { 0.60 },
            "memory_footprint_gb": footprint_gb,
            "effective_llm_ram_gb": (effective_ram_gb - footprint_gb).max(0.0),
            "status": "recommended",
            "load_status": "ready",
            "supported": true,
            "recommended_default": true,
            "display_priority": 72,
            "hardware": {
                "min_ram_gb": 2.0,
                "recommended_ram_gb": 4.0,
                "apple_silicon_pref": true
            },
            "reason": format!(
                "저사양 PC ({:.0}GB RAM) 맞춤 초경량 시맨틱 임베딩 모델 (Google EmbeddingGemma 300M, 768차원)",
                effective_ram_gb
            )
        })
    }
}
