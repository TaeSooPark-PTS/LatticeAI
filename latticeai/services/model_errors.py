"""Transport-neutral model runtime failures."""

from __future__ import annotations

from typing import Any


class ModelRuntimeError(RuntimeError):
    """A model operation failure with an API-safe status and detail payload."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(str(detail))


__all__ = ["ModelRuntimeError"]
