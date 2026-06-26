"""Structured Model Capability Registry for Lattice AI 5.2.0+.

User-focused, transparent model catalog with:
- HF repo provenance
- Modality / vision support
- Quantization, size, download/load strategies
- Hardware notes (RAM estimates, Apple Silicon affinity)
- License / safety notes
- Verification status (populated by scripts/verify_hf_model_registry.py or runtime light probe)

This replaces the flat ENGINE_MODEL_CATALOG construction with a richer,
queryable source of truth while preserving exact legacy shapes for
model_catalog / recommendation / API / frontend consumers.

All entries are recommended multimodal (VLM) first. Text-only can be added later.
Verification is honest: hf_exists + light metadata/config presence; full weights
are never auto-fetched by the verifier. Large models (>12GB) explicitly note
"local load practical only on high-RAM Apple Silicon or CUDA; expect long download".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class HardwareProfile:
    min_ram_gb: Optional[float] = None
    recommended_ram_gb: Optional[float] = None
    apple_silicon_pref: bool = False
    cuda_pref: bool = False
    notes: str = ""


@dataclass(frozen=True)
class VerificationStatus:
    hf_exists: bool = False
    hf_last_checked: Optional[str] = None  # ISO
    has_config: bool = False
    has_tokenizer: bool = False
    has_weights_hint: bool = False  # safetensors or gguf siblings seen in meta
    pipeline_tag: Optional[str] = None
    likes: Optional[int] = None
    license: Optional[str] = None
    notes: str = ""
    verified_by: str = "hf-api-light"  # or "local-load-test"


@dataclass(frozen=True)
class ModelCapability:
    """Rich capability entry. id is the canonical key used in ENGINE_MODEL_CATALOG."""
    id: str
    hf_repo_id: str  # clean HF path for download (e.g. mlx-community/xxx or Qwen/yyy)
    name: str
    family: str
    tag: str
    size: str  # display string "7.6GB", "pull required"
    modality: str = "multimodal"  # multimodal | vision | text | audio etc.
    quantization: Optional[str] = None  # "4bit", "Q4_K_M", "GGUF-Q4"
    provider_hints: List[str] = field(default_factory=lambda: ["local_mlx"])  # which engines this id primarily maps to
    download_strategy: str = "hf_hub"  # hf_hub | ollama_pull | lmstudio_app | gguf_manual
    load_strategy: str = "mlx_vlm"  # mlx_vlm | ollama | vllm | lmstudio | llamacpp_server
    hardware: HardwareProfile = field(default_factory=HardwareProfile)
    license: str = "apache-2.0"
    safety_notes: str = "Standard open weights; review license and responsible use guidelines."
    source_country: str = ""
    source_company: str = ""
    execution_method: str = "내 컴퓨터에서만 실행"
    internet_requirement: str = "모델을 다운로드할 때만 인터넷 필요; 실행 중에는 필요 없음"
    # Verification
    verification: VerificationStatus = field(default_factory=VerificationStatus)
    # UI / rec hints
    recommended_default: bool = False
    display_priority: int = 100

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Exact shape expected by older ENGINE_MODEL_CATALOG consumers + extra 5.2 fields."""
        base = {
            "id": self.id,
            "name": self.name,
            "model_name": self.name.split(" via ")[0] if " via " in self.name else self.name,
            "family": self.family,
            "tag": self.tag,
            "size": self.size,
            "pullable": True,
            "modality": self.modality,
            "source_country": self.source_country,
            "source_company": self.source_company,
            "execution_method": self.execution_method,
            "run_location": "내 컴퓨터에서만 실행",
            "internet_requirement": self.internet_requirement,
            "source_display_order": [
                "source_country", "source_company", "execution_method",
                "internet_requirement", "model_name"
            ],
            # 5.2+ rich fields (non-breaking; frontend + backend read if present)
            "hf_repo_id": self.hf_repo_id,
            "quantization": self.quantization,
            "download_strategy": self.download_strategy,
            "load_strategy": self.load_strategy,
            "hardware": {
                "min_ram_gb": self.hardware.min_ram_gb,
                "recommended_ram_gb": self.hardware.recommended_ram_gb,
                "apple_silicon_pref": self.hardware.apple_silicon_pref,
                "cuda_pref": self.hardware.cuda_pref,
                "notes": self.hardware.notes,
            },
            "license": self.license,
            "safety_notes": self.safety_notes,
            "verification": {
                "hf_exists": self.verification.hf_exists,
                "hf_last_checked": self.verification.hf_last_checked,
                "has_config": self.verification.has_config,
                "has_tokenizer": self.verification.has_tokenizer,
                "has_weights_hint": self.verification.has_weights_hint,
                "pipeline_tag": self.verification.pipeline_tag,
                "verified": bool(
                    self.verification.hf_exists
                    and self.verification.has_config
                    and self.verification.has_tokenizer
                ),
                "notes": self.verification.notes,
            },
            "recommended_default": self.recommended_default,
        }
        return base


# ── Curated 5.2.0 registry (bold user-focused: transparent, multimodal-first, verified where practical) ──
# Current Gemma-4 / Qwen3-VL / Llama-4 kept + modern additions (Gemma3, Qwen2.5-VL, Llama-3.2-Vision, Pixtral).
# Presence verified via HF API (lightweight model_info) on 2026-06-14.
# Full weight download is user-consent only; entries without config/tokenizer
# hints are shown as available-but-not-load-verified.

_REGISTRY: List[ModelCapability] = [
    # Gemma 4 family (mlx-community 4-bit, Apple-first, excellent local VLM)
    ModelCapability(
        id="mlx-community/gemma-4-e2b-4bit",
        hf_repo_id="mlx-community/gemma-4-e2b-4bit",
        name="Gemma 4 E2B Base",
        family="Gemma 4",
        tag="local-vlm",
        size="3.6GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=6.0, recommended_ram_gb=8.0, apple_silicon_pref=True, notes="Tiny but capable vision; great first local VLM."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="any-to-any", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=10,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-e2b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-e2b-it-4bit",
        name="Gemma 4 E2B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="3.6GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=6.0, recommended_ram_gb=8.0, apple_silicon_pref=True, notes="Instruct-tuned; preferred over base for chat."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="any-to-any", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=11,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-e4b-4bit",
        hf_repo_id="mlx-community/gemma-4-e4b-4bit",
        name="Gemma 4 E4B Base",
        family="Gemma 4",
        tag="local-vlm",
        size="5.2GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=10.0, apple_silicon_pref=True),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="any-to-any", license="apache-2.0", verified_by="hf-api-light"),
    ),
    ModelCapability(
        id="mlx-community/gemma-4-e4b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-e4b-it-4bit",
        name="Gemma 4 E4B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="5.2GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=10.0, apple_silicon_pref=True),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="any-to-any", license="apache-2.0", verified_by="hf-api-light"),
    ),
    ModelCapability(
        id="mlx-community/gemma-4-12b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-12b-it-4bit",
        name="Gemma 4 12B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="7.6GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=12.0, recommended_ram_gb=16.0, apple_silicon_pref=True, notes="Sweet spot for local multimodal on M-series 16GB+ or 24GB+."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=20,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-26b-a4b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-26b-a4b-it-4bit",
        name="Gemma 4 26B A4B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="15.6GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=20.0, recommended_ram_gb=28.0, apple_silicon_pref=True, notes="Large MoE-style; local load practical only on high-RAM Apple Silicon (32GB+). Long download expected."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        display_priority=50,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-31b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-31b-it-4bit",
        name="Gemma 4 31B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="18.4GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=24.0, recommended_ram_gb=32.0, apple_silicon_pref=True, notes="Very large; high-end local only. Consider cloud fallback for lower RAM."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
    ),

    # Qwen3-VL (strong real-world multimodal, good small sizes)
    ModelCapability(
        id="mlx-community/Qwen3-VL-4B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-4B-Instruct-4bit",
        name="Qwen3-VL 4B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="2.7GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=5.0, recommended_ram_gb=8.0, apple_silicon_pref=True, notes="Extremely compact strong VLM. Best default for low-RAM Macs."),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=5,
    ),
    ModelCapability(
        id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
        name="Qwen3-VL 8B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="4.8GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=12.0, apple_silicon_pref=True),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=15,
    ),
    ModelCapability(
        id="mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit",
        name="Qwen3-VL 30B A3B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="18GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=24.0, recommended_ram_gb=32.0, apple_silicon_pref=True, notes="Large MoE VLM; practical local only on 32GB+ Apple Silicon or strong CUDA. Download is multi-GB."),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
    ),

    # Llama 4
    ModelCapability(
        id="mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        hf_repo_id="mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        name="Llama 4 Scout 17B 16E",
        family="Llama 4",
        tag="local-vlm",
        size="11.8GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=16.0, recommended_ram_gb=20.0, apple_silicon_pref=True),
        source_country="미국", source_company="Meta",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="llama3.1-ish / meta-llama", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=25,
    ),

    # ── Modern additions for 5.2.0 (verified on HF, user choice expansion) ──
    # Gemma 3 (excellent real multimodal balance, smaller than 4 where present)
    ModelCapability(
        id="google/gemma-3-4b-it",
        hf_repo_id="google/gemma-3-4b-it",
        name="Gemma 3 4B Instruct (HF)",
        family="Gemma 3",
        tag="local-vlm",
        size="~5GB+",
        quantization="bf16 / 4bit variants",
        provider_hints=["local_mlx", "vllm", "ollama"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=12.0, apple_silicon_pref=True, notes="Use mlx-community quantized ports when available for best local perf."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="gemma-terms", verified_by="hf-api-light"),
        display_priority=30,
    ),
    ModelCapability(
        id="google/gemma-3-12b-it",
        hf_repo_id="google/gemma-3-12b-it",
        name="Gemma 3 12B Instruct (HF)",
        family="Gemma 3",
        tag="local-vlm",
        size="~12GB+",
        quantization="bf16 / GGUF-4bit",
        provider_hints=["ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="ollama",
        hardware=HardwareProfile(min_ram_gb=16.0, recommended_ram_gb=20.0, notes="Prefer quantized GGUF for llama.cpp / ollama on non-Apple or lower RAM."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="gemma-terms", verified_by="hf-api-light"),
    ),

    # Qwen2.5-VL (battle-tested, widely supported)
    ModelCapability(
        id="Qwen/Qwen2.5-VL-7B-Instruct",
        hf_repo_id="Qwen/Qwen2.5-VL-7B-Instruct",
        name="Qwen2.5-VL 7B Instruct",
        family="Qwen2.5-VL",
        tag="local-vlm",
        size="~8-15GB (quant dependent)",
        quantization="AWQ / GGUF / 4bit ports",
        provider_hints=["vllm", "ollama", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="vllm",
        hardware=HardwareProfile(min_ram_gb=12.0, recommended_ram_gb=16.0, cuda_pref=True, notes="Strong general VLM. mlx-community or GGUF ports recommended for local Apple."),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        display_priority=35,
    ),

    # Llama 3.2 Vision (widely available, good ecosystem)
    ModelCapability(
        id="meta-llama/Llama-3.2-11B-Vision-Instruct",
        hf_repo_id="meta-llama/Llama-3.2-11B-Vision-Instruct",
        name="Llama 3.2 11B Vision Instruct",
        family="Llama 3.2 Vision",
        tag="local-vlm",
        size="~11-22GB (quant)",
        quantization="Q4_K_M GGUF widely available",
        provider_hints=["ollama", "llamacpp", "lmstudio", "vllm"],
        download_strategy="hf_hub",
        load_strategy="ollama",
        hardware=HardwareProfile(min_ram_gb=14.0, recommended_ram_gb=18.0, notes="Excellent GGUF support. Ollama / llama.cpp default path for most users."),
        source_country="미국", source_company="Meta",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="llama3.2", verified_by="hf-api-light"),
        display_priority=40,
    ),

    # Pixtral (Mistral multimodal, strong)
    ModelCapability(
        id="mistralai/Pixtral-12B-2409",
        hf_repo_id="mistralai/Pixtral-12B-2409",
        name="Pixtral 12B (Mistral)",
        family="Pixtral",
        tag="local-vlm",
        size="~12-24GB",
        quantization="GGUF / AWQ ports",
        provider_hints=["vllm", "ollama", "lmstudio"],
        download_strategy="hf_hub",
        load_strategy="vllm",
        hardware=HardwareProfile(min_ram_gb=16.0, recommended_ram_gb=20.0, cuda_pref=True, notes="High quality vision-language. Best on CUDA / vLLM; GGUF for CPU/Apple via community ports."),
        source_country="프랑스", source_company="Mistral AI",
        verification=VerificationStatus(
            hf_exists=True,
            has_config=False,
            has_tokenizer=False,
            has_weights_hint=True,
            pipeline_tag=None,
            license="mistral-research",
            notes="HF repo and weights are present, but config/tokenizer files were not visible in the lightweight HF tree check; treat as available but not local-load verified.",
            verified_by="hf-api-light",
        ),
        display_priority=45,
    ),

    # Additional recent multimodal (2025-2026 era ports, MLX-first where possible)
    ModelCapability(
        id="mlx-community/Llama-3.2-11B-Vision-Instruct-4bit",
        hf_repo_id="mlx-community/Llama-3.2-11B-Vision-Instruct-4bit",
        name="Llama 3.2 11B Vision Instruct",
        family="Llama 3.2 Vision",
        tag="local-vlm",
        size="6.8GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=10.0, recommended_ram_gb=14.0, apple_silicon_pref=True, notes="Excellent vision for its size. Great all-rounder multimodal."),
        source_country="미국", source_company="Meta",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="llama3.2", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=25,
    ),
    ModelCapability(
        id="mlx-community/phi-3.5-vision-4bit",
        hf_repo_id="mlx-community/phi-3.5-vision-4bit",
        name="Phi-3.5 Vision 4B",
        family="Phi Vision",
        tag="local-vlm",
        size="3.2GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=6.0, recommended_ram_gb=8.0, apple_silicon_pref=True, notes="Microsoft small VLM, fast and capable for on-device vision tasks."),
        source_country="미국", source_company="Microsoft",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="mit", verified_by="hf-api-light"),
        display_priority=30,
    ),
    ModelCapability(
        id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        name="Qwen2.5-VL 7B",
        family="Qwen2.5-VL",
        tag="local-vlm",
        size="4.5GB",
        quantization="4bit",
        provider_hints=["local_mlx", "ollama"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=12.0, apple_silicon_pref=True, notes="Strong Chinese/English vision model, updated from Qwen2-VL."),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        recommended_default=True,
        display_priority=18,
    ),
    ModelCapability(
        id="mlx-community/moondream2-4bit",
        hf_repo_id="mlx-community/moondream2-4bit",
        name="Moondream2 (small VLM)",
        family="Moondream",
        tag="local-vlm",
        size="1.8GB",
        quantization="4bit",
        provider_hints=["local_mlx"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(min_ram_gb=4.0, recommended_ram_gb=6.0, apple_silicon_pref=True, notes="Ultra-light vision model for quick descriptions and simple QA. Ideal for low-resource or frequent use."),
        source_country="미국", source_company="vikhyatk",
        verification=VerificationStatus(hf_exists=True, has_config=True, has_tokenizer=True, has_weights_hint=True, pipeline_tag="image-text-to-text", license="apache-2.0", verified_by="hf-api-light"),
        display_priority=8,
    ),
]


def get_all_capabilities() -> List[ModelCapability]:
    return list(_REGISTRY)


def get_capability(model_id: str) -> Optional[ModelCapability]:
    for m in _REGISTRY:
        if m.id == model_id or m.hf_repo_id == model_id:
            return m
    return None


def build_engine_model_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return legacy ENGINE_MODEL_CATALOG shape, enriched with 5.2 fields."""
    from collections import defaultdict
    by_engine: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    engine_map = {
        "local_mlx": ["local_mlx"],
        "ollama": ["ollama"],
        "vllm": ["vllm"],
        "lmstudio": ["lmstudio"],
        "llamacpp": ["llamacpp"],
    }

    for cap in _REGISTRY:
        for eng_key, hints in engine_map.items():
            if any(h in cap.provider_hints for h in hints) or eng_key in cap.provider_hints:
                legacy = cap.to_legacy_dict()
                # Adapt id for non-mlx engines (match historical patterns)
                if eng_key == "ollama" and not legacy["id"].startswith("ollama:"):
                    # historical used prefixed or hf.co for some
                    if "gguf" in cap.tag.lower() or "gguf" in (cap.quantization or "").lower():
                        legacy["id"] = f"ollama:hf.co/ggml-org/{cap.family.lower().replace(' ', '')}-12B-it-GGUF:Q4_K_M"  # fallback, overridden by aliases
                    else:
                        legacy["id"] = f"ollama:{cap.hf_repo_id.split('/')[-1].lower()}"
                elif eng_key == "vllm" and not legacy["id"].startswith("vllm:"):
                    legacy["id"] = f"vllm:{cap.hf_repo_id}"
                elif eng_key == "lmstudio" and not legacy["id"].startswith("lmstudio:"):
                    legacy["id"] = f"lmstudio:{cap.hf_repo_id}"
                elif eng_key == "llamacpp" and not legacy["id"].startswith("llamacpp:"):
                    legacy["id"] = f"llamacpp:{cap.hf_repo_id}-GGUF"
                by_engine[eng_key].append(legacy)

    # Ensure at least the primary local_mlx ones are present (exact historical)
    # If projection missed any, inject the original local_mlx entries enriched
    if not by_engine.get("local_mlx"):
        for cap in _REGISTRY:
            if "local_mlx" in cap.provider_hints:
                by_engine["local_mlx"].append(cap.to_legacy_dict())

    return {k: v for k, v in by_engine.items()}


def get_verified_models() -> List[Dict[str, Any]]:
    """Return only load-verified HF entries with rich fields (for API/UI)."""
    return [
        c.to_legacy_dict() for c in _REGISTRY
        if c.verification.hf_exists and c.verification.has_config and c.verification.has_tokenizer
    ]


# Back-compat: expose a simple list mirroring the old top-level for mlx
LOCAL_MLX_MODELS = [c.to_legacy_dict() for c in _REGISTRY if "local_mlx" in c.provider_hints]
