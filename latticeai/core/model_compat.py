from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from latticeai.core.module_probe import module_available as _module_available

logger = logging.getLogger(__name__)


FAMILY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("gemma", re.compile(r"gemma", re.I)),
    ("qwen", re.compile(r"qwen", re.I)),
    ("llama", re.compile(r"\bllama|meta[-_]?llama", re.I)),
    ("claude", re.compile(r"claude", re.I)),
    ("gpt_oss", re.compile(r"gpt[-_]?oss", re.I)),
    ("gpt", re.compile(r"gpt[-_]?(?:4|5)|openai", re.I)),
    ("gemini", re.compile(r"gemini", re.I)),
    ("grok", re.compile(r"grok|x[-_]?ai", re.I)),
    ("lfm2", re.compile(r"\blfm[-_]?2", re.I)),
]

ARCHITECTURE_FAMILIES: Dict[str, str] = {
    "gemma2": "gemma",
    "gemma3": "gemma",
    "gemma4": "gemma",
    "gemma4_unified": "gemma",
    "qwen2_5_vl": "qwen",
    "qwen3_vl": "qwen",
    "qwen3_vl_moe": "qwen",
    "qwen3_5": "qwen",
    "qwen3_5_moe": "qwen",
    "llama": "llama",
    "llama4": "llama",
    "mllama": "llama",
    "gpt_oss": "gpt_oss",
    "lfm2": "lfm2",
}


def family_for_architecture(architecture: Optional[str]) -> str:
    return ARCHITECTURE_FAMILIES.get(str(architecture or "").strip().lower(), "unknown")


def detect_model_family(model_id: str, architecture: Optional[str] = None) -> str:
    if architecture:
        by_arch = family_for_architecture(architecture)
        if by_arch != "unknown":
            return by_arch
    if not model_id:
        return "unknown"
    raw = str(model_id)
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    for family, pattern in FAMILY_PATTERNS:
        if pattern.search(raw):
            return family
    return "unknown"


DEFAULT_STOP = ["<|im_end|>", "<|endoftext|>", "</s>", "<|user|>", "<|assistant|>"]

FAMILY_PROFILES: Dict[str, Dict[str, Any]] = {
    "gemma": {
        "family": "gemma",
        "supports_system": True,
        "supports_vision": True,
        "chat_template": "tokenizer_default_or_gemma",
        "preferred_engines": ["local_mlx", "omlx", "ollama", "llamacpp"],
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": 4096,
        "stop_sequences": ["<end_of_turn>", "</s>"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "qwen": {
        "family": "qwen",
        "supports_system": True,
        "supports_vision": True,
        "chat_template": "qwen_chatml",
        "preferred_engines": ["omlx", "local_mlx", "ollama", "llamacpp", "vllm"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["<|im_end|>", "<|endoftext|>"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "llama": {
        "family": "llama",
        "supports_system": True,
        "supports_vision": True,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx", "omlx", "llamacpp", "vllm"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["</s>", "[INST]", "[/INST]"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "gpt_oss": {
        "family": "gpt_oss",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["local_mlx", "ollama", "llamacpp"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["<|return|>", "<|call|>", "<|endoftext|>"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "lfm2": {
        "family": "lfm2",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["local_mlx", "ollama", "llamacpp"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["<|im_end|>", "<|endoftext|>"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "unknown": {
        "family": "unknown",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": [],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 2048,
        "stop_sequences": list(DEFAULT_STOP),
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
}


def get_model_profile(model_id: str, engine: Optional[str] = None) -> Dict[str, Any]:
    family = detect_model_family(model_id)
    base = dict(FAMILY_PROFILES.get(family) or FAMILY_PROFILES["unknown"])
    base["engine"] = (engine or "").strip().lower() or None
    base["model_id"] = model_id
    base.setdefault("stop_sequences", list(DEFAULT_STOP))
    return base


GEMMA4_MLX_UNIFIED_MODULE = "mlx_vlm.models.gemma4_unified"
GEMMA4_MLX_LM_MODULES = ("mlx_lm.models.gemma4", "mlx_lm.models.gemma4_text")
GEMMA4_UNIFIED_ID_PATTERN = re.compile(r"gemma[-_/ ]?4[-_/ ]?12b", re.I)


def _is_gemma4(model_id: str) -> bool:
    raw = str(model_id or "").lower()
    return bool(re.search(r"gemma[-_/ ]?4", raw))


def _hf_model_dir(repo_id: str) -> Path:
    return Path.home() / ".ltcai" / "hf-models" / repo_id.replace("/", "__")


def _local_model_type(model_id: str) -> Optional[str]:
    raw = str(model_id or "").strip()
    if "gemma4_unified" in raw.lower():
        return "gemma4_unified"
    candidates = []
    explicit = Path(raw).expanduser()
    if raw and explicit.exists():
        candidates.append(explicit / "config.json")
    candidates.append(_hf_model_dir(raw) / "config.json")
    for config_path in candidates:
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                model_type = str(data.get("model_type") or "").strip().lower()
                if model_type:
                    return model_type
        except Exception:
            logger.debug("failed to read model config %s", config_path, exc_info=True)
    if GEMMA4_UNIFIED_ID_PATTERN.search(raw):
        return "gemma4_unified"
    return None


def _gemma4_runtime_candidates(raw_model_id: str) -> List[Dict[str, Any]]:
    mlx_available = _module_available("mlx")
    mlx_vlm_available = mlx_available and _module_available("mlx_vlm")
    mlx_vlm_unified_available = mlx_vlm_available and _module_available(
        GEMMA4_MLX_UNIFIED_MODULE
    )
    mlx_lm_available = mlx_available and _module_available("mlx_lm")
    mlx_lm_gemma4_available = mlx_lm_available and any(
        _module_available(module) for module in GEMMA4_MLX_LM_MODULES
    )
    return [
        {
            "engine": "local_mlx",
            "runtime": "MLX-VLM",
            "load_id": raw_model_id,
            "available": mlx_vlm_available,
            "supports_gemma4_unified": mlx_vlm_unified_available,
            "role": "v3_primary",
        },
        {
            "engine": "local_mlx",
            "runtime": "MLX-LM",
            "load_id": raw_model_id,
            "available": mlx_lm_gemma4_available,
            "role": "v3_text_fallback",
        },
        {
            "engine": "ollama",
            "runtime": "Ollama GGUF",
            "load_id": "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_0",
            "available": None,
            "role": "gguf_local_server",
        },
        {
            "engine": "lmstudio",
            "runtime": "LM Studio GGUF",
            "load_id": "lmstudio:ggml-org/gemma-4-12B-it-GGUF",
            "available": None,
            "role": "gguf_local_server",
        },
        {
            "engine": "llamacpp",
            "runtime": "llama.cpp GGUF",
            "load_id": "llamacpp:ggml-org/gemma-4-12B-it-GGUF",
            "available": None,
            "role": "gguf_local_server",
        },
    ]


def model_runtime_compatibility(
    model_id: str, engine: Optional[str] = None
) -> Dict[str, Any]:
    normalized_engine = (engine or "").strip().lower()
    if normalized_engine in {"", "mlx"}:
        normalized_engine = "local_mlx"
    raw_model_id = str(model_id or "")
    if raw_model_id.startswith(("local_mlx:", "mlx:")):
        raw_model_id = raw_model_id.split(":", 1)[1]

    payload: Dict[str, Any] = {
        "model_id": raw_model_id,
        "engine": normalized_engine or None,
        "family": detect_model_family(raw_model_id),
        "status": "supported",
        "supported": True,
        "checked": True,
        "runtime": None,
        "preferred_runtime": None,
        "runtime_candidates": [],
        "missing_components": [],
        "user_message": None,
        "recovery_guidance": [],
        "alternatives": [],
    }

    if normalized_engine != "local_mlx" or not _is_gemma4(raw_model_id):
        return payload

    candidates = _gemma4_runtime_candidates(raw_model_id)
    payload["runtime_candidates"] = candidates
    payload["runtime"] = "MLX-VLM"
    payload["preferred_runtime"] = "MLX-VLM"
    model_type = _local_model_type(raw_model_id)
    if model_type:
        payload["model_type"] = model_type

    mlx_available = _module_available("mlx")
    mlx_vlm_available = _module_available("mlx_vlm")
    mlx_lm_available = any(
        bool(candidate.get("available"))
        for candidate in candidates
        if candidate.get("runtime") == "MLX-LM"
    )

    if not mlx_available or not (mlx_vlm_available or mlx_lm_available):
        payload.update(
            {
                "status": "runtime_not_installed",
                "checked": False,
                "supported": True,
                "user_message": (
                    "Install the local MLX runtime before loading Gemma 4, or choose "
                    "the Gemma 4 GGUF route through Ollama, LM Studio, or llama.cpp."
                ),
                "alternatives": candidates[2:],
            }
        )
        return payload

    if model_type == "gemma4_unified" and not _module_available(
        GEMMA4_MLX_UNIFIED_MODULE
    ):
        payload.update(
            {
                "status": "runtime_update_needed",
                "supported": False,
                "reason_code": "mlx_vlm_missing_gemma4_unified_model",
                "model_type": "gemma4_unified",
                "missing_components": [GEMMA4_MLX_UNIFIED_MODULE],
                "user_message": (
                    "Gemma 4 12B uses the gemma4_unified MLX format. The installed "
                    "MLX-VLM runtime does not include that loader, so this local "
                    "model cannot load until MLX-VLM is updated."
                ),
                "recovery_guidance": [
                    "Update the MLX runtime from Library/System setup, or run: pip install --upgrade 'mlx-vlm>=0.6.3'.",
                    "After the runtime update, re-open Models so Lattice can re-check this model.",
                    "Use Gemma 4 26B A4B locally or Gemma 4 12B GGUF through Ollama, LM Studio, or llama.cpp until then.",
                ],
                "alternatives": [
                    {
                        "id": "mlx-community/gemma-4-26b-a4b-it-4bit",
                        "name": "Gemma 4 26B A4B",
                        "engine": "local_mlx",
                    },
                    {
                        "id": "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_0",
                        "name": "Gemma 4 12B GGUF",
                        "engine": "ollama",
                    },
                    {
                        "id": "lmstudio:ggml-org/gemma-4-12B-it-GGUF",
                        "name": "Gemma 4 12B GGUF",
                        "engine": "lmstudio",
                    },
                ],
                "action": "Runtime update needed",
            }
        )
        return payload

    if mlx_vlm_available:
        return payload

    payload.update(
        {
            "status": "fallback_available",
            "supported": True,
            "reason_code": "mlx_vlm_missing_gemma4_standard_runtime",
            "missing_components": ["mlx_vlm"],
            "user_message": (
                "MLX-VLM is not available for this Gemma 4 model. Lattice can use "
                "the MLX-LM text fallback or a Gemma 4 GGUF local runtime."
            ),
            "recovery_guidance": [
                "Use the MLX-LM text fallback for text chat.",
                "Use a Gemma 4 GGUF model through Ollama, LM Studio, or llama.cpp if the MLX route fails.",
            ],
            "alternatives": [
                {
                    "id": candidate["load_id"],
                    "name": candidate["runtime"],
                    "engine": candidate["engine"],
                }
                for candidate in candidates
                if candidate.get("role") != "v3_primary"
            ],
        }
    )
    payload["preferred_runtime"] = "MLX-LM fallback"
    return payload


def friendly_model_runtime_error(
    error: BaseException | str,
    *,
    model_id: Optional[str] = None,
    engine: Optional[str] = None,
) -> Dict[str, Any]:
    raw = str(error or "")
    compat = model_runtime_compatibility(model_id or raw, engine=engine)
    if not compat.get("supported", True):
        return {
            "status": compat.get("status") or "unsupported",
            "model_id": model_id,
            "engine": engine,
            "user_message": compat.get("user_message")
            or ("The selected model is not supported by the installed local runtime."),
            "recovery_guidance": compat.get("recovery_guidance")
            or [
                "Choose a recommended alternative model.",
                "Update the local runtime and try validation again.",
            ],
            "alternatives": compat.get("alternatives") or [],
            "missing_components": compat.get("missing_components") or [],
            "action": compat.get("action"),
            "reason_code": compat.get("reason_code")
            or "runtime_model_type_unsupported",
        }
    return {
        "status": "load_failed",
        "model_id": model_id,
        "engine": engine,
        "user_message": (
            "The model could not be loaded. Check that the runtime is installed, "
            "the model files are present, and try a recommended alternative if the issue continues."
        ),
        "recovery_guidance": [
            "Open Models and run the setup flow again.",
            "Confirm downloads were explicitly allowed for models that are not on this computer.",
            "Try a recommended smaller local model if memory is low.",
        ],
    }


BAD_MARKERS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|user|>",
    "<|assistant|>",
    "<|endoftext|>",
    "### Instruction:",
    "### Response:",
    "[/INST]",
    "[INST]",
    "<s>",
]


def strip_role_tokens(text: str) -> str:
    if not text:
        return text
    cleaned = text
    for marker in BAD_MARKERS:
        cleaned = cleaned.replace(marker, "")
    cleaned = re.sub(r"^\s*(?:user|assistant|system)\s*:\s*", "", cleaned, flags=re.I)
    return cleaned.strip()


def trim_after_user_marker(text: str) -> str:
    if not text:
        return text
    for marker in ("<|user|>", "\nuser:", "\nUser:", "### Instruction:"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.strip()


POSTPROCESSORS = {
    "strip_role_tokens": strip_role_tokens,
    "trim_after_user_marker": trim_after_user_marker,
}


def fast_postprocess(text: str, profile: Dict[str, Any]) -> str:
    if not text:
        return text
    out = text
    for name in profile.get("postprocess") or []:
        fn = POSTPROCESSORS.get(name)
        if fn:
            try:
                out = fn(out)
            except Exception:
                logger.debug("postprocessor %s failed", name, exc_info=True)
    return out


SMOKE_PROMPT = "한국어로 한 문장만 답해. 2+2는?"


def classify_smoke_response(text: Optional[str]) -> Tuple[str, str]:
    if text is None:
        return "failed", "empty response"
    raw = str(text).strip()
    if not raw:
        return "failed", "empty response"

    for marker in BAD_MARKERS:
        if marker in raw:
            return "failed", f"role token leakage ({marker})"
    if re.search(r"<\|[^|]{0,40}\|>", raw):
        return "failed", "special token leakage"
    if re.match(r"^\s*(?:assistant|system|user)\s*:", raw, flags=re.I):
        return "failed", "role marker leakage"

    sentences = [s.strip() for s in re.split(r"[.!?\n]+", raw) if len(s.strip()) >= 3]
    counts: Dict[str, int] = {}
    for key in sentences:
        counts[key] = counts.get(key, 0) + 1
    max_rep = max(counts.values()) if counts else 0
    if max_rep >= 5:
        return "failed", "severe repetition"
    if re.search(r"(.{1,20}?)\1{6,}", raw):
        return "failed", "runaway repetition"

    if len(raw) > 4000:
        return "failed", "response too long"

    degraded: List[str] = []
    if max_rep >= 3:
        degraded.append("mild repetition")
    if len(raw) > 600:
        degraded.append("response longer than expected")
    has_answer = ("4" in raw) or ("네" in raw) or ("사" in raw)
    if not has_answer:
        degraded.append("answer did not contain expected result")
    if degraded:
        return "degraded", "; ".join(degraded)
    return "ok", "ok"


def validate_smoke_response(text: str) -> Tuple[bool, str]:
    status, reason = classify_smoke_response(text)
    return status != "failed", reason


@dataclass
class CompatProfile:
    model_id: str
    engine: Optional[str]
    family: str
    template: str
    supports_vision: bool
    stop: List[str]
    temperature: float
    top_p: float
    max_tokens: int
    disable_draft: bool
    postprocess: List[str]
    loaded: bool = False
    chat_compatible: bool = False
    quality_status: str = "unknown"
    last_test_error: Optional[str] = None
    validated_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_COMPAT_CACHE: Dict[str, CompatProfile] = {}
_CACHE_LOCK = threading.RLock()


def cache_key(model_id: str, engine: Optional[str] = None) -> str:
    eng = (engine or "").strip().lower()
    return f"{eng}:{model_id}" if eng else str(model_id)


def remember_profile(profile: CompatProfile) -> None:
    with _CACHE_LOCK:
        _COMPAT_CACHE[cache_key(profile.model_id, profile.engine)] = profile


def lookup_profile(
    model_id: str, engine: Optional[str] = None
) -> Optional[CompatProfile]:
    with _CACHE_LOCK:
        return _COMPAT_CACHE.get(cache_key(model_id, engine))


def ensure_profile(model_id: str, engine: Optional[str] = None) -> CompatProfile:
    cached = lookup_profile(model_id, engine)
    if cached:
        return cached
    base = get_model_profile(model_id, engine)
    profile = CompatProfile(
        model_id=model_id,
        engine=(engine or "").strip().lower() or None,
        family=base["family"],
        template=base["chat_template"],
        supports_vision=bool(base.get("supports_vision", False)),
        stop=list(base["stop_sequences"]),
        temperature=float(base["temperature"]),
        top_p=float(base["top_p"]),
        max_tokens=int(base["max_tokens"]),
        disable_draft=bool(base.get("disable_draft", False)),
        postprocess=list(base.get("postprocess") or []),
    )
    remember_profile(profile)
    return profile


def record_smoke_result(
    model_id: str,
    engine: Optional[str],
    ok: bool,
    reason: str,
    *,
    status: Optional[str] = None,
) -> CompatProfile:
    profile = ensure_profile(model_id, engine)
    profile.loaded = True
    profile.chat_compatible = bool(ok)
    if status in ("ok", "degraded", "failed"):
        profile.quality_status = status
    else:
        profile.quality_status = "ok" if ok else "degraded"
    profile.last_test_error = None if ok else reason
    profile.validated_at = time.time()
    remember_profile(profile)
    return profile


def list_cached_profiles() -> List[Dict[str, Any]]:
    with _CACHE_LOCK:
        return [p.to_dict() for p in _COMPAT_CACHE.values()]


def normalize_generation_params(
    profile: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = {
        "temperature": profile.get("temperature", 0.2),
        "top_p": profile.get("top_p", 0.9),
        "max_tokens": profile.get("max_tokens", 2048),
        "stop": list(profile.get("stop_sequences") or DEFAULT_STOP),
    }
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                out[k] = v
    return out


def get_stop_sequences(model_id: str, engine: Optional[str] = None) -> List[str]:
    profile = ensure_profile(model_id, engine)
    return list(profile.stop)


__all__ = [
    "ARCHITECTURE_FAMILIES",
    "FAMILY_PROFILES",
    "CompatProfile",
    "detect_model_family",
    "family_for_architecture",
    "friendly_model_runtime_error",
    "get_model_profile",
    "model_runtime_compatibility",
    "fast_postprocess",
    "validate_smoke_response",
    "classify_smoke_response",
    "ensure_profile",
    "lookup_profile",
    "remember_profile",
    "record_smoke_result",
    "list_cached_profiles",
    "normalize_generation_params",
    "get_stop_sequences",
    "strip_role_tokens",
    "SMOKE_PROMPT",
]
