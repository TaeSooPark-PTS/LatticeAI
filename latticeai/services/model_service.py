"""Model / engine state helpers used by the health and model routers.

Pure payload builders with no FastAPI dependency. The model runtime (LLMRouter),
``engine_status``, and ``runtime_features`` remain owned by ``server_app``; this
module just assembles the response shapes so the health/model routers stay thin
and the summaries live in one place.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class ModelService:
    """Assembles health/engine summary payloads from injected runtime pieces."""

    def __init__(
        self,
        *,
        model_router: Any,
        runtime_features: Callable[[], Dict[str, Any]],
        is_public: bool,
    ):
        self._router = model_router
        self._runtime_features = runtime_features
        self._is_public = is_public

    def runtime(self) -> Dict[str, Any]:
        return self._runtime_features()

    def health_base(self, *, version: str, mode: str) -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": version,
            "mode": mode,
            "platform": "AI Workspace OS",
        }

    def health_full(self, base: Dict[str, Any], engines: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            **base,
            "current_model": self._router.current_model_id,
            "loaded_models": self._router.loaded_model_ids,
            "device": "Apple Silicon MLX" if not self._is_public else "Public cloud/API runtime",
            "features": self._runtime_features(),
            "providers": self._router.detected_cloud_models(),
            "engines": engines,
        }

    def engines_payload(self, engines: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"engines": engines, "current": self._router.current_model_id}
