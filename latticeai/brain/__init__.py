"""Compatibility namespace for the standalone :mod:`lattice_brain` package."""

from lattice_brain.core import BrainCore, BrainCoreConfig
from latticeai.brain.context import AssembledContext, ContextAssembler, ContextSection
from latticeai.brain.conversations import ConversationStore
from latticeai.brain.memory import BrainMemory
from latticeai.brain.store import KnowledgeGraphStore

__all__ = [
    "AssembledContext",
    "BrainCore",
    "BrainCoreConfig",
    "BrainMemory",
    "ContextAssembler",
    "ContextSection",
    "ConversationStore",
    "KnowledgeGraphStore",
]
