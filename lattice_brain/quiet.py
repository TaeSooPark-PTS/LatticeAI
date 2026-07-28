"""Deliberately-ignored exceptions, recorded instead of erased (Brain Core).

Mirror of :mod:`latticeai.core.quiet`. Brain Core cannot import from the app
package (``tests/unit/test_import_guard.py``), and the alternative — leaving
Brain Core's suppressions silent while the app layer's are visible — would make
the quieter half the harder one to debug.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

logger = logging.getLogger("lattice_brain.suppressed")


def quiet(reason: Optional[str] = None, *, level: int = logging.DEBUG) -> None:
    """Record the exception currently being handled, then continue."""
    exc_type, exc, tb = sys.exc_info()
    if exc is None:
        return
    where = "<unknown>"
    if tb is not None:
        deepest = tb
        while deepest.tb_next is not None:
            deepest = deepest.tb_next
        frame = deepest.tb_frame
        where = f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
    label = f" ({reason})" if reason else ""
    if logger.isEnabledFor(level):
        logger.log(
            level,
            "suppressed %s at %s%s: %s",
            getattr(exc_type, "__name__", "Exception"),
            where,
            label,
            exc,
            exc_info=exc,
        )


__all__ = ["quiet"]
