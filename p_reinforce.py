"""Compatibility shim for the historical root P-Reinforce module."""

import sys
import warnings

warnings.warn(
    "Importing 'p_reinforce' from the repository root is deprecated; "
    "use 'from latticeai.services import p_reinforce' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from latticeai.services import p_reinforce as _impl  # noqa: E402

sys.modules[__name__] = _impl
