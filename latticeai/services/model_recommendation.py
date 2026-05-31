"""Hardware-aware local model recommendation.

Given a detected system profile (from :func:`auto_setup.probe`) this module
classifies every model in :data:`model_catalog.ENGINE_MODEL_CATALOG` into one of
three states — **recommended**, **compatible**, or **not_recommended** — and
groups the result by model family (Gemma, Qwen, Llama, Phi, DeepSeek, …).

It is intentionally pure and dependency-light: the only input is a plain dict
describing the machine, so it is fully unit-testable without touching real
hardware, and it does not import the FastAPI app or the runtime.  The setup /
onboarding routers build the profile via ``auto_setup.probe().to_json()`` and
hand it here.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from latticeai.services.model_catalog import ENGINE_MODEL_CATALOG

# ── status vocabulary ─────────────────────────────────────────────────────────
RECOMMENDED = "recommended"
COMPATIBLE = "compatible"
NOT_RECOMMENDED = "not_recommended"

# Engines whose models load on any OS (given the engine binary) vs. MLX which is
# Apple-Silicon only.  Used to decide platform availability before sizing.
_APPLE_ONLY_ENGINES = {"local_mlx"}

# Family display order for the grouped view (best/newest first within a brand).
_FAMILY_ORDER = [
    "Gemma 4", "Gemma 3", "Gemma 2", "Gemma",
    "Qwen3-VL", "Qwen2.5-VL", "Qwen2.5", "Qwen",
    "Llama 3.x", "Llama 3.1", "Llama",
    "Mistral", "Phi", "GPT-OSS", "DeepSeek", "SmolLM",
]

_SIZE_RE = re.compile(r"([\d.]+)\s*(TB|GB|MB)", re.IGNORECASE)
_UNIT_GB = {"TB": 1024.0, "GB": 1.0, "MB": 1.0 / 1024.0}


def parse_size_gb(size: Any) -> Optional[float]:
    """Parse a catalog ``size`` string (``"4.7GB"``, ``"963MB"``, ``"40GB+"``).

    Returns ``None`` when the size is non-numeric (e.g. ``"pull required"`` or
    ``"server model"``) so callers can treat it as "size unknown".
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
    engine_available: bool,
    ram_gb: float,
) -> Dict[str, Any]:
    size_gb = parse_size_gb(model.get("size"))
    need_gb = estimated_ram_gb(size_gb) if size_gb is not None else None

    if not engine_available:
        status, reason = NOT_RECOMMENDED, "Requires Apple Silicon (MLX runtime)"
    elif need_gb is None:
        # Server/pull models have no fixed on-disk size — treat as compatible
        # (the engine streams/pulls weights on demand).
        status, reason = COMPATIBLE, "Served/pulled on demand by the engine"
    elif ram_gb <= 0:
        status, reason = COMPATIBLE, "Memory unknown — verify before loading"
    elif need_gb <= ram_gb * 0.6:
        status, reason = RECOMMENDED, f"Fits comfortably (~{need_gb:.0f} GB of {ram_gb:.0f} GB RAM)"
    elif need_gb <= ram_gb * 0.9:
        status, reason = COMPATIBLE, f"Runs but tight (~{need_gb:.0f} GB of {ram_gb:.0f} GB RAM)"
    else:
        status, reason = NOT_RECOMMENDED, f"Needs ~{need_gb:.0f} GB RAM (have {ram_gb:.0f} GB)"

    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "family": model.get("family"),
        "tag": model.get("tag"),
        "size": model.get("size"),
        "size_gb": size_gb,
        "required_ram_gb": need_gb,
        "status": status,
        "reason": reason,
    }


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
    models = ENGINE_MODEL_CATALOG.get(engine, [])
    engine_available = _engine_available(engine, profile)
    ram_gb = _ram_gb(profile)

    classified = [
        _classify_one(m, engine_available=engine_available, ram_gb=ram_gb)
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
