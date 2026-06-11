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
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Model family detection ────────────────────────────────────────────────────

FAMILY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("gemma", re.compile(r"gemma", re.I)),
    ("qwen", re.compile(r"qwen", re.I)),
    ("llama", re.compile(r"\bllama|meta[-_]?llama", re.I)),
    ("claude", re.compile(r"claude", re.I)),
    ("gpt", re.compile(r"gpt[-_]?(?:4|5)|openai", re.I)),
    ("gemini", re.compile(r"gemini", re.I)),
    ("grok", re.compile(r"grok|x[-_]?ai", re.I)),
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
        "supports_vision": True,
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
        "supports_vision": True,
        "chat_template": "tokenizer_default",
        "preferred_engines": ["ollama", "local_mlx", "llamacpp", "vllm"],
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stop_sequences": ["</s>", "[INST]", "[/INST]"],
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


def classify_smoke_response(text: str) -> Tuple[str, str]:
    """Smoke test 응답을 ok / degraded / failed 로 분류한다. (item 3-3)

    - failed: 채팅에 쓸 수 없는 수준 (빈 응답, 특수/role 토큰 누출, 심한 반복,
      과도하게 긴 출력).
    - degraded: 로드/채팅은 되지만 품질이 일정하지 않음 (가벼운 반복, 기대한
      정답 없음, 다소 긴 출력).
    - ok: 형식·정답·길이 모두 정상.

    반환: (status, reason)
    """
    if text is None:
        return "failed", "empty response"
    raw = str(text).strip()
    if not raw:
        return "failed", "empty response"

    # 1. role / 특수 토큰 누출 → 채팅 형식이 깨진 것이므로 failed.
    for marker in BAD_MARKERS:
        if marker in raw:
            return "failed", f"role token leakage ({marker})"
    if re.search(r"<\|[^|]{0,40}\|>", raw):
        return "failed", "special token leakage"
    # role marker 줄 출력 (예: "assistant:" 로 시작)
    if re.match(r"^\s*(?:assistant|system|user)\s*:", raw, flags=re.I):
        return "failed", "role marker leakage"

    # 2. 반복 감지.
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", raw) if len(s.strip()) >= 3]
    counts: Dict[str, int] = {}
    for key in sentences:
        counts[key] = counts.get(key, 0) + 1
    max_rep = max(counts.values()) if counts else 0
    if max_rep >= 5:
        return "failed", "severe repetition"
    # 문자열 단위 폭주 반복 (예: "안녕안녕안녕…", "AAAA…")
    if re.search(r"(.{1,20}?)\1{6,}", raw):
        return "failed", "runaway repetition"

    # 3. 과도하게 긴 출력 → failed.
    if len(raw) > 4000:
        return "failed", "response too long"

    # 4. 여기까지 왔으면 채팅은 가능. degraded 신호를 모은다.
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
    """하위호환 wrapper. (ok 또는 degraded면 채팅 가능 → True)

    반환: (채팅 가능 여부, reason)
    """
    status, reason = classify_smoke_response(text)
    return status != "failed", reason


# ── Compat cache (Slow Path) ──────────────────────────────────────────────────


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
    """Smoke 결과를 프로필 캐시에 기록한다.

    status 가 주어지면 ok/degraded/failed 3분류를 그대로 저장한다.
    (하위호환: status 없이 ok bool만 오면 ok→"ok", False→"degraded")
    """
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
