from __future__ import annotations

from typing import Any


class ModelRuntimeError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(str(detail))


__all__ = ["ModelRuntimeError"]
