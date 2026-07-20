"""Compatibility shim for :mod:`latticeai.setup.auto_setup`."""

import warnings

warnings.warn(
    "Importing 'auto_setup' from the repository root is deprecated; "
    "use 'from latticeai.setup import auto_setup' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from latticeai.setup import auto_setup as _impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_impl._main())
else:
    import sys

    sys.modules[__name__] = _impl
