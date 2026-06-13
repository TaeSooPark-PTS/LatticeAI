"""Compatibility shim: physically moved to ``lattice_brain.runtime.agent_runtime``.

Aliases itself to the physical module so identity, module-level state, and
monkeypatching keep working through the old import path.
"""

import sys

import lattice_brain.runtime.agent_runtime as _impl

sys.modules[__name__] = _impl
