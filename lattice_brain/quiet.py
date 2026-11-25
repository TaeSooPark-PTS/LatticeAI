from __future__ import annotations

import logging
import sys
from typing import Optional

logger = logging.getLogger("lattice_brain.suppressed")


def record_suppressed(
    target_logger: logging.Logger,
    reason: Optional[str] = None,
    *,
    level: int = logging.DEBUG,
) -> None:
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
    if target_logger.isEnabledFor(level):
        target_logger.log(
            level,
            "suppressed %s at %s%s: %s",
            getattr(exc_type, "__name__", "Exception"),
            where,
            label,
            exc,
            exc_info=exc,
        )


def quiet(reason: Optional[str] = None, *, level: int = logging.DEBUG) -> None:
    record_suppressed(logger, reason, level=level)


__all__ = ["quiet", "record_suppressed"]
