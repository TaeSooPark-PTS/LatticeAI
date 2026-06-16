"""Compatibility shim for the historical root CLI module."""

from latticeai.cli import entrypoint as _impl


if __name__ == "__main__":
    _impl.main()
else:
    import sys

    sys.modules[__name__] = _impl
