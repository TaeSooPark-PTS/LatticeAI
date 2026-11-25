from __future__ import annotations

import logging
from typing import Optional

from lattice_brain.quiet import record_suppressed

logger = logging.getLogger("latticeai.suppressed")


def quiet(reason: Optional[str] = None, *, level: int = logging.DEBUG) -> None:
    record_suppressed(logger, reason, level=level)


__all__ = ["quiet"]
