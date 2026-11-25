from __future__ import annotations

from dataclasses import Field, fields
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Iterator, Mapping


class RuntimeStage(Mapping[str, Any]):
    if TYPE_CHECKING:
        __dataclass_fields__: ClassVar[Dict[str, Field[Any]]]

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
