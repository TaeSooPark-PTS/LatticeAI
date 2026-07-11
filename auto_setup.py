"""Compatibility shim for :mod:`latticeai.setup.auto_setup`."""

from latticeai.setup import auto_setup as _impl


if __name__ == "__main__":
    raise SystemExit(_impl._main())
else:
    import sys

    sys.modules[__name__] = _impl
