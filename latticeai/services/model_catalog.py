"""Static local-model catalog, engine installers, and family-version filtering.

5.2.0: Now sources the rich ENGINE_MODEL_CATALOG from the structured
model_capability_registry (single source of truth with HF verification,
download/load strategy, hardware, license, modality). Legacy flat shapes
preserved exactly for all downstream (recommendation, api, runtime, frontend).

The old inline _model() + ENGINE_MODEL_CATALOG data has been moved into
latticeai/services/model_capability_registry.py (see there for full 5.2 fields).
"""

from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional

# 5.2.0: Delegate catalog data to the structured capability registry (rich + verified).
# This keeps backward compat for every `from ...model_catalog import ENGINE_MODEL_CATALOG`.
from latticeai.services.model_capability_registry import (
    build_engine_model_catalog as _build_engine_model_catalog,
)
from latticeai.services.model_capability_registry import (
    get_all_capabilities as _get_all_capabilities,
)
from latticeai.services.model_capability_registry import (
    get_capability as _get_capability,
)
from latticeai.services.model_capability_registry import (
    get_embedding_capabilities as _get_embedding_capabilities,
)
from latticeai.services.model_capability_registry import (
    get_verified_models as _get_verified_models,
)
from latticeai.services.model_capability_registry import (
    recommend_embedding_for_hardware as _recommend_embedding_for_hardware,
)

ENGINE_INSTALLERS: Dict[str, Dict[str, Any]] = {
    "local_mlx": {
        "command": [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-vlm>=0.6.3", "mlx-lm", "huggingface_hub[cli]"],
        "label": "Install MLX runtime",
    },
    "openai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "openrouter": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "groq": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "together": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "xai": {
        "command": [sys.executable, "-m", "pip", "install", "openai"],
        "label": "Install OpenAI-compatible SDK",
    },
    "ollama": {
        "command": ["brew", "install", "ollama"],
        "label": "Install Ollama",
        "requires_binary": "brew",
    },
    "vllm": {
        "command": [sys.executable, "-m", "pip", "install", "vllm", "huggingface_hub[cli]"],
        "label": "Install vLLM runtime",
    },
    "lmstudio": {
        "command": ["brew", "install", "--cask", "lm-studio"],
        "label": "Install LM Studio",
        "requires_binary": "brew",
    },
    "llamacpp": {
        "command": ["brew", "install", "llama.cpp"],
        "label": "Install llama.cpp",
        "requires_binary": "brew",
    },
}

# 5.2.0 delegation: the rich catalog (with verification, hf_repo_id, strategies, hardware, license etc)
# is defined in model_capability_registry. We build the legacy-shaped ENGINE_MODEL_CATALOG here
# at import time so every existing consumer (runtime, api, recommendation, tests) is unaffected.
#
# The *raw* registry projection keeps every capability (incl. legacy generations
# like Gemma 3 / Qwen2.5-VL / Pixtral) for transparency + HF verification. The
# user-facing ENGINE_MODEL_CATALOG below is then narrowed to the aggressive 5.2.0
# policy (latest family generations only, no text-only/legacy weights) and the
# engine-specific ids/sizes are normalised. See `_finalize_engine_catalog`.
_RAW_ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = _build_engine_model_catalog()
# Filled in at module end once the blocklist, alias map and family-version filter
# are all defined; declared here so the public name exists for static readers.
ENGINE_MODEL_CATALOG: Dict[str, List[Dict[str, Any]]] = {}

# Per-engine repo ids for the recommended catalog. Every repo below was resolved
# through the HF API on 2026-08-10 and is stored in the Hub's canonical casing
# (`google/gemma-4-12b-it` answers as `google/gemma-4-12B-it`; `ggml-org/
# gemma-4-26b-a4b-it-GGUF` answers as `ggml-org/gemma-4-26B-A4B-it-GGUF`).
# Lookups are lowercase, so each model is keyed by both its short name and its
# full mlx repo id — see `_normalize_engine_entry` and `ModelResolution`.
#
# Removed in 11.2.0: `qwen3-vl-8b` and `llama-4-scout`. Their vllm/lmstudio
# targets pointed at `meta-llama/Llama-4-Scout-17B-16E-Instruct`, which is
# gated — the download could never succeed without Hub credentials.


def _gguf_routes(
    mlx_repo: str,
    gguf_repo: str,
    hf_repo: str,
    *,
    ollama_tag: str = "Q4_K_M",
) -> Dict[str, str]:
    """One model's engine map: MLX weights, a GGUF repo, and the upstream HF repo."""
    return {
        "local_mlx": mlx_repo,
        "ollama": f"hf.co/{gguf_repo}:{ollama_tag}",
        "vllm": hf_repo,
        "lmstudio": gguf_repo,
        "llamacpp": gguf_repo,
    }


_ENGINE_ROUTES: Dict[str, Dict[str, str]] = {
    "mlx-community/LFM2.5-2.6B-4bit": _gguf_routes(
        "mlx-community/LFM2.5-2.6B-4bit", "LiquidAI/LFM2.5-2.6B-GGUF", "LiquidAI/LFM2.5-2.6B",
    ),
    "mlx-community/gemma-4-e2b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-e2b-it-4bit", "ggml-org/gemma-4-E2B-it-GGUF", "google/gemma-4-E2B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-e4b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-e4b-it-4bit", "ggml-org/gemma-4-E4B-it-GGUF", "google/gemma-4-E4B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-12B-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-12B-it-4bit", "ggml-org/gemma-4-12B-it-GGUF", "google/gemma-4-12B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26B-A4B-it-qat-4bit": _gguf_routes(
        "mlx-community/gemma-4-26B-A4B-it-qat-4bit", "google/gemma-4-26B-A4B-it-qat-q4_0-gguf",
        "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26b-a4b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-26b-a4b-it-4bit", "ggml-org/gemma-4-26B-A4B-it-GGUF",
        "google/gemma-4-26B-A4B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit": _gguf_routes(
        "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit", "unsloth/gemma-4-26B-A4B-it-GGUF",
        "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized",
        ollama_tag="Q4_0",
    ),
    "mlx-community/gemma-4-31b-it-4bit": _gguf_routes(
        "mlx-community/gemma-4-31b-it-4bit", "ggml-org/gemma-4-31B-it-GGUF", "google/gemma-4-31B-it",
        ollama_tag="Q4_0",
    ),
    "mlx-community/Qwen3.8-27B-4bit": _gguf_routes(
        "mlx-community/Qwen3.8-27B-4bit", "ggml-org/Qwen3.8-27B-GGUF", "Qwen/Qwen3.8-27B",
    ),
    "mlx-community/Qwen3.6-27B-4bit": _gguf_routes(
        "mlx-community/Qwen3.6-27B-4bit", "ggml-org/Qwen3.6-27B-GGUF", "Qwen/Qwen3.6-27B",
    ),
    "mlx-community/gpt-oss-20b-MXFP4-Q8": _gguf_routes(
        "mlx-community/gpt-oss-20b-MXFP4-Q8", "ggml-org/gpt-oss-20b-GGUF", "openai/gpt-oss-20b",
        ollama_tag="MXFP4",
    ),
    "mlx-community/Qwen3.5-9B-MLX-4bit": _gguf_routes(
        "mlx-community/Qwen3.5-9B-MLX-4bit", "unsloth/Qwen3.5-9B-GGUF", "Qwen/Qwen3.5-9B",
    ),
    "mlx-community/Qwen3.6-35B-A3B-4bit": _gguf_routes(
        "mlx-community/Qwen3.6-35B-A3B-4bit", "ggml-org/Qwen3.6-35B-A3B-GGUF", "Qwen/Qwen3.6-35B-A3B",
    ),
    "google/embeddinggemma-2": _gguf_routes(
        "google/embeddinggemma-2", "ggml-org/embeddinggemma-2-GGUF", "google/embeddinggemma-2",
    ),
    "mlx-community/embeddinggemma-2-4bit": _gguf_routes(
        "mlx-community/embeddinggemma-2-4bit", "mlx-community/embeddinggemma-2-4bit-GGUF", "mlx-community/embeddinggemma-2-4bit",
    ),
    "google/embeddinggemma-300m": _gguf_routes(
        "google/embeddinggemma-300m", "ggml-org/embeddinggemma-300m-GGUF", "google/embeddinggemma-300m",
    ),
    "mlx-community/embeddinggemma-300m-4bit": _gguf_routes(
        "mlx-community/embeddinggemma-300m-4bit", "mlx-community/embeddinggemma-300m-4bit-GGUF", "mlx-community/embeddinggemma-300m-4bit",
    ),
}

# Names people actually type that are not the repo's own short name. Kept
# because a user who asks for "gemma-4-26b-it-4bit" means the A4B build — there
# is no other Gemma 4 26B — and resolving that to nothing would be pedantry.
_COMMON_MISNAMES: Dict[str, str] = {
    "gemma-4-26b-it-4bit": "mlx-community/gemma-4-26b-a4b-it-4bit",
    "gemma-4-26b-qat-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
    "gemma-4-26b-a4b-qat-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-4bit",
    "gemma-4-26b-optiq-4bit": "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit",
    "qwen-3.8-27b-4bit": "mlx-community/Qwen3.8-27B-4bit",
    "qwen3.8-27b": "mlx-community/Qwen3.8-27B-4bit",
    "embeddinggemma2": "google/embeddinggemma-2",
    "embeddinggemma-2-hf": "google/embeddinggemma-2",
}

# Keyed by both the lowercase short name ("gemma-4-12b-it-4bit") and the
# lowercase full repo id, because callers reach this map from both directions.
MODEL_ENGINE_ALIASES: Dict[str, Dict[str, str]] = {
    **{
        key: routes
        for repo, routes in _ENGINE_ROUTES.items()
        for key in (repo.split("/")[-1].lower(), repo.lower())
    },
    **{alias: _ENGINE_ROUTES[repo] for alias, repo in _COMMON_MISNAMES.items()},
}

# Also expose registry helpers directly from here for consumers who want the rich objects
get_all_capabilities = _get_all_capabilities
get_capability = _get_capability
get_embedding_capabilities = _get_embedding_capabilities
get_verified_models = _get_verified_models
recommend_embedding_for_hardware = _recommend_embedding_for_hardware

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _model_family_version(model: Dict[str, Any]) -> Optional[tuple[str, tuple[int, ...]]]:
    text = " ".join(str(model.get(key) or "") for key in ("family", "name", "id"))
    if "embeddinggemma" in text.lower() or model.get("category") == "embedding" or model.get("task") == "embedding":
        return None
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            # Every pattern captures at least one decimal digit and no other
            # character but ``.``, so the parsed tuple is never empty — the
            # old ``if version:`` guard could not fire and is gone.
            #
            # Compared at *major* granularity only (``[:1]``). The filter exists
            # to hide superseded generations — Qwen 2.5 behind Qwen 3, Gemma 3
            # behind Gemma 4 — not to pick a winner inside one generation.
            # Comparing minors made Qwen3.5 9B (the mid VLM) vanish the moment
            # Qwen3.6 27B joined the catalog, even though they fill different
            # RAM tiers and ship side by side.
            return family, _version_tuple(match.group(1))[:1]
    return None


def filter_lower_family_versions(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    max_versions: Dict[str, tuple[int, ...]] = {}
    detected: List[tuple[Dict[str, Any], Optional[tuple[str, tuple[int, ...]]]]] = []
    for model in models:
        version_info = _model_family_version(model)
        detected.append((model, version_info))
        if not version_info:
            continue
        family, version = version_info
        if version > max_versions.get(family, (0,)):
            max_versions[family] = version
    return [
        model for model, version_info in detected
        if not version_info or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]


# ── User-facing catalog assembly ──────────────────────────────────────────────
# The capability registry already keeps superseded generations out of the
# catalog by lifecycle (they live in its LEGACY list, recognised but never
# offered). This blocklist is the second lock: a regression guard so a retired
# generation cannot creep back into the model picker through a hand-edited
# entry. Anything whose id contains one of these fragments is dropped from
# ENGINE_MODEL_CATALOG.
#
# 11.2.0 removed ``gpt-oss`` from this list — GPT-OSS 20B is now a recommended
# entry, not a retired one — and added the generations retired this release.
_BLOCKED_CATALOG_FRAGMENTS = (
    "gemma-3", "gemma3", "gemma-2", "gemma2",
    "qwen2.5", "qwen-2.5", "qwen2-5",
    "qwen3-vl", "qwen3vl",
    "llama-3", "llama3.2", "llama-3.2",
    "llama-4", "llama4",
    "pixtral", "mistral",
    "smollm", "phi-", "moondream",
)


def _is_blocked_catalog_id(model: Dict[str, Any]) -> bool:
    ident = str(model.get("id") or "").lower()
    if "embeddinggemma" in ident:
        return False
    return any(fragment in ident for fragment in _BLOCKED_CATALOG_FRAGMENTS)


def _normalize_engine_entry(engine: str, model: Dict[str, Any]) -> Dict[str, Any]:
    """Apply historical engine-specific id + size conventions to a raw entry.

    * Non-MLX engines resolve to their canonical packaged id via
      :data:`MODEL_ENGINE_ALIASES` (e.g. ollama → ``hf.co/ggml-org/...GGUF``).
    * Server / tool-managed engines advertise no fixed on-disk size, so the
      execution tool validates the exact weights at pull time.
    """
    if engine == "local_mlx":
        return model
    entry = dict(model)
    hf_repo = str(entry.get("hf_repo_id") or "")
    short = hf_repo.split("/")[-1].lower()
    aliases = MODEL_ENGINE_ALIASES.get(short) or MODEL_ENGINE_ALIASES.get(hf_repo.lower())
    mapped = aliases.get(engine) if aliases else None
    if mapped:
        entry["id"] = f"{engine}:{mapped}"
    # Tool-managed engines (ollama/vllm/lmstudio/llamacpp) pull on demand; the
    # registry's MLX on-disk size does not apply to them.
    entry["size"] = "실행 도구에서 관리"
    return entry


def _finalize_engine_catalog(
    raw: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    final: Dict[str, List[Dict[str, Any]]] = {}
    for engine, models in raw.items():
        kept = [
            _normalize_engine_entry(engine, m)
            for m in models
            if not _is_blocked_catalog_id(m)
        ]
        final[engine] = filter_lower_family_versions(kept)
    return final


ENGINE_MODEL_CATALOG = _finalize_engine_catalog(_RAW_ENGINE_MODEL_CATALOG)


def recommend_models_for_pc(
    *,
    ram_gb: float = 16.0,
    apple_silicon: bool = True,
    cuda: bool = False,
    engine: str = "local_mlx",
) -> Dict[str, Any]:
    """Compute PC-tailored model recommendations including EmbeddingGemma 2.

    Evaluates the system RAM/VRAM budget while accounting for the embedding
    model's memory footprint (``google/embeddinggemma-2`` uses ~0.5 GB in 4-bit
    MLX / ~1.5 GB in FP16 HF; ``google/embeddinggemma-300m`` uses ~0.3 GB) so
    the recommended LLM + embedding model setup fits comfortably on the host.
    """
    embedding_rec = recommend_embedding_for_hardware(
        ram_gb=ram_gb,
        apple_silicon=apple_silicon,
        cuda=cuda,
    )
    embedding_ram_gb = float(embedding_rec.get("memory_footprint_gb") or 0.5)
    effective_llm_ram_gb = max(0.0, float(ram_gb) - embedding_ram_gb)

    catalog_rows = list(ENGINE_MODEL_CATALOG.get(engine) or ENGINE_MODEL_CATALOG.get("local_mlx") or [])
    models_out: List[Dict[str, Any]] = []
    chat_candidates: List[Dict[str, Any]] = []

    for row in catalog_rows:
        entry = dict(row)
        category = str(entry.get("category") or "chat")
        min_ram = float(entry.get("min_ram_gb") or 4.0)
        rec_ram = float(entry.get("recommended_ram_gb") or max(min_ram, 8.0))
        dl_gb = float(entry.get("download_size_gb") or 0.0)
        if category == "embedding":
            fits = float(ram_gb) + 0.01 >= min_ram
            comfortable = float(ram_gb) + 0.01 >= rec_ram
            status = "recommended" if comfortable else ("compatible" if fits else "not_recommended")
            entry["compatibility"] = {
                "status": status,
                "reason": embedding_rec.get("reason") if status != "not_recommended" else f"최소 {min_ram:.0f}GB RAM이 필요합니다.",
                "min_ram_gb": min_ram,
                "recommended_ram_gb": rec_ram,
            }
            models_out.append(entry)
            continue

        # Account for the embedding model footprint when evaluating LLM fit.
        exceeds_combined = dl_gb > 0.0 and (dl_gb + embedding_ram_gb) > float(ram_gb)
        if exceeds_combined or effective_llm_ram_gb + 0.01 < min_ram:
            status = "not_recommended"
            reason = (
                f"임베딩 모델({embedding_rec['id']}, ~{embedding_ram_gb:.1f}GB)과 함께 실행 시 "
                f"최소 {min_ram + embedding_ram_gb:.1f}GB RAM이 필요합니다 (현재 {ram_gb:.1f}GB)."
            )
        elif effective_llm_ram_gb + 0.01 >= rec_ram or float(ram_gb) + 0.01 >= rec_ram:
            status = "recommended"
            reason = (
                f"{ram_gb:.0f}GB RAM 환경에서 {embedding_rec['name']} (~{embedding_ram_gb:.1f}GB) 임베딩 모델과 "
                f"동시에 여유 있게 실행됩니다 (권장 {rec_ram:.0f}GB)."
            )
        else:
            status = "compatible"
            reason = (
                f"실행 가능하지만 임베딩 모델(~{embedding_ram_gb:.1f}GB) 병행 시 메모리가 빠듯할 수 있습니다 "
                f"(최소 {min_ram:.0f}GB / 권장 {rec_ram:.0f}GB)."
            )
        entry["compatibility"] = {
            "status": status,
            "reason": reason,
            "min_ram_gb": min_ram,
            "recommended_ram_gb": rec_ram,
            "embedding_reserved_ram_gb": embedding_ram_gb,
        }
        models_out.append(entry)
        if status in {"recommended", "compatible"}:
            chat_candidates.append(entry)

    # Ensure the PC-tailored embedding recommendation is present in models_out
    if not any(str(m.get("category") or "") == "embedding" for m in models_out):
        models_out.append(dict(embedding_rec))

    chat_candidates.sort(
        key=lambda m: (
            0 if m.get("compatibility", {}).get("status") == "recommended" else 1,
            -float(m.get("recommended_ram_gb") or 0.0),
            0 if m.get("recommended_default") else 1,
            int(m.get("recommendation_priority") or 99),
        )
    )
    top_pick = chat_candidates[0] if chat_candidates else None

    return {
        "ram_gb": float(ram_gb),
        "effective_ram_gb": round(effective_llm_ram_gb, 2),
        "embedding_reserved_ram_gb": embedding_ram_gb,
        "apple_silicon": bool(apple_silicon),
        "cuda": bool(cuda),
        "top_pick": top_pick,
        "embedding_model": embedding_rec["id"],
        "embedding_recommendation": embedding_rec,
        "models": models_out,
    }

