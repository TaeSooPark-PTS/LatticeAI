"""Independent Brain Core package facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .archive import BrainArchivePaths, EncryptedBrainArchive
from .storage import SQLiteEngine, StorageEngine, StorageUnavailable


@dataclass(frozen=True)
class BrainCoreConfig:
    data_dir: Path
    blob_dir: Optional[Path] = None
    storage_engine: Optional[StorageEngine] = None


class BrainCore:
    """Stable application boundary for the local Digital Brain.

    FastAPI, CLI, tests, and future tools should depend on this package-level
    facade instead of constructing scattered storage objects directly.
    """

    def __init__(self, config: BrainCoreConfig, *, embedder: Any = None) -> None:
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.db_path = self.data_dir / "knowledge_graph.sqlite"
        self.blob_dir = Path(config.blob_dir) if config.blob_dir else self.data_dir / "knowledge_graph_blobs"
        self.storage_engine = config.storage_engine or SQLiteEngine(self.db_path)
        caps = self.storage_engine.capabilities()
        if not caps.available:
            raise StorageUnavailable(caps.reason or f"{caps.engine} storage is unavailable")
        if caps.engine != "sqlite":
            raise StorageUnavailable(
                "The active FastAPI Brain Core runtime currently requires SQLiteEngine. "
                "Use PostgresEngine through the explicit migration/scale tooling; no SQLite fallback was attempted."
            )

        from .conversations import ConversationStore
        from .store import KnowledgeGraphStore

        self.knowledge = KnowledgeGraphStore(
            self.db_path,
            self.blob_dir,
            embedder=embedder,
            storage_engine=self.storage_engine,
        )
        self.conversations = ConversationStore(self.db_path)
        self.archive = EncryptedBrainArchive(
            BrainArchivePaths(db_path=self.db_path, blob_dir=self.blob_dir)
        )

    @classmethod
    def from_paths(
        cls,
        data_dir: Path,
        *,
        blob_dir: Optional[Path] = None,
        embedder: Any = None,
        storage_engine: Optional[StorageEngine] = None,
    ) -> "BrainCore":
        return cls(
            BrainCoreConfig(
                data_dir=Path(data_dir),
                blob_dir=blob_dir,
                storage_engine=storage_engine,
            ),
            embedder=embedder,
        )

    def status(self) -> dict:
        return {
            "storage": self.storage_engine.capabilities().as_dict(),
            "db_path": str(self.db_path),
            "blob_dir": str(self.blob_dir),
        }


__all__ = ["BrainCore", "BrainCoreConfig"]
