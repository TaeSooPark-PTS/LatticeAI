"""Embedding model capability registry and PC-tailored hardware recommendations.

Dedicated module to keep `model_capability_registry.py` under the 1000-line cap
while providing first-class Google EmbeddingGemma 2 / 300M recommendation logic.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def build_embedding_capabilities(
    model_capability_cls: type,
    hardware_profile_cls: type,
    verification_status_cls: type,
) -> List[Any]:
    """Construct ModelCapability instances for EmbeddingGemma 2 / 300M."""
    return [
        model_capability_cls(
            id="google/embeddinggemma-2",
            hf_repo_id="google/embeddinggemma-2",
            name="Google EmbeddingGemma 2",
            family="EmbeddingGemma",
            tag="local-embedding",
            size="1.5GB",
            modality="text",
            architecture="gemma3",
            download_size_gb=1.48,
            quantization="FP16",
            provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
            download_strategy="hf_hub",
            load_strategy="sentence_transformers",
            hardware=hardware_profile_cls(
                min_ram_gb=4.0, recommended_ram_gb=8.0, apple_silicon_pref=True, cuda_pref=True,
                notes="Google DeepMind 740M multilingual & code semantic embedding model (768d). Uses ~1.5GB in FP16 HF or ~0.5GB in 4-bit MLX.",
            ),
            source_country="미국", source_company="Google",
            verification=verification_status_cls(
                hf_exists=True, hf_last_checked="2026-11-11", has_config=True, has_tokenizer=True,
                has_weights_hint=True, pipeline_tag="feature-extraction", likes=120, license="apache-2.0",
                notes="Hugging Face google/embeddinggemma-2, 768d semantic embeddings.", verified_by="hf-api-light",
            ),
            recommended_default=True,
            display_priority=70,
            category="embedding",
            task="embedding",
            dimensions=768,
        ),
        model_capability_cls(
            id="mlx-community/embeddinggemma-2-4bit",
            hf_repo_id="mlx-community/embeddinggemma-2-4bit",
            name="Google EmbeddingGemma 2 4-bit",
            family="EmbeddingGemma",
            tag="local-embedding",
            size="0.4GB",
            modality="text",
            architecture="gemma3",
            download_size_gb=0.42,
            quantization="4bit",
            provider_hints=["local_mlx"],
            download_strategy="hf_hub",
            load_strategy="mlx_embeddings",
            hardware=hardware_profile_cls(
                min_ram_gb=4.0, recommended_ram_gb=8.0, apple_silicon_pref=True,
                notes="Apple Silicon MLX 4-bit quantized EmbeddingGemma 2 (768d, 0.42GB on disk, ~0.5GB RAM).",
            ),
            source_country="미국", source_company="Google",
            verification=verification_status_cls(
                hf_exists=True, hf_last_checked="2026-11-11", has_config=True, has_tokenizer=True,
                has_weights_hint=True, pipeline_tag="feature-extraction", likes=45, license="apache-2.0",
                notes="library_name=mlx, ungated, 4-bit quantized EmbeddingGemma 2.", verified_by="hf-api-light",
            ),
            recommended_default=True,
            display_priority=71,
            category="embedding",
            task="embedding",
            dimensions=768,
        ),
        model_capability_cls(
            id="google/embeddinggemma-300m",
            hf_repo_id="google/embeddinggemma-300m",
            name="Google EmbeddingGemma 300M",
            family="EmbeddingGemma",
            tag="local-embedding",
            size="0.6GB",
            modality="text",
            architecture="gemma3",
            download_size_gb=0.60,
            quantization="FP16",
            provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
            download_strategy="hf_hub",
            load_strategy="sentence_transformers",
            hardware=hardware_profile_cls(
                min_ram_gb=2.0, recommended_ram_gb=4.0, apple_silicon_pref=True, cuda_pref=True,
                notes="Compact 300M multilingual semantic embedding model (768d) for constrained-RAM PCs (<8GB).",
            ),
            source_country="미국", source_company="Google",
            verification=verification_status_cls(
                hf_exists=True, hf_last_checked="2026-11-11", has_config=True, has_tokenizer=True,
                has_weights_hint=True, pipeline_tag="feature-extraction", likes=85, license="apache-2.0",
                notes="Hugging Face google/embeddinggemma-300m, 768d semantic embeddings.", verified_by="hf-api-light",
            ),
            recommended_default=False,
            display_priority=72,
            category="embedding",
            task="embedding",
            dimensions=768,
        ),
        model_capability_cls(
            id="mlx-community/embeddinggemma-300m-4bit",
            hf_repo_id="mlx-community/embeddinggemma-300m-4bit",
            name="Google EmbeddingGemma 300M 4-bit",
            family="EmbeddingGemma",
            tag="local-embedding",
            size="0.3GB",
            modality="text",
            architecture="gemma3",
            download_size_gb=0.30,
            quantization="4bit",
            provider_hints=["local_mlx"],
            download_strategy="hf_hub",
            load_strategy="mlx_embeddings",
            hardware=hardware_profile_cls(
                min_ram_gb=2.0, recommended_ram_gb=4.0, apple_silicon_pref=True,
                notes="Ultralight 4-bit MLX EmbeddingGemma 300M (~0.3GB RAM) for low-memory Macs.",
            ),
            source_country="미국", source_company="Google",
            verification=verification_status_cls(
                hf_exists=True, hf_last_checked="2026-11-11", has_config=True, has_tokenizer=True,
                has_weights_hint=True, pipeline_tag="feature-extraction", likes=30, license="apache-2.0",
                notes="library_name=mlx, ungated, 4-bit quantized EmbeddingGemma 300M.", verified_by="hf-api-light",
            ),
            recommended_default=False,
            display_priority=73,
            category="embedding",
            task="embedding",
            dimensions=768,
        ),
    ]


def recommend_embedding_for_hardware(
    *,
    ram_gb: Optional[float] = None,
    apple_silicon: bool = False,
    cuda: bool = False,
    get_capability_fn: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Return a PC-tailored embedding model recommendation accounting for RAM & accelerator."""
    effective_ram = 16.0 if ram_gb is None or ram_gb <= 0 else float(ram_gb)
    cap = get_capability_fn("google/embeddinggemma-2") if get_capability_fn else None
    mlx_cap = get_capability_fn("mlx-community/embeddinggemma-2-4bit") if get_capability_fn else None

    if effective_ram >= 8.0:
        use_mlx = bool(apple_silicon and mlx_cap is not None)
        footprint_gb = 0.5 if use_mlx else 1.5
        chosen_cap = cap or mlx_cap
        base = chosen_cap.to_legacy_dict() if chosen_cap is not None else {}
        return {
            **base,
            "id": "google/embeddinggemma-2",
            "model_id": "google/embeddinggemma-2",
            "hf_repo_id": "google/embeddinggemma-2",
            "mlx_repo_id": "mlx-community/embeddinggemma-2-4bit",
            "recommended_load_id": "mlx-community/embeddinggemma-2-4bit" if use_mlx else "google/embeddinggemma-2",
            "recommended_engine": "local_mlx" if use_mlx else "huggingface",
            "provider": "mlx" if use_mlx else "huggingface",
            "profile_id": "mlx:embeddinggemma-2" if use_mlx else "local:embeddinggemma-2",
            "name": "Google EmbeddingGemma 2",
            "display_name": "Google EmbeddingGemma 2",
            "family": "EmbeddingGemma",
            "category": "embedding",
            "role": "embedding",
            "task": "embedding",
            "tag": "local-embedding",
            "modality": "text",
            "dimensions": 768,
            "size": "0.4GB" if use_mlx else "1.5GB",
            "download_size": "0.4GB" if use_mlx else "1.5GB",
            "download_size_gb": 0.42 if use_mlx else 1.48,
            "memory_footprint_gb": footprint_gb,
            "effective_llm_ram_gb": max(0.0, round(effective_ram - footprint_gb, 2)),
            "status": "recommended",
            "load_status": "ready",
            "supported": True,
            "recommended_default": True,
            "display_priority": 70,
            "reason": (
                f"Apple Silicon ({int(round(effective_ram))}GB RAM) 맞춤 4-bit MLX / Hugging Face 시맨틱 임베딩 모델 (Google EmbeddingGemma 2, 768차원)"
                if apple_silicon
                else f"{int(round(effective_ram))}GB RAM PC 맞춤 Hugging Face 시맨틱 임베딩 모델 (Google EmbeddingGemma 2, 768차원)"
            ),
        }

    cap_300m = get_capability_fn("google/embeddinggemma-300m") if get_capability_fn else None
    use_mlx = bool(apple_silicon)
    footprint_gb = 0.3
    base = cap_300m.to_legacy_dict() if cap_300m else {}
    return {
        **base,
        "id": "google/embeddinggemma-300m",
        "model_id": "google/embeddinggemma-300m",
        "hf_repo_id": "google/embeddinggemma-300m",
        "mlx_repo_id": "mlx-community/embeddinggemma-300m-4bit",
        "recommended_load_id": "mlx-community/embeddinggemma-300m-4bit" if use_mlx else "google/embeddinggemma-300m",
        "recommended_engine": "local_mlx" if use_mlx else "huggingface",
        "provider": "mlx" if use_mlx else "huggingface",
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
        "size": "0.3GB" if use_mlx else "0.6GB",
        "download_size": "0.3GB" if use_mlx else "0.6GB",
        "download_size_gb": 0.30 if use_mlx else 0.60,
        "memory_footprint_gb": footprint_gb,
        "effective_llm_ram_gb": max(0.0, round(effective_ram - footprint_gb, 2)),
        "status": "recommended",
        "load_status": "ready",
        "supported": True,
        "recommended_default": True,
        "display_priority": 72,
        "reason": f"저사양 PC ({int(round(effective_ram))}GB RAM) 맞춤 초경량 시맨틱 임베딩 모델 (Google EmbeddingGemma 300M, 768차원)",
    }
