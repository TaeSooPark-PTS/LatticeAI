"""Compatibility shim for :mod:`latticeai.services.local_knowledge`."""

import sys
import warnings

warnings.warn(
    "Importing 'local_knowledge_api' from the repository root is deprecated; "
    "use 'from latticeai.services import local_knowledge' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from latticeai.services import local_knowledge as _impl  # noqa: E402

sys.modules[__name__] = _impl
