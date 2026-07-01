"""Compatibility shim: physically moved to ``latticeai.core.mcp_registry``.

Aliases itself to the physical module so registry cache state, identity checks,
and monkeypatching keep working through the old import path.
"""

import sys

import latticeai.core.mcp_registry as _impl

sys.modules[__name__] = _impl
