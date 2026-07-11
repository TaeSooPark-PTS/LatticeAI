"""Compatibility shim for :mod:`latticeai.setup.wizard`."""

import sys

from latticeai.setup import wizard as _impl

sys.modules[__name__] = _impl
