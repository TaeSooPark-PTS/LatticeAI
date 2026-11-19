"""Dataclasses and lifecycle constants for the Model Capability Registry."""

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
    category: str = "chat"
    task: str = "chat"
    dimensions: Optional[int] = None

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
            "category": self.category,
            "task": self.task,
            "role": "embedding" if self.category == "embedding" else "chat",
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
        if self.dimensions is not None:
            base["dimensions"] = self.dimensions
        return base


