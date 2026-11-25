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
    hf_last_checked: Optional[str] = None
    has_config: bool = False
    has_tokenizer: bool = False
    has_weights_hint: bool = False
    pipeline_tag: Optional[str] = None
    likes: Optional[int] = None
    license: Optional[str] = None
    notes: str = ""
    verified_by: str = "hf-api-light"


RECOMMENDED = "recommended"
LEGACY = "legacy"


@dataclass(frozen=True)
class ModelCapability:
    id: str
    hf_repo_id: str
    name: str
    family: str
    tag: str
    size: str
    modality: str = "multimodal"
    architecture: str = ""
    download_size_gb: Optional[float] = None
    lifecycle: str = RECOMMENDED
    quantization: Optional[str] = None
    provider_hints: List[str] = field(default_factory=lambda: ["local_mlx"])
    download_strategy: str = "hf_hub"
    load_strategy: str = "mlx_vlm"
    hardware: HardwareProfile = field(default_factory=HardwareProfile)
    license: str = "apache-2.0"
    safety_notes: str = (
        "Standard open weights; review license and responsible use guidelines."
    )
    source_country: str = ""
    source_company: str = ""
    execution_method: str = "내 컴퓨터에서만 실행"
    internet_requirement: str = (
        "모델을 다운로드할 때만 인터넷 필요; 실행 중에는 필요 없음"
    )
    verification: VerificationStatus = field(default_factory=VerificationStatus)
    recommended_default: bool = False
    display_priority: int = 100
    category: str = "chat"
    task: str = "chat"
    dimensions: Optional[int] = None

    def to_legacy_dict(self) -> Dict[str, Any]:
        base = {
            "id": self.id,
            "name": self.name,
            "model_name": self.name.split(" via ")[0]
            if " via " in self.name
            else self.name,
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
                "source_country",
                "source_company",
                "execution_method",
                "internet_requirement",
                "model_name",
            ],
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
