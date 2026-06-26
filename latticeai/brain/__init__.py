"""Deprecated compatibility namespace for the standalone :mod:`lattice_brain` package.

The Brain Core implementation physically lives in ``lattice_brain`` as of
v4.4.0. This package only re-exports the public surface for older imports;
new code must import ``lattice_brain`` directly.
"""

import warnings

from lattice_brain import (
    AssembledContext,
    BrainCore,
    BrainCoreConfig,
    BrainMemory,
    ContextAssembler,
    ContextSection,
    ConversationStore,
    KnowledgeGraphStore,
)

warnings.warn(
    "latticeai.brain is deprecated; import lattice_brain instead",
    DeprecationWarning,
    stacklevel=2,
)  # one-time per import site in practice; shims remain for compat surface

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
