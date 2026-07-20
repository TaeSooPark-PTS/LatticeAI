"""Compatibility shim for the historical root CLI module."""

import warnings

warnings.warn(
    "Importing 'ltcai_cli' from the repository root is deprecated; "
    "use 'from latticeai.cli import entrypoint' instead. "
    "The root shim will be removed in a future major release.",
    DeprecationWarning,
    stacklevel=2,
)

from latticeai.cli import entrypoint as _impl  # noqa: E402


if __name__ == "__main__":
    _impl.main()
else:
    import sys

    sys.modules[__name__] = _impl
