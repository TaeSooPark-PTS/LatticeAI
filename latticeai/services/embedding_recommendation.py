"""Embedding model capability registry and PC-tailored hardware recommendations.

Dedicated module to keep `model_capability_registry.py` under the 1000-line cap
while providing first-class Google EmbeddingGemma 2 / 300M recommendation logic,
unauthenticated public mirror resolution, and quantized GGUF/MLX/ONNX fallbacks.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Mapping, Optional

DEFAULT_HF_ENDPOINT = "https://huggingface.co"

PUBLIC_EMBEDDING_MIRRORS: Dict[str, List[str]] = {
    "google/embeddinggemma-2": [
        "mlx-community/embeddinggemma-2-4bit",
        "mlx-community/embeddinggemma-2-8bit",
        "ggml-org/embeddinggemma-2-GGUF",
        "onnx-community/embeddinggemma-300m-ONNX",
    ],
    "google/embeddinggemma-300m": [
        "mlx-community/embeddinggemma-300m-4bit",
        "mlx-community/embeddinggemma-300m-8bit",
        "ggml-org/embeddinggemma-300m-GGUF",
        "onnx-community/embeddinggemma-300m-ONNX",
    ],
}

QUANTIZED_DISTRIBUTIONS: Dict[str, Dict[str, str]] = {
    "google/embeddinggemma-2": {
        "mlx_4bit": "mlx-community/embeddinggemma-2-4bit",
        "mlx_8bit": "mlx-community/embeddinggemma-2-8bit",
        "gguf": "ggml-org/embeddinggemma-2-GGUF",
        "onnx": "onnx-community/embeddinggemma-300m-ONNX",
    },
    "google/embeddinggemma-300m": {
        "mlx_4bit": "mlx-community/embeddinggemma-300m-4bit",
        "mlx_8bit": "mlx-community/embeddinggemma-300m-8bit",
        "gguf": "ggml-org/embeddinggemma-300m-GGUF",
        "onnx": "onnx-community/embeddinggemma-300m-ONNX",
    },
}


def _canonical_embedding_repo(repo_id: str) -> Optional[str]:
    norm = str(repo_id or "").strip().lower()
    if not norm:
        return None
    if norm in PUBLIC_EMBEDDING_MIRRORS:
        return norm
    if "embeddinggemma-300m" in norm or "embeddinggemma_300m" in norm:
        return "google/embeddinggemma-300m"
    if "embeddinggemma-2" in norm or "embeddinggemma_2" in norm or norm == "embeddinggemma":
        return "google/embeddinggemma-2"
    return None


def has_hf_auth_token(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when an explicit Hugging Face authentication token is configured."""
    source = env if env is not None else os.environ
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = str(source.get(key) or "").strip()
        if value:
            return True
    return False


def resolve_hf_endpoint(env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the active Hugging Face Hub endpoint or custom mirror URL."""
    source = env if env is not None else os.environ
    for key in ("HF_ENDPOINT", "HUGGINGFACE_HUB_ENDPOINT", "LATTICEAI_HF_MIRROR"):
        raw = str(source.get(key) or "").strip().rstrip("/")
        if raw:
            return raw
    return DEFAULT_HF_ENDPOINT


PUBLIC_GGUF_MIRRORS: Dict[str, List[str]] = {
    "ggml-org/qwen3.8-27b-gguf": [
        "unsloth/Qwen3.8-27B-GGUF",
    ],
}


def get_public_mirror_repos(repo_id: str, provider: str = "local_mlx") -> List[str]:
    """Return ordered unauthenticated public mirror / quantized repos for an embedding or GGUF model."""
    norm_repo = str(repo_id or "").strip().lower()
    if norm_repo in PUBLIC_GGUF_MIRRORS:
        return list(PUBLIC_GGUF_MIRRORS[norm_repo])
    canonical = _canonical_embedding_repo(repo_id)
    if canonical is None:
        return []
    dists = QUANTIZED_DISTRIBUTIONS[canonical]
    base_list = list(PUBLIC_EMBEDDING_MIRRORS[canonical])
    prov = str(provider or "local_mlx").strip().lower()
    if prov == "llamacpp":
        gguf = dists["gguf"]
        return [gguf] + [item for item in base_list if item != gguf]
    if prov in {"onnx", "cpu"}:
        onnx = dists["onnx"]
        return [onnx] + [item for item in base_list if item != onnx]
    return base_list


def select_public_fallback_repo(
    repo_id: str,
    provider: str = "local_mlx",
    *,
    prefer_8bit: bool = False,
) -> Optional[str]:
    """Select the primary public unauthenticated fallback repo for a gated embedding model."""
    canonical = _canonical_embedding_repo(repo_id)
    if canonical is None:
        return None
    dists = QUANTIZED_DISTRIBUTIONS[canonical]
    prov = str(provider or "local_mlx").strip().lower()
    if prov == "llamacpp":
        return dists["gguf"]
    if prov in {"onnx", "cpu"}:
        return dists["onnx"]
    if prefer_8bit:
        return dists["mlx_8bit"]
    return dists["mlx_4bit"]


def build_gated_fallback_metadata(
    repo_id: str,
    *,
    apple_silicon: bool = False,
    provider: str = "local_mlx",
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Build structured mirror/fallback metadata for gated HF embedding models."""
    canonical = _canonical_embedding_repo(repo_id) or "google/embeddinggemma-2"
    dists = dict(QUANTIZED_DISTRIBUTIONS[canonical])
    mirrors = get_public_mirror_repos(canonical, provider=provider)
    authenticated = has_hf_auth_token(env)
    endpoint = resolve_hf_endpoint(env)
    fallback_repo = select_public_fallback_repo(
        canonical,
        provider="local_mlx" if apple_silicon and provider in {"", "huggingface"} else provider,
    ) or dists["mlx_4bit"]
    return {
        "enabled": True,
        "active": not authenticated,
        "authenticated": authenticated,
        "requires_hf_auth": True,
        "requires_hf_token": False,
        "hf_endpoint": endpoint,
        "custom_mirror_active": endpoint != DEFAULT_HF_ENDPOINT,
        "primary_repo": canonical,
        "fallback_repo": fallback_repo,
        "mlx_4bit_repo": dists["mlx_4bit"],
        "mlx_8bit_repo": dists["mlx_8bit"],
        "gguf_repo": dists["gguf"],
        "onnx_repo": dists["onnx"],
        "public_mirror_repos": mirrors,
        "quantized_distributions": dists,
        "strategy": "unauthenticated_public_mirror_and_quantized_fallback",
        "notice": (
            f"HF_TOKEN 미설정 시 공개 무인증 미러({fallback_repo} · {dists['gguf']})로 자동 전환됩니다."
            if not authenticated
            else f"HF_TOKEN 인증 활성화됨 (게이트/권한 오류 시 {fallback_repo} 공개 미러로 자동 폴백)"
        ),
    }


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
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Return a PC-tailored embedding model recommendation accounting for RAM & accelerator."""
    effective_ram = 16.0 if ram_gb is None or ram_gb <= 0 else float(ram_gb)
    cap = get_capability_fn("google/embeddinggemma-2") if get_capability_fn else None
    mlx_cap = get_capability_fn("mlx-community/embeddinggemma-2-4bit") if get_capability_fn else None

    if effective_ram >= 8.0:
        use_mlx = bool(apple_silicon and mlx_cap is not None)
        footprint_gb = 0.5 if use_mlx else 1.5
        batch_size = 64 if (effective_ram >= 24.0 and (apple_silicon or cuda)) else 32
        chosen_cap = cap or mlx_cap
        base = chosen_cap.to_legacy_dict() if chosen_cap is not None else {}
        rec_engine = "local_mlx" if use_mlx else "huggingface"
        fallback_meta = build_gated_fallback_metadata(
            "google/embeddinggemma-2",
            apple_silicon=apple_silicon,
            provider=rec_engine,
            env=env,
        )
        return {
            **base,
            "id": "google/embeddinggemma-2",
            "model_id": "google/embeddinggemma-2",
            "hf_repo_id": "google/embeddinggemma-2",
            "mlx_repo_id": "mlx-community/embeddinggemma-2-4bit",
            "mlx_8bit_repo_id": "mlx-community/embeddinggemma-2-8bit",
            "gguf_repo_id": "ggml-org/embeddinggemma-2-GGUF",
            "onnx_repo_id": "onnx-community/embeddinggemma-300m-ONNX",
            "public_mirror_repos": fallback_meta["public_mirror_repos"],
            "quantized_distributions": fallback_meta["quantized_distributions"],
            "gated_fallback": fallback_meta,
            "recommended_load_id": "mlx-community/embeddinggemma-2-4bit" if use_mlx else "google/embeddinggemma-2",
            "recommended_engine": rec_engine,
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
            "recommended_batch_size": batch_size,
            "auto_drain_on_ready": True,
            "transition_from_fallback": True,
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
    rec_engine = "local_mlx" if use_mlx else "huggingface"
    fallback_meta = build_gated_fallback_metadata(
        "google/embeddinggemma-300m",
        apple_silicon=apple_silicon,
        provider=rec_engine,
        env=env,
    )
    return {
        **base,
        "id": "google/embeddinggemma-300m",
        "model_id": "google/embeddinggemma-300m",
        "hf_repo_id": "google/embeddinggemma-300m",
        "mlx_repo_id": "mlx-community/embeddinggemma-300m-4bit",
        "mlx_8bit_repo_id": "mlx-community/embeddinggemma-300m-8bit",
        "gguf_repo_id": "ggml-org/embeddinggemma-300m-GGUF",
        "onnx_repo_id": "onnx-community/embeddinggemma-300m-ONNX",
        "public_mirror_repos": fallback_meta["public_mirror_repos"],
        "quantized_distributions": fallback_meta["quantized_distributions"],
        "gated_fallback": fallback_meta,
        "recommended_load_id": "mlx-community/embeddinggemma-300m-4bit" if use_mlx else "google/embeddinggemma-300m",
        "recommended_engine": rec_engine,
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
        "recommended_batch_size": 16,
        "auto_drain_on_ready": True,
        "transition_from_fallback": True,
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


def is_embedding_model_id(model_id: str) -> bool:
    """Return True when ``model_id`` identifies an embedding model rather than a chat LLM."""
    lower = (model_id or "").strip().lower()
    if not lower:
        return False
    return any(
        marker in lower
        for marker in (
            "embeddinggemma",
            "bge-",
            "/bge",
            "nomic-embed",
            "text-embedding",
            "multilingual-e5",
            "gte-",
            "snowflake-arctic-embed",
        )
    )


def activate_downloaded_embedding_model(
    model_id: str,
    *,
    provider: str = "huggingface",
    dim: int = 768,
    data_dir: Optional[Any] = None,
) -> Dict[str, Any]:
    """Persist the semantic embedding contract and enqueue fallback upgrade vector_jobs."""
    import json
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    norm_provider = "mlx" if provider in {"local_mlx", "mlx"} else (provider or "huggingface")
    clean_model = (model_id or "").strip()
    if ":" in clean_model and clean_model.split(":", 1)[0] in {"local_mlx", "mlx", "huggingface", "ollama"}:
        clean_model = clean_model.split(":", 1)[1]
    canonical_model_id = f"{norm_provider}:{clean_model}:{dim}"
    resolved_dir = Path(
        data_dir or os.environ.get("LATTICEAI_DATA_DIR") or (Path.home() / ".ltcai")
    ).expanduser()
    db_path = resolved_dir / "knowledge_graph.sqlite"
    contract = {
        "schema_version": 1,
        "provider": norm_provider,
        "model_id": canonical_model_id,
        "model_revision": None,
        "dim": int(dim),
        "dimension": int(dim),
        "normalized": True,
        "normalization": "l2",
        "passage_mode": "passage",
        "query_mode": "query",
        "index_version": 1,
        "grade": "production",
        "semantic": True,
        "is_fallback": False,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    enqueued = 0
    if db_path.exists():
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS graph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO graph_meta(key, value) VALUES ('embedding_index_contract', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(contract),),
            )
            has_jobs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_jobs'"
            ).fetchone()
            has_vecs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_embeddings'"
            ).fetchone()
            if has_jobs and has_vecs:
                now = contract["updated_at"]
                rows = conn.execute(
                    "SELECT DISTINCT COALESCE(NULLIF(source_node, ''), item_id) "
                    "FROM vector_embeddings WHERE COALESCE(embedding_model, '') != ?",
                    (canonical_model_id,),
                ).fetchall()
                for (nid,) in rows:
                    if not nid:
                        continue
                    cur = conn.execute(
                        "INSERT INTO vector_jobs(node_id, status, attempts, detail, created_at, updated_at) "
                        "VALUES (?, 'pending', 0, 'semantic_upgrade', ?, ?) "
                        "ON CONFLICT(node_id) DO UPDATE SET status='pending', detail='semantic_upgrade', updated_at=excluded.updated_at "
                        "WHERE vector_jobs.status = 'done'",
                        (str(nid), now, now),
                    )
                    enqueued += max(0, cur.rowcount)
            conn.commit()
        finally:
            conn.close()
    return {
        "activated": True,
        "contract": contract,
        "enqueued_upgrade_jobs": enqueued,
    }

