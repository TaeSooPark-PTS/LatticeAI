"""Compatibility shim for the v4 Knowledge Graph schema."""

import warnings as _warnings

_warnings.warn(
    "Importing 'kg_schema' from the repository root is deprecated; "
    "use 'from lattice_brain.graph.schema import ...' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from lattice_brain.graph.schema import *  # noqa: F403,F401,E402
