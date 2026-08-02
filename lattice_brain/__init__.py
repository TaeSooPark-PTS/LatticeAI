"""lattice-brain — independent Brain Core package for Lattice AI.

Physically hosts the knowledge graph (``lattice_brain.graph``), memory,
context assembly, conversations, ingestion, agent/hook runtime
(``lattice_brain.runtime``), workflow engine, portability (backup/restore and
``.latticebrain`` archives), and the storage abstraction.

The package never imports ``latticeai``; FastAPI and the desktop product
import this package, not the other way around. Heavy graph modules are
lazy-loaded so storage and archive utilities remain usable without creating
runtime globals.
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

__version__ = "10.6.1"

__all__ = [
    "AgentRuntime",
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
    "IngestionItem",
    "IngestionPipeline",
    "KGPortabilityService",
    "KnowledgeGraphStore",
    "LatticeBrainQuality",
    "MultiAgentOrchestrator",
    "PostgresConfig",
    "PostgresEngine",
    "SQLiteEngine",
    "SQLiteToPostgresMigrator",
    "StorageCapabilities",
    "StorageEngine",
    "StorageUnavailable",
    "WorkflowEngine",
    "storage_from_env",
    "__version__",
]

_LAZY = {
    "AssembledContext": ("context", "AssembledContext"),
    "ContextAssembler": ("context", "ContextAssembler"),
    "ContextSection": ("context", "ContextSection"),
    "ConversationStore": ("conversations", "ConversationStore"),
    "BrainMemory": ("memory", "BrainMemory"),
    "KnowledgeGraphStore": ("graph.store", "KnowledgeGraphStore"),
    "LatticeBrainQuality": ("quality", "LatticeBrainQuality"),
    "IngestionItem": ("ingestion", "IngestionItem"),
    "IngestionPipeline": ("ingestion", "IngestionPipeline"),
    "KGPortabilityService": ("portability", "KGPortabilityService"),
    "WorkflowEngine": ("workflow", "WorkflowEngine"),
    "AgentRuntime": ("runtime.agent_runtime", "AgentRuntime"),
    "MultiAgentOrchestrator": ("runtime.multi_agent", "MultiAgentOrchestrator"),
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module_path, attr = target
    import importlib

    module = importlib.import_module(f".{module_path}", __name__)
    return getattr(module, attr)
