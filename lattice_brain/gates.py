from __future__ import annotations

import os
from typing import Callable, Dict, Optional

TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"0", "false", "no", "off"})


class FeatureGate:
    __slots__ = ("env_var", "default", "name", "detail", "_override", "_resolver")

    def __init__(
        self,
        env_var: str,
        *,
        default: bool = False,
        name: str = "",
        detail: str = "",
    ) -> None:
        self.env_var = env_var
        self.default = bool(default)
        self.name = name or env_var
        self.detail = detail
        self._override: Optional[bool] = None
        self._resolver: Optional[Callable[[], bool]] = None

    def __call__(self) -> bool:
        return self.enabled()

    def enabled(self) -> bool:
        if self._resolver is not None:
            return bool(self._resolver())
        return self.local()

    def local(self) -> bool:
        if self._override is not None:
            return self._override
        return self.from_env()

    def from_env(self) -> bool:
        raw = os.getenv(self.env_var, "").strip().lower()
        if raw in TRUTHY:
            return True
        if raw in FALSY:
            return False
        return self.default

    def source(self) -> str:
        if self._resolver is not None:
            return "resolver"
        if self._override is not None:
            return "override"
        if os.getenv(self.env_var, "").strip().lower() in (TRUTHY | FALSY):
            return "env"
        return "default"

    def set(self, value: Optional[bool]) -> None:
        self._override = None if value is None else bool(value)

    def bind(self, resolver: Optional[Callable[[], bool]]) -> None:
        self._resolver = resolver

    def reset(self) -> None:
        self._override = None
        self._resolver = None

    def describe(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "flag": self.env_var,
            "enabled": self.enabled(),
            "default": self.default,
            "source": self.source(),
            "detail": self.detail,
        }


__all__ = ["FALSY", "TRUTHY", "FeatureGate"]
