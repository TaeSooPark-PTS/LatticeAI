"""Typed mapping base for application assembly stages."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Iterator, Mapping


class RuntimeStage(Mapping[str, Any]):
    """Dataclass mixin that preserves the legacy mapping access during DI migration."""

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return (field.name for field in fields(self))

    def __len__(self) -> int:
        return len(fields(self))

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and any(field.name == key for field in fields(self))


__all__ = ["RuntimeStage"]
