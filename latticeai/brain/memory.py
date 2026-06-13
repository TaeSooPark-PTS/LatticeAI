"""Deprecated shim: physically moved to lattice_brain.memory.

Kept only for the compatibility window. The module aliases itself to the
physical module so identity, singletons, and monkeypatching are preserved.
"""

import sys
import warnings

import lattice_brain.memory as _impl

warnings.warn(
    "latticeai.brain.memory is deprecated; import lattice_brain.memory instead",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _impl
