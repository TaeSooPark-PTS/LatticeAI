"""Compatibility shim: physically moved to ``lattice_brain.workflow``.

Aliases itself to the physical module so identity, module-level state, and
monkeypatching keep working through the old import path.
"""

import sys

import lattice_brain.workflow as _impl

sys.modules[__name__] = _impl
