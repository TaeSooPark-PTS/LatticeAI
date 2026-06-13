"""Compatibility shim: implementation moved to lattice_brain.graph.ingest.

This module aliases itself to the physical module so identity, singletons,
and monkeypatching behave as if the old flat path were the real module.
"""

import sys

from .graph import ingest as _impl

sys.modules[__name__] = _impl
