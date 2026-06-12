"""lattice-brain — independent Brain Core package for Lattice AI.

Heavy graph modules are lazy-loaded so storage and archive utilities remain
usable without importing the FastAPI application or creating runtime globals.
"""

from .archive import BrainArchivePaths, EncryptedBrainArchive
from .core import BrainCore, BrainCoreConfig
from .storage import (
    DockerPostgresPlan,
    DockerPostgresWizard,
    PostgresConfig,
    PostgresEngine,
    SQLiteEngine,
    SQLiteToPostgresMigrator,
    StorageCapabilities,
    StorageEngine,
    StorageUnavailable,
    storage_from_env,
)

__version__ = "4.2.0"

__all__ = [
    "AssembledContext",
    "BrainArchivePaths",
    "BrainCore",
    "BrainCoreConfig",
    "BrainMemory",
    "ContextAssembler",
    "ContextSection",
    "ConversationStore",
    "DockerPostgresPlan",
    "DockerPostgresWizard",
    "EncryptedBrainArchive",
    "KnowledgeGraphStore",
    "PostgresConfig",
    "PostgresEngine",
    "SQLiteEngine",
    "SQLiteToPostgresMigrator",
    "StorageCapabilities",
    "StorageEngine",
    "StorageUnavailable",
    "storage_from_env",
    "__version__",
]


def __getattr__(name: str):
    if name in {"AssembledContext", "ContextAssembler", "ContextSection"}:
        from .context import AssembledContext, ContextAssembler, ContextSection

        return {
            "AssembledContext": AssembledContext,
            "ContextAssembler": ContextAssembler,
            "ContextSection": ContextSection,
        }[name]
    if name == "ConversationStore":
        from .conversations import ConversationStore

        return ConversationStore
    if name == "BrainMemory":
        from .memory import BrainMemory

        return BrainMemory
    if name == "KnowledgeGraphStore":
        from .store import KnowledgeGraphStore

        return KnowledgeGraphStore
    raise AttributeError(name)
