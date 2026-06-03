"""Lattice AI Model Resolution + Prepare State Machine.

피드백 #1 (lattice_ai_model_recommend_download_load_issue.txt)
피드백 #2 (lattice_ai_manual_model_select_auto_download_load_fix.txt)

핵심 문제:
- 추천 카드 ID, 다운로드 ID, 로드 ID, router cache key,
  프론트가 current로 쓰는 ID가 단계마다 달라질 수 있음.
- /models/load 와 /engines/prepare-model/stream 로직이 중복.
- 다운로드 성공과 채팅 가능 상태가 다름.

해결:
1. ModelResolution: input_id → engine/resolved_model/download_id/load_id/expected_current.
2. PrepareState: RESOLVING → ENGINE_CHECK → DOWNLOADING → SERVER_STARTING
   → MODEL_LOADING → SMOKE_TEST → READY (또는 DEGRADED/FAILED).
3. PrepareReport: 로드 직후 smoke test 결과까지 포함한 최종 응답 객체.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── State enum ────────────────────────────────────────────────────────────────


class PrepareState(str, Enum):
    RESOLVING = "RESOLVING"
    ENGINE_CHECK = "ENGINE_CHECK"
    ENGINE_INSTALL = "ENGINE_INSTALL"
    DOWNLOADING = "DOWNLOADING"
    SERVER_STARTING = "SERVER_STARTING"
    MODEL_SERVING = "MODEL_SERVING"
    MODEL_LOADING = "MODEL_LOADING"
    SMOKE_TEST = "SMOKE_TEST"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


LOCAL_ENGINES = {"local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"}
ENGINE_ALIASES = {
    "mlx": "local_mlx",
    "local-mlx": "local_mlx",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "lm-studio": "lmstudio",
    "lmstudio:openai-compatible": "lmstudio",
}


def _canonical_engine(engine: Optional[str]) -> Optional[str]:
    if not engine:
        return None
    e = str(engine).strip().lower()
    e = ENGINE_ALIASES.get(e, e)
    return e or None


# ── ModelResolution dataclass ─────────────────────────────────────────────────


@dataclass
class ModelResolution:
    """모든 단계가 공유하는 canonical model identity."""

    input_id: str
    engine: str
    provider: str
    resolved_model: str
    download_id: str
    load_id: str
    expected_current: str
    display_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def from_request(
        cls,
        input_id: str,
        engine: Optional[str] = None,
        *,
        display_name: Optional[str] = None,
        user_email: Optional[str] = None,
        alias_resolver=None,
        engine_aliases: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> "ModelResolution":
        """사용자가 클릭한 input_id + engine 힌트로부터 ModelResolution 생성.

        - alias_resolver: 선택. (model_id, engine) -> resolved_model_id
        - engine_aliases: 선택. {short_name: {engine: real_id}}
        """
        raw = str(input_id or "").strip()
        if not raw:
            raise ValueError("모델 식별자가 비어 있습니다.")

        engine_hint = _canonical_engine(engine)

        # provider prefix가 붙어 있으면 그것을 우선 사용
        provider: Optional[str] = None
        model_name = raw
        if ":" in raw:
            prefix, rest = raw.split(":", 1)
            prefix_canon = _canonical_engine(prefix)
            if prefix_canon and prefix_canon in LOCAL_ENGINES.union({"openai", "anthropic", "openrouter", "groq", "together"}):
                provider = prefix_canon
                model_name = rest.strip()

        if not provider:
            provider = engine_hint or "local_mlx"

        # alias 테이블 (예: {"gemma-4-12b-it-4bit": {"local_mlx": "mlx-community/...", "ollama": "hf.co/..."}})
        resolved_model = model_name
        if engine_aliases:
            aliases = engine_aliases.get(model_name.lower())
            if aliases:
                mapped = aliases.get(provider)
                if mapped:
                    resolved_model = mapped

        # 사용자가 외부에서 추가로 alias_resolver 제공 시 마지막에 한 번 더 정규화
        if alias_resolver:
            try:
                maybe = alias_resolver(resolved_model, provider)
                if maybe:
                    if ":" in maybe and maybe.split(":", 1)[0] in LOCAL_ENGINES:
                        provider2, resolved_model = maybe.split(":", 1)
                        provider = provider2
                    else:
                        resolved_model = maybe
            except Exception:
                logger.debug("alias_resolver failed for %s", resolved_model, exc_info=True)

        download_id = resolved_model
        if provider == "local_mlx":
            load_id = resolved_model
        else:
            load_id = f"{provider}:{resolved_model}"

        expected_current = load_id
        if user_email and provider != "local_mlx":
            expected_current = f"{load_id}::{user_email}"

        return cls(
            input_id=raw,
            engine=provider,
            provider=provider,
            resolved_model=resolved_model,
            download_id=download_id,
            load_id=load_id,
            expected_current=expected_current,
            display_name=(display_name or raw),
            metadata={"engine_hint": engine_hint or ""},
        )

    # ──────────────────────────────────────────────────────────────────────

    def update_after_load(self, *, actual_current: Optional[str]) -> None:
        """LM Studio처럼 로드 후 instance_id가 부여되는 경우 동기화."""
        if not actual_current:
            return
        self.expected_current = actual_current
        # provider:model 형태면 load_id 갱신
        if ":" in actual_current:
            head = actual_current.split("::", 1)[0]
            self.load_id = head
            if ":" in head:
                self.resolved_model = head.split(":", 1)[1]


# ── PrepareReport ─────────────────────────────────────────────────────────────


@dataclass
class PrepareReport:
    """prepare_model_core / SSE 흐름이 모두 같은 형태로 돌려주는 결과."""

    status: str  # "ok" | "degraded" | "failed"
    state: PrepareState
    resolution: ModelResolution
    current: Optional[str]
    message: Optional[str] = None
    downloaded: bool = False
    loaded: bool = False
    ready_to_chat: bool = False
    compatibility_status: str = "unknown"  # ok / degraded / failed / unknown
    smoke_test: Optional[Dict[str, Any]] = None
    stage_logs: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    install_result: Dict[str, Any] = field(default_factory=dict)
    download_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value if isinstance(self.state, PrepareState) else str(self.state)
        data["resolution"] = self.resolution.to_dict()
        return data


# ── State machine helpers ─────────────────────────────────────────────────────


def transition_log(state: PrepareState, message: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    log: Dict[str, Any] = {"state": state.value, "message": message}
    if extra:
        log["extra"] = extra
    return log


__all__ = [
    "ModelResolution",
    "PrepareState",
    "PrepareReport",
    "transition_log",
    "LOCAL_ENGINES",
]
