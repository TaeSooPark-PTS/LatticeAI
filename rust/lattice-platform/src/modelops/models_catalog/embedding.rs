//! Embedding model recommendations, public mirror / quantized fallbacks,
//! and byte-level download progress helpers for hardware-aware retrieval.
//!
//! Separated from `probe.rs` to keep individual files focused and under line limits.

use super::probe::{CatalogHint, HostProbe};
use serde_json::{json, Value};

pub const DEFAULT_HF_ENDPOINT: &str = "https://huggingface.co";

/// Catalog hints for Google EmbeddingGemma 2 / 300M models and public quantized mirrors.
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
        "mlx-community/embeddinggemma-2-8bit",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 4.0,
            recommended_ram_gb: 8.0,
            recommended_default: false,
            display_priority: 71,
        },
    ),
    (
        "ggml-org/embeddinggemma-2-GGUF",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 4.0,
            recommended_ram_gb: 8.0,
            recommended_default: false,
            display_priority: 71,
        },
    ),
    (
        "onnx-community/embeddinggemma-300m-ONNX",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 2.0,
            recommended_ram_gb: 4.0,
            recommended_default: false,
            display_priority: 74,
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
    (
        "mlx-community/embeddinggemma-300m-8bit",
        CatalogHint {
            family: "EmbeddingGemma",
            min_ram_gb: 2.0,
            recommended_ram_gb: 4.0,
            recommended_default: false,
            display_priority: 73,
        },
    ),
    (
        "ggml-org/embeddinggemma-300m-GGUF",
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

/// Ordered list of unauthenticated public mirrors and quantized distributions
/// for gated Hugging Face embedding models.
pub fn public_mirror_repos(model_id: &str) -> Vec<&'static str> {
    match model_id.trim() {
        "google/embeddinggemma-300m" | "mlx-community/embeddinggemma-300m-4bit" => vec![
            "mlx-community/embeddinggemma-300m-4bit",
            "mlx-community/embeddinggemma-300m-8bit",
            "ggml-org/embeddinggemma-300m-GGUF",
            "onnx-community/embeddinggemma-300m-ONNX",
        ],
        _ => vec![
            "mlx-community/embeddinggemma-2-4bit",
            "mlx-community/embeddinggemma-2-8bit",
            "ggml-org/embeddinggemma-2-GGUF",
            "onnx-community/embeddinggemma-300m-ONNX",
        ],
    }
}

/// Quantized GGUF / MLX / ONNX distribution map for an embedding model family.
pub fn quantized_distributions(model_id: &str) -> Value {
    match model_id.trim() {
        "google/embeddinggemma-300m" | "mlx-community/embeddinggemma-300m-4bit" => json!({
            "mlx_4bit": "mlx-community/embeddinggemma-300m-4bit",
            "mlx_8bit": "mlx-community/embeddinggemma-300m-8bit",
            "gguf": "ggml-org/embeddinggemma-300m-GGUF",
            "onnx": "onnx-community/embeddinggemma-300m-ONNX",
        }),
        _ => json!({
            "mlx_4bit": "mlx-community/embeddinggemma-2-4bit",
            "mlx_8bit": "mlx-community/embeddinggemma-2-8bit",
            "gguf": "ggml-org/embeddinggemma-2-GGUF",
            "onnx": "onnx-community/embeddinggemma-300m-ONNX",
        }),
    }
}

/// Build gated fallback & public mirror metadata for explicit token/endpoint inputs.
pub fn gated_fallback_for_env(
    model_id: &str,
    engine: &str,
    hf_token: Option<&str>,
    hf_endpoint: Option<&str>,
) -> Value {
    let mirrors = public_mirror_repos(model_id);
    let distributions = quantized_distributions(model_id);
    let authenticated = hf_token.map(|t| !t.trim().is_empty()).unwrap_or(false);
    let endpoint = hf_endpoint
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.trim_end_matches('/').to_string())
        .unwrap_or_else(|| DEFAULT_HF_ENDPOINT.to_string());
    let fallback_repo = match engine.trim().to_ascii_lowercase().as_str() {
        "llamacpp" | "gguf" => distributions
            .get("gguf")
            .and_then(Value::as_str)
            .unwrap_or(mirrors[0]),
        "onnx" => distributions
            .get("onnx")
            .and_then(Value::as_str)
            .unwrap_or(mirrors[0]),
        _ => distributions
            .get("mlx_4bit")
            .and_then(Value::as_str)
            .unwrap_or(mirrors[0]),
    };
    json!({
        "primary_repo": model_id,
        "requires_hf_auth": true,
        "authenticated": authenticated,
        "active": !authenticated,
        "fallback_repo": fallback_repo,
        "public_mirror_repos": mirrors,
        "quantized_distributions": distributions,
        "hf_endpoint": endpoint,
        "custom_mirror_active": endpoint != DEFAULT_HF_ENDPOINT,
        "strategy": "unauthenticated_public_mirror_and_quantized_fallback",
    })
}

fn ambient_hf_token() -> Option<String> {
    ["HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"]
        .iter()
        .find_map(|key| std::env::var(key).ok().filter(|v| !v.trim().is_empty()))
}

fn ambient_hf_endpoint() -> Option<String> {
    ["HF_ENDPOINT", "HUGGINGFACE_HUB_ENDPOINT"]
        .iter()
        .find_map(|key| std::env::var(key).ok().filter(|v| !v.trim().is_empty()))
}

/// Compute real-time byte-level download progress with `speed_bps` and `eta_seconds`.
pub fn compute_download_progress(
    stage: &str,
    current_file: Option<&str>,
    downloaded_bytes: u64,
    total_bytes: Option<u64>,
    elapsed_secs: f64,
    gated_fallback: bool,
    fallback_repo: Option<&str>,
) -> Value {
    let percent = match total_bytes {
        Some(total) if total > 0 => {
            ((downloaded_bytes as f64 / total as f64) * 100.0).clamp(0.0, 100.0)
        }
        _ => 0.0,
    };
    let rounded_percent = (percent * 10.0).round() / 10.0;
    let speed_bps = if elapsed_secs > 0.0 && downloaded_bytes > 0 {
        Some(((downloaded_bytes as f64 / elapsed_secs) * 100.0).round() / 100.0)
    } else {
        None
    };
    let speed_mbps = speed_bps.map(|bps| ((bps / (1024.0 * 1024.0)) * 100.0).round() / 100.0);
    let eta_seconds = match (total_bytes, speed_bps) {
        (Some(total), Some(bps)) if total > downloaded_bytes && bps > 0.0 => {
            Some(((total - downloaded_bytes) as f64 / bps).round() as u64)
        }
        (Some(total), _) if downloaded_bytes >= total && total > 0 => Some(0),
        _ => None,
    };
    let mut obj = json!({
        "stage": stage,
        "downloaded_bytes": downloaded_bytes,
        "total_bytes": total_bytes,
        "percent": rounded_percent,
        "speed_bps": speed_bps,
        "speed_mbps": speed_mbps,
        "eta_seconds": eta_seconds,
        "current_file": current_file,
        "gated_fallback": gated_fallback,
    });
    if let Some(repo) = fallback_repo {
        obj["fallback_repo"] = json!(repo);
    }
    obj
}

/// Format a progress payload as an SSE `event: progress` frame.
pub fn format_sse_download_progress(progress: &Value) -> String {
    format!("event: progress\ndata: {}\n\n", progress)
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
    let token = ambient_hf_token();
    let endpoint = ambient_hf_endpoint();
    if effective_ram_gb >= 8.0 {
        let model_id = "google/embeddinggemma-2";
        let footprint_gb = if use_mlx { 0.5 } else { 1.5 };
        let load_id = if use_mlx {
            "mlx-community/embeddinggemma-2-4bit"
        } else {
            model_id
        };
        let rec_engine = if use_mlx { "local_mlx" } else { "huggingface" };
        let mirrors = public_mirror_repos(model_id);
        let distributions = quantized_distributions(model_id);
        let fallback =
            gated_fallback_for_env(model_id, rec_engine, token.as_deref(), endpoint.as_deref());
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
            "id": model_id,
            "model_id": model_id,
            "hf_repo_id": model_id,
            "mlx_repo_id": "mlx-community/embeddinggemma-2-4bit",
            "mlx_8bit_repo_id": "mlx-community/embeddinggemma-2-8bit",
            "gguf_repo_id": "ggml-org/embeddinggemma-2-GGUF",
            "onnx_repo_id": "onnx-community/embeddinggemma-300m-ONNX",
            "public_mirror_repos": mirrors,
            "quantized_distributions": distributions,
            "gated_fallback": fallback,
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
        let model_id = "google/embeddinggemma-300m";
        let footprint_gb = 0.3;
        let load_id = if use_mlx {
            "mlx-community/embeddinggemma-300m-4bit"
        } else {
            model_id
        };
        let rec_engine = if use_mlx { "local_mlx" } else { "huggingface" };
        let mirrors = public_mirror_repos(model_id);
        let distributions = quantized_distributions(model_id);
        let fallback =
            gated_fallback_for_env(model_id, rec_engine, token.as_deref(), endpoint.as_deref());
        json!({
            "id": model_id,
            "model_id": model_id,
            "hf_repo_id": model_id,
            "mlx_repo_id": "mlx-community/embeddinggemma-300m-4bit",
            "mlx_8bit_repo_id": "mlx-community/embeddinggemma-300m-8bit",
            "gguf_repo_id": "ggml-org/embeddinggemma-300m-GGUF",
            "onnx_repo_id": "onnx-community/embeddinggemma-300m-ONNX",
            "public_mirror_repos": mirrors,
            "quantized_distributions": distributions,
            "gated_fallback": fallback,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_mirrors_and_quantized_distributions_cover_embeddinggemma_2() {
        let mirrors = public_mirror_repos("google/embeddinggemma-2");
        assert!(mirrors.contains(&"mlx-community/embeddinggemma-2-4bit"));
        assert!(mirrors.contains(&"mlx-community/embeddinggemma-2-8bit"));
        assert!(mirrors.contains(&"ggml-org/embeddinggemma-2-GGUF"));
        assert!(mirrors.contains(&"onnx-community/embeddinggemma-300m-ONNX"));

        let dist = quantized_distributions("google/embeddinggemma-2");
        assert_eq!(dist["gguf"], "ggml-org/embeddinggemma-2-GGUF");
        assert_eq!(dist["mlx_8bit"], "mlx-community/embeddinggemma-2-8bit");
    }

    #[test]
    fn gated_fallback_reflects_auth_token_and_custom_mirror_endpoint() {
        let unauth = gated_fallback_for_env(
            "google/embeddinggemma-2",
            "llamacpp",
            None,
            Some("https://hf-mirror.com/"),
        );
        assert_eq!(unauth["active"], true);
        assert_eq!(unauth["authenticated"], false);
        assert_eq!(unauth["fallback_repo"], "ggml-org/embeddinggemma-2-GGUF");
        assert_eq!(unauth["hf_endpoint"], "https://hf-mirror.com");
        assert_eq!(unauth["custom_mirror_active"], true);

        let authed = gated_fallback_for_env(
            "google/embeddinggemma-2",
            "local_mlx",
            Some("hf_secret_token"),
            None,
        );
        assert_eq!(authed["active"], false);
        assert_eq!(authed["authenticated"], true);
        assert_eq!(authed["hf_endpoint"], DEFAULT_HF_ENDPOINT);
    }

    #[test]
    fn compute_download_progress_and_sse_frame_include_speed_and_eta() {
        let progress = compute_download_progress(
            "download",
            Some("model.safetensors"),
            50 * 1024 * 1024,
            Some(100 * 1024 * 1024),
            5.0,
            true,
            Some("mlx-community/embeddinggemma-2-4bit"),
        );
        assert_eq!(progress["percent"], 50.0);
        assert_eq!(progress["speed_mbps"], 10.0);
        assert_eq!(progress["eta_seconds"], 5);
        assert_eq!(progress["current_file"], "model.safetensors");
        assert_eq!(progress["gated_fallback"], true);
        assert_eq!(
            progress["fallback_repo"],
            "mlx-community/embeddinggemma-2-4bit"
        );

        let sse = format_sse_download_progress(&progress);
        assert!(sse.starts_with("event: progress\ndata: "));
        assert!(sse.contains("\"speed_bps\""));
        assert!(sse.contains("\"eta_seconds\":5"));
    }
}
