"""Lattice AI Model Compatibility Layer.

피드백 #3 (lattice_ai_model_compat_fast_path.txt) 반영.

핵심 원칙:
- 무거운 호환성 검사는 모델 로드 시 1회만 (Slow Path).
- 실제 채팅 중에는 캐시된 profile을 사용하는 Fast Path.
- 답변이 깨졌을 때만 1회 retry하는 Recovery Path.

모든 함수는 안전한 디폴트로 동작하므로 기존 코드를 깨뜨리지 않는다.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Model family detection ────────────────────────────────────────────────────

FAMILY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("gpt-oss", re.compile(r"gpt[-_]?oss", re.I)),
    ("gemma", re.compile(r"gemma", re.I)),
    ("qwen", re.compile(r"qwen", re.I)),
    ("llama", re.compile(r"\bllama|meta[-_]?llama", re.I)),
    ("mistral", re.compile(r"mistral|mixtral", re.I)),
    ("phi", re.compile(r"\bphi[-_]?\d", re.I)),
    ("deepseek", re.compile(r"deepseek", re.I)),
    ("yi", re.compile(r"\byi[-_]?\d", re.I)),
    ("claude", re.compile(r"claude", re.I)),
    ("gpt-4", re.compile(r"gpt[-_]?4", re.I)),
    ("gpt-3.5", re.compile(r"gpt[-_]?3\.?5", re.I)),
    ("o1", re.compile(r"\bo1[-_]?", re.I)),
]


def detect_model_family(model_id: str) -> str:
    """주어진 model_id 문자열에서 family 코드를 추론한다."""
    if not model_id:
        return "unknown"
    raw = str(model_id)
    # provider prefix 제거
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    for family, pattern in FAMILY_PATTERNS:
        if pattern.search(raw):
            return family
    return "unknown"


# ── Family profiles ───────────────────────────────────────────────────────────

DEFAULT_STOP = ["<|im_end|>", "<|endoftext|>", "</s>", "<|user|>", "<|assistant|>"]

FAMILY_PROFILES: Dict[str, Dict[str, Any]] = {
    "gpt-oss": {
        "family": "gpt-oss",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "gpt_oss",
        "preferred_engines": ["ollama", "llamacpp", "vllm", "local_mlx"],
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 2048,
        "stop_sequences": ["<|im_end|>", "<|end|>", "</s>", "<|user|>", "<|assistant|>"],
        "disable_draft": True,
        # trim_after_user_marker는 <|user|>가 살아있어야 동작하므로 strip_role_tokens보다 먼저 실행.
        "postprocess": ["trim_after_user_marker", "strip_role_tokens"],
    },
    "gemma": {
        "family": "gemma",
        "supports_system": True,
        "supports_vision": True,
        "chat_template": "tokenizer_default_or_gemma",
        "preferred_engines": ["local_mlx", "ollama", "llamacpp"],
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
        "supports_vision": False,
        "chat_template": "qwen_chatml",
        "preferred_engines": ["ollama", "local_mlx", "vllm"],
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
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx", "llamacpp", "vllm"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["</s>", "[INST]", "[/INST]"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "mistral": {
        "family": "mistral",
        "supports_system": False,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx", "llamacpp"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["</s>", "[INST]", "[/INST]"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "phi": {
        "family": "phi",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 2048,
        "stop_sequences": ["<|end|>", "<|endoftext|>"],
        "disable_draft": False,
        "postprocess": ["strip_role_tokens"],
    },
    "deepseek": {
        "family": "deepseek",
        "supports_system": True,
        "supports_vision": False,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx", "vllm"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["<|EOT|>", "</s>"],
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
    """주어진 모델/엔진 조합에 대한 기본 호환성 프로파일을 반환한다."""
    family = detect_model_family(model_id)
    base = dict(FAMILY_PROFILES.get(family) or FAMILY_PROFILES["unknown"])
    base["engine"] = (engine or "").strip().lower() or None
    base["model_id"] = model_id
    base.setdefault("stop_sequences", list(DEFAULT_STOP))
    return base


# ── Postprocessing ────────────────────────────────────────────────────────────

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
    # role: 형태의 prefix 정리
    cleaned = re.sub(r"^\s*(?:user|assistant|system)\s*:\s*", "", cleaned, flags=re.I)
    return cleaned.strip()


def trim_after_user_marker(text: str) -> str:
    if not text:
        return text
    # 모델이 다음 user 발화까지 토해낸 경우 자르기
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
    """Fast Path 후처리. 매우 가볍게 동작한다."""
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


# ── Smoke test validation ─────────────────────────────────────────────────────

SMOKE_PROMPT = "한국어로 한 문장만 답해. 2+2는?"


def validate_smoke_response(text: str) -> Tuple[bool, str]:
    """Smoke test 응답의 정상성을 판단한다.

    반환: (정상 여부, reason)
    """
    if text is None:
        return False, "empty response"
    raw = str(text).strip()
    if not raw:
        return False, "empty response"
    # 특수 토큰 leakage
    for marker in BAD_MARKERS:
        if marker in raw:
            return False, f"role token leakage ({marker})"
    # 같은 문장 5회 이상 반복
    sentences = re.split(r"[.!?\n]+", raw)
    counts: Dict[str, int] = {}
    for s in sentences:
        key = s.strip()
        if len(key) >= 3:
            counts[key] = counts.get(key, 0) + 1
    if counts and max(counts.values()) >= 5:
        return False, "repetition detected"
    # 4 라는 답이 포함되어 있는지(약한 정상성 휴리스틱)
    if "4" not in raw and "네" not in raw and "사" not in raw:
        # 정답이 아니더라도 채팅 형식이 깨지지 않았으면 degraded로 통과
        if len(raw) < 200:
            return True, "no exact answer but formed"
        return False, "answer did not contain 4 and response too long"
    if len(raw) > 4000:
        return False, "response too long"
    return True, "ok"


# ── Compat cache (Slow Path) ──────────────────────────────────────────────────


@dataclass
class CompatProfile:
    model_id: str
    engine: Optional[str]
    family: str
    template: str
    stop: List[str]
    temperature: float
    top_p: float
    max_tokens: int
    disable_draft: bool
    postprocess: List[str]
    loaded: bool = False
    chat_compatible: bool = False
    quality_status: str = "unknown"  # ok / degraded / failed / unknown
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


def lookup_profile(model_id: str, engine: Optional[str] = None) -> Optional[CompatProfile]:
    with _CACHE_LOCK:
        return _COMPAT_CACHE.get(cache_key(model_id, engine))


def ensure_profile(model_id: str, engine: Optional[str] = None) -> CompatProfile:
    """캐시된 프로파일이 있으면 그것을, 없으면 기본값으로 생성한다."""
    cached = lookup_profile(model_id, engine)
    if cached:
        return cached
    base = get_model_profile(model_id, engine)
    profile = CompatProfile(
        model_id=model_id,
        engine=(engine or "").strip().lower() or None,
        family=base["family"],
        template=base["chat_template"],
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
) -> CompatProfile:
    profile = ensure_profile(model_id, engine)
    profile.loaded = True
    profile.chat_compatible = bool(ok)
    profile.quality_status = "ok" if ok else "degraded"
    profile.last_test_error = None if ok else reason
    profile.validated_at = time.time()
    remember_profile(profile)
    return profile


def list_cached_profiles() -> List[Dict[str, Any]]:
    with _CACHE_LOCK:
        return [p.to_dict() for p in _COMPAT_CACHE.values()]


# ── Public helpers ────────────────────────────────────────────────────────────


def normalize_generation_params(
    profile: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Family profile 기반으로 generation parameter를 보정한다."""
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
    "FAMILY_PROFILES",
    "CompatProfile",
    "detect_model_family",
    "get_model_profile",
    "fast_postprocess",
    "validate_smoke_response",
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
