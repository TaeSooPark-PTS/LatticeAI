from __future__ import annotations

from typing import Any, Callable, Dict, List


class ModelService:
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

    def health_full(
        self, base: Dict[str, Any], engines: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return {
            **base,
            "current_model": self._router.current_model_id,
            "loaded_models": self._router.loaded_model_ids,
            "device": "Apple Silicon MLX"
            if not self._is_public
            else "Public cloud/API runtime",
            "features": self._runtime_features(),
            "providers": self._router.detected_cloud_models(),
            "engines": engines,
        }

    def engines_payload(self, engines: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"engines": engines, "current": self._router.current_model_id}
