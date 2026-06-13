"""Deprecated shim: physically moved to lattice_brain.graph.network.

Kept only for the compatibility window. The module aliases itself to the
physical module so identity, singletons, and monkeypatching are preserved.
"""

import sys
import warnings

import lattice_brain.graph.network as _impl

warnings.warn(
    "latticeai.brain.network is deprecated; import lattice_brain.graph.network instead",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _impl
