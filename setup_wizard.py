"""Compatibility shim for :mod:`latticeai.setup.wizard`."""

import sys
import warnings

warnings.warn(
    "Importing 'setup_wizard' from the repository root is deprecated; "
    "use 'from latticeai.setup import wizard' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from latticeai.setup import wizard as _impl  # noqa: E402

sys.modules[__name__] = _impl
