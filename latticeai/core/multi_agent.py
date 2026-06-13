"""Compatibility shim: physically moved to ``lattice_brain.runtime.multi_agent``.

Aliases itself to the physical module so identity, module-level state, and
monkeypatching keep working through the old import path.
"""

import sys

import lattice_brain.runtime.multi_agent as _impl

sys.modules[__name__] = _impl
