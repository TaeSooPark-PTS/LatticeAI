"""StorageEngine contracts for the independent Brain Core package."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


class StorageUnavailable(RuntimeError):
    """Raised when an explicitly requested storage engine cannot be used."""


@dataclass(frozen=True)
class StorageCapabilities:
    engine: str
    available: bool
    reason: Optional[str] = None
    vector_backend: str = "none"
    vector_available: bool = False
    backup_restore: bool = False
    migrations: bool = False
    encrypted_archives: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "available": self.available,
            "reason": self.reason,
            "vector_backend": self.vector_backend,
            "vector_available": self.vector_available,
            "backup_restore": self.backup_restore,
            "migrations": self.migrations,
            "encrypted_archives": self.encrypted_archives,
            "metadata": self.metadata,
        }


class StorageEngine(ABC):
    """Unified storage interface used by Brain Core.

    The knowledge graph currently uses SQL directly, so ``connect`` is part of
    the contract. Engines must fail loudly when unavailable; callers must not
    silently fall back to SQLite after an explicit Postgres selection.
    """

    name: str

    @abstractmethod
    def capabilities(self) -> StorageCapabilities:
        """Return an honest capability report."""

    @abstractmethod
    def initialize(self) -> Dict[str, Any]:
        """Create required storage structures or raise ``StorageUnavailable``."""

    @abstractmethod
    def connect(self) -> Any:
        """Return a DB-API-like connection for this engine."""

    @abstractmethod
    def backup(self, destination: Path) -> Dict[str, Any]:
        """Create a faithful engine backup at ``destination``."""

    @abstractmethod
    def restore(self, source: Path) -> Dict[str, Any]:
        """Restore a faithful engine backup from ``source``."""


__all__ = ["StorageCapabilities", "StorageEngine", "StorageUnavailable"]
