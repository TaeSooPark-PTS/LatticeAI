"""Compatibility shim: physically moved to ``latticeai.core.mcp_registry``.

Aliases itself to the physical module so registry cache state, identity checks,
and monkeypatching keep working through the old import path.
"""

import sys
import warnings

warnings.warn(
    "Importing 'mcp_registry' from the repository root is deprecated; "
    "use 'import latticeai.core.mcp_registry' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

import latticeai.core.mcp_registry as _impl  # noqa: E402

sys.modules[__name__] = _impl
