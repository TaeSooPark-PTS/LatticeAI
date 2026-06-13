"""Compatibility shim: implementation moved to lattice_brain.graph.retrieval.

This module aliases itself to the physical module so identity, singletons,
and monkeypatching behave as if the old flat path were the real module.
"""

import sys

from .graph import retrieval as _impl

sys.modules[__name__] = _impl
