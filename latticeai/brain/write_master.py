"""Deprecated shim: physically moved to lattice_brain.graph.write_master.

Kept only for the compatibility window. The module aliases itself to the
physical module so identity, singletons, and monkeypatching are preserved.
"""

import sys
import warnings

import lattice_brain.graph.write_master as _impl

warnings.warn(
    "latticeai.brain.write_master is deprecated; import lattice_brain.graph.write_master instead",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _impl
