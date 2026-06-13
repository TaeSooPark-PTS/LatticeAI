"""Deprecated shim: physically moved to lattice_brain.context.

Kept only for the compatibility window. The module aliases itself to the
physical module so identity, singletons, and monkeypatching are preserved.
"""

import sys
import warnings

import lattice_brain.context as _impl

warnings.warn(
    "latticeai.brain.context is deprecated; import lattice_brain.context instead",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = _impl
