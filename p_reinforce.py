"""Compatibility shim for the historical root P-Reinforce module."""

import sys

from latticeai.services import p_reinforce as _impl

sys.modules[__name__] = _impl
