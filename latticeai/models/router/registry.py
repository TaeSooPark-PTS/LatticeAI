"""The mutable model registry: what is loaded, what is current, what to evict.

Everything here is guarded by one reentrant lock, because the eviction path
nests (``_enforce_local_model_limit`` → ``unload_model`` → ``_release_memory``)
and because generation must never change ``_current`` — that value is a
UI/default preference shared by every request, so a request-scoped model is
taken as an immutable snapshot instead.
"""

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from latticeai.models.model_providers import OPENAI_COMPATIBLE_PROVIDERS

from ._contract import RouterCore as _Core
from .catalog import CloudModel


class _RegistryMixin(_Core):
    """The loaded-model registry half of :class:`LLMRouter`."""

    def __init__(self):
        # A local entry is (model, tokenizer, draft_model, loader_kind); a
        # cloud entry is a CloudModel. `_unpack_local_cache` splits them.
        self._cache: Dict[str, Any] = {}
        self._current: Optional[str] = None
        self._last_used: Dict[str, float] = {}
        self._max_local_models = max(1, int(os.getenv("LATTICEAI_MAX_LOCAL_MODELS", "1")))
        # Guards the mutable model registry (_cache/_current/_last_used).
        # Reentrant because the eviction path nests: _enforce_local_model_limit
        # → unload_model → _release_memory. Never held across the heavy
        # ``run_in_executor`` load (only the sync insert/read is guarded), so a
        # long model load can't block a concurrent switch/unload from acquiring.
        self._lock = threading.RLock()

    @property
    def current_model_id(self) -> Optional[str]:
        with self._lock:
            return self._current

    @property
    def loaded_model_ids(self) -> List[str]:
        with self._lock:
            return list(self._cache.keys())

    def switch_model(self, model_id: str) -> None:
        with self._lock:
            if model_id not in self._cache:
                raise KeyError(model_id)
            self._current = model_id
            self._touch(model_id)

    def unload_model(self, model_id: str) -> None:
        with self._lock:
            self._cache.pop(model_id, None)
            self._last_used.pop(model_id, None)
            if self._current == model_id:
                self._current = next(iter(self._cache), None)
            self._release_memory()

    def unload_all(self) -> None:
        with self._lock:
            self._cache.clear()
            self._last_used.clear()
            self._current = None
            self._release_memory()

    def unload_idle_models(self, idle_seconds: int) -> List[str]:
        if idle_seconds <= 0:
            return []
        now = time.monotonic()
        unloaded = []
        with self._lock:
            for model_id, last_used in list(self._last_used.items()):
                if now - last_used >= idle_seconds:
                    self.unload_model(model_id)
                    unloaded.append(model_id)
        return unloaded

    def model_memory_policy(self) -> Dict[str, object]:
        with self._lock:
            return {
                "max_local_models": self._max_local_models,
                "loaded_count": len(self._cache),
                "last_used": dict(self._last_used),
            }

    def _touch(self, model_id: Optional[str] = None) -> None:
        model_id = model_id or self._current
        if model_id:
            self._last_used[model_id] = time.monotonic()

    def _is_local_model(self, model_id: str) -> bool:
        cached = self._cache.get(model_id)
        return cached is not None and not isinstance(cached, CloudModel)

    def _enforce_local_model_limit(self, incoming_key: str) -> None:
        with self._lock:
            local_ids = [model_id for model_id in self._cache if self._is_local_model(model_id)]
            while len(local_ids) >= self._max_local_models:
                victim = min(local_ids, key=lambda model_id: self._last_used.get(model_id, 0))
                if victim == incoming_key:
                    break
                print(f"🧹 Unloading local model to stay within memory policy: {victim}")
                self.unload_model(victim)
                local_ids = [model_id for model_id in self._cache if self._is_local_model(model_id)]

    def _local_server_error_hint(self, cloud: CloudModel, error: Exception) -> str:
        raw = str(error)
        if cloud.provider == "lmstudio":
            base_url = os.getenv("LMSTUDIO_BASE_URL") or OPENAI_COMPATIBLE_PROVIDERS["lmstudio"]["base_url"]
            return (
                f"LM Studio 연결 실패: {raw}\n\n"
                f"- LM Studio의 Developer/Local Server를 켜고 모델을 로드했는지 확인하세요.\n"
                f"- Lattice가 보는 주소는 {base_url} 입니다. 포트가 다르면 LMSTUDIO_BASE_URL을 맞춰주세요.\n"
                f"- 모델 선택창에는 LM Studio /v1/models에서 감지된 모델만 표시됩니다."
            )
        return raw

    def _unpack_local_cache(self, cached: Any) -> Tuple[Any, Any, Any, str]:
        model, tokenizer, draft_model = cached[:3]
        loader_kind = str(cached[3]) if len(cached) > 3 else "mlx_vlm"
        return model, tokenizer, draft_model, loader_kind

    def _model_snapshot(self, model_id: Optional[str] = None) -> tuple[Optional[str], object | None]:
        """Return an immutable request-scoped view of a loaded model.

        Generation must never change ``_current``: that value is a UI/default
        preference shared by every request.  Capturing the cache entry while
        holding the registry lock prevents concurrent requests from selecting
        or restoring each other's models.
        """
        with self._lock:
            selected = model_id or self._current
            if not selected:
                return None, None
            cached = self._cache.get(selected)
            if cached is None:
                raise ValueError(f"Model '{selected}' is not loaded. Load it first via /models/load.")
            self._touch(selected)
            return selected, cached
