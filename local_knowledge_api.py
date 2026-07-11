"""Compatibility shim for :mod:`latticeai.services.local_knowledge`."""

import sys

from latticeai.services import local_knowledge as _impl

sys.modules[__name__] = _impl
