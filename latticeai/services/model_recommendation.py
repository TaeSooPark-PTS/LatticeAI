"""Hardware-aware local model recommendation.

Given a detected system profile (from :func:`auto_setup.probe`) this module
classifies every model in :data:`model_catalog.ENGINE_MODEL_CATALOG` into one of
three states — **recommended**, **compatible**, or **not_recommended** — and
groups the result by current model family (Gemma 4, Qwen3.6, Qwen3.5, GPT-OSS,
LFM2.5).

It is intentionally pure and dependency-light: the only input is a plain dict
describing the machine, so it is fully unit-testable without touching real
hardware, and it does not import the FastAPI app or the runtime.  The setup /
onboarding routers build the profile via ``auto_setup.probe().to_json()`` and
hand it here.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from latticeai.core.model_compat import model_runtime_compatibility
from latticeai.services.model_catalog import ENGINE_MODEL_CATALOG

# ── status vocabulary ─────────────────────────────────────────────────────────
RECOMMENDED = "recommended"
COMPATIBLE = "compatible"
NOT_RECOMMENDED = "not_recommended"

# Engines whose models load on any OS (given the engine binary) vs. MLX which is
# Apple-Silicon only.  Used to decide platform availability before sizing.
_APPLE_ONLY_ENGINES = {"local_mlx"}

# Family display order for the grouped view (current generations, largest
# lineup first). Superseded families are not listed because the capability
# registry never lets them reach the catalog — they are recognised for loading
# only. A family missing from this list still renders, it just sorts last.
_FAMILY_ORDER = [
    "Gemma 4",
    "Qwen3.6",
    "Qwen3.5",
    "GPT-OSS",
    "LFM2.5",
]

# Modalities the recommender will surface. Text-only models earn their place —
# LFM2.5 is the only thing that runs comfortably on 8GB, and GPT-OSS 20B is the
# most-downloaded entry in the catalog — so filtering to `multimodal` would have
# hidden two of the tiers. Each row still reports its own `modality`, so a UI
# that wants to badge "reads pictures" can, honestly.
_RECOMMENDABLE_MODALITIES = {"multimodal", "text"}

_SIZE_RE = re.compile(r"([\d.]+)\s*(TB|GB|MB)", re.IGNORECASE)
_UNIT_GB = {"TB": 1024.0, "GB": 1.0, "MB": 1.0 / 1024.0}


def parse_size_gb(size: Any) -> Optional[float]:
    """Parse a catalog ``size`` string (``"4.7GB"``, ``"963MB"``, ``"40GB+"``).

    Returns ``None`` when the size is non-numeric (e.g. ``"pull required"`` or
    ``"실행 도구에서 관리"``) so callers can treat it as "size unknown".
    """
    if not isinstance(size, str):
        return None
    match = _SIZE_RE.search(size)
    if not match:
        return None
    value = float(match.group(1))
    return round(value * _UNIT_GB[match.group(2).upper()], 3)


def estimated_ram_gb(size_gb: float) -> float:
    """Rough RAM needed to run a model: weights + KV cache + OS working set."""
    return round(size_gb * 1.25 + 2.5, 2)


def is_apple_silicon(profile: Dict[str, Any]) -> bool:
    os_name = str(profile.get("os") or "").lower()
    arch = str(profile.get("arch") or "").lower()
    gpu = profile.get("gpu") or {}
    vendor = str(gpu.get("vendor") or "").lower()
    return os_name == "darwin" and (vendor == "apple" or arch in {"arm64", "aarch64"})


def _ram_gb(profile: Dict[str, Any]) -> float:
    try:
        return max(0.0, float(profile.get("ram_mb") or 0) / 1024.0)
    except (TypeError, ValueError):
        return 0.0


def _engine_available(engine: str, profile: Dict[str, Any]) -> bool:
    if engine in _APPLE_ONLY_ENGINES:
        return is_apple_silicon(profile)
    # ollama / llamacpp / lmstudio / vllm run cross-platform once installed.
    return True


def _classify_one(
    model: Dict[str, Any],
    *,
    engine: str,
    engine_available: bool,
    ram_gb: float,
) -> Dict[str, Any]:
    size_gb = parse_size_gb(model.get("size"))
    need_gb = estimated_ram_gb(size_gb) if size_gb is not None else None
    runtime = model_runtime_compatibility(str(model.get("id") or ""), engine=engine)

    if not engine_available:
        status, reason = NOT_RECOMMENDED, "Apple Silicon과 MLX-VLM이 필요합니다"
    elif runtime.get("supported") is False:
        status = NOT_RECOMMENDED
        reason = str(runtime.get("user_message") or "이 모델은 현재 설치된 실행 런타임에서 지원되지 않습니다")
    elif need_gb is None:
        # Tool-managed/pull models have no fixed on-disk size, so treat them as
        # compatible and let the execution tool validate the exact model.
        status, reason = COMPATIBLE, "선택한 실행 방식에서 필요할 때 모델을 받습니다"
    elif ram_gb <= 0:
        status, reason = COMPATIBLE, "메모리 정보를 확인하지 못했습니다. 불러오기 전에 검증합니다"
    elif need_gb <= ram_gb * 0.75:
        status, reason = RECOMMENDED, f"현재 메모리에서 안정적으로 사용할 가능성이 높습니다 (~{need_gb:.0f} GB / {ram_gb:.0f} GB)"
    elif need_gb <= ram_gb * 0.9:
        status, reason = COMPATIBLE, f"사용 가능하지만 여유가 적습니다 (~{need_gb:.0f} GB / {ram_gb:.0f} GB)"
    else:
        status, reason = NOT_RECOMMENDED, f"권장 메모리가 부족합니다 (~{need_gb:.0f} GB 필요, 현재 {ram_gb:.0f} GB)"

    rich = {
        "id": model.get("id"),
        "name": model.get("name"),
        "model_name": model.get("model_name") or model.get("name"),
        "family": model.get("family"),
        "tag": model.get("tag"),
        "modality": model.get("modality") or "multimodal",
        "size": model.get("size"),
        "size_gb": size_gb,
        "required_ram_gb": need_gb,
        "status": status,
        "reason": reason,
        "source_country": model.get("source_country"),
        "source_company": model.get("source_company"),
        "execution_method": model.get("execution_method"),
        "run_location": model.get("run_location"),
        "internet_requirement": model.get("internet_requirement"),
        "source_display_order": model.get("source_display_order"),
        "runtime_compatibility": runtime,
        # 5.2+ user-focused transparency
        "hf_repo_id": model.get("hf_repo_id"),
        "quantization": model.get("quantization"),
        "download_strategy": model.get("download_strategy"),
        "load_strategy": model.get("load_strategy"),
        "hardware": model.get("hardware"),
        "license": model.get("license"),
        "safety_notes": model.get("safety_notes"),
        "verification": model.get("verification"),
        "recommended_default": model.get("recommended_default", False),
    }
    return rich


def _family_rank(family: str) -> int:
    try:
        return _FAMILY_ORDER.index(family)
    except ValueError:
        return len(_FAMILY_ORDER)


def recommend_catalog(profile: Dict[str, Any], *, engine: str = "local_mlx") -> Dict[str, Any]:
    """Classify ``engine``'s catalog for the given machine ``profile``.

    ``profile`` is a dict shaped like ``auto_setup.SystemProfile.to_json()``
    (``os``, ``arch``, ``ram_mb``, ``gpu={vendor,vram_mb}`` …).
    """
    models = [
        model for model in ENGINE_MODEL_CATALOG.get(engine, [])
        if str(model.get("modality") or "").lower() in _RECOMMENDABLE_MODALITIES
    ]
    engine_available = _engine_available(engine, profile)
    ram_gb = _ram_gb(profile)

    classified = [
        _classify_one(m, engine=engine, engine_available=engine_available, ram_gb=ram_gb)
        for m in models
    ]

    counts = {RECOMMENDED: 0, COMPATIBLE: 0, NOT_RECOMMENDED: 0}
    for item in classified:
        counts[item["status"]] += 1

    # Group by family, ordered, with the best pick per family surfaced.
    by_family: Dict[str, Dict[str, Any]] = {}
    for item in classified:
        fam = item["family"] or "Other"
        bucket = by_family.setdefault(fam, {"family": fam, "models": [], "best": None})
        bucket["models"].append(item)

    def _best(models: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        # Prefer recommended, then compatible; within a tier prefer the largest
        # model that still fits (more capable).
        for tier in (RECOMMENDED, COMPATIBLE):
            tier_models = [m for m in models if m["status"] == tier]
            if tier_models:
                return max(tier_models, key=lambda m: m["size_gb"] or 0.0)
        return None

    families = []
    for fam in sorted(by_family, key=_family_rank):
        bucket = by_family[fam]
        bucket["best"] = _best(bucket["models"])
        families.append(bucket)

    # Overall top pick: the largest recommended model on this machine.
    recommended_models = [m for m in classified if m["status"] == RECOMMENDED]
    top_pick = max(recommended_models, key=lambda m: m["size_gb"] or 0.0) if recommended_models else None

    return {
        "engine": engine,
        "engine_available": engine_available,
        "apple_silicon": is_apple_silicon(profile),
        "ram_gb": round(ram_gb, 1),
        "counts": counts,
        "top_pick": top_pick,
        "families": families,
        "models": classified,
    }
