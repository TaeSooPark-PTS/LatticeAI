"""Compatibility shim: physically moved to ``latticeai.models.router``.

Aliases itself to the physical module so router globals, identity checks, and
monkeypatching keep working through the old import path.
"""

import sys

import latticeai.models.router as _impl

sys.modules[__name__] = _impl
