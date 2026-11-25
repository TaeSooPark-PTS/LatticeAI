from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Tuple

from .catalog import CloudModel


class RouterCore:
    _cache: Dict[str, Any]
    _current: Optional[str]
    _last_used: Dict[str, float]
    _max_local_models: int
    _lock: Any

    def _touch(self, model_id: Optional[str] = None) -> None:
        raise NotImplementedError

    def _enforce_local_model_limit(self, incoming_key: str) -> None:
        raise NotImplementedError

    def _release_memory(self) -> None:
        raise NotImplementedError

    def _model_snapshot(
        self, model_id: Optional[str] = None
    ) -> tuple[Optional[str], object | None]:
        raise NotImplementedError

    def _unpack_local_cache(self, cached: Any) -> Tuple[Any, Any, Any, str]:
        raise NotImplementedError

    def _local_server_error_hint(self, cloud: CloudModel, error: Exception) -> str:
        raise NotImplementedError

    @staticmethod
    def _drain_stream_queue(queue: "Any") -> AsyncIterator[str]:
        raise NotImplementedError
