"""Structured Model Capability Registry for Lattice AI.

User-focused, transparent model catalog with:
- HF repo provenance (exact-case repo id, pinned against the HF API)
- Modality / vision support and the HF config architecture (``model_type``)
- Quantization, size, download/load strategies
- Hardware notes (RAM estimates, Apple Silicon affinity)
- License / safety notes
- Verification status (populated by scripts/verify_hf_model_registry.py or runtime light probe)

This replaces the flat ENGINE_MODEL_CATALOG construction with a richer,
queryable source of truth while preserving exact legacy shapes for
model_catalog / recommendation / API / frontend consumers.

11.2.0 — two lists, one registry
================================
A model can be *offered* or merely *understood*, and conflating the two either
pushes dead downloads at people or orphans the weights already on their disk.
Every entry therefore carries a :attr:`ModelCapability.lifecycle`:

``RECOMMENDED``
    Current generation. Appears in ENGINE_MODEL_CATALOG, the recommendation
    tiers, and the download paths.
``LEGACY``
    Superseded, but real and still loadable. **Never** offered for download and
    never recommended — it exists so a model a user already downloaded keeps its
    name, size, family and runtime profile instead of showing up as an unknown
    blob. See :func:`get_legacy_capabilities` / :func:`is_recognized_model`.

Models that no longer exist on the Hub, or that cannot be fetched without
credentials (gated), are **deleted outright** rather than demoted: pretending to
recognise something nobody can obtain is not compatibility, it is noise.

Verification is honest: hf_exists + metadata/siblings presence measured through
the public HF REST API; full weights are never auto-fetched by the verifier and
no model is ever loaded to produce these flags. Large models (>12GB) explicitly
note "local load practical only on high-RAM Apple Silicon or CUDA; expect long
download".
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


# ── Lifecycle vocabulary ──────────────────────────────────────────────────────
#: Current generation: offered for download, listed in the catalog, recommended.
RECOMMENDED = "recommended"
#: Superseded but still loadable: recognised only, never offered or recommended.
LEGACY = "legacy"


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
    #: HF ``config.json`` ``model_type`` — the string mlx-lm / mlx-vlm dispatches
    #: on. Pinned from the Hub API so the verifier can judge loadability
    #: statically, without downloading or importing anything.
    architecture: str = ""
    #: Measured sum of the repo's sibling file sizes, in GB (10^9 bytes).
    #: ``None`` for tool-managed engines that pull on demand.
    download_size_gb: Optional[float] = None
    #: RECOMMENDED (offered + recommended) or LEGACY (recognised only).
    lifecycle: str = RECOMMENDED
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
            "architecture": self.architecture,
            "lifecycle": self.lifecycle,
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


# ── Curated registry ──────────────────────────────────────────────────────────
# Every field below was measured against the public HF REST API on 2026-08-10:
# repo existence, gated flag, library_name/tags, config ``model_type``, sibling
# file list and the exact byte sum of those siblings (``size`` and
# ``download_size_gb``). Nothing here was inferred from a model card, and no
# weights were downloaded to produce it — re-run
# ``scripts/verify_hf_model_registry.py`` to re-measure.
#
# Repo ids are stored in the Hub's own canonical casing (the API echoes the
# canonical id even when queried with different case, e.g. requesting
# ``gemma-4-12b-it-4bit`` answers with ``gemma-4-12B-it-4bit``). Storing the
# canonical form keeps the download path, the on-disk cache directory and the
# catalog key identical.

_REGISTRY: List[ModelCapability] = [
    # ── Ultralight tier (≤8GB RAM) ───────────────────────────────────────────
    ModelCapability(
        id="mlx-community/LFM2.5-2.6B-4bit",
        hf_repo_id="mlx-community/LFM2.5-2.6B-4bit",
        name="LFM2.5 2.6B",
        family="LFM2.5",
        tag="local-llm",
        size="1.5GB",
        modality="text",
        architecture="lfm2",
        download_size_gb=1.54,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_lm",
        hardware=HardwareProfile(
            min_ram_gb=6.0, recommended_ram_gb=8.0, apple_silicon_pref=True,
            notes="Smallest model that still answers in Korean. Text only — no image understanding.",
        ),
        source_country="미국", source_company="Liquid AI",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="text-generation", likes=3, license="apache-2.0",
            notes="library_name=mlx, ungated, 1 safetensors shard.", verified_by="hf-api-light",
        ),
        recommended_default=True,
        display_priority=5,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-e2b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-e2b-it-4bit",
        name="Gemma 4 E2B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="3.6GB",
        architecture="gemma4",
        download_size_gb=3.58,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=8.0, recommended_ram_gb=8.0, apple_silicon_pref=True,
            notes="Smallest model here that can still read pictures. Good first local VLM.",
        ),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=25, license="apache-2.0",
            notes="library_name=mlx, ungated, 1 safetensors shard.", verified_by="hf-api-light",
        ),
        recommended_default=True,
        display_priority=10,
    ),

    # ── Light tier (16GB RAM) ────────────────────────────────────────────────
    ModelCapability(
        id="mlx-community/gemma-4-e4b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-e4b-it-4bit",
        name="Gemma 4 E4B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="5.2GB",
        architecture="gemma4",
        download_size_gb=5.18,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=10.0, recommended_ram_gb=16.0, apple_silicon_pref=True,
            notes="Step up from E2B with the same tiny footprint discipline.",
        ),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=35, license="apache-2.0",
            notes="library_name=mlx, ungated, 1 safetensors shard.", verified_by="hf-api-light",
        ),
        display_priority=15,
    ),

    # ── Mid tier (24GB RAM) ──────────────────────────────────────────────────
    ModelCapability(
        id="mlx-community/gemma-4-12B-it-4bit",
        hf_repo_id="mlx-community/gemma-4-12B-it-4bit",
        name="Gemma 4 12B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="6.8GB",
        architecture="gemma4_unified",
        download_size_gb=6.77,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=12.0, recommended_ram_gb=16.0, apple_silicon_pref=True,
            notes="Sweet spot for local multimodal on M-series 16GB+. Uses the gemma4_unified MLX loader.",
        ),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=16, license="apache-2.0",
            notes="library_name=mlx, ungated, 2 safetensors shards. Canonical id capitalises the B.",
            verified_by="hf-api-light",
        ),
        recommended_default=True,
        display_priority=25,
    ),
    ModelCapability(
        id="mlx-community/Qwen3.5-9B-MLX-4bit",
        hf_repo_id="mlx-community/Qwen3.5-9B-MLX-4bit",
        name="Qwen3.5 9B",
        family="Qwen3.5",
        tag="local-vlm",
        size="6.0GB",
        architecture="qwen3_5",
        download_size_gb=5.98,
        quantization="4bit",
        provider_hints=["local_mlx", "vllm"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=12.0, recommended_ram_gb=16.0, apple_silicon_pref=True,
            notes="Mid-size vision-language model; replaces the retired Qwen2.5-VL / Llama-3.2-Vision slot.",
        ),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=153, license="apache-2.0",
            notes="library_name=mlx, ungated, 2 safetensors shards.", verified_by="hf-api-light",
        ),
        recommended_default=True,
        display_priority=20,
    ),

    # ── General-purpose ──────────────────────────────────────────────────────
    ModelCapability(
        id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        hf_repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        name="GPT-OSS 20B",
        family="GPT-OSS",
        tag="local-llm",
        size="12.1GB",
        modality="text",
        architecture="gpt_oss",
        download_size_gb=12.10,
        quantization="MXFP4-Q8",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_lm",
        hardware=HardwareProfile(
            min_ram_gb=18.0, recommended_ram_gb=24.0, apple_silicon_pref=True,
            notes="Most-downloaded entry in this catalog. Text only — no image understanding.",
        ),
        source_country="미국", source_company="OpenAI",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="text-generation", likes=84, license="apache-2.0",
            notes="library_name=mlx, ungated, 3 safetensors shards.", verified_by="hf-api-light",
        ),
        display_priority=30,
    ),

    # ── MoE tier (32GB+ RAM) ─────────────────────────────────────────────────
    ModelCapability(
        id="mlx-community/gemma-4-26b-a4b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-26b-a4b-it-4bit",
        name="Gemma 4 26B A4B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="15.4GB",
        architecture="gemma4",
        download_size_gb=15.37,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=22.0, recommended_ram_gb=32.0, apple_silicon_pref=True,
            notes="Mixture-of-experts; practical on 32GB+ Apple Silicon. Long first download.",
        ),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=80, license="apache-2.0",
            notes="library_name=mlx, ungated, 3 safetensors shards.", verified_by="hf-api-light",
        ),
        display_priority=50,
    ),
    ModelCapability(
        id="mlx-community/Qwen3.6-35B-A3B-4bit",
        hf_repo_id="mlx-community/Qwen3.6-35B-A3B-4bit",
        name="Qwen3.6 35B A3B",
        family="Qwen3.6",
        tag="local-vlm",
        size="20.4GB",
        architecture="qwen3_5_moe",
        download_size_gb=20.43,
        quantization="4bit",
        provider_hints=["local_mlx", "vllm"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=28.0, recommended_ram_gb=48.0, apple_silicon_pref=True,
            notes="Mixture-of-experts; replaces the retired Llama 4 Scout slot. Long first download.",
        ),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=94, license="apache-2.0",
            notes="library_name=mlx, ungated, 4 safetensors shards.", verified_by="hf-api-light",
        ),
        display_priority=65,
    ),

    # ── Large tier (48GB+ RAM) ───────────────────────────────────────────────
    ModelCapability(
        id="mlx-community/Qwen3.6-27B-4bit",
        hf_repo_id="mlx-community/Qwen3.6-27B-4bit",
        name="Qwen3.6 27B",
        family="Qwen3.6",
        tag="local-vlm",
        size="16.1GB",
        architecture="qwen3_5",
        download_size_gb=16.08,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=24.0, recommended_ram_gb=48.0, apple_silicon_pref=True,
            notes="Dense top tier — every parameter runs on every token, so it is slower but steadier than the MoE.",
        ),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=50, license="apache-2.0",
            notes="library_name=mlx, ungated, 3 safetensors shards.", verified_by="hf-api-light",
        ),
        display_priority=55,
    ),
    ModelCapability(
        id="mlx-community/gemma-4-31b-it-4bit",
        hf_repo_id="mlx-community/gemma-4-31b-it-4bit",
        name="Gemma 4 31B Instruct",
        family="Gemma 4",
        tag="local-vlm",
        size="18.4GB",
        architecture="gemma4",
        download_size_gb=18.44,
        quantization="4bit",
        provider_hints=["local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"],
        download_strategy="hf_hub",
        load_strategy="mlx_vlm",
        hardware=HardwareProfile(
            min_ram_gb=26.0, recommended_ram_gb=48.0, apple_silicon_pref=True,
            notes="Largest Gemma 4 here; high-end local only. Consider a cloud path on lower RAM.",
        ),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=46, license="apache-2.0",
            notes="library_name=mlx, ungated, 4 safetensors shards.", verified_by="hf-api-light",
        ),
        display_priority=60,
    ),
]


# ── Recognised-only entries (LEGACY) ──────────────────────────────────────────
# Superseded generations that are still on the Hub and still loadable. They are
# deliberately absent from ENGINE_MODEL_CATALOG, from the recommendation tiers
# and from every download path — their only job is to keep a model somebody
# already downloaded identifiable (name, family, size, runtime profile) instead
# of surfacing as an unknown blob. Verified on 2026-08-10 like the rest.

_LEGACY_REGISTRY: List[ModelCapability] = [
    ModelCapability(
        id="mlx-community/gemma-4-e2b-4bit",
        hf_repo_id="mlx-community/gemma-4-e2b-4bit",
        name="Gemma 4 E2B Base",
        family="Gemma 4",
        tag="local-vlm",
        size="3.6GB",
        architecture="gemma4",
        download_size_gb=3.61,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=8.0, apple_silicon_pref=True,
                                 notes="Base (not instruction-tuned) — the Instruct build answers chat far better."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="any-to-any", likes=3, license="apache-2.0",
            notes="Superseded by gemma-4-e2b-it-4bit for chat.", verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/gemma-4-e4b-4bit",
        hf_repo_id="mlx-community/gemma-4-e4b-4bit",
        name="Gemma 4 E4B Base",
        family="Gemma 4",
        tag="local-vlm",
        size="5.3GB",
        architecture="gemma4",
        download_size_gb=5.25,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=10.0, recommended_ram_gb=16.0, apple_silicon_pref=True,
                                 notes="Base (not instruction-tuned) — the Instruct build answers chat far better."),
        source_country="미국", source_company="Google",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="any-to-any", likes=6, license="apache-2.0",
            notes="Superseded by gemma-4-e4b-it-4bit for chat.", verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Qwen3-VL-4B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-4B-Instruct-4bit",
        name="Qwen3-VL 4B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="3.1GB",
        architecture="qwen3_vl",
        download_size_gb=3.11,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=5.0, recommended_ram_gb=8.0, apple_silicon_pref=True),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=7, license="apache-2.0",
            notes="2025-10 generation; superseded by Qwen3.5 / Qwen3.6.", verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
        name="Qwen3-VL 8B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="5.8GB",
        architecture="qwen3_vl",
        download_size_gb=5.78,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=12.0, apple_silicon_pref=True),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=6, license="apache-2.0",
            notes="2025-10 generation; superseded by Qwen3.5 9B.", verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit",
        name="Qwen3-VL 30B A3B",
        family="Qwen3-VL",
        tag="local-vlm",
        size="18.3GB",
        architecture="qwen3_vl_moe",
        download_size_gb=18.27,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=24.0, recommended_ram_gb=32.0, apple_silicon_pref=True),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=8, license="apache-2.0",
            notes="2025-10 generation; superseded by Qwen3.6 35B A3B.", verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        hf_repo_id="mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
        name="Qwen2.5-VL 7B",
        family="Qwen2.5-VL",
        tag="local-vlm",
        size="5.7GB",
        architecture="qwen2_5_vl",
        download_size_gb=5.65,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=8.0, recommended_ram_gb=12.0, apple_silicon_pref=True),
        source_country="중국", source_company="Alibaba",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=4, license="apache-2.0",
            notes="2025-02 generation, library_name=transformers; superseded by Qwen3.5 9B.",
            verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Llama-3.2-11B-Vision-Instruct-4bit",
        hf_repo_id="mlx-community/Llama-3.2-11B-Vision-Instruct-4bit",
        name="Llama 3.2 11B Vision Instruct",
        family="Llama 3.2 Vision",
        tag="local-vlm",
        size="6.0GB",
        architecture="mllama",
        download_size_gb=6.02,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(min_ram_gb=10.0, recommended_ram_gb=14.0, apple_silicon_pref=True),
        source_country="미국", source_company="Meta",
        license="llama3.2",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=7, license="llama3.2",
            notes="2024-10 generation, library_name=transformers; superseded by Qwen3.5 9B.",
            verified_by="hf-api-light",
        ),
    ),
    ModelCapability(
        id="mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        hf_repo_id="mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        name="Llama 4 Scout 17B 16E",
        family="Llama 4",
        tag="local-vlm",
        size="61.1GB",
        architecture="llama4",
        download_size_gb=61.14,
        lifecycle=LEGACY,
        quantization="4bit",
        hardware=HardwareProfile(
            min_ram_gb=72.0, recommended_ram_gb=96.0, apple_silicon_pref=True,
            notes="61GB on disk despite the \"4bit\" name — 12 shards. The registry claimed 11.8GB "
                  "until it was measured; only a very high-RAM machine can load it.",
        ),
        source_country="미국", source_company="Meta",
        license="llama4",
        verification=VerificationStatus(
            hf_exists=True, hf_last_checked="2026-08-10", has_config=True, has_tokenizer=True,
            has_weights_hint=True, pipeline_tag="image-text-to-text", likes=12, license="llama4",
            notes="2025-05 generation, library_name=transformers; the MoE slot is now Qwen3.6 35B A3B. "
                  "Meta's own repo is gated, so this port is the only anonymous route.",
            verified_by="hf-api-light",
        ),
    ),
]


def get_all_capabilities() -> List[ModelCapability]:
    """Every entry the registry knows — recommended *and* recognised-only.

    This is the verification surface: ``scripts/verify_hf_model_registry.py``
    re-measures all of it, because a legacy entry that quietly disappeared from
    the Hub should be reported rather than kept as a comforting fiction.
    """
    return [*_REGISTRY, *_LEGACY_REGISTRY]


def get_recommended_capabilities() -> List[ModelCapability]:
    """Current-generation entries: catalog, download and recommendation input."""
    return list(_REGISTRY)


def get_legacy_capabilities() -> List[ModelCapability]:
    """Recognised-only entries: never offered, never recommended, still named."""
    return list(_LEGACY_REGISTRY)


def get_capability(model_id: str) -> Optional[ModelCapability]:
    """Look an id up across both lists — recognition covers legacy weights too."""
    for m in get_all_capabilities():
        if m.id == model_id or m.hf_repo_id == model_id:
            return m
    return None


def is_recognized_model(model_id: str) -> bool:
    """True when the registry can name this model, whatever its lifecycle.

    Used by the load path: a model already on disk must keep working even after
    its generation stops being offered.
    """
    return get_capability(model_id) is not None


def is_recommended_model(model_id: str) -> bool:
    """True only for current-generation entries — the download/offer gate."""
    cap = get_capability(model_id)
    return cap is not None and cap.lifecycle == RECOMMENDED


def build_engine_model_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return legacy ENGINE_MODEL_CATALOG shape, enriched with rich fields.

    Built from the **recommended** list only: the catalog is what the product
    offers, and offering a superseded generation is how users end up downloading
    something we would not stand behind.
    """
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
                # Adapt id for non-mlx engines. These are provisional prefixed
                # ids; `model_catalog._normalize_engine_entry` then replaces them
                # with the verified per-engine repo from MODEL_ENGINE_ALIASES.
                # (Until 11.2.0 the ollama branch *fabricated* a repo path from
                # the family name — `ggml-org/<family>-12B-it-GGUF` — which
                # resolved to a 404 for anything but Gemma 4 12B.)
                if eng_key == "ollama" and not legacy["id"].startswith("ollama:"):
                    legacy["id"] = f"ollama:{cap.hf_repo_id.split('/')[-1].lower()}"
                elif eng_key == "vllm" and not legacy["id"].startswith("vllm:"):
                    legacy["id"] = f"vllm:{cap.hf_repo_id}"
                elif eng_key == "lmstudio" and not legacy["id"].startswith("lmstudio:"):
                    legacy["id"] = f"lmstudio:{cap.hf_repo_id}"
                elif eng_key == "llamacpp" and not legacy["id"].startswith("llamacpp:"):
                    legacy["id"] = f"llamacpp:{cap.hf_repo_id}-GGUF"
                by_engine[eng_key].append(legacy)

    return dict(by_engine)


def get_verified_models() -> List[Dict[str, Any]]:
    """Return only load-verified recommended entries with rich fields (API/UI)."""
    return [
        c.to_legacy_dict() for c in _REGISTRY
        if c.verification.hf_exists and c.verification.has_config and c.verification.has_tokenizer
    ]


# Back-compat: expose a simple list mirroring the old top-level for mlx.
# Recommended entries only — same reasoning as build_engine_model_catalog.
LOCAL_MLX_MODELS = [c.to_legacy_dict() for c in _REGISTRY if "local_mlx" in c.provider_hints]
