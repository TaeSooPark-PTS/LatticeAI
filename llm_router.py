"""Compatibility shim: physically moved to ``latticeai.models.router``.

Aliases itself to the physical module so router globals, identity checks, and
monkeypatching keep working through the old import path.
"""

import sys
import warnings

warnings.warn(
    "Importing 'llm_router' from the repository root is deprecated; "
    "use 'import latticeai.models.router' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

import latticeai.models.router as _impl  # noqa: E402

sys.modules[__name__] = _impl
