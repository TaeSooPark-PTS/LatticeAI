"""Compatibility shim: physically moved to ``lattice_brain.runtime.hooks``.

Aliases itself to the physical module so identity, module-level state, and
monkeypatching keep working through the old import path.
"""

import sys

import lattice_brain.runtime.hooks as _impl

sys.modules[__name__] = _impl
